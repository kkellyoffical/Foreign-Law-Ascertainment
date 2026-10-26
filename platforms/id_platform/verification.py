"""引用校验（反臆造）：从助手答案文本中抽取法源引用，逐条验真。印度尼西亚版。

四级判定（与南非 V1.2+ / 印度版逻辑一致，公开接口与印度版完全相同）：
- verified   已核验：该引用出现在本轮检索到的 citations 原文材料中；
- partial    部分核验：本轮材料未直接覆盖，但库内权威数据中确认存在（真实、但未经本轮取证）；
- declared   已声明：模型在引用同句已如实标注"未经核验/库内未收录"（诚实行为，不冤枉好人）；
- unverified 未核验：本轮材料与库内数据均未能确认（可能为模型臆造，建议人工复核）。

设计原则：校验器是**纯规则引擎**（正则抽取 + 字符串匹配 + PG 存在性回查），
绝不引入第二个大模型——校验环节自身必须不可能臆造。

印度尼西亚化差异：
1. 法源引用体系为印尼式（编号+年份是强锚）：Law No. 13 of 2003 (on Manpower) /
   UU No. 13/2003 / Government Regulation No. 35 of 2021 / Perppu No. 2 of 2022 /
   Presidential Regulation (Perpres) No. 10 of 2021 / Presidential Decree；
2. 库内英译标题自带编号年份（"Act No. 13 Of 2003" / "Presidential Regulation
   Number 124 In 2012"）→ 回查用 act_year 列 + 标题编号正则，比标题词更可靠；
3. 条文单位统一 Article（Article 156），就近归属前文法源（±300 字符）；
4. 宪法：1945 Constitution / UUD 1945（库内可能无本体，回查不到如实降级）；
5. 成文法单底座：无判例库，CASE_CITE_RES 为空；
   成文法回查走 `印尼成文法库`（config.ID_STATUTES_DB_DSN）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from za_common import evidence_guard

from . import db

logger = logging.getLogger("id_platform.verification")

MAX_REFS = 40  # 单次校验引用上限，防超长答案拖慢


def _norm_actno(num: str | None) -> str:
    """Act 编号归一：'136' → 'Act 136'；'Act 136' 原样；修正法令 'A1648' 原样。
    与库内 statutory_documents.act_number（'Act 125' / 'A1648'）同口径。"""
    n = re.sub(r"\s+", " ", (num or "")).strip()
    if not n:
        return ""
    if re.fullmatch(r"[Aa]\d+", n):
        return n.upper().replace("A", "A", 1)
    m = re.fullmatch(r"(?:Act|Akta)\s*(\d+)", n, re.I)
    if m:
        return f"Act {m.group(1)}"
    if n.isdigit():
        return f"Act {n}"
    return n


def _norm_citation(cite: str | None) -> str:
    """判例引证归一：去空格大写（[1996] 1 MLJ 261 → [1996]1MLJ261）。
    与 ingest 的 citation_norm（re.sub(r"\\s+", "", c).upper()）同口径。"""
    return re.sub(r"\s+", "", cite or "").upper()


# ---- 抽取正则（印尼式）----
# 印尼引证的强锚是"法源类型 + 编号 + 年份"，库内英译标题自带编号年份可回查。
# 法源类型词（显示名 → 库内标题类型正则），按特异性排序，Law 最后兜底
TYPE_PATTERNS: list[tuple[str, str]] = [
    ("Government Regulation in Lieu of Law", r"(government\s+regulation\s+in\s+lieu|perpu)"),
    ("Government Regulation", r"government\s+regulation"),
    ("Presidential Regulation", r"presidential\s+regulation"),
    ("Presidential Decree", r"presidential\s+decree"),
    ("Ministerial Regulation", r"minist"),
    ("Law", r"(act|law|undang)"),
]
# Law No. 13 of 2003 (on/concerning Manpower) / Law Number 11 Year 2020 /
# Government Regulation No. 35 of 2021 / Presidential Regulation (Perpres) No. 10 of 2021
LAW_NUM_RE = re.compile(
    r"\b(Law|Act|Government\s+Regulation(?:\s+in\s+Lieu\s+of\s+Law)?|"
    r"Presidential\s+Regulation|Presidential\s+Decree|"
    r"Minister(?:ial)?(?:\s+of\s+[A-Za-z ]{3,30}?)?\s+Regulation)"
    r"(?:\s*\((?:UU|PP|Perppu|Perpres|Keppres|Permen[a-z]*)\))?"
    r"\s+(?:of\s+the\s+Republic\s+of\s+Indonesia\s+)?"
    r"(?:No\.?|Number|Nomor)\s*(\d{1,4})\s*"
    r"(?:of|Year|Tahun|In|By|/)\s*((?:19|20)\d{2})"
    r"(?:\s+(?:on|concerning|about|regarding|tentang)\s+([A-Z][A-Za-z '&\-/]{2,90}?)(?=[^A-Za-z '&\-/]|$))?", re.I)
# 印尼式简写：UU 13/2003 / PP 35/2021 / Perpres 10/2021
UU_RE = re.compile(
    r"\b(UU|PP|Perppu|Perpres|Keppres)\s+(?:No\.?\s*)?(\d{1,4})/((?:19|20)\d{2})\b")
UU_TYPE = {"UU": "Law", "PP": "Government Regulation", "Perppu": "Government Regulation in Lieu of Law",
           "Perpres": "Presidential Regulation", "Keppres": "Presidential Decree"}
# 宪法：1945 Constitution / UUD 1945 / Undang-Undang Dasar 1945 / 印尼宪法
CONST_RE = re.compile(
    r"\b(?:the\s+)?1945\s+Constitution\b|\bUUD\s*(?:NRI\s*)?1945\b|"
    r"Undang-Undang\s+Dasar\s+1945|(?:印度尼西亚|印尼)宪法", re.I)
# 条文单位统一 Article（Article 156 / Art. 81 / Pasal 156 / 第 X 条）
SECTION_RES = [
    re.compile(r"\bPasal\s+(\d{1,4}[A-Z]?)\b", re.I),
    re.compile(r"第\s*(\d{1,4})\s*条"),
]
ARTICLE_RE = re.compile(r"\bArt(?:icle)?s?\.?\s*(\d{1,4}[A-Z]?)\b")
CASE_CITE_RES = []  # 成文法单底座：无判例库
# 标题分词回查剔除的虚词
STOP_TOKENS = {"on", "concerning", "about", "regarding", "tentang", "the", "of",
               "and", "in", "to", "for", "republic", "indonesia", "law", "act"}


def _title_tokens(key: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", (key or "").lower())
            if t not in STOP_TOKENS][:4]


ABBR_MAP: dict[str, tuple[str, str | None, int | None] | None] = {}

# 宪法本体在库内以官方印尼文标题收录（Undang-Undang Dasar Negara Republik
# Indonesia Tahun 1945），只按 "constitution" 匹配会漏掉它。
CONST_TITLE_SQL = "(d.title ILIKE '%%constitution%%' OR d.title ILIKE '%%undang-undang dasar%%')"


def _is_constitution_title(title: str | None) -> bool:
    # _norm 会把连字符压成空格，比对串按归一后的形态写。
    t = _norm(title or "")
    return "constitution" in t or "undang undang dasar" in t

_SENT_END = re.compile(r"[。；;\n]")
# "已声明"识别：引用同句出现如实声明字样
DECL_RE = re.compile(
    r"未经(?:本库|库内)?核验|库内(?:暂)?(?:无|未)(?:收录|检索到|判例)|判例库建设中|"
    r"(?:未能|无法|没有|未)(?:找到|查到|检索到|确认|核实)|"
    r"not\s+(?:in|found in)\s+(?:the\s+)?(?:corpus|database)|"
    r"not\s+found|(?:could\s+not|unable\s+to)\s+(?:find|locate|verify|confirm)", re.I)


def _norm(s: str) -> str:
    """归一化：小写、压空白、去非字母数字（英语/马来语标题比对用）。"""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _base_section_label(value: str | None) -> str:
    """官方网页可能重复同号 Pasal；"N#2" 与用户引用的 "N" 视为同一条号。"""
    return (value or "").split("#", 1)[0].upper()


def extract_refs(answer: str) -> dict[str, list]:
    """从答案抽取法律名式/Act 编号式法源、缩写、宪法条款、条文与判例引证。"""
    # acts: (start, end, display_name, title_key, act_number, act_year, name_span)
    # acts: (start, end, display_name, title_key, act_number, act_year)
    # 印尼式：title_key 存法名（可空），act_number 存 "类型显示名|编号"（回查解析）
    acts: list[tuple] = []
    name_spans: list[tuple[int, int]] = []
    for m in LAW_NUM_RE.finditer(answer):
        kind = re.sub(r"\s+", " ", m.group(1)).strip().title()
        kind = "Government Regulation in Lieu of Law" if "Lieu" in kind else kind
        num, yr = m.group(2), int(m.group(3))
        name = re.sub(r"\s+", " ", m.group(4) or "").strip(" .,") or None
        disp = f"{kind} No. {num} of {yr}" + (f" on {name}" if name else "")
        acts.append((m.start(), m.end(), disp, name, f"{kind}|{num}", yr))
        name_spans.append((m.start(), m.end()))
    for m in UU_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        kind = UU_TYPE[m.group(1)]
        num, yr = m.group(2), int(m.group(3))
        disp = f"{kind} No. {num} of {yr}"
        acts.append((m.start(), m.end(), disp, None, f"{kind}|{num}", yr))
        name_spans.append((m.start(), m.end()))
    # 宪法提及（归一为标准名，供 Art. 归属）
    for m in CONST_RE.finditer(answer):
        acts.append((m.start(), m.end(), "1945 Constitution", "__CONST__", None, None))

    # 条文位置：Article/Pasal/第 X 条 统一为 Article
    sections: list[tuple[int, str, bool]] = []   # (pos, label, is_article)
    for rx in SECTION_RES:
        for m in rx.finditer(answer):
            sections.append((m.start(), m.group(1).upper(), True))
    for m in ARTICLE_RE.finditer(answer):
        sections.append((m.start(), m.group(1).upper(), True))

    # 条文归属：找 ±300 字符内最近的法源提及；宪法归属单列（const_articles）
    stat_refs: list[dict] = []
    const_refs: list[dict] = []
    seen_sr, seen_ca = set(), set()
    for pos, label, is_article in sections:
        best = None
        best_d = 301
        for a in acts:
            a0, a1 = a[0], a[1]
            d = min(abs(pos - a1), abs(a0 - pos))
            if d < best_d:
                best_d, best = d, a
        if best and best[3] == "__CONST__":
            if label not in seen_ca:
                seen_ca.add(label)
                const_refs.append({"article": label,
                                   "display": f"1945 Constitution, Art. {label}"})
            continue
        if is_article and best is None:
            # 孤立 Article 且全文无任何法源提及：无法归属，跳过（Art. 默认属宪法体例，
            # 但无宪法提及时按缺上下文处理，交由下方裸条号兜底逻辑不适用 Article）
            continue
        key = ((best[2] if best else None), label)
        if key in seen_sr:
            continue
        seen_sr.add(key)
        unit = "Art." if is_article else "s."
        stat_refs.append({
            "act": best[2] if best else None,
            "title_key": best[3] if best else None,
            "act_number": best[4] if best else None,
            "act_year": best[5] if best else None,
            "article": label,
            "display": (f"{best[2]}, {unit} {label}" if best else f"s. {label}"),
        })

    # 裸条号文档级兜底：±300 内无法源提及且全文只涉及一部法律 → 归属它（单法推断）
    distinct: dict[str, tuple] = {}
    for a in acts:
        if a[3] == "__CONST__":
            continue
        distinct.setdefault(_norm(a[2]), a)
    if len(distinct) == 1:
        only = next(iter(distinct.values()))
        for r in stat_refs:
            if r["act"] is None:
                r.update({"act": only[2], "title_key": only[3], "act_number": only[4],
                          "act_year": only[5],
                          "display": f"{only[2]}, Art. {r['article']}"})

    # 裸条号收敛：同一条号若已有带法律名的归属项，裸项是同一引用的重复形态，剔除之——
    # 避免同条既 verified 又 unverified 的自相矛盾。全文无归属版本的裸条号仍保留。
    attributed = {r["article"] for r in stat_refs if r["act"]}
    stat_refs = [r for r in stat_refs if not (r["act"] is None and r["article"] in attributed)]

    # 无条号的独立法源引用（法律名式/Act 编号式带编号即实质引用；缩写带映射的也算）
    seen_acts = {_norm(r["act"]) for r in stat_refs if r["act"]}
    for a in acts:
        if a[3] == "__CONST__" or not (a[4] or a[3]):
            continue
        n = _norm(a[2])
        if n in seen_acts:
            continue
        seen_acts.add(n)
        stat_refs.append({"act": a[2], "title_key": a[3], "act_number": a[4],
                          "act_year": a[5],
                          "article": None, "display": a[2]})

    # 判例引证（[年份] 卷 MLJ/CLJ/AMR… 页）
    case_cites: list[str] = []
    seen_cc = set()
    for rx in CASE_CITE_RES:
        for m in rx.finditer(answer):
            cite = re.sub(r"\s+", " ", m.group(0)).strip(" .,")
            if cite not in seen_cc:
                seen_cc.add(cite)
                case_cites.append(cite)

    return {"statutes": stat_refs[:MAX_REFS], "const_articles": const_refs[:MAX_REFS],
            "case_cites": case_cites[:MAX_REFS]}


# ---- 库内回查（example_database）----

def _kind_pattern(kind: str) -> str:
    for k, pat in TYPE_PATTERNS:
        if k == kind:
            return pat
    return r"(act|law|undang)"


def _doc_conds(r: dict) -> list[tuple[str, list]]:
    """法律级匹配条件（按可信度排序逐一尝试）：
    ① 类型词 + 编号正则 + 年份列（最强：库内标题自带编号年份）
    ② 编号正则 + 年份列（类型词译法多变时兜底）
    ③ 法名实词 AND-ILIKE（有法名时）"""
    conds: list[tuple[str, list]] = []
    num = yr = None
    if r.get("act_number") and "|" in str(r["act_number"]):
        kind, num = str(r["act_number"]).split("|", 1)
        yr = r.get("act_year")
        if num and yr:
            num_pat = rf"(no\.?|number|nomor)\s*0?{re.escape(num)}([^0-9]|$)"
            conds.append(("d.act_year = %s AND d.title ~* %s AND d.title ~* %s",
                          [int(yr), _kind_pattern(kind), num_pat]))
            conds.append(("d.act_year = %s AND d.title ~* %s", [int(yr), num_pat]))
    key = r.get("title_key") or ""
    toks = _title_tokens(key)
    if toks:
        conds.append((" AND ".join(["d.title ILIKE %s"] * len(toks)),
                      [f"%{t}%" for t in toks]))
    return conds


def _statute_in_db(r: dict) -> tuple[bool, bool]:
    """成文法库回查（印尼成文法英译库）：返回 (法律在库, 条文在库)。
    命中即真实存在（权威路径）。act_number 未命中时退回标题匹配（编号口径鲁棒）。"""
    label = r.get("article")
    for cond, params in _doc_conds(r):
        doc_hit = bool(db.query_one_statutes(
            f"SELECT 1 AS x FROM statutory_documents d WHERE {cond} LIMIT 1", params))
        if not doc_hit:
            continue
        sec_hit = False
        if label:
            sec_hit = bool(db.query_one_statutes(
                f"""SELECT 1 AS x FROM statutory_documents d
                    JOIN statutory_sections s USING (document_id)
                    WHERE {cond} AND upper(split_part(s.section_label, '#', 1)) = %s LIMIT 1""",
                params + [label.upper()]))
        return True, sec_hit
    return False, False


def _match_cite(r: dict, c: dict) -> bool:
    """引用的法律是否即本轮 citation 的法律：编号+年份出现在 citation 标题中，
    或法名实词全部命中。"""
    title = _norm(c.get("title") or "")
    if r.get("act_number") and "|" in str(r["act_number"]) and r.get("act_year"):
        num = str(r["act_number"]).split("|", 1)[1]
        if re.search(rf"(no|number|nomor)\s*0?{re.escape(num)}(\D|$)", title) \
                and str(r["act_year"]) in title:
            return True
    toks = _title_tokens(r.get("title_key") or "")
    return bool(toks) and all(t in title for t in toks)


def verify_answer(answer: str, citations: list[dict]) -> dict[str, Any]:
    """核心校验：答案引用 vs 本轮 citations → 不足处回查 PG。返回结构化核验结果。

    返回：{"summary": {verified, partial, declared, unverified, total}, "items": [
        {"kind": "statute"|"case", "ref": 显示串, "status": 四档之一, "note": 说明}]}
    """
    refs = extract_refs(answer or "")
    # 本轮成文法 citations（search_statutes/get_statute_detail 的取证材料）
    stat_cites = [c for c in (citations or []) if c.get("type") == "statute"]

    items: list[dict] = []

    # ---- 法律/条文引用 ----
    for r in refs["statutes"]:
        label = r.get("article")
        status, note = "unverified", ""
        # ① 本轮成文法取证材料直接覆盖？（最强核验：条文原文在本轮材料里）
        for c in stat_cites:
            if not _match_cite(r, c):
                continue
            if label is None or _base_section_label(c.get("section_label")) == label.upper():
                status, note = "verified", "已在本轮检索的法条原文中核验"
                break
        # ② 成文法库权威回查
        if status != "verified" and (r.get("act_number") or r.get("title_key") or r.get("act")):
            try:
                doc_hit, sec_hit = _statute_in_db(r)
                if doc_hit and (label is None or sec_hit):
                    status, note = "partial", "成文法库确认该法/该条存在，但本轮未调取原文"
                elif doc_hit:
                    status, note = ("partial",
                                    "法律在成文法库；该条号未单独成条（分条粒度限制），建议以印尼语官方原文核对")
                else:
                    note = "成文法库未检索到该法律（州法/附属立法或未入库的法令可能），建议人工核对"
            except Exception as exc:
                logger.warning("statute verify query failed: %s", exc)
                note = "库内回查暂不可用"
        elif status != "verified":
            note = "条号缺少所属法律上下文，无法核验"
        items.append({"kind": "statute", "ref": r["display"], "status": status, "note": note})

    # ---- 宪法条款（Federal Constitution）----
    for r in refs.get("const_articles", []):
        label = r["article"]
        status, note = "unverified", ""
        # ① 本轮成文法取证覆盖（search_statutes 查过该 Art.）
        if any(_is_constitution_title(c.get("title"))
               and _base_section_label(c.get("section_label")) == label.upper() for c in stat_cites):
            status, note = "verified", "已在本轮检索的宪法条款原文中核验"
        # ② 宪法库权威回查
        if status == "unverified":
            try:
                row = db.query_one_statutes(
                    f"""SELECT 1 AS x FROM statutory_documents d
                       JOIN statutory_sections s USING (document_id)
                       WHERE {CONST_TITLE_SQL}
                         AND upper(split_part(s.section_label, '#', 1)) = %s LIMIT 1""",
                    [label.upper()])
                if row:
                    status, note = "partial", "宪法库确认该条款存在，但本轮未调取原文"
                else:
                    note = "库内未检索到该宪法条款（宪法本体或未单独入库），建议以 UUD 1945 官方文本核对"
            except Exception as exc:
                logger.warning("const verify query failed: %s", exc)
                note = "库内回查暂不可用"
        items.append({"kind": "statute", "ref": r["display"], "status": status, "note": note})

    # ---- 判例引证：引证号归一后回查 cases.citation_norm（前缀匹配）----
    for cite in refs.get("case_cites", []):
        norm = _norm_citation(cite)
        # 引证信息不足（归一后过短）：前缀匹配易误配，不做肯定核验
        if len(norm) < 9:
            items.append({"kind": "case", "ref": cite, "status": "unverified",
                          "note": "引证信息不足，无法在判例库中核验"})
            continue
        try:
            row = db.query_one_cases(
                "SELECT court, case_name FROM cases WHERE citation_norm ILIKE %s LIMIT 1",
                [norm + "%"])
        except Exception as exc:
            logger.warning("case verify query failed: %s", exc)
            items.append({"kind": "case", "ref": cite, "status": "unverified",
                          "note": "判例库回查暂不可用"})
            continue
        if row:
            note = f"判例库命中：{row.get('court') or ''}"
            if row.get("case_name"):
                note += f"，{str(row.get('case_name'))[:80]}"
            items.append({"kind": "case", "ref": cite, "status": "verified", "note": note.strip("：")})
        else:
            items.append({"kind": "case", "ref": cite, "status": "unverified",
                          "note": "未在判例库中检索到该判例"})

    for item in items:
        if (item["status"] == "unverified" and item["kind"] == "statute"
                and evidence_guard.reference_appears_in_evidence(item["ref"], stat_cites)):
            item["status"] = "partial"
            item["note"] = "该法源名称见于本轮检索原文，但未独立调取其文本或确认现行效力"

    # "已声明"降级：模型在引用同句已如实标注"未经核验/库内未收录"的，单列 declared
    for i in items:
        if i["status"] != "unverified":
            continue
        pos = (answer or "").find(i["ref"][:50])
        if pos < 0:
            # display 串（"Contracts Act 1950, s. 74"）可能与原文写法不同：退回按法律名部分定位
            pos = (answer or "").find(i["ref"].split(",")[0][:50])
        if pos < 0:
            continue
        starts = [m.end() for m in _SENT_END.finditer(answer, 0, pos)]
        s0 = starts[-1] if starts else 0
        m1 = _SENT_END.search(answer, pos + len(i["ref"]))
        s1 = m1.end() if m1 else len(answer)
        if DECL_RE.search(answer[s0:s1]):
            i["status"] = "declared"
            i["note"] = "回答中已如实声明该引用未经库内核验"

    summary = {
        "verified": sum(1 for i in items if i["status"] == "verified"),
        "partial": sum(1 for i in items if i["status"] == "partial"),
        "declared": sum(1 for i in items if i["status"] == "declared"),
        "unverified": sum(1 for i in items if i["status"] == "unverified"),
        "total": len(items),
    }
    return {"summary": summary, "items": items}

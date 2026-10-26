"""引用校验（反臆造）：从助手答案文本中抽取法源引用，逐条验真。泰国版。

四级判定（与南非 V1.2+ / 印度 / 印尼版逻辑一致，公开接口完全相同）：
- verified   已核验：该引用出现在本轮检索到的 citations 原文材料中；
- partial    部分核验：本轮材料未直接覆盖，但库内权威数据中确认存在（真实、但未经本轮取证）；
- declared   已声明：模型在引用同句已如实标注"未经核验/库内未收录"（诚实行为，不冤枉好人）；
- unverified 未核验：本轮材料与库内数据均未能确认（可能为模型臆造，建议人工复核）。

设计原则：校验器是**纯规则引擎**（正则抽取 + 字符串匹配 + PG 存在性回查），
绝不引入第二个大模型——校验环节自身必须不可能臆造。

泰国化差异（与印尼母本的关键区别）：
1. 泰国法源引用体系 = **法名 + 类型（Act/Code/Emergency Decree/Royal Decree/
   Ministerial Regulation/Notification）+ 佛历年 B.E. 25XX（常带公历括注）**。
   例："Personal Data Protection Act B.E. 2562 (2019)"、"Foreign Business Act
   B.E. 2542 (1999)"、"Civil and Commercial Code"（法典无年份，靠法名）、"Revenue Code"。
   **不用**印尼式 "Law No. X of YYYY" / "UU 13/2003"。
2. 佛历 **B.E. = 公历 CE + 543**；库内 statutory_documents.act_year 存**公历 CE**。
   抽取时把 B.E. 折算 CE 再回查（答案里写 B.E. 2562，回查 act_year=2019）。
3. 条文单位 **Section**（Section 28 / มาตรา ๒๘ / s. 28 / 第 X 条），泰数字归一到阿拉伯，
   就近归属前文法源（±300 字符）。库内 section_label 为纯数字条号，比对同口径。
4. 宪法：**2017 Constitution / Constitution of the Kingdom of Thailand B.E. 2560**
   （库内可能无本体，回查不到如实降级）。
5. 成文法单底座：无判例库，CASE_CITE_RES 为空；成文法回查走 `example_database`
   （config.TH_STATUTES_DB_DSN）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from za_common import evidence_guard

from . import db

logger = logging.getLogger("th_platform.verification")

MAX_REFS = 40  # 单次校验引用上限，防超长答案拖慢
BE_OFFSET = 543  # 佛历 B.E. - 543 = 公历 CE

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def _arabic(s: str | None) -> str:
    """泰数字归一到阿拉伯数字（มาตรา ๒๘ → 28）。"""
    return (s or "").translate(_THAI_DIGITS)


def _be_to_ce(be: int | None) -> int | None:
    """佛历折算公历；给不合理值兜底（避免负数/超界回查）。"""
    if be is None:
        return None
    ce = be - BE_OFFSET
    return ce if 1800 <= ce <= 2100 else None


def _norm_citation(cite: str | None) -> str:
    """判例引证归一：去空格大写（คำพิพากษาศาลฎีกาที่ 4040/2543 → …4040/2543）。
    与 ingest 的 citation_norm（re.sub(r"\\s+", "", c).upper()）同口径。"""
    return re.sub(r"\s+", "", cite or "").upper()


# ---- 抽取正则（泰国式）----
# 泰国引证强锚 = 法名 + 类型 + 佛历年（库内英译标题自带 B.E. 年，act_year 列存 CE 可回查）。

# 主式：<Name> <Type> B.E. 25XX [(CE)]
#   e.g. "Personal Data Protection Act B.E. 2562 (2019)"、"Foreign Business Act B.E. 2542 (1999)"
ACT_BE_RE = re.compile(
    r"([A-Z฀-๿][A-Za-z0-9 '&\-/()฀-๿]{2,80}?)\s+"
    r"(Act|Emergency\s+Decree|Royal\s+Decree|Ministerial\s+Regulation|Notification|Decree|Code)\s+"
    r"B\.E\.\s*(25\d{2})(?:\s*\(\s*((?:19|20)\d{2})\s*\))?", re.I)

# 法典式：<Name> Code（泰国六法典无年份，靠固定法名）
CODE_RE = re.compile(
    r"\b((?:Civil\s+and\s+Commercial|Civil\s+Procedure|Criminal\s+Procedure|"
    r"Penal|Criminal|Revenue|Land)\s+Code)\b", re.I)

# 无年 Act 兜底：<Name> Act（其后不接 B.E.，负向前瞻避免与 ACT_BE_RE 重复）
#   e.g. "Foreign Business Act"、"Arbitration Act"、"Public Limited Companies Act"
ACT_NOYEAR_RE = re.compile(
    r"\b([A-Z][A-Za-z '&\-/]{2,60}?\s+Act)\b(?!\s+B\.E\.)")
GENERIC_ACT_REFERENCE_RE = re.compile(r"\b(?:this|such|that)\s+Act$", re.I)

# 宪法：2017 Constitution / Constitution of the Kingdom of Thailand (B.E. 2560) / 泰国宪法
CONST_RE = re.compile(
    r"\b(?:the\s+)?20\d{2}\s+Constitution\b|"
    r"Constitution\s+of\s+the\s+Kingdom\s+of\s+Thailand(?:\s*\(?\s*B\.E\.\s*25\d{2}\s*\)?)?|"
    r"\bB\.E\.\s*2560\s+Constitution\b|"
    r"(?:泰国|泰王国)?宪法", re.I)

# 泰国条文单位 Section（Section 28 / มาตรา ๒๘ / s. 28 / 第 X 条）——抓主条号（子条号归主条回查）
SECTION_RES = [
    re.compile(r"\bSection\s+(\d{1,4})\b", re.I),
    re.compile(r"มาตรา\s*([\d๐-๙]{1,4})"),
    re.compile(r"\bs\.\s*(\d{1,4})\b"),
    re.compile(r"第\s*(\d{1,4})\s*条"),
]
CASE_CITE_RES: list[re.Pattern] = []  # 成文法单底座：无判例库

# 常用缩写映射（明确无歧义者 → (显示名, title_key, act_year CE)；歧义置 None 不臆断）
ABBR_MAP: dict[str, tuple[str, str | None, int | None] | None] = {
    "PDPA": ("Personal Data Protection Act B.E. 2562 (2019)", "Personal Data Protection Act", 2019),
    "CCC": ("Civil and Commercial Code", "Civil and Commercial Code", None),
    "FBA": ("Foreign Business Act B.E. 2542 (1999)", "Foreign Business Act", 1999),
    "CCA": ("Computer Crime Act", "Computer Crime Act", None),
    "RC": ("Revenue Code", "Revenue Code", None),
    "PC": None,   # Penal Code / Public Company —— 歧义
    "CPC": None,  # Civil vs Criminal Procedure Code —— 歧义
}

# 标题分词回查剔除的虚词（含泰国法源类型词、国名、常见前导引导词）
STOP_TOKENS = {"act", "code", "decree", "royal", "emergency", "ministerial", "regulation",
               "notification", "be", "kingdom", "thailand", "thai", "of", "the", "and",
               "in", "on", "by", "for", "to", "no", "number", "constitution", "law",
               "according", "pursuant", "accordance", "under", "per", "as", "based"}

# 法名前导短语：<Name> Act 结构里，Name 前置会吞入 "According to the / Under the" 等引导语
# （如 "According to the Foreign Business Act" → 误存 title_key）。抽取后循环剥离净化。
_LEAD_PHRASE = re.compile(
    r"^(?:according\s+to|pursuant\s+to|in\s+accordance\s+with|as\s+per|based\s+on|"
    r"under|per|the|by|of|as|see|refer\s+to|provided\s+in|set\s+out\s+in|"
    r"stipulated\s+in|governed\s+by|regulated\s+by|according)\s+", re.I)


def _clean_name(raw: str | None) -> str:
    """净化法名：压空白、去首尾标点，循环剥离前导引导短语，只留法名本体。"""
    n = re.sub(r"\s+", " ", (raw or "")).strip(" .,'&-/()")
    prev = None
    while n and n != prev:
        prev = n
        n = _LEAD_PHRASE.sub("", n).strip(" .,'&-/()")
    return n

_SENT_END = re.compile(r"[。；;\n]")
# "已声明"识别：引用同句出现如实声明字样
DECL_RE = re.compile(
    r"未经(?:本库|库内)?核验|库内(?:暂)?(?:无|未)(?:收录|检索到|判例)|判例库建设中|"
    r"(?:未能|无法|没有|未)(?:找到|查到|检索到|确认|核实)|"
    r"以泰(?:语|文)(?:原文|官方)?(?:文本)?为准|"
    r"not\s+(?:in|found in)\s+(?:the\s+)?(?:corpus|database)|"
    r"not\s+found|(?:could\s+not|unable\s+to)\s+(?:find|locate|verify|confirm)", re.I)


def _title_tokens(key: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", (key or "").lower())
            if t not in STOP_TOKENS][:4]


def _norm(s: str) -> str:
    """归一化：小写、压空白、去非字母数字（英译标题比对用）。"""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_refs(answer: str) -> dict[str, list]:
    """从答案抽取泰国法源（法名式 + 法典 + 宪法 + 条文），条文就近归属所属法源。

    acts 元组：(start, end, display_name, title_key, act_number(泰国恒 None), act_year(CE))
    泰国靠"法名 + 佛历折算 CE 年"回查，不用印尼式 No.编号。
    """
    acts: list[tuple] = []
    name_spans: list[tuple[int, int]] = []

    # ① 主式 <Name> <Type> B.E. 25XX [(CE)]
    for m in ACT_BE_RE.finditer(answer):
        name = _clean_name(m.group(1))
        typ = re.sub(r"\s+", " ", m.group(2)).strip().title()
        be = int(m.group(3))
        ce = int(m.group(4)) if m.group(4) else _be_to_ce(be)
        disp = f"{name} {typ} B.E. {be}" + (f" ({ce})" if ce else "")
        acts.append((m.start(), m.end(), disp, name, None, ce))
        name_spans.append((m.start(), m.end()))

    # ② 法典 <Name> Code（无年份）
    for m in CODE_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        acts.append((m.start(), m.end(), name, name, None, None))
        name_spans.append((m.start(), m.end()))

    # ③ 无年 Act 兜底
    for m in ACT_NOYEAR_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        name = _clean_name(m.group(1))
        if GENERIC_ACT_REFERENCE_RE.search(name):
            continue
        acts.append((m.start(), m.end(), name, name, None, None))
        name_spans.append((m.start(), m.end()))

    # ④ 缩写（明确映射者）
    for m in re.finditer(r"\b([A-Z]{2,5})\b", answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        hit = ABBR_MAP.get(m.group(1))
        if hit:
            disp, key, yr = hit
            acts.append((m.start(), m.end(), disp, key, None, yr))
            name_spans.append((m.start(), m.end()))

    # ⑤ 宪法提及（归一为标准名，供条文归属）
    for m in CONST_RE.finditer(answer):
        acts.append((m.start(), m.end(), "Constitution of the Kingdom of Thailand",
                     "__CONST__", None, None))

    # 条文位置：Section/มาตรา/s./第 X 条 → 主条号（阿拉伯）
    sections: list[tuple[int, str]] = []
    for rx in SECTION_RES:
        for m in rx.finditer(answer):
            sections.append((m.start(), _arabic(m.group(1)).upper()))

    # 条文归属：找 ±300 字符内最近的法源提及；宪法归属单列（const_articles）
    stat_refs: list[dict] = []
    const_refs: list[dict] = []
    seen_sr, seen_ca = set(), set()
    for pos, label in sections:
        best, best_d = None, 301
        for a in acts:
            a0, a1 = a[0], a[1]
            d = min(abs(pos - a1), abs(a0 - pos))
            if d < best_d:
                best_d, best = d, a
        if best and best[3] == "__CONST__":
            if label not in seen_ca:
                seen_ca.add(label)
                const_refs.append({"article": label,
                                   "display": f"Constitution of Thailand, Section {label}"})
            continue
        key = ((best[2] if best else None), label)
        if key in seen_sr:
            continue
        seen_sr.add(key)
        stat_refs.append({
            "act": best[2] if best else None,
            "title_key": best[3] if best else None,
            "act_number": None,
            "act_year": best[5] if best else None,
            "article": label,
            "display": (f"{best[2]}, Section {label}" if best else f"Section {label}"),
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
                r.update({"act": only[2], "title_key": only[3], "act_year": only[5],
                          "display": f"{only[2]}, Section {r['article']}"})

    # 裸条号收敛：同一条号若已有带法名的归属项，裸项是同一引用的重复形态，剔除之
    attributed = {r["article"] for r in stat_refs if r["act"]}
    stat_refs = [r for r in stat_refs if not (r["act"] is None and r["article"] in attributed)]

    # 无条号的独立法源引用（法名式带回查锚点即实质引用）
    seen_acts = {_norm(r["act"]) for r in stat_refs if r["act"]}
    for a in acts:
        if a[3] == "__CONST__" or not a[3]:
            continue
        n = _norm(a[2])
        if n in seen_acts:
            continue
        seen_acts.add(n)
        stat_refs.append({"act": a[2], "title_key": a[3], "act_number": None,
                          "act_year": a[5], "article": None, "display": a[2]})

    # 判例引证（成文法单底座：CASE_CITE_RES 为空，恒空）
    case_cites: list[str] = []
    seen_cc: set[str] = set()
    for rx in CASE_CITE_RES:
        for m in rx.finditer(answer):
            cite = re.sub(r"\s+", " ", m.group(0)).strip(" .,")
            if cite not in seen_cc:
                seen_cc.add(cite)
                case_cites.append(cite)

    return {"statutes": stat_refs[:MAX_REFS], "const_articles": const_refs[:MAX_REFS],
            "case_cites": case_cites[:MAX_REFS]}


# ---- 库内回查（example_database）----

def _doc_conds(r: dict) -> list[tuple[str, list]]:
    """法律级匹配条件（按可信度排序逐一尝试）：
    ① 公历年(CE) + 法名实词 AND-ILIKE（最强：库内标题自带法名，act_year 列存 CE）
    ② 法名实词 AND-ILIKE（法典无年 / 佛历折算对不上时兜底）
    ③ 仅公历年（无法名词时）"""
    conds: list[tuple[str, list]] = []
    toks = _title_tokens(r.get("title_key") or "")
    yr = r.get("act_year")
    if toks and yr:
        conds.append(("d.act_year = %s AND " + " AND ".join(["d.title ILIKE %s"] * len(toks)),
                      [int(yr)] + [f"%{t}%" for t in toks]))
    if toks:
        conds.append((" AND ".join(["d.title ILIKE %s"] * len(toks)),
                      [f"%{t}%" for t in toks]))
    if yr and not toks:
        conds.append(("d.act_year = %s", [int(yr)]))
    return conds


def _statute_in_db(r: dict) -> tuple[bool, bool]:
    """成文法库回查（泰国成文法英译库）：返回 (法律在库, 条文在库)。
    命中即真实存在（权威路径）。条文按纯数字 section_label 精确匹配。"""
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
                    WHERE {cond} AND upper(s.section_label) = %s LIMIT 1""",
                params + [label.upper()]))
        return True, sec_hit
    return False, False


def _match_cite(r: dict, c: dict) -> bool:
    """引用的法律是否即本轮 citation 的法律：法名实词全部命中标题
    （有公历年时并要求年份也在标题里，收紧误配）。"""
    title = _norm(c.get("title") or "")
    toks = _title_tokens(r.get("title_key") or "")
    if not toks or not all(t in title for t in toks):
        return False
    if r.get("act_year"):
        # 库内标题多带 B.E. 年（如 "…B.E. 2562 (2019)"）：CE 或 BE 命中其一即可
        ce = int(r["act_year"])
        return str(ce) in title or str(ce + BE_OFFSET) in title
    return True


def verify_answer(answer: str, citations: list[dict]) -> dict[str, Any]:
    """核心校验：答案引用 vs 本轮 citations → 不足处回查 PG。返回结构化核验结果。

    返回：{"summary": {verified, partial, declared, unverified, total}, "items": [
        {"kind": "statute"|"case", "ref": 显示串, "status": 四档之一, "note": 说明}]}
    """
    refs = extract_refs(answer or "")
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
            if label is None or (c.get("section_label") or "").upper() == label.upper():
                status, note = "verified", "已在本轮检索的法条原文中核验"
                break
        # ② 成文法库权威回查
        if status != "verified" and (r.get("title_key") or r.get("act")):
            try:
                doc_hit, sec_hit = _statute_in_db(r)
                if doc_hit and (label is None or sec_hit):
                    status, note = "partial", "成文法库确认该法/该条存在，但本轮未调取原文"
                elif doc_hit:
                    status, note = ("partial",
                                    "法律在成文法库；该条号未单独成条（分条粒度限制），建议以泰语官方原文核对")
                else:
                    note = "成文法库未检索到该法律（附属立法/公告或未入库的法令可能），建议人工核对"
            except Exception as exc:
                logger.warning("statute verify query failed: %s", exc)
                note = "库内回查暂不可用"
        elif status != "verified":
            note = "条号缺少所属法律上下文，无法核验"
        items.append({"kind": "statute", "ref": r["display"], "status": status, "note": note})

    # ---- 宪法条款（Constitution of the Kingdom of Thailand）----
    for r in refs.get("const_articles", []):
        label = r["article"]
        status, note = "unverified", ""
        # ① 本轮成文法取证覆盖（search_statutes 查过该 Section）
        if any("constitution" in _norm(c.get("title") or "")
               and (c.get("section_label") or "").upper() == label.upper() for c in stat_cites):
            status, note = "verified", "已在本轮检索的宪法条款原文中核验"
        # ② 宪法库权威回查
        if status == "unverified":
            try:
                row = db.query_one_statutes(
                    """SELECT 1 AS x FROM statutory_documents d
                       JOIN statutory_sections s USING (document_id)
                       WHERE d.title ILIKE '%%constitution%%'
                         AND upper(s.section_label) = %s LIMIT 1""",
                    [label.upper()])
                if row:
                    status, note = "partial", "宪法库确认该条款存在，但本轮未调取原文"
                else:
                    note = ("库内未检索到该宪法条款（宪法本体或未单独入库），"
                            "建议以现行 2017 (B.E. 2560) 宪法官方文本核对")
            except Exception as exc:
                logger.warning("const verify query failed: %s", exc)
                note = "库内回查暂不可用"
        items.append({"kind": "statute", "ref": r["display"], "status": status, "note": note})

    # ---- 判例引证（成文法单底座：refs["case_cites"] 恒空，保留结构对齐全域接口）----
    for cite in refs.get("case_cites", []):
        norm = _norm_citation(cite)
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
            items.append({"kind": "case", "ref": cite, "status": "verified",
                          "note": note.strip("：")})
        else:
            items.append({"kind": "case", "ref": cite, "status": "unverified",
                          "note": "未在判例库中检索到该判例"})

    for item in items:
        if (item["status"] == "unverified" and item["kind"] == "statute"
                and evidence_guard.reference_appears_in_evidence(item["ref"], stat_cites)):
            item["status"] = "partial"
            item["note"] = "该法源名称见于本轮检索原文，但未独立调取其文本或确认现行效力"

    # "已声明"降级：模型在引用同句已如实标注"未经核验/以泰语原文为准"的，单列 declared
    for i in items:
        if i["status"] != "unverified":
            continue
        pos = (answer or "").find(i["ref"][:50])
        if pos < 0:
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

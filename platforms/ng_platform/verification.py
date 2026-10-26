"""引用校验（反臆造）：从助手答案文本中抽取法源引用，逐条验真。尼日利亚版。

四级判定（与南非 V1.2+ / 印度版逻辑一致，公开接口与印度版完全相同）：
- verified   已核验：该引用出现在本轮检索到的 citations 原文材料中；
- partial    部分核验：本轮材料未直接覆盖，但库内权威数据中确认存在（真实、但未经本轮取证）；
- declared   已声明：模型在引用同句已如实标注"未经核验/库内未收录"（诚实行为，不冤枉好人）；
- unverified 未核验：本轮材料与库内数据均未能确认（可能为模型臆造，建议人工复核）。

设计原则：校验器是**纯规则引擎**（正则抽取 + 字符串匹配 + PG 存在性回查），
绝不引入第二个大模型——校验环节自身必须不可能臆造。

尼日利亚化差异（对照巴西版）：
1. 法源引用体系换尼日利亚式：法律名式 "Contracts Act 1950" / "Companies Act 2016"，
   Act 编号式 "Act 136" / "Akta 136" / 修正法令 "A1648"；常用缩写
   （CA 2016 / EA 1955 / PDPA / CIPAA / IRA / NLC / CPC…）经 ABBR_MAP 归一到
   标题匹配词 + act_number 后回查库内；
2. 条文单位换 Section：s. 74 / Section 74 / 第 X 条，宪法条款用 Article（Art. 5(1)），
   就近归属前文法律（±300 字符）；
3. Federal Constitution / FC / Perlembagaan Persekutuan → 宪法条款，
   回查 title ILIKE '%constitution%'；
4. **判例核验**：答案中的判例引证（[年份] NGSC/NGCA 号 或 (年份) 卷 NWLR 页）抽取后
   归一化（去空格大写，与 ingest 的 citation_norm 同口径）→ 回查
   `example_database` cases 表 citation_norm 前缀匹配：命中 →
   status='verified'（note 附命中法院 + 案名）；未命中 → status='unverified'。
5. 成文法回查走 `example_database`（config.NG_STATUTES_DB_DSN）；判例回查走
   `example_database` cases 表（config.NG_CASES_DB_DSN），均 env 可覆盖。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from za_common import evidence_guard

from . import db

logger = logging.getLogger("ng_platform.verification")

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
    """判例引证归一：去空格大写（[2016] NGSC 42 → [2016]NGSC42）。
    与 ingest 的 citation_norm（re.sub(r"\\s+", "", c).upper()）同口径。"""
    return re.sub(r"\s+", "", cite or "").upper()


# ---- 抽取正则 ----
# 法律名式法源：Contracts Act 1950 / Companies Act 2016 / Evidence Act 1950 /
# National Land Code 1965 等（首字母大写词串 + Act/Code/Ordinance/Enactment + 年份）。
ACT_NAME_RE = re.compile(
    r"\b([A-Z][A-Za-z()&',\-]*(?:\s+[A-Za-z()&',\-]+)*?\s+"
    r"(?:Act|Code|Ordinance|Enactment))\s+((?:18|19|20)\d{2})\b"
)
# Act 编号式：Act 136 / Akta 136 / 修正法令 A1648（就近归属与库内 act_number 回查）。
ACT_NO_RE = re.compile(r"\b(?:Act|Akta)\s+(\d{1,4})\b|\b(A\d{3,4})\b")
# 宪法：Constitution of the Federal Republic of Nigeria 1999 / 1999 Constitution / 尼日利亚宪法
CONST_RE = re.compile(
    r"\bConstitution\s+of\s+the\s+Federal\s+Republic\s+of\s+Nigeria\b|"
    r"\b1999\s+Constitution\b|(?:尼日利亚)?(?:联邦)?宪法", re.I)
# 条文引用：s. 74 / ss. 20 / Section 74 / s 75A / 第 74 条；宪法条款 Art. 5 / Article 121
SECTION_RES = [
    re.compile(r"(?<![A-Za-z])[Ss]s?\.?\s*(\d{1,4}[A-Z]?)\b"),
    re.compile(r"\bSections?\s+(\d{1,4}[A-Z]?)\b", re.I),
    re.compile(r"第\s*(\d{1,4})\s*条"),
]
ARTICLE_RE = re.compile(r"\bArt(?:icle)?s?\.?\s*(\d{1,3}[A-Z]?)\b")
# 判例引证：[年份] 卷 系列缩写 页（尼日利亚常见判例汇编系列）
CASE_CITE_RES = [
    re.compile(r"\[(\d{4})\]\s*(NGSC|NGCA)\s*(\d+)"),
    re.compile(r"\((\d{4})\)\s*(\d+)\s*(NWLR|SCNJ|LRCN|FWLR)[^,\n]{0,20}"),
]
# 法规缩写/通称 → (库内标题匹配词, act_number, act_year)。有歧义的缩写标 None 占位防误配。
# 尼日利亚成文法 act_number 多为空（LFN 部分为 'CAP C20'），故 title_key 用**无逗号无年份的核心词**
# 走 title ILIKE 兜底最鲁棒（库内标题形如 'Petroleum Industry Act, 2021'）。
ABBR_MAP: dict[str, tuple[str, str | None, int | None] | None] = {
    "CAMA 2020": ("Companies and Allied Matters Act", None, 2020),
    "CAMA": ("Companies and Allied Matters Act", None, None),
    "PIA 2021": ("Petroleum Industry Act", None, 2021),
    "PIA": ("Petroleum Industry Act", None, None),
    "AMA 2023": ("Arbitration and Mediation Act", None, 2023),
    "AMA": ("Arbitration and Mediation Act", None, None),
    "BOFIA 2020": ("Banks and Other Financial Institutions Act", None, 2020),
    "BOFIA": ("Banks and Other Financial Institutions Act", None, None),
    "FCCPA 2018": ("Federal Competition and Consumer Protection Act", None, 2018),
    "FCCPA": ("Federal Competition and Consumer Protection Act", None, 2018),
    "NDPA 2023": ("Nigeria Data Protection Act", None, 2023),
    "NDPA": ("Nigeria Data Protection Act", None, 2023),
    "NDPR": ("Nigeria Data Protection", None, None),   # 2019 条例，泛匹配
    "NIPC Act": ("Nigerian Investment Promotion Commission", None, None),
    "ISA": ("Investments and Securities Act", None, None),  # 2007/2025 两代
    "Evidence Act": ("Evidence Act", None, 2011),
    "Companies and Allied Matters Act": ("Companies and Allied Matters Act", None, None),
    "Petroleum Industry Act": ("Petroleum Industry Act", None, None),
    "Arbitration and Mediation Act": ("Arbitration and Mediation Act", None, None),
    "CAP C20": ("Companies and Allied Matters Act", "CAP C20", None),  # 旧版汇编
    "CA": None,    # 歧义：Companies Act / Court of Appeal，不自动展开
    "EA": None,    # 歧义：Evidence Act / Electricity Act，不自动展开
    "FC": None,    # 歧义：Federal Constitution / Federal Court
    "AA": None,    # 歧义：Arbitration Act 等
    "SEC": None,   # 机构 Securities and Exchange Commission，非法律
}

# 法名前导虚词剥离：ACT_NAME_RE 会把句首 "Under the/Pursuant to the …" 吞进法名，
# 回查前剥离，否则 title ILIKE 永远匹配不到（PIA 全 unverified 事故）
LEAD_STRIP = re.compile(r"^(?:under|pursuant\s+to|the|a|an|in|of|see|per|by|and|to|as)\s+", re.I)


_SENT_END = re.compile(r"[。；;\n]")
# "已声明"识别：引用同句出现如实声明字样
DECL_RE = re.compile(
    r"未经(?:本库|库内)?核验|库内(?:暂)?(?:无|未)(?:收录|检索到|判例)|判例库建设中|"
    r"(?:未能|无法|没有|未)(?:找到|查到|检索到|确认|核实)|"
    r"not\s+(?:in|found in)\s+(?:the\s+)?(?:corpus|database)|"
    r"not\s+found|(?:could\s+not|unable\s+to)\s+(?:find|locate|verify|confirm)", re.I)


def _norm(s: str) -> str:
    """归一化：小写、压空白、去非字母数字（英语标题比对用）。"""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_refs(answer: str) -> dict[str, list]:
    """从答案抽取法律名式/Act 编号式法源、缩写、宪法条款、条文与判例引证。"""
    # acts: (start, end, display_name, title_key, act_number, act_year, name_span)
    acts: list[tuple] = []
    name_spans: list[tuple[int, int]] = []
    for m in ACT_NAME_RE.finditer(answer):
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        while (n2 := LEAD_STRIP.sub("", name)) != name:
            name = n2
        yr = int(m.group(2))
        disp = f"{name} {yr}"
        mapped = ABBR_MAP.get(disp) or ABBR_MAP.get(name)
        if mapped:
            title_key, num, myr = mapped
            acts.append((m.start(), m.end(), disp, title_key, num, myr or yr))
        else:
            acts.append((m.start(), m.end(), disp, name, None, yr))
        name_spans.append((m.start(), m.end()))
    # Act 编号式（跳过法律名式内部的年份，如 "Contracts Act 1950" 里的 "Act 1950"）
    for m in ACT_NO_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        raw = m.group(1) or m.group(2)
        # 4 位数字且形如年份的，视为年份噪声而非 Act 编号（尼日利亚 Act 编号至今 <1000）
        if m.group(1) and re.fullmatch(r"(?:18|19|20)\d{2}", m.group(1)):
            continue
        num = _norm_actno(raw)
        disp = num
        acts.append((m.start(), m.end(), disp, None, num, None))
    # 宪法提及（归一为标准名，供 Art./Section 归属）
    for m in CONST_RE.finditer(answer):
        acts.append((m.start(), m.end(), "Federal Constitution", "__CONST__", None, None))
    # 缩写/通称提及（PDPA/CIPAA/IRA/NLC/CPC…）归一，同样参与就近归属
    seen_spans = set(name_spans)
    for abbr, mapped in ABBR_MAP.items():
        if not mapped:
            continue
        abbr_pat = re.escape(abbr).replace("\\ ", " ").replace(" ", r"\s+")
        for m in re.finditer(r"(?<![A-Za-z])" + abbr_pat + r"(?![A-Za-z])", answer):
            if any(s <= m.start() < e for s, e in seen_spans):
                continue
            title_key, num, yr = mapped
            acts.append((m.start(), m.end(), abbr, title_key, num, yr))

    # 条文位置：Section（s./Section/第 X 条）与宪法 Article 统一收集
    sections: list[tuple[int, str, bool]] = []   # (pos, label, is_article)
    for rx in SECTION_RES:
        for m in rx.finditer(answer):
            sections.append((m.start(), m.group(1).upper(), False))
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
                                   "display": f"Federal Constitution, Art. {label}"})
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
                          "display": f"{only[2]}, s. {r['article']}"})

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

    # 判例引证（[年份] NGSC/NGCA 号）
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

def _doc_conds(r: dict) -> list[tuple[str, list]]:
    """法律级匹配条件（按可信度排序逐一尝试）：act_number 精确 → 标题 ILIKE。"""
    conds: list[tuple[str, list]] = []
    if r.get("act_number"):
        conds.append(("d.act_number = %s", [_norm_actno(r["act_number"])]))
    key = r.get("title_key") or r.get("act") or ""
    if key:
        conds.append(("d.title ILIKE %s", [f"%{key}%"]))
    return conds


def _statute_in_db(r: dict) -> tuple[bool, bool]:
    """成文法库回查（官方 Laws of Nigeria）：返回 (法律在库, 条文在库)。
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
                    WHERE {cond} AND upper(s.section_label) = %s LIMIT 1""",
                params + [label.upper()]))
        return True, sec_hit
    return False, False


def _match_cite(r: dict, c: dict) -> bool:
    """引用的法律是否即本轮 citation 的法律：Act 编号一致，或标题词包含。"""
    if r.get("act_number") and c.get("act_number"):
        if _norm_actno(str(c["act_number"])).upper() != _norm_actno(r["act_number"]).upper():
            return False
        return True
    key = _norm(r.get("title_key") or r.get("act") or "")
    if not key:
        return False
    return key in _norm(c.get("title") or "")


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
            if label is None or (c.get("section_label") or "").upper() == label.upper():
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
                                    "法律在成文法库；该条号未单独成条（分条粒度限制），建议人工核对官方 Laws of Nigeria 文本")
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
                    note = "宪法库未检索到该条款（分条粒度限制可能），建议人工核对 Federal Constitution 官方文本"
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

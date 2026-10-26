"""引用校验（反臆造）：从助手答案文本中抽取法源引用，逐条验真。俄罗斯版。

四级判定（与南非 V1.2+ / 印度版逻辑一致，公开接口与印度版完全相同）：
- verified   已核验：该引用出现在本轮检索到的 citations 原文材料中；
- partial    部分核验：本轮材料未直接覆盖，但库内权威数据中确认存在（真实、但未经本轮取证）；
- declared   已声明：模型在引用同句已如实标注"未经核验/库内未收录"（诚实行为，不冤枉好人）；
- unverified 未核验：本轮材料与库内数据均未能确认（可能为模型臆造，建议人工复核）。

设计原则：校验器是**纯规则引擎**（正则抽取 + 字符串匹配 + PG 存在性回查），
绝不引入第二个大模型——校验环节自身必须不可能臆造。

俄罗斯化差异：
1. 法源引用体系为俄式：引号法名 Federal Law No. 152-FZ "On Personal Data"、
   裸法名 Federal Law On Personal Data、法典 Civil Code (of the Russian Federation)、
   宪法 Constitution of the Russian Federation；
2. 官方俄文层优先以俄式法律编号回查，历史英译层以标题实词回查；
3. 条文单位兼容 Article / Статья（可带小数条号 54.1），就近归属前文法源（±300 字符）；
4. 成文法单底座：具体裁判号会被抽取并标为未核验，不会虚构判例库命中；
5. 成文法回查走 `example_database`（config.RU_STATUTES_DB_DSN）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from za_common import evidence_guard

from . import db

logger = logging.getLogger("ru_platform.verification")

MAX_REFS = 40  # 单次校验引用上限，防超长答案拖慢


# ---- 抽取正则（俄罗斯式）----
# 库内为英译文本、act_number 全空，核验以**标题关键词**为锚：
# "Federal Law No. 152-FZ \"On Personal Data\"" → 取引号内法名 → 分词回查 title ILIKE。
CODE_STEMS = (
    r"(?:Civil|Labou?r|Tax|Criminal|Family|Land|Housing|Customs|Budget|Air|Forest|Water|"
    r"Town[- ]Planning|Urban[- ]Planning|Merchant\s+Shipping|Criminal\s+Enforcement|"
    r"Arbitrazh(?:\s+Procedure)?|Civil\s+Procedure|Criminal\s+Procedure|"
    r"Administrative\s+Offences?)")
CODE_RE = re.compile(rf"\b({CODE_STEMS}\s+Code)(?:\s+of\s+the\s+Russian\s+Federation)?\b", re.I)
_CODE_NAMES = {
    "劳动法典": ("Labour Code", "Трудовой кодекс"),
    "刑法典": ("Criminal Code", "Уголовный кодекс"),
    "税法典": ("Tax Code", "Налоговый кодекс"),
    "民法典": ("Civil Code", "Гражданский кодекс"),
    "家庭法典": ("Family Code", "Семейный кодекс"),
}
CN_CODE_RE = re.compile(r"(?:俄罗斯(?:联邦)?)?[《\s]*(" + "|".join(_CODE_NAMES) + r")[》]?")


def _canonical_title(value: str) -> str:
    value = value.casefold()
    for chinese, (english, russian) in _CODE_NAMES.items():
        value = value.replace(english.casefold(), russian.casefold()).replace(chinese, russian.casefold())
    return value.replace("labor code", "трудовой кодекс")
# 带引号法名（可带 No. 152-FZ 前缀与日期）："...Law No. 152-FZ of 27 July 2006 \"On Personal Data\""
LAW_QUOTED_RE = re.compile(
    r"(?:Federal\s+(?:Constitutional\s+)?Law|Law)\s*"
    r"(?:No\.?\s*(\d+(?:\.\d+)?(?:-(?:FZ|FKZ))?)\s*)?"
    r"(?:of\s+[\w ,]{4,40}?)?"
    r"\s*[«\"“]\s*((?:On|About)\s[^»\"”]{3,110})[»\"”]", re.I)
# 不带引号："Federal Law On Personal Data"
LAW_BARE_RE = re.compile(
    r"\bFederal\s+(?:Constitutional\s+)?Law\s+"
    r"((?:On|About)\s+[A-Z][A-Za-z ,'\-]{3,90}?)(?=[.,;:()\n]|$)")
RU_LAW_QUOTED_RE = re.compile(
    r"\b(?:Федеральный\s+(?:конституционный\s+)?закон|Закон\s+"
    r"Российской\s+Федерации)\s*"
    r"(?:от\s+\d{1,2}[.\s]\w+[.\s]\d{4}\s*(?:г\.?)?\s*)?"
    r"(?:№|N|No\.?)?\s*"
    r"(\d+(?:\.\d+)?\s*[-–—]\s*(?:ФЗ|ФКЗ))?\s*"
    r"[«\"“]\s*([^»\"”]{3,180})[»\"”]",
    re.IGNORECASE,
)
RU_CODE_RE = re.compile(
    r"\b((?:Гражданский|Трудовой|Налоговый|Уголовный|Семейный|Земельный|"
    r"Жилищный|Бюджетный|Воздушный|Лесной|Водный|Градостроительный|"
    r"Таможенный|Арбитражный\s+процессуальный|Гражданский\s+процессуальный|"
    r"Уголовно-процессуальный)\s+кодекс)"
    r"(?:\s+Российской\s+Федерации)?\b",
    re.IGNORECASE,
)
# 宪法：Constitution of the Russian Federation / 俄罗斯联邦宪法
CONST_RE = re.compile(
    r"Constitution\s+of\s+the\s+Russian\s+Federation|"
    r"Конституци[яи]\s+Российской\s+Федерации|俄罗斯(?:联邦)?宪法(?!法院)",
    re.I,
)
# 条文单位为 Article（俄式条号可带小数：Article 54.1）；中文"第 X 条"同义
SECTION_RES = [
    re.compile(r"第\s*(\d{1,4}(?:\.\d{1,2})?)\s*条"),
    re.compile(
        r"\b(?:Статья|Ст\.?)\s*(\d{1,4}(?:\.\d{1,3})?)\b",
        re.IGNORECASE,
    ),
]
ARTICLE_RE = re.compile(
    r"\bArt(?:icle)?s?\.?\s*(\d{1,4}(?:\.\d{1,3})?)\b",
    re.IGNORECASE,
)
CASE_CITE_RES = [
    re.compile(
        r"\b(?:Определение|Постановление|Решение)\s+"
        r"[^.\n]{0,160}?"
        r"(?:Верховного\s+Суда|Конституционного\s+Суда|"
        r"арбитражного\s+суда)[^.\n]{0,120}?"
        r"от\s+\d{1,2}\.\d{1,2}\.\d{4}\s+"
        r"(?:№|N)\s*[А-ЯA-Z0-9][А-ЯA-Z0-9./№\-–—]{2,60}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:Decision|Ruling|Resolution)\s+of\s+the\s+"
        r"(?:Supreme|Constitutional|Arbitrazh)\s+Court"
        r"[^.\n]{0,140}?(?:No\.?|№)\s*[A-ZА-Я0-9][A-ZА-Я0-9./\-–—]{2,60}",
        re.IGNORECASE,
    ),
]
# 标题分词回查时剔除的虚词（英译标题高频、无区分度）
STOP_TOKENS = {"on", "about", "the", "of", "and", "in", "to", "for",
               "russian", "federation", "federal", "law", "о", "об", "и",
               "в", "на", "российской", "федерации", "федеральный",
               "федерального", "закон"}


def _title_tokens(key: str) -> list[str]:
    return [t for t in re.findall(r"[a-z\u0400-\u052f]+", _canonical_title(key or ""))
            if t not in STOP_TOKENS][:4]


def _norm_law_number(value: str | None) -> str:
    number = re.sub(r"\s+", "", (value or "").upper())
    return number.replace("–", "-").replace("—", "-").replace("-FKZ", "-ФКЗ").replace("-FZ", "-ФЗ")


ABBR_MAP: dict[str, tuple[str, str | None, int | None] | None] = {}

_SENT_END = re.compile(r"[。；;\n]")
# "已声明"识别：引用同句出现如实声明字样
DECL_RE = re.compile(
    r"未经(?:本库|库内)?核验|库内(?:暂)?(?:无|未)(?:收录|检索到|判例)|判例库建设中|"
    r"(?:未能|无法|没有|未)(?:找到|查到|检索到|确认|核实)|"
    r"not\s+(?:in|found in)\s+(?:the\s+)?(?:corpus|database)|"
    r"not\s+found|(?:could\s+not|unable\s+to)\s+(?:find|locate|verify|confirm)", re.I)


def _norm(s: str) -> str:
    """归一化：小写、压空白，保留拉丁字母、俄文字母和数字。"""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\u0400-\u052f ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_refs(answer: str) -> dict[str, list]:
    """从答案抽取俄式法源（引号法名/裸法名/法典/宪法）与 Article 条文引用。"""
    # acts: (start, end, display_name, title_key, act_number, act_year)
    acts: list[tuple] = []
    name_spans: list[tuple[int, int]] = []
    for m in LAW_QUOTED_RE.finditer(answer):
        name = re.sub(r"\s+", " ", m.group(2)).strip(" .,")
        num = (m.group(1) or "").upper() or None
        disp = f'Federal Law No. {num} "{name}"' if num else f'Federal Law "{name}"'
        acts.append((m.start(), m.end(), disp, name, num, None))
        name_spans.append((m.start(), m.end()))
    for m in LAW_BARE_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        name = re.sub(r"\s+", " ", m.group(1)).strip(" .,")
        acts.append((m.start(), m.end(), f"Federal Law {name}", name, None, None))
        name_spans.append((m.start(), m.end()))
    for m in RU_LAW_QUOTED_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        number = _norm_law_number(m.group(1)) or None
        name = re.sub(r"\s+", " ", m.group(2)).strip(" .,")
        display = (
            f"Федеральный закон № {number} «{name}»"
            if number
            else f"Федеральный закон «{name}»"
        )
        acts.append(
            (m.start(), m.end(), display, name, number, None)
        )
        name_spans.append((m.start(), m.end()))
    for m in CODE_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        acts.append((m.start(), m.end(), f"{name} of the Russian Federation",
                     name, None, None))
        name_spans.append((m.start(), m.end()))
    for m in RU_CODE_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        name = re.sub(r"\s+", " ", m.group(1)).strip()
        acts.append(
            (
                m.start(),
                m.end(),
                f"{name} Российской Федерации",
                name,
                None,
                None,
            )
        )
        name_spans.append((m.start(), m.end()))
    for m in CN_CODE_RE.finditer(answer):
        if any(s <= m.start() < e for s, e in name_spans):
            continue
        english, russian = _CODE_NAMES[m.group(1)]
        acts.append((m.start(), m.end(), english + " of the Russian Federation", russian, None, None))
        name_spans.append((m.start(), m.end()))
    # 宪法提及（归一为标准名，供 Art. 归属）
    for m in CONST_RE.finditer(answer):
        acts.append((m.start(), m.end(), "Constitution of the Russian Federation",
                     "__CONST__", None, None))

    # 条文位置：Section（s./Section/第 X 条）与宪法 Article 统一收集
    sections: list[tuple[int, int, str, bool]] = []   # (pos, end, label, is_article)
    for rx in SECTION_RES:
        for m in rx.finditer(answer):
            sections.append((m.start(), m.end(), m.group(1).upper(), True))
    for m in ARTICLE_RE.finditer(answer):
        sections.append((m.start(), m.end(), m.group(1).upper(), True))

    # 紧接条号的后置法名优先，其余使用前文法名上下文；宪法归属单列。
    stat_refs: list[dict] = []
    const_refs: list[dict] = []
    seen_sr, seen_ca = set(), set()
    for pos, end, label, is_article in sections:
        best = None
        best_d = 301
        prior = [a for a in acts if a[0] <= pos and pos - a[1] <= 300]
        attached = [a for a in acts if end <= a[0] <= end + 30
                    and re.fullmatch(r"[\s,，:：()（）《》-]*(?:(?:of(?:\s+the)?|из|的)\s*)?[\s《(（]*",
                                     answer[end:a[0]], re.I)]
        for a in attached or prior or [a for a in acts if pos < a[0] <= pos + 80
                           and not re.search(r"\n\s*\n|。", answer[pos:a[0]])]:
            a0, a1 = a[0], a[1]
            d = min(abs(pos - a1), abs(a0 - pos))
            if d < best_d:
                best_d, best = d, a
        if best and best[3] == "__CONST__":
            if label not in seen_ca:
                seen_ca.add(label)
                const_refs.append({"article": label,
                                   "display": f"Constitution of the Russian Federation, Art. {label}"})
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

    # 具体俄罗斯裁判号只做风险抽取；当前没有判例正文库。
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
    """法律级匹配条件：官方编号优先，再退到俄文/英译标题实词。"""
    conds: list[tuple[str, list]] = []
    number = _norm_law_number(r.get("act_number"))
    if number:
        conds.append((
            "replace(replace(regexp_replace("
            "upper(coalesce(d.act_number,'')), '[[:space:]]+', '', 'g'), "
            "'–', '-'), '—', '-') = %s",
            [number],
        ))
    key = r.get("title_key") or r.get("act") or ""
    if key:
        conds.append(("d.title ILIKE %s", [f"%{key}%"]))
        canonical = _canonical_title(key)
        if canonical != key.casefold():
            conds.append(("d.title ILIKE %s", [f"%{canonical}%"]))
        toks = _title_tokens(key)
        if len(toks) >= 2:
            conds.append((" AND ".join(["d.title ILIKE %s"] * len(toks)),
                          [f"%{t}%" for t in toks]))
    return conds


def _statute_in_db(r: dict) -> tuple[bool, bool]:
    """成文法库回查（俄联邦成文法英译库）：返回 (法律在库, 条文在库)。
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
    """引用的法律是否即本轮 citation 的法律：编号优先、标题实词兜底。"""
    number = _norm_law_number(r.get("act_number"))
    cited_number = _norm_law_number(c.get("act_number"))
    if number and cited_number:
        return number == cited_number
    key = r.get("title_key") or r.get("act") or ""
    toks = _title_tokens(key)
    if not toks:
        return False
    title = _norm(_canonical_title(c.get("title") or ""))
    return all(t in title for t in toks)


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
                                    "法律在成文法库；该条号未单独成条（分条粒度限制），建议以俄语官方原文核对")
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
        if any(
            (
                "constitution" in _norm(c.get("title") or "")
                or "конституц" in _norm(c.get("title") or "")
            )
            and (c.get("section_label") or "").upper() == label.upper()
            for c in stat_cites
        ):
            status, note = "verified", "已在本轮检索的宪法条款原文中核验"
        # ② 宪法库权威回查
        if status == "unverified":
            try:
                row = db.query_one_statutes(
                    """SELECT 1 AS x FROM statutory_documents d
                       JOIN statutory_sections s USING (document_id)
                       WHERE (
                           d.title ILIKE '%%constitution%%'
                           OR d.title ILIKE '%%конституц%%'
                       )
                         AND upper(s.section_label) = %s LIMIT 1""",
                    [label.upper()])
                if row:
                    status, note = "partial", "宪法库确认该条款存在，但本轮未调取原文"
                else:
                    note = "库内未检索到该宪法条款（宪法本体或未单独入库），建议以俄联邦宪法官方文本核对"
            except Exception as exc:
                logger.warning("const verify query failed: %s", exc)
                note = "库内回查暂不可用"
        items.append({"kind": "statute", "ref": r["display"], "status": status, "note": note})

    # 俄罗斯平台当前没有可核验的判例全文库。模型若仍提及判例，必须明确
    # 标记为未核验，不能借用其他法域的 cases 表给出虚假的肯定结论。
    for cite in refs.get("case_cites", []):
        items.append({
            "kind": "case",
            "ref": cite,
            "status": "unverified",
            "note": "俄罗斯判例正文库尚未收录，请通过俄罗斯法院官方来源人工核对",
        })

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

"""引用校验（反臆造）：从助手答案文本中抽取法源引用，逐条验真。巴西版。

四级判定（与南非 V1.2+ / 印度版逻辑一致，公开接口与印度版完全相同）：
- verified   已核验：该引用出现在本轮检索到的 citations 原文材料中；
- partial    部分核验：本轮材料未直接覆盖，但库内权威数据中确认存在（真实、但未经本轮取证）；
- declared   已声明：模型在引用同句已如实标注"未经核验/库内未收录"（诚实行为，不冤枉好人）；
- unverified 未核验：本轮材料与库内数据均未能确认（可能为模型臆造，建议人工复核）。

设计原则：校验器是**纯规则引擎**（正则抽取 + 字符串匹配 + PG 存在性回查），
绝不引入第二个大模型——校验环节自身必须不可能臆造。

巴西化差异（对照印度版）：
1. 法源引用体系换巴西式：Lei nº 10.406/2002 / Lei Complementar nº 123/2006 /
   Decreto-Lei nº 5.452/1943 / Decreto nº … / Medida Provisória nº …；
   法典与常用缩写（Código Civil / CLT / CDC / CTN / LGPD…）经 ABBR_MAP 归一到
   law_number + law_year 后回查库内；
2. 条文单位换 Art.（Artigo）：Art. 927 / Art. 5º / Art. 103-A / 第 X 条，
   就近归属前文法律（±300 字符，参考南非 bare-section 归属思路的简化版）；
3. **法律编号千分点归一**：10.406 与 10406 视为同一编号（库内 law_number 无千分点）；
4. CF/88 / Constituição Federal → 宪法条款，回查 document_type='constitution'；
5. **判例核验（V1.1 判例库已上线）**：答案中的判例引证（RE/REsp/AREsp/ADI/ADPF/HC/
   AgRg 等类别缩写 + 案号）抽取 processo 号 → 归一化（去点划斜杠空白，与 ingest
   norm_proc 同口径）→ 回查 `example_database` cases 表 processo_norm 前缀匹配：
   命中 → status='verified'（note 附命中法院 + relator）；未命中 →
   status='unverified'（note='未在判例库中检索到该判例'）。**Súmula（判例汇编条目，
   无 processo 号）保持 declared**——无案号可回查。
6. 成文法回查走 `example_database`（config.BR_STATUTES_DB_DSN）；判例回查走
   `example_database` cases 表（config.BR_CASES_DB_DSN），均 env 可覆盖。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from za_common import evidence_guard

from . import db

logger = logging.getLogger("br_platform.verification")

MAX_REFS = 40  # 单次校验引用上限，防超长答案拖慢


def _norm_lawno(num: str | None) -> str:
    """法律编号归一：剥千分点与空白（10.406 → 10406）。保留连字符（MP 2.200-2 → 2200-2）。"""
    return re.sub(r"[.\s]", "", num or "")


# 判例是否为 Súmula（判例汇编条目，无 processo 案号 → 不做案号回查）
SUMULA_RE = re.compile(r"S[úu]mula", re.I)


def _norm_processo(cite: str | None) -> str:
    """案号归一：剥 nº/no 标记后去点划斜杠空白，保留类别缩写字母与数字。
    与 ingest_br_cases.norm_proc（re.sub(r"[.\\-/\\s]", "", p)）同口径，供 processo_norm 匹配。"""
    s = re.sub(r"[nN][ºo°.]{1,3}", " ", cite or "")   # 去 "nº"/"no."/"n°" 噪声
    return re.sub(r"[.\-/\s]", "", s)


# ---- 抽取正则 ----
# 编号式法源：Lei nº 10.406/2002、Lei Complementar nº 123/2006、Decreto-Lei nº 5.452/1943、
# Decreto nº 9.580/2018、Medida Provisória nº 2.200-2/2001；兼容长式
# "Lei nº 10.406, de 10 de janeiro de 2002" 与 nº/no/n°/N.º 等写法、可省略 nº。
LEI_RE = re.compile(
    r"\b(Lei\s+Complementar|Decreto-Lei|Decreto\s+Legislativo|Medida\s+Provis[óo]ria|"
    r"Emenda\s+Constitucional|Lei|Decreto)"
    r"(?:\s+Federal)?\s+(?:[Nn][ºo°.ª]{0,2}\s*)?(\d[\d.]*(?:-\d+)?)"
    r"(?:\s*/\s*((?:18|19|20)\d{2})|\s*,?\s+de\s+(?:\d{1,2}[ºo°]?\s+de\s+)?"
    r"[a-zç]+\s+de\s+((?:18|19|20)\d{2}))?"
)
_KIND_DOCTYPE = {  # 编号式引用的类别 → 库内 document_type 候选（宽口径，含法典化文本）
    "lei": ("law", "code"),
    "lei complementar": ("complementary_law",),
    "decreto-lei": ("decree_law", "code"),
    "decreto": ("decree",),
    "decreto legislativo": ("legislative_decree",),
    "medida provisoria": ("provisional_measure",),
    "emenda constitucional": ("constitutional_amendment",),
}
# 宪法：CF/88、Constituição Federal（de 1988）、Constituição da República…
CONST_RE = re.compile(
    r"\bCF\s*/\s*88\b|\bCRFB(?:\s*/\s*88)?\b|"
    r"\bConstitui[çc][ãa]o\s+(?:Federal|da\s+Rep[úu]blica)(?:\s+Federativa\s+do\s+Brasil)?(?:\s+de\s+1988)?|"
    r"(?:巴西)?(?:联邦)?宪法", re.I)
# 法典名（葡语）：Código Civil / Código de Processo Civil / Código de Defesa do Consumidor…
CODIGO_RE = re.compile(
    r"\bC[óo]digo\s+(?:d[eoa]s?\s+)?[A-ZÀ-Ý][a-zà-ÿ]+"
    r"(?:\s+(?:d[eoa]s?)\s+[A-ZÀ-Ý]?[a-zà-ÿ]+|\s+[A-ZÀ-Ý][a-zà-ÿ]+)*")
# 条文引用：Art. 927 / Artigo 5º / Arts. 186 e 927 / art. 103-A / 第 927 条
ARTICLE_RES = [
    re.compile(r"\b[Aa]rt(?:igo)?s?\.?\s*(\d{1,4})(?:[ºo°ª])?(-[A-Z])?\b"),
    re.compile(r"第\s*(\d{1,4})()\s*条"),
]
# 判例引证（巴西 V1.0 无判例库 → 一律 declared）：STF/STJ 案件类别缩写 + 编号、Súmula
CASE_CITE_RES = [
    re.compile(r"\b(?:RE|REsp|AREsp|ADI|ADC|ADPF|ADO|HC|RHC|MS|RMS|AgRg|AgInt|EDcl|EREsp|IAC|IRDR)\b"
               r"(?:\s+(?:n[ºo°.]{0,2}\s*)?)?[\d][\d.,/-]*"),
    re.compile(r"\bS[úu]mula(?:\s+Vinculante)?\s+(?:n[ºo°.]{0,2}\s*)?\d+\b", re.I),
]
# 法规缩写/通称 → (库内标题匹配词, law_number, law_year)。有歧义的缩写标 None 占位防误配。
# law_number 为归一化编号（无千分点）；标题匹配词用于 title/title_pt ILIKE 兜底。
ABBR_MAP: dict[str, tuple[str, str | None, int | None] | None] = {
    "CLT": ("Consolidação das Leis do Trabalho", "5452", 1943),
    "CDC": ("Código de Defesa do Consumidor", "8078", 1990),
    "CTN": ("Código Tributário Nacional", "5172", 1966),
    "LGPD": ("Lei Geral de Proteção de Dados", "13709", 2018),
    "Lei das S.A.": ("Sociedades por Ações", "6404", 1976),
    "Lei das SA": ("Sociedades por Ações", "6404", 1976),
    "Lei das Sociedades Anônimas": ("Sociedades por Ações", "6404", 1976),
    "Lei Anticorrupção": ("Anticorrupção", "12846", 2013),
    "Lei de Arbitragem": ("Arbitragem", "9307", 1996),
    "Lei de Falências": ("recuperação judicial", "11101", 2005),
    "Lei de Recuperação Judicial": ("recuperação judicial", "11101", 2005),
    "Marco Civil da Internet": ("Marco Civil da Internet", "12965", 2014),
    "ECA": ("Estatuto da Criança e do Adolescente", "8069", 1990),
    "Código Civil": ("Código Civil", "10406", 2002),
    "Código Penal": ("Código Penal", "2848", 1940),
    "Código de Processo Civil": ("Código de Processo Civil", "13105", 2015),
    "Código de Processo Penal": ("Código de Processo Penal", "3689", 1941),
    "Código de Defesa do Consumidor": ("Código de Defesa do Consumidor", "8078", 1990),
    "Código Tributário Nacional": ("Código Tributário Nacional", "5172", 1966),
    "CPC": ("Código de Processo Civil", "13105", 2015),
    "CPP": ("Código de Processo Penal", "3689", 1941),
    "CC": None,    # 歧义：Código Civil / 抄送 / 其他缩写，不自动展开
    "CP": None,    # 歧义：Código Penal / 过短易误配，不自动展开
    "MP": None,    # 歧义：Medida Provisória / Ministério Público，不自动展开
}

_SENT_END = re.compile(r"[。；;\n]")
# "已声明"识别：引用同句出现如实声明字样
DECL_RE = re.compile(
    r"未经(?:本库|库内)?核验|库内(?:暂)?(?:无|未)(?:收录|检索到|判例)|判例库建设中|"
    r"(?:未能|无法|没有|未)(?:找到|查到|检索到|确认|核实)|"
    r"not\s+(?:in|found in)\s+(?:the\s+)?(?:corpus|database)|"
    r"not\s+found|(?:could\s+not|unable\s+to)\s+(?:find|locate|verify|confirm)", re.I)


def _norm(s: str) -> str:
    """归一化：小写、去重音符号、压空白（葡语标题/术语比对用）。"""
    s = (s or "").lower()
    table = str.maketrans("áàâãäéèêëíìîïóòôõöúùûüç", "aaaaaeeeeiiiiooooouuuuc")
    s = s.translate(table)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _base_label(num: str, suffix: str | None) -> str:
    """条号主标号：'927' + '-A' -> '927-A'；剥序数符（5º → 5）。"""
    return (num or "") + (suffix or "").upper()


def extract_refs(answer: str) -> dict[str, list]:
    """从答案抽取编号式法源、法典/缩写、宪法条款、条文与判例引证。"""
    # acts: (start, end, display_name, title_key, law_number, law_year, doc_types)
    acts: list[tuple] = []
    for m in LEI_RE.finditer(answer):
        kind_raw = re.sub(r"\s+", " ", m.group(1)).strip()
        kind = _norm(kind_raw)
        num = _norm_lawno(m.group(2))
        yr = m.group(3) or m.group(4)
        if not num:
            continue
        disp = f"{kind_raw} nº {m.group(2)}" + (f"/{yr}" if yr else "")
        acts.append((m.start(), m.end(), disp, None, num,
                     int(yr) if yr else None, _KIND_DOCTYPE.get(kind, ())))
    # 宪法提及（归一为标准名，供 Art. 归属）
    for m in CONST_RE.finditer(answer):
        acts.append((m.start(), m.end(), "CF/88", "__CONST__", None, None, ("constitution",)))
    # 法典名（Código …）：先查 ABBR_MAP 精确映射，未收录的按标题词回查
    seen_spans = set()
    for m in CODIGO_RE.finditer(answer):
        name = re.sub(r"\s+", " ", m.group(0)).strip()
        mapped = ABBR_MAP.get(name)
        if mapped:
            title_key, num, yr = mapped
            acts.append((m.start(), m.end(), name, title_key, num, yr, ()))
        else:
            acts.append((m.start(), m.end(), name, name, None, None, ()))
        seen_spans.add((m.start(), m.end()))
    # 缩写/通称提及（CLT/CDC/CTN/LGPD/Lei das S.A.…）归一，同样参与就近归属
    for abbr, mapped in ABBR_MAP.items():
        if not mapped or abbr.startswith("Código"):
            continue  # Código 全称已由 CODIGO_RE 覆盖
        abbr_pat = re.escape(abbr).replace("\\ ", " ").replace(" ", r"\s+")
        for m in re.finditer(r"(?<![A-Za-z])" + abbr_pat + r"(?![A-Za-z])", answer):
            if (m.start(), m.end()) in seen_spans:
                continue
            title_key, num, yr = mapped
            acts.append((m.start(), m.end(), abbr, title_key, num, yr, ()))

    # 条文位置
    articles: list[tuple[int, str]] = []
    for rx in ARTICLE_RES:
        for m in rx.finditer(answer):
            articles.append((m.start(), _base_label(m.group(1), m.group(2))))

    # 条文归属：找 ±300 字符内最近的法源提及；宪法归属单列（const_articles）
    stat_refs: list[dict] = []
    const_refs: list[dict] = []
    seen_sr, seen_ca = set(), set()
    for pos, label in articles:
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
                const_refs.append({"article": label, "display": f"CF/88 Art. {label}"})
            continue
        key = ((best[2] if best else None), label)
        if key in seen_sr:
            continue
        seen_sr.add(key)
        stat_refs.append({
            "act": best[2] if best else None,
            "title_key": best[3] if best else None,
            "law_number": best[4] if best else None,
            "law_year": best[5] if best else None,
            "doc_types": best[6] if best else (),
            "article": label,
            "display": (f"{best[2]}, Art. {label}" if best else f"Art. {label}"),
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
                r.update({"act": only[2], "title_key": only[3], "law_number": only[4],
                          "law_year": only[5], "doc_types": only[6],
                          "display": f"{only[2]}, Art. {r['article']}"})

    # 裸条号收敛：同一条号若已有带法律名的归属项，裸项是同一引用的重复形态，剔除之——
    # 避免同条既 verified 又 unverified 的自相矛盾。全文无归属版本的裸条号仍保留。
    attributed = {r["article"] for r in stat_refs if r["act"]}
    stat_refs = [r for r in stat_refs if not (r["act"] is None and r["article"] in attributed)]

    # 无条号的独立法源引用（编号式带编号即实质引用；缩写/法典名带映射编号的也算）
    seen_acts = {_norm(r["act"]) for r in stat_refs if r["act"]}
    for a in acts:
        if a[3] == "__CONST__" or not (a[4] or a[3]):
            continue
        n = _norm(a[2])
        if n in seen_acts:
            continue
        seen_acts.add(n)
        stat_refs.append({"act": a[2], "title_key": a[3], "law_number": a[4],
                          "law_year": a[5], "doc_types": a[6],
                          "article": None, "display": a[2]})

    # 判例引证（RE/REsp/ADI/ADPF/HC/AgRg + 数字、Súmula）：V1.0 无判例库 → declared
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

def _doc_where(r: dict) -> tuple[str, list]:
    """法律级匹配条件：优先 law_number（+年份），否则标题 ILIKE（title 与 title_pt 双列）。"""
    if r.get("law_number"):
        cond = "d.law_number = %s"
        params: list = [_norm_lawno(r["law_number"])]
        if r.get("law_year"):
            cond += " AND d.law_year = %s"
            params.append(int(r["law_year"]))
        if r.get("doc_types"):
            cond += " AND d.document_type = ANY(%s)"
            params.append(list(r["doc_types"]))
        return cond, params
    key = r.get("title_key") or r.get("act") or ""
    return "(d.title ILIKE %s OR d.title_pt ILIKE %s)", [f"%{key}%", f"%{key}%"]


def _statute_in_db(r: dict) -> tuple[bool, bool]:
    """成文法库回查（官方葡语核心法 + 英译）：返回 (法律在库, 条文在库)。
    命中即真实存在（权威路径）。"""
    cond, params = _doc_where(r)
    doc_hit = bool(db.query_one_statutes(
        f"SELECT 1 AS x FROM statutory_documents d WHERE {cond} LIMIT 1", params))
    sec_hit = False
    label = r.get("article")
    if doc_hit and label:
        sec_hit = bool(db.query_one_statutes(
            f"""SELECT 1 AS x FROM statutory_documents d
                JOIN statutory_sections s USING (document_id)
                WHERE {cond} AND s.section_label = %s LIMIT 1""",
            params + [label]))
    return doc_hit, sec_hit


def _match_cite(r: dict, c: dict) -> bool:
    """引用的法律是否即本轮 citation 的法律：编号+年份一致，或标题词包含。"""
    if r.get("law_number") and c.get("law_number"):
        if _norm_lawno(str(c["law_number"])) != _norm_lawno(r["law_number"]):
            return False
        if r.get("law_year") and c.get("law_year") and int(c["law_year"]) != int(r["law_year"]):
            return False
        return True
    key = _norm(r.get("title_key") or r.get("act") or "")
    if not key:
        return False
    return key in _norm(c.get("title") or "") or key in _norm(c.get("title_pt") or "")


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
        if status != "verified" and (r.get("law_number") or r.get("title_key") or r.get("act")):
            try:
                doc_hit, sec_hit = _statute_in_db(r)
                if doc_hit and (label is None or sec_hit):
                    status, note = "partial", "成文法库确认该法/该条存在，但本轮未调取原文"
                elif doc_hit:
                    status, note = ("partial",
                                    "法律在成文法库；该条号未单独成条（分条粒度限制），建议人工核对 planalto 官方文本")
                else:
                    note = "成文法库未检索到该法律（州法/市法或未入库的联邦法可能），建议人工核对"
            except Exception as exc:
                logger.warning("statute verify query failed: %s", exc)
                note = "库内回查暂不可用"
        elif status != "verified":
            note = "条号缺少所属法律上下文，无法核验"
        items.append({"kind": "statute", "ref": r["display"], "status": status, "note": note})

    # ---- 宪法条款（CF/88）----
    for r in refs.get("const_articles", []):
        label = r["article"]
        status, note = "unverified", ""
        # ① 本轮成文法取证覆盖（search_statutes 查过该 Art.）
        if any((c.get("document_type") == "constitution" or "constitui" in _norm(c.get("title") or "")
                or "constitui" in _norm(c.get("title_pt") or ""))
               and (c.get("section_label") or "").upper() == label.upper() for c in stat_cites):
            status, note = "verified", "已在本轮检索的宪法条款原文中核验"
        # ② 宪法库权威回查
        if status == "unverified":
            try:
                row = db.query_one_statutes(
                    """SELECT 1 AS x FROM statutory_documents d
                       JOIN statutory_sections s USING (document_id)
                       WHERE d.document_type = 'constitution' AND s.section_label = %s LIMIT 1""",
                    [label])
                if row:
                    status, note = "partial", "宪法库确认该条款存在，但本轮未调取原文"
                else:
                    note = "宪法库未检索到该条款（分条粒度限制可能），建议人工核对 CF/88 官方文本"
            except Exception as exc:
                logger.warning("const verify query failed: %s", exc)
                note = "库内回查暂不可用"
        items.append({"kind": "statute", "ref": r["display"], "status": status, "note": note})

    # ---- 判例引证（V1.1 判例库已上线）：processo 归一后回查 cases.processo_norm ----
    for cite in refs.get("case_cites", []):
        # Súmula（判例汇编条目，无 processo 案号）：无案号可回查 → 保持 declared
        if SUMULA_RE.search(cite):
            items.append({"kind": "case", "ref": cite, "status": "declared",
                          "note": "Súmula（判例汇编条目，无 processo 案号），未做库内案号核验"})
            continue
        norm = _norm_processo(cite)
        # 案号信息不足（归一后过短，如仅 "HC 12"）：前缀匹配易误配，不做肯定核验
        if len(norm) < 6:
            items.append({"kind": "case", "ref": cite, "status": "unverified",
                          "note": "案号信息不足，无法在判例库中核验"})
            continue
        try:
            row = db.query_one_cases(
                "SELECT court, relator FROM cases WHERE processo_norm ILIKE %s LIMIT 1",
                [norm + "%"])
        except Exception as exc:
            logger.warning("case verify query failed: %s", exc)
            items.append({"kind": "case", "ref": cite, "status": "unverified",
                          "note": "判例库回查暂不可用"})
            continue
        if row:
            note = f"判例库命中：{row.get('court') or ''}"
            if row.get("relator"):
                note += f"，Rel. {row.get('relator')}"
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
            # display 串（"Lei nº X/Y, Art. N"）可能与原文写法不同：退回按法律名部分定位
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

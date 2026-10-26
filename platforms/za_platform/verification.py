"""引用校验（反臆造）：从助手答案文本中抽取法条 / 判例引用，逐条验真。

三级判定：
- verified   已核验：该引用出现在本轮检索到的 citations 原文材料中；
- partial    部分核验：本轮材料未直接覆盖，但库内权威数据中确认存在（真实、但未经本轮取证）；
- unverified 未核验：本轮材料与库内数据均未能确认（可能为模型臆造，建议人工复核）。

设计原则：校验器是**纯规则引擎**（正则抽取 + 字符串匹配 + PG 存在性回查），
绝不引入第二个大模型——校验环节自身必须不可能臆造。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from za_common import evidence_guard

from . import db

logger = logging.getLogger("za_platform.verification")

MAX_REFS = 40  # 单次校验引用上限，防超长答案拖慢

# ---- 抽取正则 ----
# Act 全称 + 编号年份：Companies Act 71 of 2008 / Protection of Personal Information Act 4 of 2013
ACT_RE = re.compile(
    r"\b([A-Z][A-Za-z'()\-]*(?:[ ,][A-Za-z'()\-]+){0,8}?\s+Act)"
    r"(?:[ ,]*(?:No\.?\s*)?(\d{1,3})\s+of\s+((?:19|20)\d{2}))?\b"
)
# 宪法：名称不以 "Act" 结尾，需单独识别（南非宪法条款引用极高频，如平等权 §9）
CONST_RE = re.compile(r"\b(Constitution(?:\s+of\s+the\s+Republic\s+of\s+South\s+Africa)?(?:,?\s*1996)?)\b|宪法")
# 条文引用：Section 76 / Sections 76(2) / §76 / 第 76 条 / 第76(2)条 / s 76(2)(a) / ss 12-14（V1.3 补小写缩写形态）
SECTION_RES = [
    re.compile(r"[Ss]ections?\s+(\d{1,4}[A-Z]{0,2})(?:\s*\([^)\s]{1,8}\))*"),
    re.compile(r"§\s*(\d{1,4}[A-Z]{0,2})"),
    re.compile(r"第\s*(\d{1,4}[A-Z]{0,2})\s*(?:\([^)]{1,8}\)\s*)?条"),
    # 南非法律写作最常用的小写缩写：s 76(2)(a) / s.9 / ss 12（前缀不可是字母，防 "articles 5"）
    re.compile(r"(?<![A-Za-z])ss?\s*\.?\s*(\d{1,4}[A-Z]{0,2})(?=\s*\(|\b)"),
]
# 条约条款：Article 5 / Art. 5（南非成文法用 section 不用 article，可安全归条约类）
ARTICLE_RE = re.compile(r"\b(?:Article|Art\.?)\s+(\d{1,3}[A-Z]?)\b", re.I)
# 中性引证：[2021] ZACC 13 / [2004] 3 All SA 280 (SCA) 等
NEUTRAL_CITE_RE = re.compile(
    r"\[(?:19|20)\d{2}\]\s+(?:\d+\s+)?"
    r"(?:ZACC|ZASCA|ZAGP\w{2,4}|ZAWCHC|ZAKZ\w{2,4}|ZAEC\w{2,4}|ZAFS\w{2,4}|ZANW\w{2,4}|ZALCC?|ZALCJHB|ZALAC|ZACT|ZACAC|ZACC|All\s+SA|SA|BCLR|JOL)\s+\d+"
)
# 条约语境关键词（"第 N 条"出现在这些词 ±200 字符内→按条约条款核验，不走成文法回查）
TREATY_CTX_RE = re.compile(r"协定|条约|treaty|DTA|BIT|双重征税|投资协定|Agreement|Convention|Protocol", re.I)
# Act 缩写→官方全称（子串，用于与 citations/库内标题匹配；有歧义的缩写不收录）
ABBR_MAP = {
    "POPIA": "Protection of Personal Information Act",
    "LRA": "Labour Relations Act",
    "BCEA": "Basic Conditions of Employment Act",
    "EEA": "Employment Equity Act",
    "MPRDA": "Mineral and Petroleum Resources Development Act",
    "NEMA": "National Environmental Management Act",
    "PAJA": "Promotion of Administrative Justice Act",
    "PFMA": "Public Finance Management Act",
    "NCA": "National Credit Act",
    "ECTA": "Electronic Communications and Transactions Act",
    "FICA": "Financial Intelligence Centre Act",
    "COIDA": "Compensation for Occupational Injuries and Diseases Act",
    "TAA": "Tax Administration Act",
    "FAIS": "Financial Advisory and Intermediary Services Act",
    "B-BBEE": "Broad-Based Black Economic Empowerment Act",
    "BBBEE": "Broad-Based Black Economic Empowerment Act",
    "CCMA": None,  # 机构非法规，占位防误配（None 跳过）
}
# 判例案名：Party v Party。要点：
# - 当事人字符集含括号（"Venator Africa (Pty) Ltd" 这类公司后缀），否则名称被截断成垃圾引用；
# - 终止预查含数字与"["（案名后紧跟年份/引证 "2022 (1) SA" / "[2004]"）与任意中文字符（"X v Y 一案"）；
# - 第一当事人允许单字母（南非刑案 "S v Makwanyane" 极常见）。
CASE_RE = re.compile(
    r"\b([A-Z][A-Za-z'.,&()\- ]{0,80}?\s+v\s+[A-Z][A-Za-z'.,&()\- ]{2,80}?)"
    r"(?=\s*[，。；、：:,;\[\]（）\n*0-9一-鿿]|\s*$)"   # 终止符含 *（模型常用 **粗体** 包案名）
)


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    # "and" 与 "&" 等价：& 已被标点清洗剥掉，同步剥 "and" 使两种写法归一
    s = re.sub(r"\band\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_parties(name: str) -> tuple[str, str] | None:
    """拆案名双方当事人并剥离随行人后缀（"& others"/"and Another"）。

    库内案名多带随行人后缀，整串包含匹配会漏；拆 v 两侧做 AND 匹配更稳健。
    供核验引擎与 lookup_case 溯源工具共用。
    """
    parts = re.split(r"\s+v\s+", name, maxsplit=1)
    if len(parts) != 2:
        return None

    def clean(p: str) -> str:
        p = re.sub(r"\b(?:and|&)\s+(?:\d+\s+)?others?\b|\band\s+another\b", " ", p, flags=re.I)
        return re.sub(r"\s+", " ", p).strip(" .,&-")

    p1, p2 = clean(parts[0]), clean(parts[1])
    return (p1, p2) if len(p1) >= 2 and len(p2) >= 4 else None


def _quote_tolerant(s: str) -> str:
    """引号容错（V1.5）：库内标题多用弯引号（Judges’），答案写直引号（Judges'），
    ILIKE 永不匹配——把两种引号统一替换为 SQL 单字符通配符 `_`。"""
    return re.sub(r"[’'`]", "_", s)


def party_like(p: str) -> str:
    """当事人 → ILIKE 模式：'and'/'&' 互换为通配（库内两种写法混用，如 "Gay & Lesbian"）。"""
    return "%" + re.sub(r"\s+(?:and|&)\s+", "%", _quote_tolerant(p)) + "%"


# 案名当事人别名表（V1.4-D4）：答案里常用缩写 ↔ 库内全称（或反向，如库内存 "SARFU"）。
# 只在**整方精确等于**缩写时扩展（"EFF"→扩展；"EFF Holdings" 不动），避免误伤。
# 实证来源：cited_but_missing 工单里 "EFF v Speaker"/"President v SARU" 均为库内已有案，
# 纯因别名不匹配被判 unverified。
PARTY_ALIASES: dict[str, list[str]] = {
    "eff": ["economic freedom fighters"],
    "saru": ["sarfu", "south african rugby"],
    "sarfu": ["saru", "south african rugby"],
    "south african rugby football union": ["sarfu", "saru"],
    "numsa": ["national union of metalworkers"],
    "saccawu": ["south african commercial catering"],
    "nehawu": ["national education health and allied workers"],
    "popcru": ["police and prisons civil rights"],
    "samwu": ["south african municipal workers"],
    "cosatu": ["congress of south african trade unions"],
    "num": ["national union of mineworkers"],
    "amcu": ["association of mineworkers and construction"],
    "anc": ["african national congress"],
    "da": ["democratic alliance"],
    "ifp": ["inkatha freedom party"],
    "sabc": ["south african broadcasting corporation"],
    "sanral": ["south african national roads agency"],
    "outa": ["organisation undoing tax abuse"],
    "sars": ["south african revenue service", "commissioner for the south african revenue"],
    "ccma": ["commission for conciliation"],
}


def _alias_key(p: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", (p or "").lower())).strip()


def party_patterns(p: str) -> list[str]:
    """当事人 → 候选 ILIKE 模式列表（本名 + 别名扩展），配 `ILIKE ANY(%s)` 使用。"""
    pats = [party_like(p)]
    for alt in PARTY_ALIASES.get(_alias_key(p), []):
        pats.append(party_like(alt))
    return pats


def _norm_alts(p: str) -> list[str]:
    """当事人归一串的候选列表（本名 + 别名），供本轮 citations 内文匹配。"""
    return [_norm(p)] + [_norm(a) for a in PARTY_ALIASES.get(_alias_key(p), [])]


def _base_label(label: str) -> str:
    """条号取主标号：'76(2)(a)' -> '76'；'11A' 保留。"""
    m = re.match(r"(\d{1,4}[A-Z]{0,2})", label or "")
    return m.group(1) if m else (label or "")


def extract_refs(answer: str) -> dict[str, list]:
    """从答案抽取 (act, number, year, spans) 与条文、案名引用。"""
    acts = []  # (start, end, name, number, year)
    for m in ACT_RE.finditer(answer):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" ,")
        # 过滤伪命中（如 "The Act"、句首误配）
        if len(name) < 8 or name.lower() in ("the act", "this act", "that act"):
            continue
        num, yr = m.group(2), m.group(3)
        # V1.4.1：宪法带 "Act" 的变体名归一（模型常写 "Constitution of the Republic of
        # South Africa Act 108 of 1996"，库内标题无 "Act" 字样且不带编号，ILIKE 必漏）
        if re.match(r"constitution of the republic of south africa\b", name, re.I):
            name, num, yr = "Constitution of the Republic of South Africa, 1996", None, None
        acts.append((m.start(), m.end(), name, num, yr))
    # 宪法提及（统一归一为标准名，供 §N 就近归属）
    for m in CONST_RE.finditer(answer):
        acts.append((m.start(), m.end(),
                     "Constitution of the Republic of South Africa, 1996", None, None))
    # Act 缩写提及（POPIA/LRA…）归一为官方全称，同样参与就近归属（V1.3）
    for abbr, full in ABBR_MAP.items():
        if not full:
            continue  # None=已知非法规缩写（如 CCMA 机构名），仅占位防将来误配
        for m in re.finditer(r"\b" + re.escape(abbr) + r"\b", answer):
            acts.append((m.start(), m.end(), full, None, None))

    # 条约语境区间（±200 字符内出现协定/DTA/BIT 等关键词的位置集合）
    treaty_spans = [(m.start(), m.end()) for m in TREATY_CTX_RE.finditer(answer)]

    def _treaty_dist(pos: int) -> int:
        """到最近条约关键词的距离（无则大数）。与 Act 提及距离竞争，谁近归谁。"""
        if not treaty_spans:
            return 10 ** 9
        return min(min(abs(pos - s), abs(pos - e)) for s, e in treaty_spans)

    sections = []  # (pos, label)
    for rx in SECTION_RES:
        for m in rx.finditer(answer):
            sections.append((m.start(), _base_label(m.group(1))))

    # 条约条款（Article N 一律归条约；"第 N 条"在条约语境内也改归条约，不误走成文法回查）
    treaty_refs: list[dict] = []
    seen_tr = set()
    for m in ARTICLE_RE.finditer(answer):
        label = _base_label(m.group(1))
        if label not in seen_tr:
            seen_tr.add(label)
            treaty_refs.append({"article": label, "display": f"条约 Article {label}"})

    # 条文归属：找 ±300 字符内最近的 Act 提及；条约语境下的"第 N 条"改归条约
    stat_refs: list[dict] = []
    seen_sr = set()
    for pos, label in sections:
        best = None
        best_d = 301
        second_d = 10 ** 9
        for a0, a1, name, num, yr in acts:
            d = min(abs(pos - a1), abs(a0 - pos))
            if d < best_d:
                if best is not None and best[0] != name:
                    second_d = best_d
                best_d, best = d, (name, num, yr)
            elif best is not None and name != best[0] and d < second_d:
                second_d = d
        # V1.4.1 显著就近兜底：±300 内无 Act 时，若 800 内有唯一显著最近的 Act
        # （比次近者近 150+ 字符），仍归属它——长答案的总结段/表格常离 Act 提及较远，
        # 原先直接放弃归属被计 unverified（constitutional_rights §9/§36 实证）。
        if best is None:
            cand = None
            cand_d = 801
            cand2 = 10 ** 9
            for a0, a1, name, num, yr in acts:
                d = min(abs(pos - a1), abs(a0 - pos))
                if d < cand_d:
                    if cand is not None and cand[0] != name:
                        cand2 = cand_d
                    cand_d, cand = d, (name, num, yr)
                elif cand is not None and name != cand[0] and d < cand2:
                    cand2 = d
            if cand is not None and cand2 - cand_d > 150:
                best, best_d = cand, cand_d
        act_name = best[0] if best else None
        # 缩写归一（V1.3）：ACT_RE 可能以缩写形态命中（"B-BBEE Act"），统一展开为官方全称
        if act_name:
            for abbr, full in ABBR_MAP.items():
                if full and abbr in act_name and full not in act_name:
                    act_name = full
                    break
        # 条约语境竞争归属：条约关键词比最近 Act 提及更近（且在 200 内）→ 归条约条款
        td = _treaty_dist(pos)
        if td <= 200 and (act_name is None or td < best_d):
            if label not in seen_tr:
                seen_tr.add(label)
                treaty_refs.append({"article": label, "display": f"条约第 {label} 条"})
            continue
        key = (act_name, label)
        if key in seen_sr:
            continue
        seen_sr.add(key)
        disp = (f"{act_name} §{label}" if act_name else f"§{label}")
        stat_refs.append({"act": act_name, "act_number": best[1] if best else None,
                          "act_year": best[2] if best else None,
                          "section": label, "display": disp})

    # 裸条号文档级兜底（V1.3）：±300 内无 Act 提及（常见于表格/列表密集条号），
    # 若全文只涉及一部 Act（distinct 全称=1）→ 归属它（与 case_provision_refs 的
    # inferred 同款"单法推断"，精度优先；多法并存时保持无归属，不硬猜）。
    distinct_acts = {}
    for _, _, name, num, yr in acts:
        for abbr, full in ABBR_MAP.items():
            if full and abbr in name and full not in name:
                name = full
                break
        distinct_acts.setdefault(name, (num, yr))
    if len(distinct_acts) == 1:
        only_act, (only_num, only_yr) = next(iter(distinct_acts.items()))
        for r in stat_refs:
            if r["act"] is None:
                r["act"], r["act_number"], r["act_year"] = only_act, only_num, only_yr
                r["display"] = f"{only_act} §{r['section']}"

    # 无条号的独立 Act 引用（带编号年份的才算实质引用）；同样做缩写归一
    seen_acts = {r["act"] for r in stat_refs if r["act"]}
    for _, _, name, num, yr in acts:
        if num and yr and name not in seen_acts:
            for abbr, full in ABBR_MAP.items():
                if full and abbr in name and full not in name:
                    name = full
                    break
            if name in seen_acts:
                continue
            seen_acts.add(name)
            stat_refs.append({"act": name, "act_number": num, "act_year": yr,
                              "section": None, "display": f"{name} {num} of {yr}"})

    cases = []
    seen_c = set()
    for m in CASE_RE.finditer(answer):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" .,-")
        n = _norm(name)
        # 过滤过短/疑似非案名（如 "A v B" 太短）
        if len(n) < 10 or n in seen_c:
            continue
        seen_c.add(n)
        cases.append(name)

    # 中性引证（[2021] ZACC 13 等）——独立核验，不依赖案名（V1.3）
    neutral = []
    seen_n = set()
    for m in NEUTRAL_CITE_RE.finditer(answer):
        cite = re.sub(r"\s+", " ", m.group(0)).strip()
        if cite not in seen_n:
            seen_n.add(cite)
            neutral.append(cite)

    return {"statutes": stat_refs[:MAX_REFS], "cases": cases[:MAX_REFS],
            "treaty_articles": treaty_refs[:MAX_REFS], "neutral_cites": neutral[:MAX_REFS]}


def _act_matches_title(act: str, num: str | None, yr: str | None, title: str,
                       c_num: str | None, c_yr) -> bool:
    """引用的 Act 是否即 citation 的法规：名称子串 + （若有）编号年份一致。"""
    if _norm(act) not in _norm(title):
        return False
    if num and yr:
        cn = str(c_num or "")
        if cn and not cn.startswith(str(num)):
            return False
        if c_yr and str(c_yr) != str(yr):
            return False
    return True


def verify_answer(answer: str, citations: list[dict]) -> dict[str, Any]:
    """核心校验：答案引用 vs 本轮 citations → 不足处回查 PG。返回结构化核验结果。"""
    refs = extract_refs(answer or "")
    stat_cites = [c for c in (citations or []) if c.get("type") == "statute"]
    case_names = [_norm(c.get("case_name") or c.get("title") or "")
                  for c in (citations or []) if c.get("type") == "case"]
    # 官方材料/条约标题也纳入"本轮材料"匹配面（答案可能引 Amendment Act 之类的沿革标题）
    official_titles = [_norm(c.get("title") or "") for c in (citations or [])
                       if c.get("type") in ("regulation", "guidance", "treaty", "amendment")]

    items: list[dict] = []

    # ---- 成文法引用 ----
    for r in refs["statutes"]:
        act, label = r["act"], r["section"]
        status, note = "unverified", ""
        # ① 本轮 citations 直接覆盖？
        for c in stat_cites:
            if act and not _act_matches_title(act, r["act_number"], r["act_year"],
                                              c.get("title") or "", c.get("act_number"),
                                              c.get("act_year")):
                continue
            if label is None or _base_label(c.get("section_label") or "") == label:
                # act 为 None 时要求条号匹配才算（避免裸条号乱配）
                if act or label is not None:
                    status, note = "verified", "已在本轮检索材料中核验"
                    break
        # 沿革/官方材料标题兜底（如引用 "Companies Amendment Act 16 of 2024"）
        if status != "verified" and act and any(_norm(act) in t for t in official_titles):
            status, note = "verified", "见于本轮检索的官方材料标题"
        # ② 库内存在性回查（宪法已入成文法库，与普通 Act 同路径核验；V1.2.1）
        if status != "verified" and act:
            try:
                act_pat = f"%{_quote_tolerant(act)}%"
                doc_row = db.query_one_statutes(
                    "SELECT 1 AS x FROM statutory_documents WHERE title ILIKE %s LIMIT 1",
                    [act_pat])
                sec_row = None
                if doc_row and label:
                    sec_row = db.query_one_statutes(
                        """SELECT 1 AS x FROM statutory_documents d
                           JOIN statutory_sections s ON s.document_id=d.document_id
                           WHERE d.title ILIKE %s AND s.section_label LIKE %s LIMIT 1""",
                        [act_pat, f"{label}%"])
                if doc_row and (label is None or sec_row):
                    status, note = "partial", "库内确认存在，但本轮未调取该条原文"
                elif doc_row:
                    # 法规在库但该条未单独成条（老法残源/分条缺口）——不宜按臆造处理
                    status, note = "partial", "法规库内存在；该条未在库内单独成条（老法源文限制），建议人工核对条号"
                else:
                    note = "本轮材料与库内均未检索到"
            except Exception as exc:
                logger.warning("statute verify query failed: %s", exc)
                note = "库内回查暂不可用"
        elif status != "verified" and not act:
            note = "条号缺少所属法规上下文，无法核验"
        items.append({"kind": "statute", "ref": r["display"], "status": status, "note": note})

    # ---- 判例引用 ----
    for name in refs["cases"]:
        n = _norm(name)
        pp = split_parties(name)
        status, note = "unverified", ""
        hit = any(n in cn or cn in n for cn in case_names if len(cn) >= 10)
        if not hit and pp:
            alts1, alts2 = _norm_alts(pp[0]), _norm_alts(pp[1])
            hit = any(any(a in cn for a in alts1) and any(b in cn for b in alts2)
                      for cn in case_names if len(cn) >= 10)
        if hit:
            status, note = "verified", "已在本轮检索材料中核验"
        else:
            try:
                if pp:
                    row = db.query_one(
                        "SELECT 1 AS x FROM cases WHERE case_name ILIKE ANY(%s) AND case_name ILIKE ANY(%s) LIMIT 1",
                        [party_patterns(pp[0]), party_patterns(pp[1])])
                else:
                    np = f"%{_quote_tolerant(name)}%"
                    row = db.query_one(
                        "SELECT 1 AS x FROM cases WHERE case_name ILIKE %s OR title ILIKE %s LIMIT 1",
                        [np, np])
                if not row:
                    # trigram 相似度兜底：源数据案名存在 OCR 笔误（如 Equality→Equility），
                    # 精确 ILIKE 会漏；相似度≥0.55 视为同案（走 trgm 索引）。
                    row = db.query_one(
                        """SELECT 1 AS x FROM cases
                           WHERE case_name %% %s AND similarity(case_name, %s) >= 0.55 LIMIT 1""",
                        [name, name])
                if row:
                    status, note = "partial", "库内确认存在该案，但本轮未调取"
                else:
                    note = "本轮材料与库内均未检索到该案名"
            except Exception as exc:
                logger.warning("case verify query failed: %s", exc)
                note = "库内回查暂不可用"
        items.append({"kind": "case", "ref": name, "status": status, "note": note})

    # ---- 条约条款（V1.3）：只与本轮条约类 citations 比对，不走成文法回查 ----
    # V1.4.1：候选池含 regulation——引渡/MLA 双边条约以官方公报形式存于公告库
    # （INSTRUCTIONS 指路 search_regulations），其正文同样是 ARTICLE N 结构；
    # 只扫 treaty 型会把这些条款全误判 unverified（mla 题 7/9 误判实证）。
    treaty_chunks = [(c.get("chunk_text") or "") + " " + (c.get("title") or "")
                     for c in (citations or []) if c.get("type") in ("treaty", "regulation")]
    for r in refs.get("treaty_articles", []):
        label = r["article"]
        status, note = "unverified", "本轮未检索到含该条款的条约材料"
        if treaty_chunks:
            pat = re.compile(r"\bArticle\s+" + re.escape(label) + r"\b", re.I)
            if any(pat.search(t) for t in treaty_chunks):
                status, note = "verified", "已在本轮检索的条约条款原文中核验"
            else:
                status, note = "partial", "本轮含相关条约材料，但未调取该条全文"
        items.append({"kind": "treaty", "ref": r["display"], "status": status, "note": note})

    # ---- 中性引证（V1.3）：[2021] ZACC 13 与 citations/库内 citation 字段比对 ----
    cite_fields = [_norm(c.get("citation") or "") for c in (citations or [])
                   if c.get("type") == "case" and c.get("citation")]
    for cite in refs.get("neutral_cites", []):
        nc = _norm(cite)
        status, note = "unverified", ""
        if any(nc in cf or cf in nc for cf in cite_fields if len(cf) >= 8):
            status, note = "verified", "已在本轮检索材料的引证中核验"
        else:
            try:
                row = db.query_one(
                    "SELECT 1 AS x FROM cases WHERE citation ILIKE %s LIMIT 1", [f"%{cite}%"])
                if row:
                    status, note = "partial", "库内存在该引证判例，但本轮未调取"
                else:
                    note = "本轮材料与库内均未检索到该引证"
            except Exception as exc:
                logger.warning("neutral cite verify failed: %s", exc)
                note = "库内回查暂不可用"
        items.append({"kind": "citation", "ref": cite, "status": status, "note": note})

    for item in items:
        if (item["status"] == "unverified" and item["kind"] == "statute"
                and evidence_guard.reference_appears_in_evidence(item["ref"], stat_cites)):
            item["status"] = "partial"
            item["note"] = "该法源名称见于本轮检索原文，但未独立调取其文本或确认现行效力"

    # "已声明"降级（V1.2.1）：模型在引用附近已如实标注"未经核验/库内未收录"的，
    # 属于遵守溯源纪律的诚实行为，单列 declared、不再计入 unverified（不冤枉好人）。
    DECL_RE = re.compile(
        r"未经(?:本库|库内)?核验|库内未收录|未收录|"
        r"(?:未能|无法|没有|未)(?:找到|查到|检索到|确认|核实)|"
        r"not\s+(?:in|found in)\s+(?:the\s+)?(?:corpus|database)|"
        r"not\s+found|(?:could\s+not|unable\s+to)\s+(?:find|locate|verify|confirm)",
        re.I,
    )
    _sent_end = re.compile(r"[。；;\n]")
    for i in items:
        if i["status"] != "unverified":
            continue
        pos = (answer or "").find(i["ref"][:50])
        if pos < 0:
            continue
        # 只看引用所在句（上一句边界到下一句边界），避免相邻引用蹭到别句的声明
        starts = [m.end() for m in _sent_end.finditer(answer, 0, pos)]
        s0 = starts[-1] if starts else 0
        m1 = _sent_end.search(answer, pos + len(i["ref"]))
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

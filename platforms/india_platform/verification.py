"""引用校验（反臆造）：从助手答案文本中抽取法条 / 判例引用，逐条验真。印度版。

四级判定（与南非 V1.2+ 逻辑一致）：
- verified   已核验：该引用出现在本轮检索到的 citations 原文材料中；
- partial    部分核验：本轮材料未直接覆盖，但库内权威数据中确认存在（真实、但未经本轮取证）；
- declared   已声明：模型在引用同句已如实标注"未经核验/库内未收录"（诚实行为，不冤枉好人）；
- unverified 未核验：本轮材料与库内数据均未能确认（可能为模型臆造，建议人工复核）。

设计原则：校验器是**纯规则引擎**（正则抽取 + 字符串匹配 + PG 存在性回查），
绝不引入第二个大模型——校验环节自身必须不可能臆造。

印度化差异（对照南非版）：
1. 引证体系换 LNIND / (2020) 5 SCC 1 / AIR 2019 SC 1234 / [2014] 2 MLJ 64 / 2023 INSC 1；
2. **Article N 默认归宪法**（Constitution of India；南非版归条约——印度宪法条款引用极高频），
   仅当条约语境（±200 字符内出现协定/treaty/DTA 等关键词）时才归条约条款；
3. V1.0 无成文法库：法规/条文的"库内存在性回查"走判例语料聚合——
   known_statutes 物化表（legal_entities.statutes 聚合）+ case_provision_refs（条款级）；
4. ABBR_MAP 换印度缩写（IPC/CrPC/CPC/PMLA/NDPS/IBC/BNS/BNSS/BSA…）；
5. **新旧法典映射**（BNS↔IPC / BNSS↔CrPC / BSA↔Evidence Act，2024-07-01 切换）：
   引用新法典条文时在核验注记中给出旧法典对应条号（库内判例多引旧条号），反之亦然；
6. 案名分隔词兼容 v / v. / vs / vs. / versus（库内 Lexis 案名用 VERSUS）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from za_common import evidence_guard

from . import db

logger = logging.getLogger("india_platform.verification")

MAX_REFS = 40  # 单次校验引用上限，防超长答案拖慢

# ---- 抽取正则 ----
# 法规全称：印度法规以 Act/Code/Sanhita/Adhiniyam 结尾，年份多为 ", 2013" 后缀式
# （Companies Act, 2013 / Indian Penal Code, 1860 / Bharatiya Nyaya Sanhita, 2023）；
# 也兼容 "Act 20 of 2013" 编号式。
ACT_RE = re.compile(
    r"\b([A-Z][A-Za-z'()\-]*(?:[ ,][A-Za-z'()\-]+){0,9}?\s+(?:Act|Code|Sanhita|Adhiniyam))"
    r"(?:(?:,?\s*|\s+(?:No\.?\s*)?(\d{1,3})\s+of\s+)((?:18|19|20)\d{2}))?\b"
)
# 两部程序法以 Code 开头而不是结尾，不能由 ACT_RE 的通用尾缀规则识别。
PREFIX_CODE_RE = re.compile(
    r"\b(Code\s+of\s+(?:Civil|Criminal)\s+Procedure)(?:,?\s*((?:18|19|20)\d{2}))?\b"
)
# 宪法：名称不以 Act 结尾，单独识别（印度宪法条款引用极高频，Article 14/21/226…）
CONST_RE = re.compile(r"\b(Constitution(?:\s+of\s+India)?(?:,?\s*1950)?)\b|宪法")
# 条文引用：Section 302 / §302 / 第 302 条 / s 302(1)(a)
SECTION_RES = [
    re.compile(r"[Ss]ections?\s+(\d{1,4}[A-Z]{0,2})(?:\s*\([^)\s]{1,8}\))*"),
    re.compile(r"§\s*(\d{1,4}[A-Z]{0,2})"),
    re.compile(r"第\s*(\d{1,4}[A-Z]{0,2})\s*(?:\([^)]{1,8}\)\s*)?条"),
    # 小写缩写 s 302 / s.302 / ss 34（前缀不可是字母，防 "articles 5"）
    re.compile(r"(?<![A-Za-z])ss?\s*\.?\s*(\d{1,4}[A-Z]{0,2})(?=\s*\(|\b)"),
]
# 宪法/条约条款：Article 21 / Art. 21（印度默认归宪法，条约语境竞争归属见 extract_refs）
ARTICLE_RE = re.compile(r"\b(?:Article|Art\.?)\s+(\d{1,3}[A-Z]?)\b|宪法第\s*(\d{1,3}[A-Z]?)\s*条", re.I)
# 判例引证：LNIND 系列（库内主引证）+ SCC/AIR/SCR/MLJ/INSC 等汇编与中性引证
CITE_RES = [
    re.compile(r"\bLNIND[A-Z]*\s+(?:19|20)\d{2}\s+[A-Z]{2,6}\s+\d+\b"),
    re.compile(r"\(\s*(?:19|20)\d{2}\s*\)\s+\d{1,2}\s+SCC\s+\d+\b"),
    re.compile(r"\bAIR\s+(?:18|19|20)\d{2}\s+[A-Za-z.&()\s]{2,12}?\s+\d+\b"),
    re.compile(r"\[\s*(?:19|20)\d{2}\s*\]\s+\d{1,2}\s+(?:SCR|MLJ(?:\s*\(Crl\))?|SCALE|JT)\s+\d+\b"),
    re.compile(r"\b(?:19|20)\d{2}\s+INSC\s+\d+\b"),
    re.compile(r"\(\s*(?:19|20)\d{2}\s*\)\s+\d{1,2}\s+MLJ(?:\s*\(Crl\))?\s+\d+\b"),
]
# 条约语境关键词（Article 出现在这些词 ±200 字符内→按条约条款处理，不归宪法）
TREATY_CTX_RE = re.compile(
    r"协定|条约|treaty|DTAA?|BIT|双重征税|投资协定|Agreement|Convention|Protocol|CISG|纽约公约", re.I)
# 法规缩写→官方全称（子串匹配用；有歧义的缩写标 None 占位防误配）
ABBR_MAP = {
    "IPC": "Indian Penal Code",
    "CrPC": "Code of Criminal Procedure",
    "CRPC": "Code of Criminal Procedure",
    "CPC": "Code of Civil Procedure",
    "BNS": "Bharatiya Nyaya Sanhita",
    "BNSS": "Bharatiya Nagarik Suraksha Sanhita",
    "BSA": "Bharatiya Sakshya Adhiniyam",
    "PMLA": "Prevention of Money-Laundering Act",
    "NDPS": "Narcotic Drugs and Psychotropic Substances Act",
    "NI Act": "Negotiable Instruments Act",
    "IBC": "Insolvency and Bankruptcy Code",
    "SARFAESI": "Securitisation and Reconstruction of Financial Assets and Enforcement of Security Interest Act",
    "POCSO": "Protection of Children from Sexual Offences Act",
    "UAPA": "Unlawful Activities (Prevention) Act",
    "RTI": "Right to Information Act",
    "FEMA": "Foreign Exchange Management Act",
    "MV Act": "Motor Vehicles Act",
    "PC Act": "Prevention of Corruption Act",
    "CGST": "Central Goods and Services Tax Act",
    "RERA": "Real Estate (Regulation and Development) Act",
    "JJ Act": "Juvenile Justice (Care and Protection of Children) Act",
    "IT Act": None,   # 歧义：Income-tax Act 1961 / Information Technology Act 2000，不自动展开
    "ITA": None,      # 同上歧义
    "ED": None,       # 机构非法规
    "CBI": None,      # 机构非法规
}
# 新旧法典条号映射（2024-07-01 切换；核验注记用——库内判例绝大多数引旧条号）。
# 只收录高频且对应关系明确的条目，宁缺毋滥。
BNS_IPC_MAP = {  # BNS 条号 -> (IPC 条号, 罪名提示)
    "103": ("302", "谋杀 murder"),
    "105": ("304", "非谋杀致死 culpable homicide"),
    "106": ("304A", "过失致死 causing death by negligence"),
    "109": ("307", "谋杀未遂 attempt to murder"),
    "115": ("323", "故意伤害 voluntarily causing hurt"),
    "118": ("324/326", "利器/危险手段伤害"),
    "137": ("363", "绑架 kidnapping"),
    "303": ("378/379", "盗窃 theft"),
    "309": ("392", "抢劫 robbery"),
    "316": ("405/406", "刑事背信 criminal breach of trust"),
    "318": ("420", "诈骗 cheating"),
    "351": ("503/506", "刑事恐吓 criminal intimidation"),
    "356": ("499/500", "诽谤 defamation"),
    "85": ("498A", "夫家虐待 cruelty by husband/relatives"),
    "80": ("304B", "嫁妆死亡 dowry death"),
    "63": ("375/376", "强奸 rape"),
    "111": ("120B", "共谋 criminal conspiracy(组织犯罪条款重构)"),
}
IPC_BNS_MAP = {}  # 反向映射（IPC 条号 -> (BNS 条号, 提示)），模块加载时自动生成
for _bns, (_ipc, _hint) in BNS_IPC_MAP.items():
    for _p in _ipc.split("/"):
        IPC_BNS_MAP.setdefault(_p, (_bns, _hint))
BNSS_CRPC_MAP = {  # BNSS -> CrPC 高频条目
    "528": ("482", "高等法院固有权力 inherent powers"),
    "483": ("438", "预期保释 anticipatory bail"),
    "480": ("437", "保释 bail"),
    "144": ("125", "赡养令 maintenance"),
    "175": ("156(3)", "治安法官命令侦查"),
    "223": ("200", "控告审查 examination of complainant"),
}

# 判例案名：Party v Party。印度化：
# - 分隔词兼容 v / v. / vs / vs. / versus / Versus（库内 Lexis 案名用大写 VERSUS）；
# - 当事人字符集含 @（印度别名习惯 "Amit @ Bandya"）、/（"M/S" 公司前缀）；
# - 终止预查含数字与 "["（案名后紧跟引证）与任意中文字符。
CASE_RE = re.compile(
    r"\b([A-Z][A-Za-z'.,&()@/\- ]{0,90}?\s+(?:v|vs|versus|Versus|VERSUS)\.?\s+"
    r"[A-Z][A-Za-z'.,&()@/\- ]{2,90}?)"
    r"(?=\s*[，。；、：:,;\[\]（）\n*0-9一-鿿]|\s*$)"
)
_V_SPLIT_RE = re.compile(r"\s+(?:v|vs|versus)\.?\s+", re.I)


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    s = re.sub(r"\band\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def split_parties(name: str) -> tuple[str, str] | None:
    """拆案名双方当事人并剥离随行人后缀（"& others"/"and Another"）。

    库内案名多带随行人后缀（"AND OTHERS"），整串包含匹配会漏；拆 v 两侧做 AND 匹配更稳健。
    供核验引擎与 lookup_case 溯源工具共用。
    """
    parts = _V_SPLIT_RE.split(name, maxsplit=1)
    if len(parts) != 2:
        return None

    def clean(p: str) -> str:
        p = re.sub(r"\b(?:and|&)\s+(?:\d+\s+)?others?\b|\band\s+another\b|\bETC\b", " ", p, flags=re.I)
        # 印度公司前缀 M/S. / M/S 不入匹配模式（库内写法不稳定）
        p = re.sub(r"\bM/S\.?\s*", " ", p, flags=re.I)
        return re.sub(r"\s+", " ", p).strip(" .,&-")

    p1, p2 = clean(parts[0]), clean(parts[1])
    return (p1, p2) if len(p1) >= 2 and len(p2) >= 4 else None


def party_like(p: str) -> str:
    """当事人 → ILIKE 模式：'and'/'&' 互换为通配（库内两种写法混用）。"""
    return "%" + re.sub(r"\s+(?:and|&)\s+", "%", p) + "%"


# 案名当事人别名表：答案里常用缩写 ↔ 库内全称。只在**整方精确等于**缩写时扩展，避免误伤。
PARTY_ALIASES: dict[str, list[str]] = {
    "uoi": ["union of india"],
    "union of india": ["uoi"],
    "state of nct of delhi": ["nct of delhi", "govt of nct of delhi"],
    "nct of delhi": ["state of nct of delhi", "govt of nct of delhi"],
    "cbi": ["central bureau of investigation"],
    "central bureau of investigation": ["cbi"],
    "ed": ["directorate of enforcement", "enforcement directorate"],
    "directorate of enforcement": ["enforcement directorate"],
    "enforcement directorate": ["directorate of enforcement"],
    "sebi": ["securities and exchange board of india"],
    "rbi": ["reserve bank of india"],
    "lic": ["life insurance corporation"],
    "ongc": ["oil and natural gas"],
    "sail": ["steel authority of india"],
    "bsnl": ["bharat sanchar nigam"],
    "ntpc": ["national thermal power"],
    "dda": ["delhi development authority"],
    "mcd": ["municipal corporation of delhi"],
    "nhai": ["national highways authority of india"],
    "itat": None and [],  # 机构（裁判所）非当事人常客，不扩展
}
PARTY_ALIASES = {k: v for k, v in PARTY_ALIASES.items() if v}


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
    """条号取主标号：'302(1)(a)' -> '302'；'498A' 保留。"""
    m = re.match(r"(\d{1,4}[A-Z]{0,2})", label or "")
    return m.group(1) if m else (label or "")


def _code_transition_note(act_name: str | None, label: str | None) -> str:
    """新旧法典对应注记：引 BNS/BNSS 条文提示旧条号（库内判例多引旧条号），反之亦然。"""
    if not act_name or not label:
        return ""
    low = act_name.lower()
    if "nyaya sanhita" in low and label in BNS_IPC_MAP:
        ipc, hint = BNS_IPC_MAP[label]
        return f"（新法典条文：对应旧 IPC §{ipc}·{hint}；库内历史判例多引旧条号）"
    if "penal code" in low and label in IPC_BNS_MAP:
        bns, hint = IPC_BNS_MAP[label]
        return f"（旧法典条文：2024-07 起对应 BNS §{bns}·{hint}）"
    if "nagarik suraksha" in low and label in BNSS_CRPC_MAP:
        crpc, hint = BNSS_CRPC_MAP[label]
        return f"（新法典条文：对应旧 CrPC §{crpc}·{hint}；库内历史判例多引旧条号）"
    return ""


def extract_refs(answer: str) -> dict[str, list]:
    """从答案抽取法规/条文、宪法条款、案名与判例引证。"""
    acts = []  # (start, end, name, number, year)
    for m in ACT_RE.finditer(answer):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" ,")
        # 非法规名黑名单："India Code" 是官方数据库名（红线文案高频出现），不是法规
        if len(name) < 6 or name.lower() in (
                "the act", "this act", "that act", "the code", "this code", "india code"):
            continue
        acts.append((m.start(), m.end(), name, m.group(2), m.group(3)))
    for m in PREFIX_CODE_RE.finditer(answer):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" ,")
        acts.append((m.start(), m.end(), name, None, m.group(2)))
    # 宪法提及（归一为标准名，供 Article 归属）
    for m in CONST_RE.finditer(answer):
        acts.append((m.start(), m.end(), "Constitution of India", None, None))
    # 法规缩写提及（IPC/CrPC/PMLA…）归一为官方全称，同样参与就近归属
    for abbr, full in ABBR_MAP.items():
        if not full:
            continue
        for m in re.finditer(r"\b" + re.escape(abbr) + r"\b", answer):
            acts.append((m.start(), m.end(), full, None, None))

    # 条约语境区间
    treaty_spans = [(m.start(), m.end()) for m in TREATY_CTX_RE.finditer(answer)]

    def _treaty_dist(pos: int) -> int:
        if not treaty_spans:
            return 10 ** 9
        return min(min(abs(pos - s), abs(pos - e)) for s, e in treaty_spans)

    sections = []  # (pos, label)
    for rx in SECTION_RES:
        for m in rx.finditer(answer):
            sections.append((m.start(), _base_label(m.group(1))))

    # Article N：印度默认归宪法条款；条约语境（±200 内条约词更近）改归条约
    const_refs: list[dict] = []
    treaty_refs: list[dict] = []
    seen_ca, seen_tr = set(), set()
    for m in ARTICLE_RE.finditer(answer):
        label = _base_label(m.group(1) or m.group(2))
        if _treaty_dist(m.start()) <= 200:
            if label not in seen_tr:
                seen_tr.add(label)
                treaty_refs.append({"article": label, "display": f"条约 Article {label}"})
        else:
            if label not in seen_ca:
                seen_ca.add(label)
                const_refs.append({"article": label,
                                   "display": f"Constitution of India Article {label}"})

    # 条文归属：限定在同一语句/表格单元内，优先采用条号之前最近的法规名。
    # 不能单纯取 ±300 字符绝对距离，否则“CPC §44A，另受 Limitation Act
    # 约束”会把 §44A 错配给后出现的 Limitation Act。
    segment_delimiters = "\n。；;！？!?|"

    def _segment_bounds(pos: int) -> tuple[int, int]:
        left = max(answer.rfind(mark, 0, pos) for mark in segment_delimiters) + 1
        right_candidates = [answer.find(mark, pos) for mark in segment_delimiters]
        right_candidates = [value for value in right_candidates if value >= 0]
        return left, min(right_candidates, default=len(answer))

    stat_refs: list[dict] = []
    seen_sr = set()
    for pos, label in sections:
        left, right = _segment_bounds(pos)
        local_acts = [
            act for act in acts
            if act[0] >= left and act[1] <= right
            and min(abs(pos - act[1]), abs(act[0] - pos)) <= 220
        ]
        preceding = [act for act in local_acts if act[1] <= pos]
        following = [act for act in local_acts if act[0] > pos]
        explicit_following = [
            act for act in following
            if re.fullmatch(r".{0,24}\b(?:of|under|in)\s+(?:the\s+)?", answer[pos:act[0]], re.I | re.S)
        ]
        if explicit_following:
            chosen = min(explicit_following, key=lambda act: act[0])
        elif preceding:
            chosen = max(preceding, key=lambda act: act[1])
        elif following:
            chosen = min(following, key=lambda act: act[0])
        else:
            chosen = None
        best = (chosen[2], chosen[3], chosen[4]) if chosen else None
        act_name = best[0] if best else None
        if act_name:
            for abbr, full in ABBR_MAP.items():
                if full and abbr in act_name and full not in act_name:
                    act_name = full
                    break
        key = (act_name, label)
        if key in seen_sr:
            continue
        seen_sr.add(key)
        disp = (f"{act_name} §{label}" if act_name else f"§{label}")
        stat_refs.append({"act": act_name, "act_number": best[1] if best else None,
                          "act_year": best[2] if best else None,
                          "section": label, "display": disp})

    # 裸条号文档级兜底：±300 内无法规提及且全文只涉及一部法规 → 归属它（单法推断）
    distinct_acts = {}
    for _, _, name, num, yr in acts:
        for abbr, full in ABBR_MAP.items():
            if full and abbr in name and full not in name:
                name = full
                break
        if name != "Constitution of India":
            distinct_acts.setdefault(name, (num, yr))
    if len(distinct_acts) == 1:
        only_act, (only_num, only_yr) = next(iter(distinct_acts.items()))
        for r in stat_refs:
            if r["act"] is None:
                r["act"], r["act_number"], r["act_year"] = only_act, only_num, only_yr
                r["display"] = f"{only_act} §{r['section']}"

    # 裸条号收敛（印度版改进）：同一条号若已有带法规名的归属项（如 "NI Act §138" 与表格里
    # 孤立的 "§138" 并存），裸项是同一引用的重复形态，剔除之——避免同条既 verified 又
    # unverified 的自相矛盾。全文无归属版本的裸条号仍保留（真·孤立条号该被点名）。
    attributed_labels = {r["section"] for r in stat_refs if r["act"] and r["section"]}
    stat_refs = [r for r in stat_refs
                 if not (r["act"] is None and r["section"] in attributed_labels)]

    # 无条号的独立法规引用（带年份的才算实质引用）；同样做缩写归一
    seen_acts = {r["act"] for r in stat_refs if r["act"]}
    for _, _, name, num, yr in acts:
        if yr and name not in seen_acts and name != "Constitution of India":
            for abbr, full in ABBR_MAP.items():
                if full and abbr in name and full not in name:
                    name = full
                    break
            if name in seen_acts:
                continue
            seen_acts.add(name)
            disp = f"{name}, {yr}" if not num else f"{name} {num} of {yr}"
            stat_refs.append({"act": name, "act_number": num, "act_year": yr,
                              "section": None, "display": disp})

    cases = []
    seen_c = set()
    for m in CASE_RE.finditer(answer):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" .,-")
        n = _norm(name)
        if len(n) < 10 or n in seen_c:
            continue
        seen_c.add(n)
        cases.append(name)

    # 判例引证（LNIND / SCC / AIR / MLJ / INSC）——独立核验，不依赖案名
    neutral = []
    seen_n = set()
    for rx in CITE_RES:
        for m in rx.finditer(answer):
            cite = re.sub(r"\s+", " ", m.group(0)).strip()
            if cite not in seen_n:
                seen_n.add(cite)
                neutral.append(cite)

    return {"statutes": stat_refs[:MAX_REFS], "cases": cases[:MAX_REFS],
            "const_articles": const_refs[:MAX_REFS],
            "treaty_articles": treaty_refs[:MAX_REFS], "neutral_cites": neutral[:MAX_REFS]}


def _act_matches_title(act: str, num: str | None, yr: str | None, title: str,
                       c_num: str | None, c_yr) -> bool:
    """引用的法规是否即 citation 的法规：名称子串 + （若有）年份一致。"""
    if _norm(act) not in _norm(title):
        return False
    if yr and c_yr and str(c_yr) != str(yr):
        return False
    return True


def _statute_in_db(act: str, label: str | None) -> tuple[bool, bool]:
    """【V1.1】成文法库回查（India Code 846 部 + 宪法）：
    返回 (法规在库, 条文在库)。这是权威路径，命中即真实存在。"""
    doc_hit = bool(db.query_one_statutes(
        "SELECT 1 AS x FROM statutory_documents WHERE title ILIKE %s LIMIT 1",
        [f"%{act}%"]))
    sec_hit = False
    if doc_hit and label:
        sec_hit = bool(db.query_one_statutes(
            """SELECT 1 AS x FROM statutory_documents d
               JOIN statutory_sections s USING (document_id)
               WHERE d.title ILIKE %s AND s.section_label = %s LIMIT 1""",
            [f"%{act}%", label]))
    return doc_hit, sec_hit


def _statute_in_corpus(act: str, label: str | None) -> tuple[bool, bool]:
    """判例语料回查（成文法库未命中时的兜底——州法律/已废止法不在 India Code 中央库，
    但可能被判例大量援引）：返回 (法规被判例援引过, 条款级援引记录存在)。"""
    act_hit = bool(db.query_one(
        "SELECT 1 AS x FROM known_statutes WHERE act_title ILIKE %s LIMIT 1",
        [f"%{act}%"]))
    sec_hit = False
    if act_hit and label:
        sec_hit = bool(db.query_one(
            """SELECT 1 AS x FROM case_provision_refs
               WHERE act_title ILIKE %s AND section_label = %s LIMIT 1""",
            [f"%{act}%", label]))
    return act_hit, sec_hit


def verify_answer(answer: str, citations: list[dict]) -> dict[str, Any]:
    """核心校验：答案引用 vs 本轮 citations → 不足处回查 PG。返回结构化核验结果。"""
    refs = extract_refs(answer or "")
    case_names = [_norm(c.get("case_name") or c.get("title") or "")
                  for c in (citations or []) if c.get("type") == "case"]
    # 本轮判例 citations 携带的援引法规（get_case_detail 会带 statutes）——
    # V1.0 的"本轮材料"匹配面：答案里的法规引用若出现在本轮判例的援引清单中即 verified
    cited_statute_texts = []
    for c in (citations or []):
        if c.get("type") == "case":
            for k in ("statutes", "provisions"):
                v = c.get(k)
                if isinstance(v, list):
                    cited_statute_texts.extend(_norm(str(x)) for x in v)

    # 本轮成文法 citations（V1.1：search_statutes/get_statute_detail 的取证材料）
    stat_cites = [c for c in (citations or []) if c.get("type") == "statute"]

    items: list[dict] = []

    # ---- 法规/条文引用 ----
    for r in refs["statutes"]:
        act, label = r["act"], r["section"]
        status, note = "unverified", ""
        trans = _code_transition_note(act, label)
        # ① 本轮成文法取证材料直接覆盖？（最强核验：条文原文在本轮materials里）
        if act:
            for c in stat_cites:
                if not _act_matches_title(act, r["act_number"], r["act_year"],
                                          c.get("title") or "", c.get("act_number"),
                                          c.get("act_year")):
                    continue
                if label is None or _base_label(c.get("section_label") or "") == label:
                    status, note = "verified", "已在本轮检索的法条原文中核验"
                    break
        # ② 本轮判例材料的援引清单覆盖？
        if status != "verified" and act:
            act_n = _norm(act)
            hit_act = any(act_n in t for t in cited_statute_texts)
            hit_sec = (label is None) or any(
                act_n in t and re.search(rf"\b{re.escape(label.lower())}\b", t)
                for t in cited_statute_texts)
            if hit_act and hit_sec:
                status, note = "verified", "已在本轮检索判例的援引法规中核验"
        # ③ 成文法库权威回查（V1.1 India Code）→ ④ 判例语料援引兜底
        if status != "verified" and act:
            try:
                doc_hit, sec_hit = _statute_in_db(act, label)
                if doc_hit and (label is None or sec_hit):
                    status, note = "partial", "成文法库确认该法/该条存在，但本轮未调取原文"
                elif doc_hit:
                    status, note = "partial", "法规在成文法库；该条号未单独成条（as-enacted 版式限制），建议人工核对 India Code"
                else:
                    act_hit, sec_hit2 = _statute_in_corpus(act, label)
                    if act_hit and (label is None or sec_hit2):
                        status, note = "partial", "不在中央成文法库（州法/废止法可能），但库内判例援引过该法/该条"
                    elif act_hit:
                        status, note = "partial", "不在中央成文法库；法规名见于判例援引，该条号未见记录，建议人工核对"
                    else:
                        note = "成文法库与判例援引记录均未检索到该法规"
            except Exception as exc:
                logger.warning("statute verify query failed: %s", exc)
                note = "库内回查暂不可用"
        elif status != "verified" and not act:
            note = "条号缺少所属法规上下文，无法核验"
        items.append({"kind": "statute", "ref": r["display"], "status": status,
                      "note": (note + trans).strip()})

    # ---- 宪法条款（印度特有高频引用；V1.1 起以宪法库为权威源）----
    for r in refs.get("const_articles", []):
        label = r["article"]
        status, note = "unverified", ""
        # ① 本轮成文法取证覆盖（search_statutes 查过该 Article）
        if any((c.get("document_type") == "constitution" or "constitution" in _norm(c.get("title") or ""))
               and _base_label(c.get("section_label") or "") == label for c in stat_cites):
            status, note = "verified", "已在本轮检索的宪法条款原文中核验"
        # ② 本轮判例援引清单覆盖
        if status == "unverified":
            pat = re.compile(rf"\barticle\s+{re.escape(label.lower())}\b")
            if any(pat.search(t) for t in cited_statute_texts):
                status, note = "verified", "已在本轮检索判例的援引条款中核验"
        # ③ 宪法库权威回查 → ④ 判例援引兜底
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
                    row = db.query_one(
                        """SELECT 1 AS x FROM case_provision_refs
                           WHERE act_title ILIKE '%%Constitution%%' AND section_label = %s LIMIT 1""",
                        [label])
                    if row:
                        status, note = "partial", "库内判例援引过该宪法条款，但本轮未取证"
                    else:
                        note = "宪法库与判例援引记录均未检索到该条款"
            except Exception as exc:
                logger.warning("const verify query failed: %s", exc)
                note = "库内回查暂不可用"
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
                    row = db.query_one(
                        "SELECT 1 AS x FROM cases WHERE case_name ILIKE %s OR title ILIKE %s LIMIT 1",
                        [f"%{name}%", f"%{name}%"])
                if not row:
                    # trigram 相似度兜底：源数据案名存在导出/OCR 笔误，精确 ILIKE 会漏
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

    # ---- 条约条款：V1.0 无条约库，仅与本轮 citations（若未来接入）比对 ----
    treaty_chunks = [(c.get("chunk_text") or "") + " " + (c.get("title") or "")
                     for c in (citations or []) if c.get("type") == "treaty"]
    for r in refs.get("treaty_articles", []):
        label = r["article"]
        status, note = "unverified", "本库 V1.0 未收录条约文本，无法核验该条款"
        if treaty_chunks:
            pat = re.compile(r"\bArticle\s+" + re.escape(label) + r"\b", re.I)
            if any(pat.search(t) for t in treaty_chunks):
                status, note = "verified", "已在本轮检索的条约条款原文中核验"
            else:
                status, note = "partial", "本轮含相关条约材料，但未调取该条全文"
        items.append({"kind": "treaty", "ref": r["display"], "status": status, "note": note})

    # ---- 判例引证（LNIND/SCC/AIR/MLJ/INSC）：与 citations/库内 citation 字段比对 ----
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
                    """SELECT 1 AS x FROM cases
                       WHERE citation ILIKE %s OR parallel_citation ILIKE %s LIMIT 1""",
                    [f"%{cite}%", f"%{cite}%"])
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

    # "已声明"降级：模型在引用同句已如实标注"未经核验/库内未收录"的，单列 declared
    DECL_RE = re.compile(
        r"未经(?:本库|库内)?核验|库内未收录|未收录|India Code|"
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

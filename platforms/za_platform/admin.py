"""管理路由（仅 admin）：用户管理、邀请管理、案例关键词检索、案例详情、统计。"""
from __future__ import annotations

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from . import config, db, deps, models, security

logger = logging.getLogger("za_platform.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 案例元数据列表字段
_CASE_LIST_COLS = """
    c.document_id, c.case_name, c.title, c.court, c.court_level, c.decision_date,
    c.case_number, c.citation, c.document_type,
    cc.case_nature, cc.compliance_relevance, cc.compliance_scenarios
"""


# ---------------- 用户管理 ----------------
@router.get("/users")
def list_users(admin: dict = Depends(deps.require_admin)):
    rows = db.query_all(
        """
        SELECT u.id, u.username, u.role, u.display_name, u.is_active, u.must_change_password,
               u.created_at, u.last_login_at, cb.username AS created_by_username
        FROM app_users u
        LEFT JOIN app_users cb ON cb.id = u.created_by
        ORDER BY u.id
        """
    )
    return {"users": rows}


@router.post("/users")
def create_user(req: models.CreateUserReq, admin: dict = Depends(deps.require_admin)):
    if db.query_one("SELECT 1 FROM app_users WHERE username = %s", [req.username]):
        raise HTTPException(status_code=409, detail="用户名已存在")
    new = db.execute(
        """
        INSERT INTO app_users (username, password_hash, role, display_name, created_by, must_change_password)
        VALUES (%s, %s, %s, %s, %s, true)
        RETURNING id, username, role, display_name, is_active
        """,
        [req.username, security.hash_password(req.password), req.role, req.display_name, admin["id"]],
    )
    db.audit(admin["id"], "create_user", {"username": req.username, "role": req.role})
    return {"user": new}


@router.patch("/users/{user_id}")
def update_user(user_id: int, req: models.UpdateUserReq, admin: dict = Depends(deps.require_admin)):
    target = db.query_one("SELECT * FROM app_users WHERE id = %s", [user_id])
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 防止把当前登录的管理员自己禁用/降级，避免锁死后台
    if user_id == admin["id"] and (req.is_active is False or req.role == "researcher"):
        raise HTTPException(status_code=400, detail="不能禁用或降级当前登录的管理员账号")

    sets, params = [], []
    if req.is_active is not None:
        sets.append("is_active = %s")
        params.append(req.is_active)
    if req.role is not None:
        sets.append("role = %s")
        params.append(req.role)
    if req.reset_password is not None:
        sets.append("password_hash = %s")
        params.append(security.hash_password(req.reset_password))
        sets.append("must_change_password = true")
        # 管理员重置密码即时吊销目标用户所有旧 token
        sets.append("password_changed_at = now()")
    if not sets:
        raise HTTPException(status_code=400, detail="没有要更新的字段")
    params.append(user_id)
    updated = db.execute(
        f"UPDATE app_users SET {', '.join(sets)} WHERE id = %s "
        f"RETURNING id, username, role, display_name, is_active, must_change_password",
        params,
    )
    db.audit(admin["id"], "update_user", {"target": user_id})
    return {"user": updated}


# ---------------- 邀请管理 ----------------
@router.post("/invitations")
def create_invite(req: models.CreateInviteReq, admin: dict = Depends(deps.require_admin)):
    code = secrets.token_urlsafe(9)
    inv = db.execute(
        """
        INSERT INTO app_invitations (code, role, note, created_by, expires_at)
        VALUES (%s, %s, %s, %s, now() + (%s || ' days')::interval)
        RETURNING id, code, role, note, expires_at, created_at
        """,
        [code, req.role, req.note, admin["id"], str(req.expires_in_days)],
    )
    db.audit(admin["id"], "create_invite", {"code": code, "role": req.role})
    return {"invitation": inv}


@router.get("/invitations")
def list_invites(admin: dict = Depends(deps.require_admin)):
    rows = db.query_all(
        """
        SELECT i.id, i.code, i.role, i.note, i.expires_at, i.created_at, i.used_at,
               u.username AS used_by_username,
               (i.used_by IS NULL AND (i.expires_at IS NULL OR i.expires_at > now())) AS is_valid
        FROM app_invitations i
        LEFT JOIN app_users u ON u.id = i.used_by
        ORDER BY i.created_at DESC
        """
    )
    return {"invitations": rows}


@router.delete("/invitations/{invite_id}")
def delete_invite(invite_id: int, admin: dict = Depends(deps.require_admin)):
    db.execute("DELETE FROM app_invitations WHERE id = %s AND used_by IS NULL", [invite_id])
    db.audit(admin["id"], "delete_invite", {"invite_id": invite_id})
    return {"ok": True}


@router.post("/users/{user_id}/reset-link")
def create_reset_link(user_id: int, admin: dict = Depends(deps.require_admin)):
    """签发一次性密码重置链接（V1.3-B4.7）：1 小时有效，用户改密即作废。"""
    import time as _t

    import jwt as _jwt

    from . import config as _cfg
    target = db.query_one("SELECT id, username, is_active FROM app_users WHERE id = %s", [user_id])
    if not target or not target["is_active"]:
        raise HTTPException(status_code=404, detail="用户不存在或已停用")
    now = int(_t.time())
    token = _jwt.encode(
        {"purpose": "pwreset", "uid": user_id, "iat": now, "exp": now + 3600},
        _cfg.JWT_SECRET, algorithm=_cfg.JWT_ALG,
    )
    db.audit(admin["id"], "create_reset_link", {"target_user": target["username"]})
    return {"reset_url": f"/app?reset={token}", "expires_in": 3600,
            "username": target["username"]}


# ---------------- 查明质量看板（V1.3-B5.1：核验四档汇总 + 被引未收录清单）----------------

@router.get("/quality")
def quality_dashboard(admin: dict = Depends(deps.require_admin)):
    # 核验四档累计 + 近30天逐日未核验率
    totals = db.query_one(
        """
        SELECT
          coalesce(sum((verification->'summary'->>'verified')::int), 0)   AS verified,
          coalesce(sum((verification->'summary'->>'partial')::int), 0)    AS partial,
          coalesce(sum((verification->'summary'->>'declared')::int), 0)   AS declared,
          coalesce(sum((verification->'summary'->>'unverified')::int), 0) AS unverified,
          count(*) FILTER (WHERE verification IS NOT NULL)                AS messages_verified,
          count(*)                                                        AS messages_total
        FROM app_messages WHERE role = 'assistant'
        """
    )
    daily = db.query_all(
        """
        SELECT date_trunc('day', created_at)::date AS day,
               coalesce(sum((verification->'summary'->>'unverified')::int), 0) AS unverified,
               coalesce(sum((verification->'summary'->>'total')::int), 0)      AS total
        FROM app_messages
        WHERE role = 'assistant' AND verification IS NOT NULL
          AND created_at > now() - interval '30 days'
        GROUP BY 1 ORDER BY 1
        """
    )
    missing = db.query_all(
        "SELECT ref, kind, times, last_seen FROM cited_but_missing LIMIT 50"
    )
    return {"totals": totals, "daily": daily, "cited_but_missing": missing}


# ---------------- 系统告警（V1.3-B1.3：备份/看门狗/对账异常落库，此处供后台查看）----------------

@router.get("/alerts")
def list_alerts(limit: int = Query(default=50, ge=1, le=200),
                admin: dict = Depends(deps.require_admin)):
    rows = db.query_all(
        """
        SELECT id, detail->>'component' AS component, detail->>'message' AS message, created_at
        FROM app_audit_log WHERE action = 'system_alert'
        ORDER BY id DESC LIMIT %s
        """,
        [limit],
    )
    # 近 7 天按组件计数（后台看板摘要用）
    summary = db.query_all(
        """
        SELECT detail->>'component' AS component, count(*) AS n
        FROM app_audit_log
        WHERE action = 'system_alert' AND created_at > now() - interval '7 days'
        GROUP BY 1 ORDER BY 2 DESC
        """
    )
    return {"alerts": rows, "summary_7d": summary}


# ---------------- 案例关键词检索（元数据）----------------
@router.get("/cases/search")
def cases_search(
    admin: dict = Depends(deps.require_admin),
    q: str | None = None,
    case_nature: str | None = None,
    court_level: str | None = None,
    compliance_relevance: str | None = None,
    compliance_scenario: str | None = None,
    document_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    where = ["c.archive_country = 'South Africa'"]
    params: list = []
    if q:
        where.append("c.search_vector @@ websearch_to_tsquery('english', %s)")
        params.append(q)
    if case_nature:
        where.append("cc.case_nature = %s")
        params.append(case_nature)
    if court_level:
        where.append("c.court_level = %s")
        params.append(court_level)
    if compliance_relevance:
        where.append("cc.compliance_relevance = %s")
        params.append(compliance_relevance)
    if compliance_scenario:
        where.append("jsonb_exists(cc.compliance_scenarios, %s)")
        params.append(compliance_scenario)
    if document_type:
        where.append("c.document_type = %s")
        params.append(document_type)
    where_sql = " AND ".join(where)

    total = db.query_one(
        f"SELECT count(*) AS n FROM cases c JOIN case_classification cc USING (document_id) WHERE {where_sql}",
        params,
    )["n"]

    offset = (page - 1) * page_size
    if q:
        rows = db.query_all(
            f"""
            SELECT {_CASE_LIST_COLS},
                   ts_rank(c.search_vector, websearch_to_tsquery('english', %s)) AS rank
            FROM cases c JOIN case_classification cc USING (document_id)
            WHERE {where_sql}
            ORDER BY rank DESC, c.decision_date DESC NULLS LAST
            LIMIT %s OFFSET %s
            """,
            # SELECT 里的 ts_rank(%s) 在 SQL 文本中先于 WHERE 出现，故 q 放最前
            [q, *params, page_size, offset],
        )
    else:
        rows = db.query_all(
            f"""
            SELECT {_CASE_LIST_COLS}
            FROM cases c JOIN case_classification cc USING (document_id)
            WHERE {where_sql}
            ORDER BY c.decision_date DESC NULLS LAST, c.document_id DESC
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        )
    return {"total": total, "page": page, "page_size": page_size, "results": rows}


@router.get("/cases/{document_id}")
def case_detail(document_id: int, admin: dict = Depends(deps.require_admin)):
    row = db.query_one(
        "SELECT * FROM api_south_africa_case_records WHERE document_id = %s", [document_id]
    )
    if not row:
        raise HTTPException(status_code=404, detail="案例不存在")
    return {"case": row}


# ---------------- 成文法检索（代理 8040 混合检索）----------------
@router.get("/statutes/search")
def statutes_search(
    admin: dict = Depends(deps.require_admin),
    q: str = Query(..., min_length=1),
    act_year: int | None = None,
    title_contains: str | None = None,
    limit: int = Query(15, ge=1, le=50),
):
    payload = {"query": q, "limit": limit, "keyword_limit": 60, "vector_limit": 60, "rerank": True}
    if act_year is not None:
        payload["act_year"] = act_year
    if title_contains:
        payload["title_contains"] = title_contains
    try:
        with httpx.Client(timeout=config.SEARCH_TIMEOUT) as client:
            r = client.post(f"{config.STATUTE_SEARCH_API_URL}/search", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.exception("statutes search proxy failed: %s", exc)
        raise HTTPException(status_code=502, detail="成文法检索服务暂时不可用，请稍后重试")
    return data


# ---------------- 统计概览 ----------------
@router.get("/stats")
def stats(admin: dict = Depends(deps.require_admin)):
    sa = "WHERE archive_country = 'South Africa'"
    total = db.query_one(f"SELECT count(*) AS n FROM cases {sa}")["n"]
    dedup = db.query_one("SELECT count(*) AS n FROM api_south_africa_deduplicated_case_records")["n"]
    chunks = db.query_one("SELECT count(*) AS n FROM retrieval_chunks")["n"]
    by_court = db.query_all(
        f"SELECT coalesce(court_level, '(未分类)') AS k, count(*) AS n FROM cases {sa} GROUP BY court_level ORDER BY n DESC"
    )
    by_doctype = db.query_all(
        f"SELECT document_type AS k, count(*) AS n FROM cases {sa} GROUP BY document_type ORDER BY n DESC"
    )
    by_nature = db.query_all(
        "SELECT cc.case_nature AS k, count(*) AS n FROM cases c JOIN case_classification cc USING (document_id) "
        "WHERE c.archive_country = 'South Africa' GROUP BY cc.case_nature ORDER BY n DESC"
    )
    by_relevance = db.query_all(
        "SELECT cc.compliance_relevance AS k, count(*) AS n FROM cases c JOIN case_classification cc USING (document_id) "
        "WHERE c.archive_country = 'South Africa' GROUP BY cc.compliance_relevance ORDER BY n DESC"
    )
    by_scenario = db.query_all(
        "SELECT s AS k, count(*) AS n FROM cases c JOIN case_classification cc USING (document_id), "
        "jsonb_array_elements_text(cc.compliance_scenarios) AS s "
        "WHERE c.archive_country = 'South Africa' GROUP BY s ORDER BY n DESC LIMIT 30"
    )
    users = db.query_one("SELECT count(*) AS n FROM app_users")["n"]
    convs = db.query_one("SELECT count(*) AS n FROM app_conversations")["n"]
    # 成文法规模（独立服务 8040）
    statutes = {"documents": None, "sections": None}
    try:
        with httpx.Client(timeout=6) as client:
            sresp = client.get(f"{config.STATUTE_SEARCH_API_URL}/stats")
        if sresp.status_code == 200:
            sd = sresp.json()
            statutes = {"documents": sd.get("documents"), "sections": sd.get("sections")}
    except Exception:
        pass
    # 官方材料全分类规模（同 8040 服务的官方材料路由）：
    # regulations_notices / regulatory_guidance / statute_history / treaties
    regulations = {"documents": None, "chunks": None}
    officials: dict = {}
    try:
        with httpx.Client(timeout=6) as client:
            rresp = client.get(f"{config.STATUTE_SEARCH_API_URL}/official-materials/stats")
        if rresp.status_code == 200:
            officials = (rresp.json() or {}).get("by_category") or {}
            rd = officials.get("regulations_notices") or {}
            regulations = {"documents": rd.get("documents"), "chunks": rd.get("chunks")}
    except Exception:
        pass
    return {
        "total_cases": total,
        "dedup_cases": dedup,
        "retrieval_chunks": chunks,
        "users": users,
        "conversations": convs,
        "statutes": statutes,
        "regulations": regulations,
        "officials": officials,
        "by_court_level": by_court,
        "by_document_type": by_doctype,
        "by_case_nature": by_nature,
        "by_compliance_relevance": by_relevance,
        "by_compliance_scenario": by_scenario,
    }

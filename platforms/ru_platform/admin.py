"""俄罗斯平台管理路由：用户、邀请、成文法检索与运行统计。"""
from __future__ import annotations

import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from . import config, db, deps, models, security

logger = logging.getLogger("ru_platform.admin")

router = APIRouter(prefix="/api/admin", tags=["admin"])


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
    """签发一次性密码重置链接：1 小时有效，用户改密即作废。"""
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


# ---------------- 查明质量看板（核验四档汇总 + 被引未收录清单）----------------

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
    # 被引未收录清单（cited_but_missing 视图为可选运维产物，未建时返回空）
    try:
        missing = db.query_all(
            "SELECT ref, kind, times, last_seen FROM cited_but_missing LIMIT 50"
        )
    except Exception:
        missing = []
    return {"totals": totals, "daily": daily, "cited_but_missing": missing}


# ---------------- 系统告警（备份/看门狗/对账异常落库，此处供后台查看）----------------

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


# ---------------- 成文法检索（代理 9083 混合检索）----------------
@router.get("/statutes/search")
def statutes_search(
    admin: dict = Depends(deps.require_admin),
    q: str = Query(..., min_length=1),
    title_contains: str | None = None,
    act_number: str | None = None,
    status: str | None = None,
    limit: int = Query(15, ge=1, le=50),
):
    payload = {"query": q, "limit": limit, "keyword_limit": 60, "vector_limit": 60, "rerank": True}
    if title_contains:
        payload["title_contains"] = title_contains
    if act_number:
        payload["act_number"] = act_number
    if status in ("in_force", "repealed"):
        payload["status"] = status
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
    users = db.query_one("SELECT count(*) AS n FROM app_users")["n"]
    convs = db.query_one("SELECT count(*) AS n FROM app_conversations")["n"]
    # 成文法规模（独立服务 9083）
    statutes = {
        "documents": None,
        "official_documents": None,
        "sections": None,
        "embedded_sections": None,
    }
    try:
        with httpx.Client(timeout=6) as client:
            sresp = client.get(f"{config.STATUTE_SEARCH_API_URL}/stats")
        if sresp.status_code == 200:
            sd = sresp.json()
            statutes = {
                "documents": sd.get("documents"),
                "official_documents": sd.get("official_documents"),
                "sections": sd.get("sections"),
                "embedded_sections": sd.get("embedded_sections"),
            }
    except Exception:
        pass
    return {
        "users": users,
        "conversations": convs,
        "statutes": statutes,
    }

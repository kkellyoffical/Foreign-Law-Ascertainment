"""统一身份门户内部SSO接入：受认证的POST消费一次性票据。

链路：门户工作区签票（60s、独立密钥和法域aud）→ 内部POST提交 → 验签与
集中核销 → 匹配或开通哨兵账号 → 返回服务端代理使用的token。
浏览器GET票据消费已停用；原有本地账号登录入口保留。

安全约束：
- fail-closed：env 未配 PORTAL_SSO_SECRET 时本路由 404，平台行为与改造前完全一致；
- 对 app_users **只 INSERT 新行**，绝不 UPDATE 既有行（本地账号的密码/角色零触碰）；
- 自动开通的账号写入哨兵密码 sso$portal$-（不符合 scrypt 六段格式 →
  verify_password 恒 False → 无法本地密码登录，只能走门户）；
- 已存在的本地同名账号沿用其本地角色（门户身份不带来任何提权）。
"""
from __future__ import annotations

import logging

import httpx
import jwt
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from za_common.portal_boundary import require_portal_bridge
from fastapi.responses import HTMLResponse, JSONResponse

from . import config, db, security

logger = logging.getLogger("ru_platform.sso")

router = APIRouter(prefix="/api/auth", tags=["sso"])

# 哨兵密码：非 scrypt 六段格式，security.verify_password 必然返回 False
_SSO_SENTINEL = "sso$portal$-"

_LANDING = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>正在进入…</title></head>
<body style="font-family:sans-serif;display:flex;height:100vh;align-items:center;justify-content:center;color:#847c78">
<p>正在进入平台…</p>
<script>
localStorage.setItem("za_token", {token_json});
location.replace("/app");
</script></body></html>"""


def _enabled() -> bool:
    return len((config.PORTAL_SSO_SECRET or "").encode("utf-8")) >= 32


class SsoConsumeReq(BaseModel):
    ticket: str = Field(min_length=10, max_length=4096)


@router.get("/sso")
def legacy_sso_consume():
    if not _enabled():
        raise HTTPException(404, detail="Not Found")
    raise HTTPException(403, detail="请从统一门户进入该法域")


@router.post("/sso")
def sso_consume(
    req: SsoConsumeReq,
    portal_secret: str | None = Header(default=None, alias="X-Portal-SSO-Secret"),
):
    if not _enabled():
        raise HTTPException(404, detail="Not Found")
    require_portal_bridge(True, portal_secret, config.PORTAL_SSO_SECRET)
    return _consume_ticket(req.ticket, "json")


def _consume_ticket(ticket: str, format: str = "json"):
    # fail-closed：未配置密钥即视为无此功能（回滚 = 删 env 两行 + 重启）
    if not _enabled():
        raise HTTPException(status_code=404, detail="Not Found")
    if not ticket or len(ticket) > 4096:
        raise HTTPException(status_code=401, detail="票据无效")
    # ① 本地验签 + 强校验 aud/exp/purpose/iss
    try:
        payload = jwt.decode(ticket, config.PORTAL_SSO_SECRET, algorithms=["HS256"],
                             audience=config.PLATFORM_SSO_AUD)
    except Exception:
        raise HTTPException(status_code=401, detail="票据无效或已过期")
    if payload.get("purpose") != "sso" or payload.get("iss") != "portal":
        raise HTTPException(status_code=401, detail="票据无效")
    username = (payload.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=401, detail="票据无效")
    # ② 回拨门户集中核销（127.0.0.1；一次性原子核销 = 防重放的唯一事实源）
    try:
        with httpx.Client(timeout=5, trust_env=False) as client:
            r = client.post(f"{config.PORTAL_INTERNAL_URL}/api/sso/redeem",
                            json={"ticket": ticket})
    except Exception as exc:
        logger.warning("sso: 门户核销不可达: %s", exc)
        raise HTTPException(status_code=502, detail="单点登录服务暂不可用，请从门户重试或使用本地账号登录")
    if r.status_code != 200:
        raise HTTPException(status_code=401, detail="票据已使用或已过期")
    # ③ username 匹配 / 自动开通（只 INSERT，永不 UPDATE 既有行）
    user = db.query_one("SELECT * FROM app_users WHERE username = %s", [username])
    if user:
        # 存量本地账号（真实 scrypt 密码）对 SSO 不可见：老账号只走平台原登录入口，
        # 统一身份只服务门户开通的新账号（哨兵密码前缀），彻底杜绝经门户顶号老账号。
        if not (user["password_hash"] or "").startswith("sso$portal$"):
            db.audit(None, "sso_rejected_local_account", {"username": username})
            raise HTTPException(status_code=403,
                                detail="该用户名为平台本地账号，请从平台原登录入口登录")
        if not user["is_active"]:
            raise HTTPException(status_code=403, detail="账号已被本平台禁用")
    else:
        user = db.execute(
            """
            INSERT INTO app_users (username, password_hash, role, display_name)
            VALUES (%s, %s, 'researcher', %s)
            RETURNING *
            """,
            [username, _SSO_SENTINEL, payload.get("display_name")],
        )
        db.audit(user["id"], "sso_provision", {"username": username, "via": "portal"})
    # ④ 签发本平台 token（角色 = 本地既有角色，门户身份不提权）
    token = security.create_token(user["id"], user["username"], user["role"])
    db.execute("UPDATE app_users SET last_login_at = now() WHERE id = %s", [user["id"]])
    db.audit(user["id"], "sso_login", {"username": username, "jti": payload.get("jti")})
    # format=json：供统一门户服务端换取平台 token（法域代理层），不走浏览器落地页
    if format == "json":
        resp = JSONResponse({"ok": True, "token": token, "username": user["username"]})
        resp.headers["Cache-Control"] = "no-store"
        return resp
    import json
    resp = HTMLResponse(_LANDING.replace("{token_json}", json.dumps(token)))
    resp.headers["Cache-Control"] = "no-store"
    return resp

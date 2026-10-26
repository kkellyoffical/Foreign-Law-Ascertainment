"""FastAPI 依赖：JWT 鉴权与角色校验。"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException

from za_common.portal_boundary import require_portal_bridge

from . import config, db, security


def get_current_user(
    authorization: str | None = Header(default=None),
    portal_secret: str | None = Header(default=None, alias="X-Portal-SSO-Secret"),
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = security.decode_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="令牌无效或已过期")
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="令牌内容异常")
    user = db.query_one(
        """
        SELECT id, username, role, display_name, is_active, must_change_password,
               created_at, last_login_at, password_changed_at,
               left(password_hash, 11) = 'sso$portal$' AS portal_managed
        FROM app_users WHERE id = %s
        """,
        [user_id],
    )
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    require_portal_bridge(bool(user.get("portal_managed")), portal_secret, config.PORTAL_SSO_SECRET)
    # 服务端撤销：早于最近一次改密/重置签发的 token 一律失效（改密、管理员重置即时生效）。
    pca = user.get("password_changed_at")
    iat = payload.get("iat")
    if pca is not None and iat is not None:
        # iat 为签发时刻 epoch 秒；留 5s 余量避免同秒签发/改密的边界误杀。
        if int(iat) + 5 < int(pca.timestamp()):
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user

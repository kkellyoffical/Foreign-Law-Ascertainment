"""认证路由：登录、凭邀请码注册、当前用户、修改密码。"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from . import config, db, deps, models, security

logger = logging.getLogger("za_platform.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 抗时序侧信道：用户不存在时也执行一次等价 scrypt 校验，使响应耗时与存在用户一致，
# 杜绝据响应快慢枚举用户名。在模块加载时预计算一个绝不会匹配的 dummy hash。
_DUMMY_HASH = security.hash_password("za-login-timing-equalizer-dummy")

# 登录失败节流（单进程内存计数；进程重启即清零，可接受）。两层并存：
#  ① 按用户名：连续失败达阈值临时锁定，缓解针对单账号的爆破。
#  ② 按来源 IP：窗口内累计失败达阈值即封禁 3 小时，缓解换账号的同源爆破。
_LOGIN_FAIL_MAX = 8
_LOGIN_LOCK_SECONDS = 300
_login_fails: dict[str, list] = {}  # username -> [fail_count, window_start_ts, locked_until_ts]
_ip_fails: dict[str, list] = {}     # ip -> [fail_count, window_start_ts, banned_until_ts]
_login_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    """取真实客户端 IP。平台经 frp(L4) → 公网 nginx 暴露，nginx 用
    $proxy_add_x_forwarded_for 会把真实客户端 IP 追加到 XFF 最右段（frp 是四层不改 HTTP 头），
    故取 XFF 最后一段最可靠且不可被客户端伪造；无 XFF 时退回直连地址。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


def _username_locked(username: str) -> bool:
    now = time.time()
    with _login_lock:
        rec = _login_fails.get(username)
        return bool(rec and rec[2] > now)


def _login_record_fail(username: str) -> None:
    now = time.time()
    with _login_lock:
        rec = _login_fails.get(username)
        if not rec or now - rec[1] > _LOGIN_LOCK_SECONDS:
            rec = [0, now, 0.0]
        rec[0] += 1
        if rec[0] >= _LOGIN_FAIL_MAX:
            rec[2] = now + _LOGIN_LOCK_SECONDS
        _login_fails[username] = rec


def _login_record_success(username: str) -> None:
    with _login_lock:
        _login_fails.pop(username, None)


def _ip_check_banned(ip: str) -> None:
    now = time.time()
    with _login_lock:
        rec = _ip_fails.get(ip)
        if rec and rec[2] > now:
            mins = int((rec[2] - now) // 60) + 1
            raise HTTPException(status_code=429, detail=f"该来源登录失败过多，已临时封禁，请约 {mins} 分钟后重试")


def _ip_record_fail(ip: str) -> bool:
    """记录一次该 IP 的登录失败；若本次触发封禁返回 True。"""
    now = time.time()
    with _login_lock:
        # 顺手清理已过期条目，避免内存无界增长
        if len(_ip_fails) > 4096:
            for k in [k for k, v in _ip_fails.items() if v[2] < now and now - v[1] > config.LOGIN_IP_WINDOW_SECONDS]:
                _ip_fails.pop(k, None)
        rec = _ip_fails.get(ip)
        if not rec or now - rec[1] > config.LOGIN_IP_WINDOW_SECONDS:
            rec = [0, now, 0.0]  # [count, window_start, banned_until]
        rec[0] += 1
        newly_banned = False
        if rec[0] >= config.LOGIN_IP_FAIL_MAX and rec[2] <= now:
            rec[2] = now + config.LOGIN_IP_BAN_SECONDS
            newly_banned = True
        _ip_fails[ip] = rec
        return newly_banned


def _ip_record_success(ip: str) -> None:
    with _login_lock:
        _ip_fails.pop(ip, None)


def _maybe_ban_ip(ip: str) -> None:
    """记录一次该 IP 的登录失败；若本次刚好触发封禁则记日志+审计。"""
    if _ip_record_fail(ip):
        logger.warning("IP %s 登录失败达 %d 次，已封禁 %d 分钟", ip,
                       config.LOGIN_IP_FAIL_MAX, config.LOGIN_IP_BAN_SECONDS // 60)
        db.audit(None, "login_ip_banned", {"ip": ip, "ban_seconds": config.LOGIN_IP_BAN_SECONDS})


def user_public(u: dict) -> dict:
    return {
        "id": u["id"],
        "username": u["username"],
        "role": u["role"],
        "display_name": u.get("display_name"),
        "must_change_password": bool(u.get("must_change_password")),
    }


@router.post("/login")
def login(req: models.LoginReq, request: Request):
    ip = _client_ip(request)
    _ip_check_banned(ip)           # 该 IP 已封禁则直接 429（最优先）
    # 用户名级锁定：失败尝试同样计入 IP 计数（确保"同 IP 错 N 次必封"不被用户名锁定屏蔽）
    if _username_locked(req.username):
        _maybe_ban_ip(ip)
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后再试")
    user = db.query_one("SELECT * FROM app_users WHERE username = %s", [req.username])
    # 无论用户是否存在，都执行一次 scrypt 校验（不存在用户用 dummy hash），
    # 使两种情形耗时一致，消除用户枚举的计时侧信道。
    stored = user["password_hash"] if user else _DUMMY_HASH
    password_ok = security.verify_password(req.password, stored)
    if not user or not password_ok:
        _login_record_fail(req.username)
        _maybe_ban_ip(ip)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    _login_record_success(req.username)
    _ip_record_success(ip)
    db.execute("UPDATE app_users SET last_login_at = now() WHERE id = %s", [user["id"]])
    db.audit(user["id"], "login", {"username": user["username"], "ip": ip})
    token = security.create_token(user["id"], user["username"], user["role"])
    return {"token": token, "user": user_public(user)}


# V1.3-B4.6 注册节流：邀请码可被枚举试错，同 IP 无效码尝试 1 小时内达 10 次即封 1 小时
_invite_fails: dict[str, list] = {}


def _invite_throttle(ip: str, ok: bool) -> None:
    now = time.time()
    with _login_lock:
        rec = _invite_fails.get(ip)
        if rec and rec[2] > now:
            raise HTTPException(status_code=429, detail="尝试过于频繁，请稍后再试")
        if ok:
            return
        if not rec or now - rec[1] > 3600:
            rec = [0, now, 0.0]
        rec[0] += 1
        if rec[0] >= 10:
            rec[2] = now + 3600
            db.audit(None, "system_alert",
                     {"component": "register", "message": f"IP {ip} 邀请码试错超限已封 1 小时"})
        _invite_fails[ip] = rec


@router.post("/register")
def register(req: models.RegisterReq, request: Request):
    ip = _client_ip(request)
    _invite_throttle(ip, ok=True)  # 入口只查封禁态
    with db.pool.connection() as conn:
        with conn.transaction():
            inv = conn.execute(
                "SELECT * FROM app_invitations WHERE code = %s FOR UPDATE", [req.invite_code]
            ).fetchone()
            if not inv:
                _invite_throttle(ip, ok=False)
                raise HTTPException(status_code=400, detail="邀请码无效")
            if inv["used_by"] is not None:
                raise HTTPException(status_code=400, detail="邀请码已被使用")
            if inv["expires_at"] is not None and inv["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(status_code=400, detail="邀请码已过期")
            if conn.execute("SELECT 1 FROM app_users WHERE username = %s", [req.username]).fetchone():
                raise HTTPException(status_code=409, detail="用户名已存在")
            new_user = conn.execute(
                """
                INSERT INTO app_users (username, password_hash, role, display_name, created_by)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING *
                """,
                [
                    req.username,
                    security.hash_password(req.password),
                    inv["role"],
                    req.display_name,
                    inv["created_by"],
                ],
            ).fetchone()
            conn.execute(
                "UPDATE app_invitations SET used_by = %s, used_at = now() WHERE id = %s",
                [new_user["id"], inv["id"]],
            )
    db.audit(new_user["id"], "register", {"username": req.username})
    token = security.create_token(new_user["id"], new_user["username"], new_user["role"])
    return {"token": token, "user": user_public(new_user)}


@router.get("/me")
def me(user: dict = Depends(deps.get_current_user)):
    return {"user": user_public(user)}


@router.post("/reset-password")
def reset_password(req: models.ResetPasswordReq):
    """凭管理员签发的一次性重置令牌设置新密码（V1.3-B4.7 密码找回轻方案）。"""
    import jwt as _jwt
    try:
        payload = _jwt.decode(req.token, config.JWT_SECRET, algorithms=[config.JWT_ALG])
    except Exception:
        raise HTTPException(status_code=400, detail="重置链接无效或已过期")
    if payload.get("purpose") != "pwreset":
        raise HTTPException(status_code=400, detail="重置链接无效")
    uid = payload.get("uid")
    user = db.query_one("SELECT * FROM app_users WHERE id = %s AND is_active", [uid])
    if not user:
        raise HTTPException(status_code=400, detail="账号不存在或已停用")
    # 一次性：令牌签发后若密码已被改过（password_changed_at 晚于签发时刻），令牌作废
    iat = payload.get("iat", 0)
    pca = user.get("password_changed_at")
    if pca and pca.timestamp() > iat + 5:
        raise HTTPException(status_code=400, detail="重置链接已被使用")
    if len(req.new_password or "") < 8:
        raise HTTPException(status_code=400, detail="新密码至少 8 位")
    db.execute(
        "UPDATE app_users SET password_hash = %s, must_change_password = false, "
        "password_changed_at = now() WHERE id = %s",
        [security.hash_password(req.new_password), uid],
    )
    db.audit(uid, "reset_password", {"via": "admin_link"})
    token = security.create_token(user["id"], user["username"], user["role"])
    return {"ok": True, "token": token, "user": user_public(user)}


@router.post("/change-password")
def change_password(req: models.ChangePasswordReq, user: dict = Depends(deps.get_current_user)):
    full = db.query_one("SELECT password_hash FROM app_users WHERE id = %s", [user["id"]])
    if not full or not security.verify_password(req.old_password, full["password_hash"]):
        raise HTTPException(status_code=400, detail="原密码错误")
    db.execute(
        "UPDATE app_users SET password_hash = %s, must_change_password = false, "
        "password_changed_at = now() WHERE id = %s",
        [security.hash_password(req.new_password), user["id"]],
    )
    db.audit(user["id"], "change_password", {})
    # 改密会使本人其它端的旧 token 失效；给当前端签发新 token，避免把刚改密的本人也踢下线。
    token = security.create_token(user["id"], user["username"], user["role"])
    return {"ok": True, "token": token}

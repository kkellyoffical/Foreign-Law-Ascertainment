"""FastAPI 应用装配：路由 + 健康检查 + 静态前端。"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import admin, auth, chat, config, db, report, sso

logger = logging.getLogger("my_platform.app")

# 启动即 fail-closed：JWT 密钥缺失/过弱直接拒绝启动，避免空密钥伪造 token。
config.require_secure_jwt_secret()

# 关闭自动 API 文档：内部受限系统不对外暴露完整接口结构/参数（含 admin 路由），
# 降低被攻击者按图索骥的暴露面。需调试时可临时打开。
app = FastAPI(
    title=config.PLATFORM_NAME, version="0.1.0",
    docs_url=None, redoc_url=None, openapi_url=None,
)


@app.middleware("http")
async def security_headers(request, call_next):
    """纵深防御安全响应头：限制脚本来源、禁止被嵌套、禁止 MIME 嗅探。

    CSP 允许 'self' + 'unsafe-inline'（主页与 SPA 依赖少量内联脚本/样式），但
    object-src/base-uri/frame-ancestors 收紧，且禁止加载外部脚本——即便 DOMPurify
    被绕过，注入的 <script src=外部> 也无法执行。
    """
    resp = await call_next(request)
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


app.include_router(auth.router)
app.include_router(sso.router)
app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(report.router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/health")
def health():
    # 公开端点：只返回各组件 ok 布尔，不回传异常详情（内部 URL/依赖细节只写服务端日志）。
    out: dict = {"ok": True, "components": {}}
    # 数据库（致命）
    try:
        db.query_one("SELECT 1 AS x")
        out["components"]["database"] = {"ok": True}
    except Exception as exc:
        logger.warning("health: database check failed: %s", exc)
        out["ok"] = False
        out["components"]["database"] = {"ok": False}
    # 成文法/判例检索后端（影响问答，非致命）
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{config.STATUTE_SEARCH_API_URL}/health")
        out["components"]["search_api"] = {"ok": r.status_code == 200}
    except Exception as exc:
        logger.warning("health: search_api check failed: %s", exc)
        out["components"]["search_api"] = {"ok": False}
    # 印尼为成文法单底座（无判例库），不探测 case_search_api
    # 千问模型（影响问答，非致命）
    try:
        with httpx.Client(timeout=6) as client:
            r = client.get(
                f"{config.DASHSCOPE_BASE_URL}/models",
                headers={"Authorization": f"Bearer {config.DASHSCOPE_API_KEY}"},
            )
        out["components"]["model"] = {"ok": r.status_code == 200}
    except Exception as exc:
        logger.warning("health: model check failed: %s", exc)
        out["components"]["model"] = {"ok": False}
    return JSONResponse(out, status_code=200 if out["ok"] else 503)


@app.get("/api/config")
def public_config():
    return {"platform_name": config.PLATFORM_NAME, "platform_subtitle": config.PLATFORM_SUBTITLE}


app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")


@app.get("/")
def home():
    # 开屏主页（公开宣传页）：导航 → 登录 → 查明系统
    return FileResponse(os.path.join(STATIC_DIR, "home.html"))


@app.get("/app")
def index():
    # 查明系统 SPA（登录 / 对话 / 管理台）
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/register")
def register_page():
    # 邀请注册链接 /register?code=... 同样返回 SPA
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/robots.txt")
def robots():
    # 私有内部法查明工具：禁止任何搜索引擎 / 爬虫索引全站任何路径。
    body = (
        "# 印度尼西亚域外法查明系统 —— 内部受限系统，禁止任何自动化抓取与索引\n"
        "User-agent: *\n"
        "Disallow: /\n"
    )
    return Response(content=body, media_type="text/plain; charset=utf-8")


@app.get("/favicon.ico")
def favicon():
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        "<rect width='64' height='64' rx='14' fill='#b5232a'/>"
        "<text x='32' y='46' font-size='38' text-anchor='middle' fill='white'>⚖</text></svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")

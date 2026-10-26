"""初始化脚本（幂等）：

1. 确保 <外部配置路径> 存在（随机 JWT_SECRET + 千问 key + DSN 等）；
2. 按 schema.sql 建表；
3. 若无管理员，创建 admin 账号（随机强密码，标记 must_change_password），打印明文一次。

运行：DASHSCOPE_API_KEY=sk-... python3 -m za_platform.bootstrap
（在 <项目目录> 目录下，或确保该目录在 sys.path）
"""
from __future__ import annotations

import os
import pathlib
import secrets
import string

import psycopg
from psycopg.rows import dict_row

from . import config, security

ENV_PATH = config.ENV_FILE


def _gen_password(n: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    # 保证包含多种字符类别
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(n))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)):
            return pw


def ensure_env() -> dict:
    existing: dict[str, str] = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()

    if not existing.get("ZA_JWT_SECRET"):
        existing["ZA_JWT_SECRET"] = secrets.token_urlsafe(48)

    env_key = os.environ.get("DASHSCOPE_API_KEY")
    if env_key:
        existing["DASHSCOPE_API_KEY"] = env_key

    existing.setdefault("ZA_DB_DSN", config.DB_DSN)
    existing.setdefault("ZA_MODEL_NAME", config.MODEL_NAME)
    existing.setdefault("ZA_HOST", config.HOST)
    existing.setdefault("ZA_PORT", str(config.PORT))
    existing.setdefault("ZA_SEARCH_API_URL", config.SEARCH_API_URL)
    existing.setdefault(
        "DASHSCOPE_BASE_URL", config.DASHSCOPE_BASE_URL
    )

    os.makedirs(os.path.dirname(ENV_PATH), exist_ok=True)
    with open(ENV_PATH, "w", encoding="utf-8") as fh:
        fh.write("# 南非域外法查明系统 运行配置（自动生成，含敏感信息，权限 600）\n")
        for k, v in existing.items():
            fh.write(f"{k}={v}\n")
    os.chmod(ENV_PATH, 0o600)
    return existing


def init_db() -> None:
    raw = (pathlib.Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    # 先剔除整行注释（注释里可能含分号/引号，会干扰下面的简单拆分）
    sql = "\n".join(ln for ln in raw.splitlines() if not ln.strip().startswith("--"))
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    with psycopg.connect(config.DB_DSN, autocommit=True) as conn:
        for stmt in statements:
            conn.execute(stmt)


def ensure_admin() -> str | None:
    with psycopg.connect(config.DB_DSN, autocommit=True, row_factory=dict_row) as conn:
        row = conn.execute("SELECT id FROM app_users WHERE role = 'admin' LIMIT 1").fetchone()
        if row:
            return None
        password = _gen_password()
        conn.execute(
            """
            INSERT INTO app_users (username, password_hash, role, display_name, must_change_password)
            VALUES (%s, %s, 'admin', %s, true)
            """,
            ["admin", security.hash_password(password), "系统管理员"],
        )
        return password


def main() -> int:
    ensure_env()
    init_db()
    password = ensure_admin()
    line = "=" * 56
    print(line)
    print("南非域外法查明系统 —— 初始化完成")
    print(line)
    print(f"配置文件: {ENV_PATH} (权限 600)")
    if password:
        print("管理员账号已创建：")
        print("  用户名: admin")
        print(f"  初始密码: {password}")
        print("  ⚠ 请妥善保存；首次登录后请立即修改密码。")
    else:
        print("已存在管理员账号，未重复创建。")
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

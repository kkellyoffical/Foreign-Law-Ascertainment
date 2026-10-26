"""集中配置：从环境变量 / EnvironmentFile 读取，全部可覆盖。

systemd 通过 EnvironmentFile=<外部配置路径> 注入；直接运行时由
_load_env_file 作兜底加载。真实环境变量优先于文件值。
"""
from __future__ import annotations

import os
from za_common.branding import PLATFORM_NAME as PRODUCT_NAME

from za_common.model_policy import (
    QWEN_DEEP_MODEL,
    QWEN_STANDARD_MODEL,
    require_current_qwen_models,
)


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                # 已存在的真实环境变量优先
                os.environ.setdefault(key, value)
    except OSError:
        pass


ENV_FILE = os.environ.get("TH_PLATFORM_ENV_FILE", "")
_load_env_file(ENV_FILE)

# ---- 数据库 ----
DB_DSN = os.environ.get("TH_DB_DSN", "")
# 成文法库（example_database，含官方机构提供/托管及非官方英文译本）：核验引擎与
# get_statute_detail 直读条文全文。泰国为成文法单底座，无判例库（不设判例 DSN）。
# 连接配置由环境注入，prod env 用应用账号 TCP DSN 覆盖。
TH_STATUTES_DB_DSN = os.environ.get("TH_STATUTES_DB_DSN", "")
STATUTES_DB_DSN = TH_STATUTES_DB_DSN  # 通用层（db.py）引用的别名
DB_POOL_MIN = int(os.environ.get("TH_DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.environ.get("TH_DB_POOL_MAX", "12"))
# 成文法库为只读详情/核验用途，并发需求低，独立小池避免与主库争用。
STATUTES_DB_POOL_MIN = int(os.environ.get("TH_STATUTES_DB_POOL_MIN", "1"))
STATUTES_DB_POOL_MAX = int(os.environ.get("TH_STATUTES_DB_POOL_MAX", "6"))
DB_POOL_ACQUIRE_TIMEOUT = float(os.environ.get("TH_DB_POOL_ACQUIRE_TIMEOUT", "10"))

# ---- 鉴权 ----
JWT_SECRET = os.environ.get("TH_JWT_SECRET", "")
JWT_ALG = "HS256"
JWT_TTL_HOURS = int(os.environ.get("TH_JWT_TTL_HOURS", "72"))

# 登录失败封禁（按来源 IP）：窗口内累计失败达阈值即封禁一段时间，缓解在线爆破。
LOGIN_IP_FAIL_MAX = int(os.environ.get("TH_LOGIN_IP_FAIL_MAX", "10"))          # 触发封禁的失败次数
LOGIN_IP_BAN_SECONDS = int(os.environ.get("TH_LOGIN_IP_BAN_SECONDS", "10800"))  # 封禁时长，默认 3 小时
LOGIN_IP_WINDOW_SECONDS = int(os.environ.get("TH_LOGIN_IP_WINDOW_SECONDS", "3600"))  # 失败计数滚动窗口


def require_secure_jwt_secret() -> None:
    """启动时 fail-closed 校验：密钥缺失或过弱直接拒绝启动，杜绝空密钥伪造 token。

    由 Web 应用入口（app.py）调用；bootstrap.py 生成密钥的流程不调用本函数，
    因此首次初始化不受影响。可设 TH_ALLOW_INSECURE_JWT=1 临时绕过（仅限本地调试）。
    """
    if os.environ.get("TH_ALLOW_INSECURE_JWT") == "1":
        return
    secret = JWT_SECRET or ""
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError(
            "TH_JWT_SECRET 缺失或过弱（要求至少 32 字节）。"
            "请在 <外部配置路径> 配置高强度随机密钥后再启动平台。"
        )

# ---- 千问 / Agent ----
def _decrypt_secret(env_key: str) -> str:
    """密钥加密存储：env 不放明文，只放密文文件路径（{env_key}_ENC）。

    密文 = openssl aes-256-cbc -pbkdf2，口令文件默认 <外部配置路径>
    （0400、服务账号属主、不进 git/备份同步）。本机 systemd 249 无 systemd-creds，
    此为等价退级方案；解密失败时回退读明文 env（兼容老部署）。
    """
    enc_path = os.environ.get(f"{env_key}_ENC", "")
    if enc_path and os.path.exists(enc_path):
        import subprocess
        keyfile = os.environ.get("TH_CRED_KEYFILE", "")
        out = subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-d",
             "-in", enc_path, "-pass", f"file:{keyfile}"],
            capture_output=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.decode("utf-8").strip()
    return os.environ.get(env_key, "")


DASHSCOPE_API_KEY = _decrypt_secret("DASHSCOPE_API_KEY")
DASHSCOPE_BASE_URL = os.environ.get(
    "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
)
MODEL_NAME = os.environ.get("TH_MODEL_NAME", QWEN_DEEP_MODEL)  # 深度模式
FAST_MODEL_NAME = os.environ.get("TH_FAST_MODEL_NAME", QWEN_STANDARD_MODEL)  # 标准模式
require_current_qwen_models(
    standard_model=FAST_MODEL_NAME,
    deep_model=MODEL_NAME,
)
MODEL_TEMPERATURE = float(os.environ.get("TH_MODEL_TEMPERATURE", "0.3"))
AGENT_MAX_TURNS = int(os.environ.get("TH_AGENT_MAX_TURNS", "16"))  # 深度（轮次缓冲，超限走兜底）
AGENT_MAX_TURNS_FAST = int(os.environ.get("TH_AGENT_MAX_TURNS_FAST", "14"))  # 标准
# 资源/费用 DoS 防护：并发额度与单次问答墙钟上限
AGENT_MAX_CONCURRENCY = int(os.environ.get("TH_AGENT_MAX_CONCURRENCY", "6"))   # 全局同时进行的问答数
AGENT_MAX_PER_USER = int(os.environ.get("TH_AGENT_MAX_PER_USER", "2"))         # 单用户同时进行的问答数
AGENT_WALL_CLOCK_SECONDS = float(os.environ.get("TH_AGENT_WALL_CLOCK_SECONDS", "300"))  # 单次问答墙钟上限（chat.py 硬中断，无兜底）
AGENT_WALL_CLOCK_SOFT_SECONDS = float(os.environ.get("TH_AGENT_WALL_CLOCK_SOFT_SECONDS", "240"))  # 软墙钟：agent_core 内部提前熔断+引用兜底救场，留出余量在硬墙钟前完成

# ---- 检索后端（复用既有服务）----
# 泰国查明为成文法单底座：成文法混合检索（:9103，成文法英译文本，条文级）。无判例库。
STATUTE_SEARCH_API_URL = os.environ.get("TH_STATUTE_SEARCH_API_URL", "http://127.0.0.1:9103").rstrip("/")  # 成文法
SEARCH_TIMEOUT = float(os.environ.get("TH_SEARCH_TIMEOUT", "60"))

# ---- 平台展示 ----
PLATFORM_NAME = PRODUCT_NAME
PLATFORM_SUBTITLE = os.environ.get(
    "TH_PLATFORM_SUBTITLE", "Thailand Foreign-Law Research Platform"
)

# ---- 统一身份门户 SSO ----
# 未配置 PORTAL_SSO_SECRET 时 SSO 端点直接 404（fail-closed），平台行为与改造前一致。
PORTAL_SSO_SECRET = os.environ.get("PORTAL_SSO_SECRET", "")
PORTAL_INTERNAL_URL = os.environ.get("PORTAL_INTERNAL_URL", "http://127.0.0.1:10000").rstrip("/")
PLATFORM_SSO_AUD = os.environ.get("PLATFORM_SSO_AUD", "thailand")

# ---- 服务 ----
HOST = os.environ.get("TH_HOST", "127.0.0.1")
PORT = int(os.environ.get("TH_PORT", "9101"))

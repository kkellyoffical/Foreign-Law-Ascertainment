"""南非域外法查明系统 —— 后端应用包。

一个带邀请、带管理员、有鉴权的多用户法律查明平台：
- 中心 Agent（OpenAI Agents SDK + 千问模型）自主调用检索工具完成域外法查明；
- 复用既有南非混合检索服务（127.0.0.1:8020）与 PostgreSQL「example_database」。
"""

__version__ = "0.1.0"

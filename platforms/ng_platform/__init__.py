"""尼日利亚域外法查明系统 —— 后端应用包。

一个带邀请、带管理员、有鉴权的多用户法律查明平台：
- 中心 Agent（OpenAI Agents SDK + 千问模型）自主调用检索工具完成域外法查明；
- 成文法 + 判例双底座：官方 Laws of Nigeria 成文法混合检索（127.0.0.1:9073）
  + 尼日利亚判例混合检索（127.0.0.1:9072，最高法院/上诉法院/高等法院 1,859 件），
  以及 PostgreSQL「example_database」「example_database」。
"""

__version__ = "0.1.0"

"""案例原文路由：登录用户可按 case_id 获取判决全文（尼日利亚判例库 cases 表）。

用于在对话引用详情中直接展示原文，替代外部来源跳转。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from . import db, deps

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("/{case_id}/fulltext")
def case_fulltext(case_id: int, user: dict = Depends(deps.get_current_user)):
    row = db.query_one_cases(
        """
        SELECT case_id, case_name, citation, court, court_level, judges,
               decision_date, case_number, language, full_text
        FROM cases WHERE case_id = %s
        """,
        [case_id],
    )
    if not row:
        raise HTTPException(status_code=404, detail="案例不存在")
    return {"case": row}

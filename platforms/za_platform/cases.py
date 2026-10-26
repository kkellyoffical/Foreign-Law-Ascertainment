"""案例原文路由：登录用户可按 document_id 获取清洗出的判决全文。

用于在对话引用与管理后台详情中直接展示原文，替代外部来源跳转。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from . import db, deps

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("/{document_id}/fulltext")
def case_fulltext(document_id: int, user: dict = Depends(deps.get_current_user)):
    row = db.query_one(
        """
        SELECT document_id, case_name, title, court, court_level, decision_date,
               case_number, citation, full_text
        FROM cases WHERE document_id = %s
        """,
        [document_id],
    )
    if not row:
        raise HTTPException(status_code=404, detail="案例不存在")
    return {"case": row}

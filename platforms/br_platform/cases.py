"""案例原文路由（stub）：巴西 V1.0 为成文法查明底座，判例库（STF/STJ）建设中。

保留路由结构与前端契约（/api/cases/{document_id}/fulltext），判例库上线后
恢复为按 document_id 返回清洗全文的实现（参照印度版 cases.py）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from . import deps

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.get("/{document_id}/fulltext")
def case_fulltext(document_id: int, user: dict = Depends(deps.get_current_user)):
    raise HTTPException(status_code=404, detail="巴西判例库建设中")

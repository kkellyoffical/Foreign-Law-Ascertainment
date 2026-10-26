"""请求体 pydantic 模型。响应统一用 dict，便于灵活组织。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["admin", "researcher"]


class LoginReq(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RegisterReq(BaseModel):
    invite_code: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=64)


class ChangePasswordReq(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ResetPasswordReq(BaseModel):
    token: str = Field(min_length=10, max_length=2048)
    new_password: str = Field(min_length=8, max_length=128)


class CreateUserReq(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: Role = "researcher"
    display_name: Optional[str] = Field(default=None, max_length=64)


class UpdateUserReq(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[Role] = None
    reset_password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class CreateInviteReq(BaseModel):
    role: Role = "researcher"
    note: Optional[str] = Field(default=None, max_length=200)
    expires_in_days: int = Field(default=7, ge=1, le=365)


class ConversationCreateReq(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)


class ConversationPatchReq(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChatReq(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    mode: Literal["fast", "deep"] = "fast"
    answer_language: Literal["zh-CN", "en"] | None = None

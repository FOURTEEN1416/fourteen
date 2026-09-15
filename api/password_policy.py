"""密码策略 —— 全站唯一真源（2026-09-15 建立）。

背景：此前密码规则散落四处且互相矛盾 —— `/auth/register` 要 ≥8 且含字母数字，
`/auth/register-invite` 只要 ≥6，`/admin/users` 建/改用户只要 ≥6，前端写 6。
后果：登录页注册时后端 422 导致界面白屏；邀请码路径可绕过强度要求。

约定：**任何设置或修改密码的入口都必须引用本模块**，禁止再自行写死长度或强度。
"""
import re
from typing import Annotated

from fastapi import HTTPException
from pydantic import StringConstraints

PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

#: 请求体中的密码字段统一用这个类型（长度由 Pydantic 保证，违反返回 422）
PasswordStr = Annotated[
    str,
    StringConstraints(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH),
]


def validate_password_strength(password: str) -> str | None:
    """强度校验：长度 + 必须同时包含字母与数字。通过返回 None，否则返回中文文案。"""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"密码长度至少{PASSWORD_MIN_LENGTH}位"
    if not re.search(r"[A-Za-z]", password):
        return "密码必须包含字母"
    if not re.search(r"\d", password):
        return "密码必须包含数字"
    return None


def ensure_password_strength(password: str) -> None:
    """不满足强度策略则抛 HTTPException(422，中文文案)，供路由直接调用。"""
    error = validate_password_strength(password)
    if error:
        raise HTTPException(status_code=422, detail=error)

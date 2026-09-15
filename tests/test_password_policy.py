"""密码策略唯一真源单测（api/password_policy.py）。

背景（2026-09-15）：密码规则曾散落四处且互相矛盾 —— /auth/register 要 ≥8 且含字母数字，
/auth/register-invite 与 /admin/users 只要 ≥6，前端写 6。后果是注册页后端 422
导致界面白屏，且邀请码路径可绕过强度要求。现所有入口统一引用本模块，测试锁死该策略。
"""
from __future__ import annotations

import sys

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

sys.path.insert(0, ".")

from api.password_policy import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PasswordStr,
    ensure_password_strength,
    validate_password_strength,
)


def test_policy_constants():
    assert PASSWORD_MIN_LENGTH == 8
    assert PASSWORD_MAX_LENGTH == 128


@pytest.mark.parametrize("pwd", ["password123", "abc12345", "A1" * 10])
def test_strong_passwords_pass(pwd):
    assert validate_password_strength(pwd) is None


@pytest.mark.parametrize(
    ("pwd", "expected"),
    [
        ("pass123", "密码长度至少8位"),  # 7 位（正是注册页白屏的触发值）
        ("pass1234", None),  # 8 位且含字母数字 —— 长度边界
        ("12345678", "密码必须包含字母"),  # 纯数字
        ("abcdefgh", "密码必须包含数字"),  # 纯字母
    ],
)
def test_strength_rules(pwd, expected):
    assert validate_password_strength(pwd) == expected


def test_ensure_raises_422_with_chinese_detail():
    with pytest.raises(HTTPException) as exc_info:
        ensure_password_strength("12345678")
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "密码必须包含字母"


class _Model(BaseModel):
    password: PasswordStr


class _OptionalModel(BaseModel):
    password: PasswordStr | None = None


def test_password_str_enforces_length():
    with pytest.raises(ValidationError):
        _Model(password="pass123")  # 7 位
    assert _Model(password="pass1234").password == "pass1234"
    with pytest.raises(ValidationError):
        _Model(password="x" * (PASSWORD_MAX_LENGTH + 1))


def test_password_str_optional_accepts_none():
    assert _OptionalModel().password is None
    assert _OptionalModel(password="pass1234").password == "pass1234"
    with pytest.raises(ValidationError):
        _OptionalModel(password="pass123")

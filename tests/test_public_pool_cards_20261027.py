"""公共池卡可见性回归（2026-10-27）。

背景：41 张成品卡的 `user_id` 原为占位串 `default`，旧 `card_access_allowed`
要求 `bool(owner)`，导致**除管理员外谁都看不到**——新注册用户进控制台只有自己
那 1 张卡，与「四十多张人设卡随便换」的产品承诺不符。

改动后语义（本测试锁定）：
- 无主卡（owner 为空 / default / system）= 公共池：任意登录用户**可读、可激活**
- 无主卡对普通用户**不可改、不可删**（否则任何人能污染或清空公共池）
- 私有卡仍然只对本人与管理员可见
"""
from __future__ import annotations

import pytest

from api.auth_jwt import AuthPrincipal
from api.routers.character_routes import card_access_allowed, card_write_allowed


def _principal(user_id: int, role: str = "user") -> AuthPrincipal:
    return AuthPrincipal(user_id=user_id, role=role, token_version=0, user=None)  # type: ignore[arg-type]


PUBLIC_CARD = {"id": "abc", "name": "公共卡", "user_id": ""}
OTHER_PRIVATE = {"id": "def", "name": "别人的卡", "user_id": "2"}
MINE = {"id": "ghi", "name": "我的卡", "user_id": "9"}


@pytest.mark.parametrize("owner_field", ["", None])
def test_unowned_card_readable_by_user(owner_field):
    """无主卡对普通用户可读（否则公共池对谁都不可见，等于没有）。"""
    card = {"id": "abc", "name": "公共卡", "user_id": owner_field}
    assert card_access_allowed(card, _principal(9)) is True


def test_unowned_card_not_writable_by_user():
    """无主卡对普通用户不可写：防止任意用户改写公共池成品卡。"""
    assert card_write_allowed(PUBLIC_CARD, _principal(9)) is False


def test_own_private_card_read_write():
    assert card_access_allowed(MINE, _principal(9)) is True
    assert card_write_allowed(MINE, _principal(9)) is True


def test_others_private_card_denied():
    assert card_access_allowed(OTHER_PRIVATE, _principal(9)) is False
    assert card_write_allowed(OTHER_PRIVATE, _principal(9)) is False


@pytest.mark.parametrize("marker", ["default", "system"])
def test_legacy_unowned_markers_are_public(marker):
    """`user_id` 落在 `_UNOWNED_MARKERS`（""/default/system）即视为无主卡。

    生产上 41 张成品卡的 `user_id` 正是占位串 `default`，因此本次改造**不需要
    改动服务器上的卡文件**——判定层一改，它们自动进入公共池。
    """
    card = {"id": "jkl", "name": "历史占位卡", "user_id": marker}
    assert card_access_allowed(card, _principal(9)) is True
    assert card_write_allowed(card, _principal(9)) is False


def test_admin_can_read_and_write_all():
    admin = _principal(1, role="admin")
    for card in (PUBLIC_CARD, OTHER_PRIVATE, MINE):
        assert card_access_allowed(card, admin) is True
        assert card_write_allowed(card, admin) is True


def test_machine_face_unrestricted():
    """principal 为 None = 部署脚本 / 无登录控制台，维持既有放行语义。"""
    assert card_access_allowed(OTHER_PRIVATE, None) is True
    assert card_write_allowed(OTHER_PRIVATE, None) is True

"""CharacterId 单元测试"""

import pytest

from shisi.core.models.character_id import CharacterId


def test_generate():
    cid = CharacterId.generate()
    assert len(cid.value) == 8


def test_empty_raises():
    with pytest.raises(ValueError):
        CharacterId("")


def test_from_string():
    cid = CharacterId.from_string("abc123")
    assert cid.value == "abc123"


def test_frozen():
    cid = CharacterId("test")
    with pytest.raises(AttributeError):
        cid.value = "other"

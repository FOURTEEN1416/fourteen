"""单元测试: Reply/ReplyType"""
import sys
sys.path.insert(0, ".")

from cowagent_src.bridge.reply import Reply, ReplyType


def test_reply_type_values():
    assert ReplyType.TEXT.value == 1
    assert ReplyType.VOICE.value == 2
    assert ReplyType.IMAGE.value == 3
    assert ReplyType.STICKER.value == 14


def test_reply_type_sticker_exists():
    assert hasattr(ReplyType, "STICKER")
    assert ReplyType.STICKER.name == "STICKER"


def test_reply_type_str():
    assert str(ReplyType.TEXT) == "TEXT"
    assert str(ReplyType.STICKER) == "STICKER"


def test_reply_default():
    r = Reply()
    assert r.type is None
    assert r.content is None
    assert r.sticker is None


def test_reply_with_sticker():
    r = Reply(ReplyType.STICKER, "/path/to/sticker.gif")
    assert r.type == ReplyType.STICKER
    assert r.content == "/path/to/sticker.gif"
    assert r.sticker is None


def test_reply_sticker_attribute():
    r = Reply(ReplyType.TEXT, "hello")
    r.sticker = {"path": "/path/to/sticker.gif", "sticker_id": "s1"}
    assert r.sticker["path"] == "/path/to/sticker.gif"


def test_reply_str():
    r = Reply(ReplyType.TEXT, "test")
    s = str(r)
    assert "TEXT" in s
    assert "test" in s


def test_reply_type_no_duplicate_values():
    values = [m.value for m in ReplyType]
    assert len(values) == len(set(values)), "ReplyType有重复枚举值"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All reply tests passed!")

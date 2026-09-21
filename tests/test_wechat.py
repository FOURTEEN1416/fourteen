"""微信适配层单元测试 — 表情适配 + 主动消息增强。

2026-09-17：指令系统（command_handler/command_parser）测试随模块删除而移除（生产链路未接线，用户裁决清洗）。"""

import sys

sys.path.insert(0, ".")

import pytest

from shisi.config import reset_config
from shisi.migrations import run_migrations
from shisi.wechat.sticker_adapter import WeChatStickerAdapter


@pytest.fixture(autouse=True)
def reset_config_each():
    reset_config()


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return db


class TestWeChatStickerAdapter:
    def test_no_sticker_manager(self):
        adapter = WeChatStickerAdapter()
        assert adapter.get_sticker_for_reply("开心") is None

    def test_format_sticker_message(self):
        adapter = WeChatStickerAdapter()
        msg = adapter.format_sticker_message("你好", None)
        assert msg["type"] == "text"
        assert msg["content"] == "你好"

    def test_format_with_sticker(self):
        adapter = WeChatStickerAdapter()
        msg = adapter.format_sticker_message("你好", {"sticker_id": "s1", "file_path": "/s1.png", "category": "可爱"})
        assert "sticker" in msg


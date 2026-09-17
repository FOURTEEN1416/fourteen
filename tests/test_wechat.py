"""微信适配层单元测试 — 表情适配 + 主动消息增强。

2026-09-17：指令系统（command_handler/command_parser）测试随模块删除而移除（生产链路未接线，用户裁决清洗）。"""

import sys

sys.path.insert(0, ".")

import pytest

from shisi.affinity.enhancer import AffinityEnhancer
from shisi.character.manager import CharacterManager
from shisi.character.models import CharaCardV2, CharacterData
from shisi.character.store import CharacterStore
from shisi.config import reset_config
from shisi.emotion_stage.stage_engine import EmotionStageEngine
from shisi.migrations import run_migrations
from shisi.wechat.proactive_messenger import WeChatProactiveMessenger
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


class TestWeChatProactiveMessenger:
    def test_enhance_with_summary(self):
        affinity = AffinityEnhancer()
        affinity.update("c1", 60, "chat")
        stage = EmotionStageEngine()
        stage.evaluate("c1", 60)
        pro = WeChatProactiveMessenger(affinity, stage)
        result = pro.enhance_proactive_message("早安~", "c1", "开心")
        assert result["summary"] is not None
        assert "开心" in result["summary"]
        assert "60" in result["summary"]

    def test_morning_greeting_high_affinity(self):
        affinity = AffinityEnhancer()
        affinity.update("c1", 80, "chat")
        pro = WeChatProactiveMessenger(affinity)
        msg = pro.format_morning_greeting("真昼", "c1")
        assert "想你" in msg

    def test_morning_greeting_mid_affinity(self):
        affinity = AffinityEnhancer()
        affinity.update("c1", 55, "chat")
        pro = WeChatProactiveMessenger(affinity)
        msg = pro.format_morning_greeting("真昼", "c1")
        assert "开心" in msg

    def test_night_greeting(self):
        affinity = AffinityEnhancer()
        affinity.update("c1", 80, "chat")
        pro = WeChatProactiveMessenger(affinity)
        msg = pro.format_night_greeting("真昼", "c1")
        assert "晚安" in msg

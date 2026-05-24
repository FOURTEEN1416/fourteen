"""微信指令系统 单元测试。"""

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
from shisi.wechat.command_handler import WeChatCommandHandler
from shisi.wechat.command_parser import WeChatCommandParser
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


@pytest.fixture
def handler(tmp_db):
    char_mgr = CharacterManager(store=CharacterStore(tmp_db))
    char_mgr.initialize()
    affinity = AffinityEnhancer()
    stage = EmotionStageEngine()
    return WeChatCommandHandler(
        character_manager=char_mgr,
        affinity_enhancer=affinity,
        stage_engine=stage,
    ), char_mgr, affinity, stage


class TestWeChatCommandParser:
    def test_switch_character(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("切换角色：椎名真昼")
        assert cmd is not None
        assert cmd.action == "switch_character"
        assert cmd.params["character_name"] == "椎名真昼"

    def test_switch_character_colon_variant(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("切换角色:真昼")
        assert cmd is not None
        assert cmd.action == "switch_character"

    def test_affinity(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("好感度")
        assert cmd.action == "affinity"

    def test_affinity_short(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("好感")
        assert cmd.action == "affinity"

    def test_emotion_status(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("情感状态")
        assert cmd.action == "emotion_status"

    def test_vital_signs(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("生理指标")
        assert cmd.action == "vital_signs"

    def test_send_sticker(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("发表情")
        assert cmd.action == "send_sticker"

    def test_favorite(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("收藏")
        assert cmd.action == "favorite"

    def test_forward(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("转发给：十四")
        assert cmd.action == "forward"
        assert cmd.params["target_character"] == "十四"

    def test_normal_message_returns_none(self):
        parser = WeChatCommandParser()
        assert parser.parse("你好呀") is None
        assert parser.parse("今天天气怎么样") is None
        assert parser.parse("") is None

    def test_command_preserves_raw(self):
        parser = WeChatCommandParser()
        cmd = parser.parse("好感度")
        assert cmd.raw == "好感度"


class TestWeChatCommandHandler:
    def test_affinity_command(self, handler):
        h, _, affinity, _ = handler
        affinity.update("test_char", 72, "chat")
        ok, msg = h.handle("好感度", "test_char")
        assert ok is True
        assert "72" in msg

    def test_emotion_status_command(self, handler):
        h, _, _, stage = handler
        stage.evaluate("test_char", 60)
        ok, msg = h.handle("情感状态", "test_char")
        assert ok is True
        assert "亲密" in msg

    def test_switch_character_command(self, handler):
        h, char_mgr, _, _ = handler
        char_mgr.store.save_character(
            CharaCardV2(data=CharacterData(name="椎名真昼", description="完美"))
        )
        ok, msg = h.handle("切换角色：椎名真昼")
        assert ok is True
        assert "椎名真昼" in msg

    def test_favorite_command(self, handler):
        h = handler[0]
        ok, msg = h.handle("收藏")
        assert ok is True

    def test_forward_command(self, handler):
        h = handler[0]
        ok, msg = h.handle("转发给：十四")
        assert ok is True
        assert "十四" in msg

    def test_normal_message_not_handled(self, handler):
        h = handler[0]
        ok, msg = h.handle("普通聊天消息")
        assert ok is False

    def test_vital_signs_placeholder(self, handler):
        h = handler[0]
        ok, msg = h.handle("生理指标")
        assert ok is True


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

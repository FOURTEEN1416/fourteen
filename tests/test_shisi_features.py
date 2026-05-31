"""shisi 特性模块测试 — StickerManager + VitalSignsEngine + EmotionStageEngine + FavoriteManager"""

from pathlib import Path

import pytest

from shisi.emotion_stage.stage_engine import EmotionStageEngine
from shisi.memory.favorite_manager import FavoriteManager
from shisi.memory.forward_manager import ForwardManager
from shisi.migrations import run_migrations
from shisi.sticker.sticker_manager import StickerManager
from shisi.vital_signs.vital_engine import VitalSignsEngine


# ═══════════════════════════════════════════════════════
# StickerManager 测试
# ═══════════════════════════════════════════════════════


@pytest.fixture
def sticker_mgr(tmp_db, tmp_path):
    run_migrations(tmp_db)
    return StickerManager(
        db_path=Path(tmp_db),
        data_dir=Path(tmp_path) / "stickers",
    )


class TestStickerManager:
    """表情包管理 CRUD + 绑定角色"""

    def test_list_empty(self, sticker_mgr):
        stickers = sticker_mgr.list_by_category()
        assert stickers == []

    def test_list_by_category_empty(self, sticker_mgr):
        assert sticker_mgr.list_by_category("happy") == []

    def test_bind_to_character(self, sticker_mgr):
        count = sticker_mgr.bind_to_character("char_1", ["sti_a", "sti_b"])
        assert count == 2

    def test_bind_to_character_duplicate(self, sticker_mgr):
        """重复绑定仍计入计数（INSERT OR IGNORE 返回已尝试数）"""
        sticker_mgr.bind_to_character("char_1", ["sti_a"])
        count = sticker_mgr.bind_to_character("char_1", ["sti_a", "sti_b"])
        assert count == 2  # 真实实现计数所有尝试

    def test_bind_multiple_characters(self, sticker_mgr):
        sticker_mgr.bind_to_character("char_1", ["sti_a"])
        sticker_mgr.bind_to_character("char_2", ["sti_a"])
        # verify in DB
        import sqlite3
        conn = sqlite3.connect(str(sticker_mgr._db_path))
        try:
            rows = conn.execute(
                "SELECT character_id, sticker_id FROM character_stickers ORDER BY rowid"
            ).fetchall()
            assert len(rows) == 2
        finally:
            conn.close()

    def test_recommend_with_no_stickers(self, sticker_mgr):
        result = sticker_mgr.recommend(["happy"])
        assert result == []


# ═══════════════════════════════════════════════════════
# VitalSignsEngine 测试
# ═══════════════════════════════════════════════════════


class TestVitalSignsEngine:
    """生命体征引擎 — 纯内存，无需数据库"""

    @pytest.fixture
    def vital(self):
        return VitalSignsEngine()

    def test_get_default_state(self, vital):
        state = vital.get_current("char_1")
        assert state.heart_rate == 72.0
        assert state.temperature == 36.5
        assert state.breath_rate == 16.0
        assert state.last_emotion == "平静"

    def test_update_emotion_changes_vitals(self, vital):
        vital.update_on_emotion("char_1", "开心")
        state = vital.get_current("char_1")
        assert state.heart_rate > 72.0  # 开心时心率升高

    def test_update_emotion_calm(self, vital):
        vital.update_on_emotion("char_1", "平静")
        state = vital.get_current("char_1")
        assert state.heart_rate == 72.0  # 平静时回到默认

    def test_update_emotion_angry(self, vital):
        vital.update_on_emotion("char_1", "生气")
        state = vital.get_current("char_1")
        assert state.heart_rate > 72.0
        assert state.temperature > 36.5  # 生气时体温略升

    def test_multiple_characters_independent(self, vital):
        vital.update_on_emotion("char_1", "开心")
        vital.update_on_emotion("char_2", "悲伤")
        s1 = vital.get_current("char_1")
        s2 = vital.get_current("char_2")
        assert s1.heart_rate != s2.heart_rate

    def test_format_wechat_message(self, vital):
        vital.update_on_emotion("char_1", "开心")
        msg = vital.format_wechat_message("char_1")
        assert "bpm" in msg
        assert "℃" in msg

    def test_tick_returns_default_for_unknown(self, vital):
        state = vital.tick("unknown")
        assert state.heart_rate == 72.0


# ═══════════════════════════════════════════════════════
# EmotionStageEngine 测试
# ═══════════════════════════════════════════════════════


class TestEmotionStageEngine:
    """情感阶段引擎 — 纯内存，无需数据库"""

    @pytest.fixture
    def stage(self):
        return EmotionStageEngine()

    def test_initial_stage(self, stage):
        info = stage.get_progress("char_1")
        assert info["current_stage"] == "陌生"
        assert info["stage_index"] == 0
        assert info["affinity"] == 0.0

    def test_evaluate_updates_stage(self, stage):
        result = stage.evaluate("char_1", affinity=30.0)
        assert result is not None
        assert result.name != ""

    def test_evaluate_high_affinity(self, stage):
        stage.evaluate("char_1", affinity=80.0)
        info = stage.get_progress("char_1")
        assert info["stage_index"] > 0  # 高好感度 → 更高阶段

    def test_evaluate_affinity_persisted(self, stage):
        stage.evaluate("char_1", affinity=50.0)
        info = stage.get_progress("char_1")
        assert info["affinity"] == 50.0

    def test_multiple_characters(self, stage):
        stage.evaluate("char_a", affinity=20.0)
        stage.evaluate("char_b", affinity=80.0)
        a = stage.get_progress("char_a")
        b = stage.get_progress("char_b")
        assert a["stage_index"] < b["stage_index"]


# ═══════════════════════════════════════════════════════
# FavoriteManager + ForwardManager 测试
# ═══════════════════════════════════════════════════════


class TestFavoriteManager:
    """收藏管理器"""

    @pytest.fixture
    def fav(self, tmp_db):
        run_migrations(tmp_db)
        return FavoriteManager(db_path=Path(tmp_db))

    def test_list_empty(self, fav):
        assert fav.list_favorites("char_1") == []

    def test_favorite_and_list(self, fav):
        fav.favorite("char_1", "mem_001")
        favorites = fav.list_favorites("char_1")
        assert len(favorites) == 1
        assert favorites[0]["memory_id"] == "mem_001"

    def test_favorite_duplicate(self, fav):
        fav.favorite("char_1", "mem_001")
        fav.favorite("char_1", "mem_001")  # should not error
        assert len(fav.list_favorites("char_1")) == 1

    def test_unfavorite(self, fav):
        fav.favorite("char_1", "mem_001")
        fav.unfavorite("char_1", "mem_001")
        assert fav.list_favorites("char_1") == []

    def test_is_favorite(self, fav):
        assert fav.is_favorite("char_1", "mem_001") is False
        fav.favorite("char_1", "mem_001")
        assert fav.is_favorite("char_1", "mem_001") is True

    def test_favorites_scoped_per_character(self, fav):
        fav.favorite("char_a", "mem_001")
        fav.favorite("char_b", "mem_001")
        assert len(fav.list_favorites("char_a")) == 1
        assert len(fav.list_favorites("char_b")) == 1


class TestForwardManager:
    """转发管理器 — 纯内存，无需数据库"""

    @pytest.fixture
    def fwd(self):
        return ForwardManager()

    def test_get_forwards_empty(self, fwd):
        assert fwd.get_forwards("char_1") == []

    def test_forward_and_get(self, fwd):
        fwd.forward("char_source", "char_target", "mem_001", "转发内容")
        items = fwd.get_forwards("char_target")
        assert len(items) == 1
        assert items[0]["memory_id"] == "mem_001"

    def test_forward_with_content(self, fwd):
        fwd.forward("char_source", "char_target", "mem_001", "测试转发内容")
        items = fwd.get_forwards("char_target")
        assert "测试转发内容" in str(items[0]["content"])

    def test_scoped_to_target(self, fwd):
        fwd.forward("char_a", "char_b", "mem_001", "内容")
        assert fwd.get_forwards("char_a") == []  # char_a 是来源
        assert len(fwd.get_forwards("char_b")) == 1  # char_b 是目标

    def test_multiple_forwards(self, fwd):
        fwd.forward("char_a", "char_b", "mem_001", "内容1")
        fwd.forward("char_c", "char_b", "mem_002", "内容2")
        items = fwd.get_forwards("char_b")
        assert len(items) == 2

"""表情包 + 生理指标 + 语音 + 统计 单元测试。"""

import sys

sys.path.insert(0, ".")

import json

import pytest

from shisi.config import reset_config
from shisi.memory.favorite_manager import FavoriteManager
from shisi.memory.forward_manager import ForwardManager
from shisi.migrations import run_migrations
from shisi.stats.analytics import AnalyticsService
from shisi.sticker.emotion_recommender import EmotionRecommender
from shisi.sticker.safety_check import check_sticker_safety
from shisi.sticker.sticker_manager import StickerManager
from shisi.vital_signs.emotion_mapping import EMOTION_VITAL_MAP
from shisi.vital_signs.vital_engine import VitalSignsEngine
from shisi.voice.emotion_tts import VoiceEnhancer


@pytest.fixture(autouse=True)
def reset_config_each():
    reset_config()


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return db


class TestStickerManager:
    def test_add_and_get(self, tmp_db):
        mgr = StickerManager(db_path=tmp_db)
        mgr.add_sticker("s1", "可爱", ["开心", "撒娇"], "/path/s1.png")
        s = mgr.get_sticker("s1")
        assert s is not None
        assert s["category"] == "可爱"

    def test_list_by_category(self, tmp_db):
        mgr = StickerManager(db_path=tmp_db)
        mgr.add_sticker("s1", "可爱", ["开心"], "/s1.png")
        mgr.add_sticker("s2", "傲娇", ["生气"], "/s2.png")
        cute = mgr.list_by_category("可爱")
        assert len(cute) == 1

    def test_delete(self, tmp_db):
        mgr = StickerManager(db_path=tmp_db)
        mgr.add_sticker("s1", "可爱", ["开心"], "/s1.png")
        assert mgr.delete_sticker("s1") is True
        assert mgr.get_sticker("s1") is None

    def test_bind_to_character(self, tmp_db):
        mgr = StickerManager(db_path=tmp_db)
        mgr.add_sticker("s1", "可爱", ["开心"], "/s1.png")
        count = mgr.bind_to_character("char1", ["s1"], unlock_threshold=50)
        assert count == 1


class TestEmotionRecommender:
    def test_jaccard_recommend(self):
        rec = EmotionRecommender()
        stickers = [
            {"sticker_id": "s1", "emotion_tags": json.dumps(["开心", "可爱"])},
            {"sticker_id": "s2", "emotion_tags": json.dumps(["生气", "傲娇"])},
            {"sticker_id": "s3", "emotion_tags": json.dumps(["开心", "撒娇"])},
        ]
        results = rec.recommend(["开心", "撒娇"], stickers, limit=2)
        assert len(results) <= 2
        assert results[0]["sticker_id"] == "s3"

    def test_empty_input(self):
        rec = EmotionRecommender()
        assert rec.recommend([], [{"emotion_tags": "[]"}]) == []

    def test_no_match(self):
        rec = EmotionRecommender()
        stickers = [{"sticker_id": "s1", "emotion_tags": json.dumps(["生气"])}]
        assert rec.recommend(["开心"], stickers) == []


class TestSafetyCheck:
    def test_safe_filename(self):
        ok, msg = check_sticker_safety("cute_cat.png")
        assert ok is True

    def test_unsafe_filename(self):
        ok, msg = check_sticker_safety("暴力_content.png")
        assert ok is False


class TestVitalSignsEngine:
    def test_default_state(self):
        engine = VitalSignsEngine()
        state = engine.get_current("c1")
        assert state.heart_rate == 72.0
        assert state.temperature == 36.5
        assert state.breath_rate == 16.0

    def test_anger_increases_heart_rate(self):
        engine = VitalSignsEngine()
        state = engine.update_on_emotion("c1", "生气")
        assert state.heart_rate > 90

    def test_sad_decreases_heart_rate(self):
        engine = VitalSignsEngine()
        state = engine.update_on_emotion("c1", "伤心")
        assert state.heart_rate < 72.0

    def test_clamp_values(self):
        engine = VitalSignsEngine()
        state = engine.update_on_emotion("c1", "生气")
        assert 60 <= state.heart_rate <= 120
        assert 36.0 <= state.temperature <= 37.5
        assert 12 <= state.breath_rate <= 25

    def test_smoothing(self):
        engine = VitalSignsEngine()
        engine.update_on_emotion("c1", "平静")
        state = engine.update_on_emotion("c1", "生气")
        assert state.heart_rate < 102

    def test_tick_noise(self):
        engine = VitalSignsEngine()
        engine.update_on_emotion("c1", "平静")
        s1 = engine.tick("c1")
        s2 = engine.tick("c1")
        assert abs(s1.heart_rate - s2.heart_rate) < 10

    def test_wechat_format(self):
        engine = VitalSignsEngine()
        engine.update_on_emotion("c1", "开心")
        msg = engine.format_wechat_message("c1")
        assert "心率" in msg
        assert "体温" in msg
        assert "呼吸" in msg

    def test_emotion_mapping_completeness(self):
        for emotion in ["开心", "伤心", "生气", "害怕", "撒娇", "害羞", "惊讶", "平静"]:
            assert emotion in EMOTION_VITAL_MAP


class TestVoiceEnhancer:
    def test_default_config(self):
        enhancer = VoiceEnhancer()
        config = enhancer.get_tts_config("c1")
        assert config["tts_engine"] == "edge-tts"
        assert config["speed"] == 1.0

    def test_emotion_params(self):
        enhancer = VoiceEnhancer()
        config = enhancer.get_tts_config("c1", "开心")
        assert config["speed"] > 1.0
        assert config["pitch"] > 1.0

    def test_bind_character(self):
        enhancer = VoiceEnhancer()
        enhancer.bind_character_tts("c1", "gpt-sovits", voice_id="v1")
        config = enhancer.get_tts_config("c1", "撒娇")
        assert config["tts_engine"] == "gpt-sovits"
        assert config["speed"] < 1.0  # 撒娇speed=0.95


class TestAnalyticsService:
    def test_record_and_stats(self):
        svc = AnalyticsService()
        svc.record_message("c1", "开心", 60)
        svc.record_message("c1", "撒娇", 62)
        svc.record_message("c2", "伤心", 30)
        stats = svc.get_stats()
        assert stats["total_messages"] == 3
        assert stats["character_distribution"]["c1"] == 2
        assert stats["emotion_distribution"]["开心"] == 1

    def test_empty_stats(self):
        svc = AnalyticsService()
        stats = svc.get_stats()
        assert stats["total_messages"] == 0


class TestFavoriteManager:
    def test_favorite_and_list(self, tmp_db):
        mgr = FavoriteManager(tmp_db)
        assert mgr.favorite("c1", "mem1") is True
        favs = mgr.list_favorites("c1")
        assert len(favs) == 1

    def test_unfavorite(self, tmp_db):
        mgr = FavoriteManager(tmp_db)
        mgr.favorite("c1", "mem1")
        assert mgr.unfavorite("c1", "mem1") is True
        assert len(mgr.list_favorites("c1")) == 0

    def test_is_favorite(self, tmp_db):
        mgr = FavoriteManager(tmp_db)
        mgr.favorite("c1", "mem1")
        assert mgr.is_favorite("c1", "mem1") is True
        assert mgr.is_favorite("c1", "mem2") is False


class TestForwardManager:
    def test_forward(self):
        mgr = ForwardManager()
        assert mgr.forward("c1", "c2", "mem1", "内容") is True
        forwards = mgr.get_forwards("c2")
        assert len(forwards) == 1
        assert forwards[0]["from"] == "c1"

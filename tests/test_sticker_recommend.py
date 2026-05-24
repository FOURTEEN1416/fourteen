"""T-18: 表情包推荐+角色过滤 单元测试"""
import pytest
from unittest.mock import MagicMock, patch


class TestStickerRecommendWithCharacterFilter:
    def _make_manager(self, stickers=None, char_stickers=None):
        from shisi.sticker.sticker_manager import StickerManager
        mgr = MagicMock(spec=StickerManager)
        all_stickers = stickers or [
            {"sticker_id": "happy_01", "category": "开心", "emotion_tags": ["开心"]},
            {"sticker_id": "sad_01", "category": "伤心", "emotion_tags": ["伤心"]},
            {"sticker_id": "char_happy", "category": "开心", "emotion_tags": ["开心"]},
        ]
        mgr.list_by_category.return_value = all_stickers
        mgr._db_path = ":memory:"
        if char_stickers is not None:
            mgr._get_character_sticker_ids = MagicMock(return_value=char_stickers)
        else:
            mgr._get_character_sticker_ids = MagicMock(return_value=set())
        return mgr

    def test_recommend_without_character_id(self):
        from shisi.sticker.sticker_manager import StickerManager
        with patch.object(StickerManager, "__init__", lambda self, *a, **k: None):
            mgr = StickerManager.__new__(StickerManager)
            mgr.list_by_category = MagicMock(return_value=[
                {"sticker_id": "happy_01", "emotion_tags": ["开心"]},
            ])
            mgr._get_character_sticker_ids = MagicMock(return_value=set())
            from shisi.sticker.emotion_recommender import EmotionRecommender
            with patch.object(EmotionRecommender, "recommend", return_value=[
                {"sticker_id": "happy_01", "emotion_tags": ["开心"]},
            ]):
                result = mgr.recommend(["开心"])
                assert len(result) == 1

    def test_recommend_with_character_filter(self):
        from shisi.sticker.sticker_manager import StickerManager
        with patch.object(StickerManager, "__init__", lambda self, *a, **k: None):
            mgr = StickerManager.__new__(StickerManager)
            all_stickers = [
                {"sticker_id": "char_happy", "category": "开心", "emotion_tags": ["开心"]},
                {"sticker_id": "happy_01", "category": "开心", "emotion_tags": ["开心"]},
            ]
            mgr.list_by_category = MagicMock(return_value=all_stickers)
            mgr._get_character_sticker_ids = MagicMock(return_value={"char_happy"})
            from shisi.sticker.emotion_recommender import EmotionRecommender
            with patch.object(EmotionRecommender, "recommend", side_effect=lambda tags, stickers, limit: stickers[:limit]):
                result = mgr.recommend(["开心"], character_id="char1", limit=2)
                ids = [s["sticker_id"] for s in result]
                assert "char_happy" in ids


class TestDefaultStickerProvider:
    def test_initialize_skips_when_existing(self):
        from shisi.sticker.default_provider import DefaultStickerProvider
        from shisi.sticker.sticker_manager import StickerManager
        mgr = MagicMock(spec=StickerManager)
        mgr.list_by_category.return_value = [{"sticker_id": "existing"}]
        provider = DefaultStickerProvider(mgr)
        count = provider.initialize()
        assert count == 0

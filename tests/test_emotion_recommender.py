"""单元测试: 表情包推荐器"""
import sys

sys.path.insert(0, ".")

from shisi.sticker.emotion_recommender import EmotionRecommender


def test_jaccard_basic():
    rec = EmotionRecommender()
    stickers = [
        {"sticker_id": "s1", "emotion_tags": ["开心", "可爱"]},
        {"sticker_id": "s2", "emotion_tags": ["伤心", "委屈"]},
        {"sticker_id": "s3", "emotion_tags": ["生气"]},
    ]
    results = rec.recommend(["开心", "撒娇"], stickers, limit=2)
    assert len(results) > 0
    assert results[0]["sticker_id"] == "s1"


def test_empty_input():
    rec = EmotionRecommender()
    assert rec.recommend([], [{"sticker_id": "s1", "emotion_tags": ["开心"]}]) == []
    assert rec.recommend(["开心"], []) == []


def test_no_match():
    rec = EmotionRecommender()
    stickers = [{"sticker_id": "s1", "emotion_tags": ["伤心"]}]
    results = rec.recommend(["开心"], stickers)
    assert len(results) == 0


def test_json_tags():
    import json
    rec = EmotionRecommender()
    stickers = [{"sticker_id": "s1", "emotion_tags": json.dumps(["开心", "可爱"])}]
    results = rec.recommend(["开心"], stickers)
    assert len(results) > 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All emotion_recommender tests passed!")

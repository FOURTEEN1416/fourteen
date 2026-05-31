"""shisi 基础设施层测试 — SQLiteCharacterRepository (characters_v2)"""

from pathlib import Path

import pytest

from shisi.core.models.affinity_level import AffinityLevel
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.core.models.emotion_type import EmotionType
from shisi.infrastructure.persistence.sqlite_repository import (
    SQLiteCharacterRepository,
)


@pytest.fixture
def repo(tmp_db):
    return SQLiteCharacterRepository(db_path=Path(tmp_db))


@pytest.fixture
def sample_char():
    return CharacterAggregate(
        name="测试角色",
        description="一个可爱的测试角色",
        tags=["可爱", "测试"],
    )


class TestSQLiteCharacterRepository:
    """characters_v2 表 CRUD 测试"""

    def test_save_and_get_by_id(self, repo, sample_char):
        repo.save(sample_char)
        loaded = repo.get_by_id(sample_char.id)
        assert loaded is not None
        assert loaded.id == sample_char.id
        assert loaded.name == "测试角色"
        assert loaded.description == "一个可爱的测试角色"
        assert loaded.tags == ["可爱", "测试"]

    def test_get_by_id_not_found(self, repo):
        assert repo.get_by_id("nonexistent") is None

    def test_get_active_none_initially(self, repo):
        assert repo.get_active() is None

    def test_save_and_get_active(self, repo, sample_char):
        repo.save(sample_char)
        repo.set_active(sample_char.id)
        active = repo.get_active()
        assert active is not None
        assert active.id == sample_char.id

    def test_set_active_deactivates_others(self, repo):
        char_a = CharacterAggregate(name="角色A")
        char_b = CharacterAggregate(name="角色B")
        repo.save(char_a)
        repo.save(char_b)
        repo.set_active(char_a.id)
        repo.set_active(char_b.id)
        active = repo.get_active()
        assert active is not None
        assert active.id == char_b.id

    def test_list_all(self, repo):
        chars = [CharacterAggregate(name=f"角色{i}") for i in range(3)]
        for c in chars:
            repo.save(c)
        all_chars = repo.list_all()
        assert len(all_chars) == 3
        # 按 updated_at DESC 排序
        assert all_chars[0].name == "角色2"

    def test_list_all_empty(self, repo):
        assert repo.list_all() == []

    def test_delete(self, repo, sample_char):
        repo.save(sample_char)
        assert repo.get_by_id(sample_char.id) is not None
        deleted = repo.delete(sample_char.id)
        assert deleted is True
        assert repo.get_by_id(sample_char.id) is None

    def test_delete_not_found(self, repo):
        assert repo.delete("nonexistent") is False

    def test_save_updates_existing(self, repo, sample_char):
        repo.save(sample_char)
        sample_char.name = "新名字"
        repo.save(sample_char)
        loaded = repo.get_by_id(sample_char.id)
        assert loaded is not None
        assert loaded.name == "新名字"
        assert loaded.version == 2  # save 自增 version

    def test_save_preserves_emotional_state(self, repo, sample_char):
        sample_char.update_emotion("我想你了")
        repo.save(sample_char)
        loaded = repo.get_by_id(sample_char.id)
        assert loaded is not None
        assert loaded.emotional_state.primary_emotion == EmotionType.LOVELY
        assert loaded.emotional_state.affection_points > 0

    def test_save_preserves_persona(self, repo, sample_char):
        sample_char.persona.core_anchors = ["温柔体贴"]
        sample_char.persona.warmth = 0.8
        repo.save(sample_char)
        loaded = repo.get_by_id(sample_char.id)
        assert loaded is not None
        assert "温柔体贴" in loaded.persona.core_anchors
        assert loaded.persona.warmth == 0.8

    def test_set_active_returns_false_for_missing(self, repo):
        result = repo.set_active("nonexistent")
        assert result is False

    def test_set_active_empty_string(self, repo):
        """清空活跃角色"""
        repo.set_active("")  # should not error
        assert repo.get_active() is None

    def test_full_lifecycle(self, repo):
        """创建 → 激活 → 查询 → 更新 → 删除"""
        char = CharacterAggregate(name="生命周期角色")
        repo.save(char)
        assert repo.get_active() is None

        repo.set_active(char.id)
        assert repo.get_active() is not None

        char.description = "已更新描述"
        repo.save(char)
        loaded = repo.get_by_id(char.id)
        assert loaded is not None
        assert loaded.description == "已更新描述"

        repo.delete(char.id)
        assert repo.get_by_id(char.id) is None
        assert repo.get_active() is None

"""shisi 角色模块测试 — CharacterStore + CharacterManager"""

from pathlib import Path

import pytest

from shisi.character.manager import CharacterManager
from shisi.character.models import CharaCardV2, CharacterData
from shisi.character.store import CharacterStore
from shisi.migrations import run_migrations

# ═══════════════════════════════════════════════════════
# CharacterStore 测试
# ═══════════════════════════════════════════════════════


@pytest.fixture
def store(tmp_db):
    run_migrations(tmp_db)
    return CharacterStore(db_path=Path(tmp_db))


@pytest.fixture
def sample_card():
    return CharaCardV2(
        data=CharacterData(
            name="测试角色",
            description="可爱又温柔",
            personality="温柔体贴",
            tags=["可爱", "温柔"],
        )
    )


class TestCharacterStore:
    """characters 表 CRUD 测试"""

    def test_save_and_get(self, store, sample_card):
        char_id = store.save_character(sample_card)
        assert char_id is not None
        assert len(char_id) > 0

        loaded = store.get_character(char_id)
        assert loaded is not None
        assert loaded.data.name == "测试角色"

    def test_get_nonexistent(self, store):
        assert store.get_character("nonexistent") is None

    def test_list_characters(self, store, sample_card):
        store.save_character(sample_card)
        chars = store.list_characters()
        assert len(chars) == 1
        assert chars[0].name == "测试角色"
        assert chars[0].is_active is False

    def test_list_empty(self, store):
        assert store.list_characters() == []

    def test_set_active(self, store, sample_card):
        char_id = store.save_character(sample_card)
        result = store.set_active(char_id)
        assert result == char_id

        active_id = store.get_active_id()
        assert active_id == char_id

        chars = store.list_characters()
        active = [c for c in chars if c.is_active]
        assert len(active) == 1
        assert active[0].character_id == char_id

    def test_set_active_nonexistent(self, store):
        assert store.set_active("nonexistent") is None

    def test_active_switches_correctly(self, store):
        card_a = CharaCardV2(data=CharacterData(name="角色A"))
        card_b = CharaCardV2(data=CharacterData(name="角色B"))
        id_a = store.save_character(card_a)
        id_b = store.save_character(card_b)

        store.set_active(id_a)
        assert store.get_active_id() == id_a

        store.set_active(id_b)
        assert store.get_active_id() == id_b

        # A should no longer be active
        chars = store.list_characters()
        active_ids = {c.character_id for c in chars if c.is_active}
        assert active_ids == {id_b}

    def test_delete(self, store, sample_card):
        char_id = store.save_character(sample_card)
        assert store.delete_character(char_id) is True
        assert store.get_character(char_id) is None

    def test_delete_nonexistent(self, store):
        assert store.delete_character("nonexistent") is False

    def test_update(self, store, sample_card):
        char_id = store.save_character(sample_card)
        sample_card.data.name = "新名称"
        assert store.update_character(char_id, sample_card) is True

        loaded = store.get_character(char_id)
        assert loaded is not None
        assert loaded.data.name == "新名称"

    def test_update_nonexistent(self, store, sample_card):
        assert store.update_character("nonexistent", sample_card) is False

    def test_save_generates_id_from_name(self, store):
        card = CharaCardV2(data=CharacterData(name="中文名"))
        char_id = store.save_character(card)
        assert "中文名" in char_id or char_id.startswith("char_")

    def test_save_fallback_id_for_invalid_name(self, store):
        """纯符号名称被清洗后触发 fallback ID 生成"""
        card = CharaCardV2(data=CharacterData(name="!@#$%"))
        char_id = store.save_character(card)
        assert char_id.startswith("char_")


# ═══════════════════════════════════════════════════════
# CharacterManager 测试
# ═══════════════════════════════════════════════════════


class TestCharacterManager:
    """高层面角色管理测试"""

    @pytest.fixture
    def mgr(self, tmp_db):
        run_migrations(tmp_db)
        store = CharacterStore(db_path=Path(tmp_db))
        return CharacterManager(store=store, cache_size=3)

    @pytest.fixture
    def chars(self, mgr):
        """预存几个角色"""
        cards = []
        for name in ["小红", "小明", "小华"]:
            card = CharaCardV2(data=CharacterData(name=name))
            char_id = mgr.store.save_character(card)
            cards.append((char_id, card))
        return cards

    def test_initialize_with_active(self, mgr, chars):
        char_id, _ = chars[0]
        mgr.store.set_active(char_id)
        mgr.initialize()
        assert mgr.get_active_id() == char_id
        assert mgr.get_active() is not None
        assert mgr.get_active().data.name == "小红"

    def test_initialize_no_active(self, mgr):
        mgr.initialize()
        assert mgr.get_active_id() is None
        assert mgr.get_active() is None

    def test_initialize_idempotent(self, mgr, chars):
        mgr.store.set_active(chars[0][0])
        mgr.initialize()
        first_active = mgr.get_active_id()
        mgr.initialize()  # second call
        assert mgr.get_active_id() == first_active

    def test_load_character(self, mgr, chars):
        char_id, _ = chars[0]
        card = mgr.load_character(char_id)
        assert card is not None
        assert card.data.name == "小红"

    def test_load_nonexistent(self, mgr):
        assert mgr.load_character("nonexistent") is None

    def test_load_caches(self, mgr, chars):
        char_id, _ = chars[0]
        # First load populates cache
        card1 = mgr.load_character(char_id)
        # Second load from cache
        card2 = mgr.load_character(char_id)
        assert card1 is card2  # same object from cache

    def test_cache_lru_eviction(self, mgr, chars):
        """cache_size=3, 加载第4个应淘汰最老的"""
        for char_id, _ in chars[:3]:
            mgr.load_character(char_id)
        assert len(mgr._cache) == 3

        # Load a 4th character (different name)
        extra = CharaCardV2(data=CharacterData(name="小芳"))
        extra_id = mgr.store.save_character(extra)
        mgr.load_character(extra_id)

        assert len(mgr._cache) == 3
        # 第一个（小红）应该被淘汰
        assert chars[0][0] not in mgr._cache

    def test_switch_character(self, mgr, chars):
        char_id_a, _ = chars[0]
        char_id_b, _ = chars[1]

        ok, msg = mgr.switch_character(char_id_a)
        assert ok is True
        assert mgr.get_active_id() == char_id_a

        ok, msg = mgr.switch_character(char_id_b)
        assert ok is True
        assert mgr.get_active_id() == char_id_b

    def test_switch_nonexistent(self, mgr):
        ok, msg = mgr.switch_character("nonexistent")
        assert ok is False
        assert "不存在" in msg

    def test_list_characters(self, mgr, chars):
        result = mgr.list_characters()
        assert len(result) == 3

    def test_delete_character(self, mgr, chars):
        char_id, _ = chars[0]
        assert mgr.delete_character(char_id) is True
        assert mgr.load_character(char_id) is None

    def test_delete_active_resets_active(self, mgr, chars):
        char_id, _ = chars[0]
        mgr.switch_character(char_id)
        assert mgr.get_active_id() == char_id

        mgr.delete_character(char_id)
        assert mgr.get_active_id() is None
        assert mgr.get_active() is None

    def test_get_active_persona_config(self, mgr, chars):
        char_id, _ = chars[0]
        mgr.switch_character(char_id)
        config = mgr.get_active_persona_config()
        assert config is not None
        assert "name" in config

    def test_get_active_persona_config_no_active(self, mgr):
        assert mgr.get_active_persona_config() is None

    def test_set_on_switch_callback(self, mgr, chars):
        char_id, _ = chars[0]
        calls = []

        def callback(card):
            calls.append(card.data.name)

        mgr.set_on_switch_callback(callback)
        mgr.switch_character(char_id)
        assert len(calls) == 1
        assert calls[0] == "小红"

"""角色管理 + 导入导出 + 校验 单元测试。"""

import json

import pytest

from shisi.character.chara_card_v2 import (
    ParserDispatcher,
    to_persona_config,
)
from shisi.character.exporter import PersonaExporter
from shisi.character.importer import PersonaImporter
from shisi.character.manager import CharacterManager
from shisi.character.models import (
    CardFormat,
    CharaCardV2,
    CharacterData,
    CharacterState,
    ImportResult,
)
from shisi.character.store import CharacterStore
from shisi.character.validator import (
    ValidationError,
    sanitize_text,
    validate_card,
    validate_card_strict,
)
from shisi.migrations import run_migrations


@pytest.fixture
def tmp_db(tmp_path):
    db = tmp_path / "test.db"
    run_migrations(db)
    return db


@pytest.fixture
def store(tmp_db):
    return CharacterStore(tmp_db)


@pytest.fixture
def manager(tmp_db):
    mgr = CharacterManager(store=CharacterStore(tmp_db), cache_size=3)
    mgr.initialize()
    return mgr


def _make_card(name="测试角色", **kwargs):
    data = {"name": name, "description": "测试描述", "personality": "温柔", **kwargs}
    return CharaCardV2(data=CharacterData(**data))


class TestCharaCardV2Models:
    def test_valid_card(self):
        card = _make_card("椎名真昼")
        assert card.data.name == "椎名真昼"
        assert card.spec == "chara_card_v2"

    def test_empty_name_rejected(self):
        with pytest.raises(Exception):
            CharaCardV2(data=CharacterData(name="", description="x"))

    def test_invalid_spec_rejected(self):
        with pytest.raises(Exception):
            CharaCardV2(spec="invalid_spec", data=CharacterData(name="x"))

    def test_character_state(self):
        state = CharacterState(character_id="test", name="测试", affinity=50.0, emotion_stage="亲密")
        assert state.affinity == 50.0
        assert state.emotion_stage == "亲密"

    def test_model_dump_json(self):
        card = _make_card()
        j = card.model_dump_json()
        data = json.loads(j)
        assert data["data"]["name"] == "测试角色"


class TestParser:
    def test_standard_v2(self):
        raw = {"spec": "chara_card_v2", "spec_version": "2.0", "data": {"name": "真昼", "description": "完美"}}
        card, fmt = ParserDispatcher.parse(raw)
        assert card.data.name == "真昼"
        assert fmt == CardFormat.CHARA_CARD_V2

    def test_standard_v3(self):
        raw = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {"name": "真昼"}}
        card, fmt = ParserDispatcher.parse(raw)
        assert fmt == CardFormat.CHARA_CARD_V3

    def test_shisi_prompts_format(self):
        raw = {"data": {"prompts": {"uuid-1": {"data": {"name": "阿哈", "description": "内向少年", "personality": "温柔", "scenario": "初期", "creator_notes": "病娇 纯爱"}}}}}
        card, fmt = ParserDispatcher.parse(raw)
        assert card.data.name == "阿哈"
        assert fmt == CardFormat.AIYU_PROMPTS
        assert "病娇" in card.data.tags

    def test_v1_fallback(self):
        raw = {"name": "旧格式", "description": "旧版", "personality": "傲娇"}
        card, fmt = ParserDispatcher.parse(raw)
        assert card.data.name == "旧格式"

    def test_to_persona_config(self):
        card = _make_card("真昼", personality="完美", scenario="校内", first_mes="你好")
        config = to_persona_config(card)
        assert config["name"] == "真昼"
        assert config["greeting"] == "你好"


class TestValidator:
    def test_valid_card_passes(self):
        card = _make_card()
        errors = validate_card(card)
        assert errors == []

    def test_xss_detected(self):
        card = _make_card(description="<script>alert(1)</script>")
        errors = validate_card(card)
        assert any("注入" in e or "XSS" in e for e in errors)

    def test_javascript_uri_detected(self):
        card = _make_card(description="javascript:alert(1)")
        errors = validate_card(card)
        assert len(errors) > 0

    def test_code_injection_detected(self):
        card = _make_card(personality="__import__('os')")
        errors = validate_card(card)
        assert len(errors) > 0

    def test_strict_raises(self):
        card = _make_card(description="<script>x</script>")
        with pytest.raises(ValidationError):
            validate_card_strict(card)

    def test_sanitize_text(self):
        assert "script" not in sanitize_text("<script>bad</script>hello").lower() or sanitize_text("<script>bad</script>hello") == "hello"


class TestImporterExporter:
    def test_import_file(self, tmp_path):
        card = _make_card("导入测试")
        f = tmp_path / "test.json"
        f.write_text(card.model_dump_json(), encoding="utf-8")
        importer = PersonaImporter(output_dir=tmp_path / "out")
        result_card, error = importer.import_file(f)
        assert error is None
        assert result_card.data.name == "导入测试"

    def test_export_roundtrip(self, tmp_path):
        card = _make_card("导出测试")
        out_dir = tmp_path / "export"
        exporter = PersonaExporter(out_dir)
        path = exporter.export_card(card)
        card2, fmt = ParserDispatcher.parse_file(path)
        assert card2.data.name == card.data.name
        assert card2.data.description == card.data.description

    def test_import_result_model(self):
        r = ImportResult(total=10, success=8, failed=2, errors=["e1", "e2"])
        assert r.total == 10
        assert r.success == 8


class TestCharacterStore:
    def test_save_and_get(self, store):
        card = _make_card("存储测试")
        cid = store.save_character(card)
        retrieved = store.get_character(cid)
        assert retrieved is not None
        assert retrieved.data.name == "存储测试"

    def test_list_characters(self, store):
        store.save_character(_make_card("角色A"))
        store.save_character(_make_card("角色B"))
        chars = store.list_characters()
        assert len(chars) >= 2

    def test_set_active(self, store):
        cid = store.save_character(_make_card("活跃测试"))
        result = store.set_active(cid)
        assert result == cid
        assert store.get_active_id() == cid

    def test_delete(self, store):
        cid = store.save_character(_make_card("删除测试"))
        assert store.delete_character(cid) is True
        assert store.get_character(cid) is None

    def test_update(self, store):
        cid = store.save_character(_make_card("更新测试"))
        new_card = _make_card("更新后")
        assert store.update_character(cid, new_card) is True
        assert store.get_character(cid).data.name == "更新后"


class TestCharacterManager:
    def test_switch_character(self, manager, store):
        cid = store.save_character(_make_card("切换角色"))
        ok, msg = manager.switch_character(cid)
        assert ok is True
        assert manager.get_active_id() == cid

    def test_lru_cache(self, manager, store):
        ids = []
        for i in range(5):
            ids.append(store.save_character(_make_card(f"缓存{i}")))
        for cid in ids:
            manager.load_character(cid)
        assert len(manager._cache) <= 3

    def test_delete_active_character(self, manager, store):
        cid = store.save_character(_make_card("活跃删除"))
        manager.switch_character(cid)
        manager.delete_character(cid)
        assert manager.get_active() is None

    def test_get_active_persona_config(self, manager, store):
        cid = store.save_character(_make_card("人设配置"))
        manager.switch_character(cid)
        config = manager.get_active_persona_config()
        assert config is not None
        assert config["name"] == "人设配置"

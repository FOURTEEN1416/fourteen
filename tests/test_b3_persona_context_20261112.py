"""B-3b 唯一人设源（2026-11-12）—— 角色卡定位 / 原始读取 / persona 渲染。

契约：``shisi/application/persona_context.py`` 是角色卡定位与 persona 渲染的
唯一 owner；``proactive/proactive_context.py`` 只保留公共入口转发；
``shisi/application/persona_service.py`` 的卡文件定位与读取复用它。

本文件钉死：
1. ``resolve_card_path`` 四条路径：文件名直查 / 内部 id / match_name 的 name /
   缺失 None；
2. ``render_persona_hint`` 段落顺序 + 1200 上限；
3. 迁移等价：同一张临时卡与迁移前逻辑的期望字符串**完全相等**（固定 fixture，
   不调用旧函数）；
4. ``load_full_persona_hint`` 委托 ``persona_context.render_persona_hint``；
5. ``PersonaService._load_character_card`` 仍能经新 owner 加载同一张卡。
"""

from __future__ import annotations

import json
from pathlib import Path

from shisi.application import persona_context


def _write_card(root: Path, filename: str, **fields) -> Path:
    card = {
        "id": fields.pop("id", filename.removesuffix(".json")),
        "name": fields.pop("name", "测试角色"),
        **fields,
    }
    path = root / filename
    path.write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    return path


def _patch_card_root(monkeypatch, root: Path) -> None:
    """把 ``project_path("config", "characters")`` 钉到临时卡库。

    两处消费方都要打：persona_context 函数内导入（打 utils.project_paths），
    persona_service 模块级绑定（打其模块属性）。
    """
    from shisi.application import persona_service
    from utils import project_paths

    fake = lambda *parts: root if parts == ("config", "characters") else Path(*parts)  # noqa: E731

    monkeypatch.setattr(project_paths, "project_path", fake)
    monkeypatch.setattr(persona_service, "project_path", fake)


def _force_mode(monkeypatch, mode: str) -> None:
    """把回复模式真源钉到指定值（生产代码函数内局部 import → 打模块属性即生效）。"""
    import utils.reply_mode as rm

    monkeypatch.setattr(rm, "read_reply_mode", lambda *a, **k: mode)


# ── 1. resolve_card_path ────────────────────────────────────


def test_resolve_by_filename_direct(tmp_path):
    path = _write_card(tmp_path, "direct-id.json", name="直查角色")

    assert persona_context.resolve_card_path("direct-id", root=tmp_path) == path


def test_resolve_by_internal_id(tmp_path):
    path = _write_card(tmp_path, "file-name-is-different.json", id="internal-id", name="内部角色")

    assert persona_context.resolve_card_path("internal-id", root=tmp_path) == path


def test_resolve_by_name_only_when_match_name_true(tmp_path):
    path = _write_card(tmp_path, "name-file.json", id="id-001", name="名字角色")

    assert persona_context.resolve_card_path("名字角色", root=tmp_path) is None
    assert (
        persona_context.resolve_card_path("名字角色", match_name=True, root=tmp_path)
        == path
    )


def test_resolve_missing_returns_none(tmp_path):
    assert persona_context.resolve_card_path("nobody", root=tmp_path) is None
    assert persona_context.resolve_card_path("nobody", root=tmp_path / "no-such-dir") is None


def test_load_raw_card_malformed_does_not_raise(tmp_path):
    (tmp_path / "broken.json").write_text("{ not json", encoding="utf-8")

    assert persona_context.load_raw_card("broken", root=tmp_path) == (None, None)
    assert persona_context.load_raw_card("missing", root=tmp_path) == (None, None)


# ── 2. render_persona_hint：段落顺序与上限 ─────────────────


def test_render_persona_hint_segment_order(tmp_path, monkeypatch):
    _write_card(
        tmp_path,
        "order-card.json",
        name="顺序角色",
        description="描述正文。",
        personality_text="性格正文。",
        speaking_style="风格正文。",
        mes_example="用户：早\n顺序角色：早呀。",
        creator_notes="规则正文。",
    )
    _patch_card_root(monkeypatch, tmp_path)
    _force_mode(monkeypatch, "novel")

    hint = persona_context.render_persona_hint("order-card")

    assert hint.startswith("角色：顺序角色")
    titles = ["【角色设定】", "【性格】", "【说话风格】", "【回复示例】", "【扮演规则】"]
    positions = [hint.index(title) for title in titles]
    assert positions == sorted(positions), f"段落顺序漂移: {positions}"
    assert len(hint) <= 1200


def test_render_persona_hint_total_cap_1200(tmp_path, monkeypatch):
    _write_card(tmp_path, "long-card.json", name="长卡", description="长" * 2000)
    _patch_card_root(monkeypatch, tmp_path)
    _force_mode(monkeypatch, "novel")

    hint = persona_context.render_persona_hint("long-card")

    assert len(hint) == 1200
    assert hint.startswith("角色：长卡")


# ── 3. 迁移等价：与迁移前逻辑的期望字符串完全相等 ───────────


def test_render_persona_hint_matches_legacy_expected_string(tmp_path, monkeypatch):
    _write_card(
        tmp_path,
        "fixed-card.json",
        id="fixed-card",
        name="固定角色",
        description="描述正文。",
        personality_text="性格正文。",
        speaking_style="风格正文。",
        mes_example="用户：早\n固定角色：早呀。",
        creator_notes="规则正文。",
    )
    _patch_card_root(monkeypatch, tmp_path)
    _force_mode(monkeypatch, "novel")

    expected = (
        "角色：固定角色\n\n"
        "【角色设定】\n描述正文。\n\n"
        "【性格】\n性格正文。\n\n"
        "【说话风格】\n风格正文。\n\n"
        "【回复示例】\n用户：早\n固定角色：早呀。\n\n"
        "【扮演规则】\n规则正文。"
    )
    assert persona_context.render_persona_hint("fixed-card") == expected


# ── 4. load_full_persona_hint 委托 ─────────────────────────


def test_load_full_persona_hint_delegates_to_persona_context(monkeypatch):
    from proactive import proactive_context as pctx

    calls: list[tuple[str, str | None]] = []

    def _fake(cid, user_key=None):
        calls.append((cid, user_key))
        return "SENTINEL-PERSONA"

    monkeypatch.setattr(persona_context, "render_persona_hint", _fake)

    assert pctx.load_full_persona_hint("  card-x  ", "user-1") == "SENTINEL-PERSONA"
    assert calls == [("card-x", "user-1")]
    # 空 id 短路：不触达渲染层（绝不退化成读某个全局角色）
    assert pctx.load_full_persona_hint("   ") == ""
    assert len(calls) == 1


# ── 5. persona_service 仍能加载同一张卡 ────────────────────


def test_persona_service_loads_card_via_persona_context(tmp_path, monkeypatch):
    from shisi.application.persona_service import PersonaService

    _write_card(
        tmp_path,
        "not-the-id.json",
        id="svc-id",
        name="服务卡角色",
        description="服务卡描述。",
        personality_text="服务卡性格。",
    )
    _patch_card_root(monkeypatch, tmp_path)

    service = object.__new__(PersonaService)
    service._card_cache = {}
    service._card_cache_max = 64

    card = service._load_character_card("svc-id")

    assert card is not None, "persona_service 必须经新 owner 找到内部 id 匹配的卡"
    assert card["id"] == "svc-id"
    assert card["name"] == "服务卡角色"
    # 再读一次仍返回同一张卡（id 匹配路径的缓存前置检查沿用旧语义：
    # 文件名直查命中 mtime>0 才命中缓存；内部 id 匹配路径每轮重读）
    again = service._load_character_card("svc-id")
    assert again is not None and again["id"] == "svc-id" and again["name"] == "服务卡角色"


def test_persona_service_corrupt_direct_file_falls_back_to_id(tmp_path, monkeypatch):
    """旧语义：同名文件损坏时，persona_service 仍按内部 id 找下一张可用卡。"""
    from shisi.application.persona_service import PersonaService

    (tmp_path / "broken-id.json").write_text("{not json", encoding="utf-8")
    _write_card(tmp_path, "other.json", id="broken-id", name="兜底卡", description="兜底描述。")
    _patch_card_root(monkeypatch, tmp_path)

    service = object.__new__(PersonaService)
    service._card_cache = {}
    service._card_cache_max = 64

    card = service._load_character_card("broken-id")
    assert card is not None, "同名文件损坏时必须继续按内部 id 兜底（旧实现语义）"
    assert card["id"] == "broken-id"
    assert card["name"] == "兜底卡"

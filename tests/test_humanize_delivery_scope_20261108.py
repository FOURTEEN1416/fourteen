"""投递链的拟人度作用域 = 「用户 × 角色」快照（2026-11-08 D-A 验收修复）。

背景：用户裁决把拟人度从「账号级偏好」改成「用户 × 角色」设置（per-user 覆盖层
`user_humanize` 整体退役）。用户在角色设置里调过的 `max_segments` / 段间节奏 /
「主动拆条」只存在于 `user_character_settings` 全量快照里；若投递链仍按全局块
读取，这些值在真实微信投递上**不生效**（配置能存但不生效 = 能力空转）。

本文件钉住四件事（全部在真实 `WeChatConnector` 上跑，只打桩网络与凭证路径）：
1. `send_reply_segments` 的拆条上限取快照（全局 2 / 快照 4 ⇒ 真发 4 段）；
2. 段间延时取**同一份**快照（快照 `delay_mode=off` ⇒ 零延时），不是再读全局；
3. `humanize_proactive_split_enabled(user_key, character_id)` 取快照开关，
   无参调用仍读全局（机器面零行为变更）；
4. 作用域里的角色 id 由 `utils.character_resolver` 从 `user_manager` 解析，
   别的角色/别人的快照不被串用（多用户隔离）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils import character_settings, humanize_config
from wechat_direct import wechat_connector as wc

OWNER = 42
PEER = "peer_wx"
SESSION_KEY = f"{OWNER}:{PEER}"
CHARACTER = "card_scope_001"
OTHER_CHARACTER = "card_scope_002"


def _mk_connector(tmp_path, monkeypatch, *, character_id: str = CHARACTER) -> wc.WeChatConnector:
    """真连接器 + 只读桩用户管理器（`get_user_character` 是角色解析的唯一入口）。"""
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    mgr = SimpleNamespace(get_user_character=lambda session_key: character_id)
    conn = wc.WeChatConnector(mgr, owner_user_id=OWNER)
    conn.token = "test-token"
    monkeypatch.setattr(conn, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(conn, "_save_context_tokens", lambda: None)
    return conn


def _record_sends(monkeypatch) -> list[str]:
    sent: list[str] = []

    def _send(**kwargs):
        sent.append(kwargs["text"])
        return {"ret": 0}

    monkeypatch.setattr(wc, "_send_text", _send)
    return sent


def test_split_cap_uses_user_character_snapshot(tmp_path, monkeypatch):
    """全局 2 段 / 该用户×角色 4 段 ⇒ 投递走 4 段（不是全局的 2）。"""
    humanize_config.write_humanize(max_segments=2)
    character_settings.write(SESSION_KEY, CHARACTER, {"max_segments": 4})

    conn = _mk_connector(tmp_path, monkeypatch)
    sent = _record_sends(monkeypatch)
    monkeypatch.setattr(wc, "_segment_delay", lambda text, **kwargs: 0)

    accepted = conn.send_reply_segments(PEER, "一\n二\n三\n四")

    assert sent == ["一", "二", "三", "四"], (
        "投递链未读「用户 × 角色」快照：max_segments 仍是全局值（能力空转）"
    )
    assert accepted == "一\n二\n三\n四"


def test_split_cap_does_not_leak_across_characters(tmp_path, monkeypatch):
    """快照只挂在 CHARACTER 上 ⇒ 解析到别的角色时回落全局（不串用）。"""
    humanize_config.write_humanize(max_segments=2)
    character_settings.write(SESSION_KEY, CHARACTER, {"max_segments": 4})

    conn = _mk_connector(tmp_path, monkeypatch, character_id=OTHER_CHARACTER)
    sent = _record_sends(monkeypatch)
    monkeypatch.setattr(wc, "_segment_delay", lambda text, **kwargs: 0)

    conn.send_reply_segments(PEER, "一\n二\n三\n四")

    # 全局 max_segments=2 ⇒ 第一段独立、其余并入第二段
    assert sent == ["一", "二\n三\n四"], "别人的角色快照被串用了（多用户/多角色隔离破坏）"


def test_segment_delay_uses_same_snapshot(tmp_path, monkeypatch):
    """段间延时取同一份快照：快照 `delay_mode=off` ⇒ 真零延时（全局是 dynamic）。"""
    humanize_config.write_humanize(delay_mode="dynamic", per_char_ms=90)
    character_settings.write(SESSION_KEY, CHARACTER, {"delay_mode": "off"})

    conn = _mk_connector(tmp_path, monkeypatch)
    _record_sends(monkeypatch)
    seen: list[object] = []
    real = wc._segment_delay

    def _spy(text, **kwargs):
        seen.append(kwargs.get("humanize"))
        return real(text, **kwargs)

    monkeypatch.setattr(wc, "_segment_delay", _spy)

    sleeps: list[float] = []
    monkeypatch.setattr(wc.time, "sleep", lambda s: sleeps.append(s))

    conn.send_reply_segments(PEER, "第一段长一点\n第二段\n第三段")

    assert seen, "投递链根本没把快照传给 _segment_delay"
    assert all(cfg is not None for cfg in seen), "快照未透传（_segment_delay 会回落全局）"
    assert all(getattr(cfg, "delay_mode", "") == "off" for cfg in seen), (
        "透传的不是「用户 × 角色」快照（delay_mode 应为快照里的 off）"
    )
    assert sleeps == [0.0, 0.0], f"快照 off 档位应零延时，实得 {sleeps}"


def test_proactive_split_flag_follows_snapshot(tmp_path, monkeypatch):
    """「主动拆条」开关：快照 True / 全局 False ⇒ 该用户开启；无参仍读全局。"""
    from utils.humanize_delivery import humanize_proactive_split_enabled

    humanize_config.write_humanize(proactive_split=False)
    character_settings.write(SESSION_KEY, CHARACTER, {"proactive_split": True})

    conn = _mk_connector(tmp_path, monkeypatch)
    user_key, character_id = conn.humanize_scope(PEER)
    assert user_key == SESSION_KEY
    assert character_id == CHARACTER

    assert humanize_proactive_split_enabled(user_key, character_id) is True
    assert humanize_proactive_split_enabled() is False, "无参调用必须仍读全局默认"
    assert humanize_proactive_split_enabled(SESSION_KEY, OTHER_CHARACTER) is False, (
        "别的角色快照被串用（角色维度隔离破坏）"
    )


def test_humanize_scope_resolves_character_from_user_manager(tmp_path, monkeypatch):
    """角色解析走唯一 owner：`user_manager.get_user_character(会话键)`。"""
    calls: list[str] = []

    def _get(key: str) -> str:
        calls.append(key)
        return CHARACTER

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    conn = wc.WeChatConnector(SimpleNamespace(get_user_character=_get), owner_user_id=OWNER)

    assert conn.humanize_scope(PEER) == (SESSION_KEY, CHARACTER)
    assert calls == [SESSION_KEY], "解析键必须是完整会话键（owner:peer），不得用裸 peer"


def test_humanize_scope_falls_back_to_default_without_manager(tmp_path, monkeypatch):
    """无用户管理器 ⇒ 角色回落 `default`（resolver 既有语义），不抛异常。"""
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    conn = wc.WeChatConnector(None, owner_user_id=OWNER)

    assert conn.humanize_scope(PEER) == (SESSION_KEY, "default")


@pytest.mark.parametrize("cap", [2, 3, 4])
def test_snapshot_cap_honoured_for_all_allowed_values(tmp_path, monkeypatch, cap):
    """2/3/4 三个合法档位都要真的生效（不是只对某个值碰巧对）。"""
    humanize_config.write_humanize(max_segments=2)
    character_settings.write(SESSION_KEY, CHARACTER, {"max_segments": cap})

    conn = _mk_connector(tmp_path, monkeypatch)
    sent = _record_sends(monkeypatch)
    monkeypatch.setattr(wc, "_segment_delay", lambda text, **kwargs: 0)

    conn.send_reply_segments(PEER, "一\n二\n三\n四")
    assert len(sent) == cap


# ── 2026-11-08 验收修复：失败归因闸与兜底文案也必须按作用域读 ──────────


def test_proactive_split_helper_uses_same_scope_as_delivery(tmp_path, monkeypatch):
    """失败归因闸与投递链同源：快照 True / 全局 False ⇒ 归因闸也 True。

    回归场景（main.py wechat 通道发送器）：`direct_send_proactive` 已按
    「用户 × 角色」快照走拆条链，但失败归因分支旧用无参
    `humanize_proactive_split_enabled()` 只读全局 → 把「未完整受理」误报成
    「微信定向投递失败」（错误语义，影响排障）。
    """
    from utils.humanize_delivery import (
        humanize_proactive_split_enabled,
        proactive_split_enabled,
    )

    humanize_config.write_humanize(proactive_split=False)
    character_settings.write(SESSION_KEY, CHARACTER, {"proactive_split": True})

    conn = _mk_connector(tmp_path, monkeypatch)
    assert humanize_proactive_split_enabled() is False, "全局块应仍为关闭"
    assert proactive_split_enabled(conn, PEER) is True, (
        "归因闸读了全局块：按角色开了主动拆条时会把「未完整受理」误报成"
        "「整段直发被拒」"
    )
    # 连接器不提供 `humanize_scope`（机器面/测试替身）⇒ 回落全局，零行为变更
    assert proactive_split_enabled(object(), PEER) is False


def test_fallback_lines_read_scope_reply_mode(tmp_path, monkeypatch):
    """三处兜底文案按「用户 × 角色」快照读回复模式，不再只读全局默认。

    站点：① 路由异常 exception 兜底；② 空回复 empty_reply 兜底；
    ③ 媒体不可用降级话术。全局 immersive + 快照 novel ⇒ 三处都必须带上
    `(会话键, 角色 id)` 解析模式（旧无参调用会让小说式角色拿到沉浸式口径）。
    """
    from utils import fallback_lines
    from utils import reply_mode as rm

    monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    rm.write_reply_mode("immersive")
    character_settings.write(SESSION_KEY, CHARACTER, {"reply_mode": "novel"})

    seen: list[tuple[object, object]] = []
    real = rm.read_reply_mode

    def _spy(user_key=None, character_id=None):
        seen.append((user_key, character_id))
        return real(user_key, character_id)

    monkeypatch.setattr(rm, "read_reply_mode", _spy)
    fallback_lines.reset_daily_dedup()

    conn = _mk_connector(tmp_path, monkeypatch)
    monkeypatch.setattr(conn, "_record_media_step", lambda *a, **k: None)
    _record_sends(monkeypatch)

    def _boom(*args, **kwargs):
        raise RuntimeError("route boom")

    monkeypatch.setattr(wc, "_call_user_manager", _boom)
    conn._handle_message_serial(
        {"item_list": [{"type": 1, "text_item": {"text": "在吗"}}]}, "m1", PEER, "ctx",
    )
    assert seen[-1] == (SESSION_KEY, CHARACTER), "异常兜底读了全局回复模式"

    monkeypatch.setattr(wc, "_call_user_manager", lambda *a, **k: {"reply": ""})
    conn._handle_message_serial(
        {"item_list": [{"type": 1, "text_item": {"text": "在吗"}}]}, "m2", PEER, "ctx",
    )
    assert seen[-1] == (SESSION_KEY, CHARACTER), "空回复兜底读了全局回复模式"

    conn._send_media_unsupported_notice(PEER, "ctx")
    assert seen[-1] == (SESSION_KEY, CHARACTER), "媒体降级话术读了全局回复模式"

"""D16 通道产品语义守卫（2026-10-12 裁决「谁扫码谁聊」）。

裁决：一个注册账号 = 一个用户；用户扫码绑定 bot，bot 出现在其微信里，
谁扫码谁聊、一对一；角色在网页控制台选；非扫码人消息在产品语义中
不存在（生产取证：活跃通道第三方消息 0 条），按异常拒绝。

本文件钉死：
1. 入站守卫：非扫码人消息拒绝（不进 LLM/不落库）；扫码人正常；凭证缺
   扫码人 id 时 fail-open 放行但必须 ERROR 告警；遗留全局通道不受影响。
2. scanner_wxid 三条装载路径（登录确认 / 凭证快速登录 / 实例重建预载）。
3. 角色解析：pref:owner:peer 自选层退役；扫码人绑定（activate 同步）为正主。
4. 主动节奏：按账号聚合（同 owner 双通道共享 min_interval / cooldown 账）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wechat_direct import channel_paths
from wechat_direct import wechat_connector as wc


@pytest.fixture()
def tmp_state_root(tmp_path, monkeypatch):
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    return tmp_path


def _make_owner_conn(tmp_path, owner_user_id: int = 7) -> wc.WeChatConnector:
    session_dir = Path(tmp_path) / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    conn = wc.WeChatConnector(
        MagicMock(), owner_user_id=owner_user_id, slot=0, session_dir=session_dir
    )
    conn.token = "tok"
    return conn


def _text_msg(msg_id: str = "m1", from_user: str = "p1", text: str = "hi") -> dict:
    return {
        "message_type": 1,
        "message_id": msg_id,
        "from_user_id": from_user,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }


# ── 1. 入站守卫 ──────────────────────────────────────────


def test_non_scanner_message_rejected(tmp_state_root, caplog):
    conn = _make_owner_conn(tmp_state_root)
    conn.scanner_wxid = "wx_owner@im.wechat"
    seen: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            wc, "_call_user_manager",
            lambda *a, **k: seen.append(a[1]) or {"reply": "不该到这"},
        )
        with caplog.at_level(logging.WARNING):
            conn._handle_message(
                _text_msg("s1", from_user="stranger@im.wechat", text="你好")
            )
    assert seen == [], "非扫码人消息必须被拒绝，不得进入主链"
    assert "non_scanner_rejected" in caplog.text


def test_scanner_message_passes(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    conn.scanner_wxid = "wx_owner@im.wechat"
    seen: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            wc, "_call_user_manager",
            lambda *a, **k: seen.append(a[1]) or {"reply": "ok"},
        )
        conn._handle_message(
            _text_msg("m1", from_user="wx_owner@im.wechat", text="今天好累")
        )
    assert seen == ["7:wx_owner@im.wechat"]


def test_missing_scanner_id_fails_open_with_error(tmp_state_root, caplog):
    """凭证缺扫码人 id 属登录链异常：放行但必须告警（不静默 fail-open）。"""
    conn = _make_owner_conn(tmp_state_root)
    assert conn.scanner_wxid == ""
    seen: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            wc, "_call_user_manager",
            lambda *a, **k: seen.append(a[1]) or {"reply": "ok"},
        )
        with caplog.at_level(logging.ERROR):
            conn._handle_message(_text_msg("m1", from_user="anyone", text="hi"))
    assert seen == ["7:anyone"], "fail-open：缺 id 不锁死用户"
    assert "scanner_id_missing" in caplog.text


def test_legacy_global_channel_not_gated(tmp_state_root):
    """遗留全局通道（admin 兼容面，无 owner）不适用扫码人守卫。"""
    session_dir = Path(tmp_state_root) / "legacy"
    session_dir.mkdir(parents=True, exist_ok=True)
    conn = wc.WeChatConnector(MagicMock(), session_dir=session_dir)
    assert conn.owner_user_id is None
    seen: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            wc, "_call_user_manager",
            lambda *a, **k: seen.append(a[1]) or {"reply": "ok"},
        )
        conn._handle_message(_text_msg("m1", from_user="anyone", text="hi"))
    assert seen == ["anyone"]


# ── 2. scanner_wxid 装载路径 ─────────────────────────────


def test_login_confirm_loads_scanner(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    confirmed = {
        "status": "confirmed",
        "bot_token": "bt",
        "ilink_bot_id": "bid",
        "baseurl": "https://ilinkai.weixin.qq.com",
        "ilink_user_id": "wx_scan@im.wechat",
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(wc, "_fetch_qr_code", lambda *a, **k: {"qrcode": "q", "qrcode_img_content": "i"})
        mp.setattr(wc, "_poll_qr_status", lambda *a, **k: confirmed)
        assert conn.login() is True
    assert conn.scanner_wxid == "wx_scan@im.wechat"
    # 凭证落盘带上扫码人 id（重启可恢复）
    creds = json.loads(Path(conn._credentials_path).read_text(encoding="utf-8"))
    assert creds["user_id"] == "wx_scan@im.wechat"


def test_login_saved_credentials_reload_scanner(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    Path(conn._credentials_path).write_text(
        json.dumps({
            "token": "tok_saved", "base_url": wc.DEFAULT_BASE_URL,
            "bot_id": "bid", "user_id": "wx_saved@im.wechat",
        }),
        encoding="utf-8",
    )
    with pytest.MonkeyPatch.context() as mp:
        # 凭证有效：getupdates 不返回 -14 → 快速登录成功
        mp.setattr(wc, "_get_updates", lambda *a, **k: {"ret": 0, "msgs": []})
        assert conn.login() is True
    assert conn.scanner_wxid == "wx_saved@im.wechat"


# ── 3. 角色解析链（D16：pref 层退役，扫码人绑定为正主）────


def _bare_user_manager(bindings: dict):
    from user_scheduler import UserManager

    mgr = object.__new__(UserManager)
    mgr._bindings = bindings
    return mgr


def test_resolve_character_pref_layer_retired():
    mgr = _bare_user_manager({
        # 旧 peer 自选层：不得再参与解析
        "pref:7:wx_owner@im.wechat": {"wxid": "wx_owner", "character_card_id": "STALE"},
        # 扫码人绑定（activate 同步）= 正主
        "wx_owner@im.wechat": {"wxid": "wx_owner", "character_card_id": "ACTIVATED"},
    })
    assert mgr._resolve_character_id("7:wx_owner@im.wechat") == "ACTIVATED"


def test_resolve_character_owner_binding_fallback():
    mgr = _bare_user_manager({
        "7": {"wxid": "7", "character_card_id": "OWNER_DEFAULT"},
    })
    # 账号激活角色对账号域内全部会话生效（D16：身份域=账号）
    assert mgr._resolve_character_id("7:wx_owner@im.wechat") == "OWNER_DEFAULT"
    assert mgr._resolve_character_id("7:anyone_else") == "OWNER_DEFAULT"
    # 无任何绑定的账号 → default
    assert mgr._resolve_character_id("8:wx_none@im.wechat") == "default"


def test_scanner_ids_for_user_reads_credentials(tmp_state_root):
    channel_paths.ensure_session_dir(9, 0)
    channel_paths.credentials_path(9, 0).write_text(
        json.dumps({"token": "t", "user_id": "wx_a@im.wechat"}), encoding="utf-8"
    )
    channel_paths.ensure_session_dir(9, 1)
    channel_paths.credentials_path(9, 1).write_text(
        json.dumps({"token": "t", "user_id": "wx_a@im.wechat"}), encoding="utf-8"
    )
    channel_paths.ensure_session_dir(9, 2)
    channel_paths.credentials_path(9, 2).write_text(
        json.dumps({"token": "t", "user_id": ""}), encoding="utf-8"
    )
    ids = channel_paths.scanner_ids_for_user(9)
    assert ids == ["wx_a@im.wechat"], "空 id 剔除、重复去重"


# ── 4. 主动节奏按账号聚合 ─────────────────────────────────


class _FakeEng:
    def __init__(self, hours: float, last_delivery: datetime | None):
        self._hours = hours
        # 基准纠偏（2026-10-12）：账号聚合读投递基准 _last_delivery_time
        # （仅 commit_sent 推进），非被 on_chat 拨动的 _last_proactive_time
        self._last_delivery_time = last_delivery

    def _hours_since_last_chat(self) -> float:
        return self._hours


class _FakeHub:
    def __init__(self, engines: dict[str, _FakeEng]):
        self._engines = engines

    def get(self, key):
        return self._engines.get(key)

    def known_user_keys(self):
        return list(self._engines)


def test_account_pacing_aggregates_same_owner():
    from proactive.llm_proactive import (
        account_pacing_state,
        should_skip_proactive_decision,
    )

    now = datetime.now(tz=timezone.utc)
    # 账号 2 两条通道：A 通道 30 分钟前刚投递过主动消息；B 通道从没投递、
    # 且用户 10 小时前在 B 通道聊过
    hub = _FakeHub({
        "2:wx_a@im.wechat": _FakeEng(30.0, now - timedelta(minutes=30)),
        "2:wx_b@im.wechat": _FakeEng(10.0, None),
        "9:wx_other@im.wechat": _FakeEng(1.0, now - timedelta(minutes=5)),
    })
    hours, last = account_pacing_state(
        hub, "2:wx_b@im.wechat", hub.get("2:wx_b@im.wechat"),
    )
    # hours 取组内最小（10 < 30，但另一账号的 1.0 不得混入）
    assert hours == 10.0
    # last_proactive 取组内最大 → A 通道 30min 前的投递对 B 通道生效
    assert last == now - timedelta(minutes=30)
    skip, reason = should_skip_proactive_decision(
        hours_since_last_chat=hours, last_proactive_at=last
    )
    assert skip is True and reason == "min_interval", (
        "同账号另一通道 3h 前刚投递 → B 通道必须被间隔闸拦（旧口径各自记账会放行）"
    )


def test_account_pacing_single_key_unchanged():
    from proactive.llm_proactive import account_pacing_state

    eng = _FakeEng(20.0, None)
    hub = _FakeHub({"5:wx_x@im.wechat": eng})
    hours, last = account_pacing_state(hub, "5:wx_x@im.wechat", eng)
    assert hours == 20.0 and last is None


def test_account_pacing_missing_engine_defaults_far():
    from proactive.llm_proactive import account_pacing_state

    hub = _FakeHub({})
    hours, last = account_pacing_state(hub, "5:wx_x@im.wechat", None)
    assert hours == 99.0 and last is None


def test_scheduler_uses_account_pacing_source():
    """接线守卫：_llm_proactive_one_user 必须经 account_pacing_state 取节奏。"""
    import inspect

    from proactive import scheduler as sched

    src = inspect.getsource(sched.ProactiveScheduler._llm_proactive_one_user)
    assert "account_pacing_state" in src

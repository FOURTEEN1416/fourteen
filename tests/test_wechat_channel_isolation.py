"""每人独立微信通道 — 隔离与权限回归测试（2026-09-19 用户裁决）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from wechat_direct import channel_paths
from wechat_direct.connector_registry import (
    ChannelSlotError,
    ConnectorRegistry,
)
from wechat_direct.wechat_connector import (
    WeChatConnector,
    get_wechat_state,
    load_session_state,
    save_session_state,
)


def test_session_paths_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    d1 = channel_paths.session_dir(1, 0)
    d2 = channel_paths.session_dir(2, 0)
    assert d1 != d2
    channel_paths.ensure_session_dir(1, 0)
    channel_paths.ensure_session_dir(2, 0)
    assert d1.exists() and d2.exists()
    assert channel_paths.credentials_path(1, 0).parent == d1
    assert channel_paths.credentials_path(2, 0).parent == d2


def test_max_channels_per_user_is_two():
    assert channel_paths.MAX_CHANNELS_PER_USER == 2


def test_default_global_cap_is_100():
    assert channel_paths.DEFAULT_MAX_CHANNELS == 100
    assert channel_paths.max_channels() == 100


def test_registry_user_cannot_see_other_status(tmp_path, monkeypatch):
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    # 用户 1 已连接（状态文件）
    save_session_state(
        1,
        0,
        {"connected": True, "bot_id": "bot_owner_1@im.bot", "status": "connected"},
    )
    # 用户 2 无任何会话
    st2 = load_session_state(2, 0)
    assert st2["connected"] is False
    assert "bot_owner_1" not in json.dumps(st2)

    reg = ConnectorRegistry()
    primary2 = reg.primary_status(2)
    assert primary2.get("connected") in (False, None)
    assert primary2.get("bot_id") in ("", None)
    assert primary2.get("owner_user_id") == 2


def test_registry_slot_limit_two(monkeypatch):
    reg = ConnectorRegistry()

    class Dummy:
        def __init__(self, uid, slot):
            self.owner_user_id = uid
            self.slot = slot
            self.token = ""

        def stop(self):
            pass

    monkeypatch.setattr(
        "wechat_direct.wechat_connector.WeChatConnector",
        lambda user_manager=None, **kw: Dummy(kw.get("owner_user_id"), kw.get("slot", 0)),
    )
    monkeypatch.setattr(channel_paths, "ensure_session_dir", lambda u, s=0: Path(f"/tmp/wx/{u}/{s}"))
    c0 = reg.ensure(9, slot=0)
    c1 = reg.ensure(9, slot=1)
    assert c0.slot == 0 and c1.slot == 1
    with pytest.raises(ChannelSlotError):
        reg.ensure(9, slot=2)


def test_connector_session_key_and_send_requires_target():
    c = WeChatConnector(
        None,
        owner_user_id=42,
        slot=0,
        session_dir=Path("/tmp/wx-test-42"),
        credentials_path="/tmp/wx-test-42/credentials.json",
        state_path="/tmp/wx-test-42/state.json",
        context_tokens_path="/tmp/wx-test-42/ctx.json",
    )
    assert c._session_key("friend_a") == "42:friend_a"
    # owner 通道禁止无目标发送
    assert c.send_text("hi", to_user="") is False


def test_legacy_connector_session_key_unchanged():
    c = WeChatConnector(None)
    assert c._session_key("friend_a") == "friend_a"


def test_get_wechat_state_requires_user_for_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    save_session_state(7, 0, {"connected": True, "bot_id": "u7bot", "status": "connected"})
    st = get_wechat_state(user_id=8)
    assert st.get("connected") in (False, None)
    assert st.get("bot_id") in ("", None)


def test_restore_uses_poll_lock_dedup(monkeypatch):
    """多 worker：restore 对同一 (user,slot) 只应有一个进程真正启动轮询。"""
    reg = ConnectorRegistry()
    calls = {"run": 0}

    class Dummy:
        def __init__(self, uid, slot):
            self.owner_user_id = uid
            self.slot = slot
            self.token = ""
            self._stop = False

        def run(self):
            calls["run"] += 1

        def stop(self):
            self._stop = True

    monkeypatch.setattr(
        "wechat_direct.wechat_connector.WeChatConnector",
        lambda user_manager=None, **kw: Dummy(kw.get("owner_user_id"), kw.get("slot", 0)),
    )
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", Path("/tmp/wx-lock-test"))
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: Path("/tmp/wx-lock-test/data/wechat_sessions")
    )
    Path("/tmp/wx-lock-test/data/wechat_sessions/1/slot0").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        channel_paths,
        "list_user_slots_with_credentials",
        lambda uid: [0],
    )
    # 模拟第二个 worker 抢锁失败
    monkeypatch.setattr(
        ConnectorRegistry,
        "_try_acquire_poll_lock",
        staticmethod(lambda uid, slot: None),
    )
    n = reg.restore_on_boot()
    assert n == 0
    assert calls["run"] == 0

    monkeypatch.setattr(
        ConnectorRegistry,
        "_try_acquire_poll_lock",
        staticmethod(lambda uid, slot: 0),
    )
    n2 = reg.restore_on_boot()
    assert n2 == 1
    assert calls["run"] == 1


def test_poll_lock_fds_linux_fd_positive(tmp_path, monkeypatch):
    """Linux 真实分支（flock fd>0）：fd 必须按 (user,slot) 记账并可释放。

    生产回归（2026-09-21 部署 ecd6ce2 后微信通道全灭）：上一用例只覆盖了
    None（他人持锁）与 0（Windows 无 fcntl），fd>0 分支在本机永不执行，
    旧实现 `_poll_lock_fds` 未在 __init__ 初始化 → restore_on_boot 第一把锁
    即 AttributeError，被 run_api 宽 except 吞成一条 WARNING。
    """
    reg = ConnectorRegistry()
    calls = {"run": 0}

    class Dummy:
        def __init__(self, uid, slot):
            self.owner_user_id = uid
            self.slot = slot
            self.token = ""
            self._stop = False

        def run(self):
            calls["run"] += 1

        def stop(self):
            self._stop = True

    monkeypatch.setattr(
        "wechat_direct.wechat_connector.WeChatConnector",
        lambda user_manager=None, **kw: Dummy(kw.get("owner_user_id"), kw.get("slot", 0)),
    )
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths,
        "sessions_root",
        lambda: tmp_path / "data" / "wechat_sessions",
    )
    monkeypatch.setattr(
        channel_paths, "list_user_slots_with_credentials", lambda uid: [0]
    )
    (tmp_path / "data" / "wechat_sessions" / "1" / "slot0").mkdir(parents=True)
    # 模拟 Linux：每次拿锁得到一个真实 fd（假整数会在释放时关掉无关 CRT 描述符）
    def _fake_lock(uid: int, slot: int) -> int:
        return os.open(
            str(tmp_path / f"lock-{uid}-{slot}.lock"),
            os.O_CREAT | os.O_RDWR,
            0o644,
        )

    monkeypatch.setattr(ConnectorRegistry, "_try_acquire_poll_lock", staticmethod(_fake_lock))

    assert reg.restore_on_boot() == 1
    assert list(reg._poll_lock_fds) == [(1, 0)]

    # 释放后按键弹出，fd 不再被本进程持有
    reg._release_poll_lock(1, 0)
    assert reg._poll_lock_fds == {}

    # start_login 同一路径：fd>0 也必须按 (uid,slot) 记账（旧实现建成 list 并 append）
    reg.start_login(1, slot=0)
    assert list(reg._poll_lock_fds) == [(1, 0)]
    reg._release_poll_lock(1, 0)


def test_peer_character_menu_and_choice():
    from wechat_direct.peer_character import (
        build_character_menu,
        try_handle_character_choice,
    )

    cards = [{"id": "a", "name": "阿哈"}, {"id": "b", "name": "林初夏"}]
    menu = build_character_menu(cards)
    assert "1. 阿哈" in menu and "2. 林初夏" in menu
    act = try_handle_character_choice("角色", 1, "wx_f", cards, {})
    assert act and act["action"] == "show_menu"
    # P1-审查 item28：数字必须跟在待确认菜单后（pending 非空）才生效
    pending = {"wx_f": (9_999_999_999.0, cards)}
    act2 = try_handle_character_choice("2", 1, "wx_f", cards, pending)
    assert act2 and act2["action"] == "selected" and act2["character_id"] == "b"
    assert try_handle_character_choice("2", 1, "wx_f", cards, {}) is None
    assert try_handle_character_choice("你好", 1, "wx_f", cards, pending) is None

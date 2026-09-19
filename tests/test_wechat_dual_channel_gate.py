"""双通道并发门禁：Registry 允许两个用户各自 slot0，互不共享 token/状态。"""

from __future__ import annotations

from pathlib import Path

from wechat_direct import channel_paths
from wechat_direct.connector_registry import ConnectorRegistry
from wechat_direct.wechat_connector import WeChatConnector, save_session_state


def test_two_users_have_isolated_connectors(monkeypatch, tmp_path):
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    reg = ConnectorRegistry()

    class Dummy:
        def __init__(self, user_manager=None, **kw):
            self.owner_user_id = kw.get("owner_user_id")
            self.slot = kw.get("slot", 0)
            self.token = ""
            self.bot_id = ""
            self.session_dir = Path(kw.get("session_dir") or "")

        def stop(self):
            pass

    monkeypatch.setattr("wechat_direct.wechat_connector.WeChatConnector", Dummy)
    c1 = reg.ensure(101, slot=0)
    c2 = reg.ensure(202, slot=0)
    assert c1 is not c2
    assert c1.owner_user_id == 101 and c2.owner_user_id == 202

    save_session_state(101, 0, {"connected": True, "bot_id": "bot_A@im.bot", "status": "connected"})
    save_session_state(202, 0, {"connected": True, "bot_id": "bot_B@im.bot", "status": "connected"})
    s1 = reg.primary_status(101)
    s2 = reg.primary_status(202)
    assert s1.get("bot_id") != s2.get("bot_id")
    assert "bot_B" not in str(s1)
    assert "bot_A" not in str(s2)

    # 发送目标隔离：owner 通道禁止无目标
    real = WeChatConnector(
        None,
        owner_user_id=101,
        slot=0,
        session_dir=tmp_path / "data" / "wechat_sessions" / "101" / "slot0",
    )
    assert real._session_key("friend_x") == "101:friend_x"
    assert real.send_text("hi") is False

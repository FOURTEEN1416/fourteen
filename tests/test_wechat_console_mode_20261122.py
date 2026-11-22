"""控制台微信模式（python main.py）修复验证（2026-11-22，H4·high）。

背景（对抗验收 H4）：
- D16 fail-closed 后，``run_wechat_mode`` 以 ``WeChatConnector(None)`` 构造
  即抛 ``ValueError``（连接器必须绑定 owner_user_id）；且控制台路径从不设置
  ``allow_interactive_login=True``（全仓唯一置 True 处在用户通道
  ``start_login``）——无凭证时直接进 needs_reauth 终态，二维码永不出现。
- 本文件钉死修复后的不变量（构造与旗标层面，不真扫码）：
  ① 连接器以合法 owner 构造（env ``WECHAT_CONSOLE_OWNER_ID`` > users 表首个用户）；
  ② 控制台模式显式 ``allow_interactive_login=True``（控制台本就交互式）；
  ③ 无任何 owner 时给出明确引导并退出，不抛裸异常。
"""

from __future__ import annotations

import main as main_mod
from wechat_direct import channel_paths


class _RecordingConnector:
    """捕获构造参数与旗标的替身连接器（不真扫码、不联网）。"""

    def __init__(self, user_manager=None, **kwargs):
        self.user_manager = user_manager
        self.kwargs = dict(kwargs)
        self.owner_user_id = kwargs.get("owner_user_id")
        self.allow_interactive_login = False
        self.ran = False
        self.stopped = False
        self.interactive_at_run: bool | None = None

    def run(self):
        self.ran = True
        self.interactive_at_run = self.allow_interactive_login

    def stop(self):
        self.stopped = True


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """替换 ``main._async_session`` 的最小异步上下文管理器。"""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return _FakeResult(self._value)


def _install_connector_factory(monkeypatch) -> list[_RecordingConnector]:
    created: list[_RecordingConnector] = []

    def _factory(user_manager=None, **kwargs):
        conn = _RecordingConnector(user_manager, **kwargs)
        created.append(conn)
        return conn

    # run_wechat_mode 在函数体内 `from wechat_direct import WeChatConnector`，
    # 因此打补丁的位置是包属性（调用时解析）。
    monkeypatch.setattr("wechat_direct.WeChatConnector", _factory)
    return created


def test_console_wechat_mode_uses_env_owner_and_enables_interactive(monkeypatch):
    """env 指定 owner：连接器按 per-user 路径构造，且交互旗标在 run 前置位。"""
    monkeypatch.setenv("WECHAT_CONSOLE_OWNER_ID", "123")
    created = _install_connector_factory(monkeypatch)

    holder: dict = {}
    main_mod.run_wechat_mode(user_manager=None, wechat_connector_holder=holder)

    assert len(created) == 1, "必须以合法 owner 构造连接器（修复前 None 构造即抛 ValueError）"
    conn = created[0]
    assert conn.owner_user_id == 123
    assert conn.kwargs.get("slot") == 0
    assert conn.kwargs.get("session_dir") == channel_paths.session_dir(123, 0)
    assert conn.kwargs.get("credentials_path") == str(channel_paths.credentials_path(123, 0))
    assert conn.kwargs.get("context_tokens_path") == str(
        channel_paths.context_tokens_path(123, 0)
    )
    assert conn.interactive_at_run is True, (
        "控制台模式必须显式 allow_interactive_login=True，否则无凭证直接进终态、二维码永不出现"
    )
    assert conn.ran is True
    assert holder.get("connector") is conn, "连接器必须注入主动消息 holder"


def test_console_wechat_mode_falls_back_to_first_db_user(monkeypatch):
    """未指定 env：回落 users 表首个用户（按 id 升序）。"""
    monkeypatch.delenv("WECHAT_CONSOLE_OWNER_ID", raising=False)
    monkeypatch.setattr(main_mod, "_async_session", lambda: _FakeSession(7))
    created = _install_connector_factory(monkeypatch)

    main_mod.run_wechat_mode(user_manager=None, wechat_connector_holder=None)

    assert len(created) == 1
    assert created[0].owner_user_id == 7, "未指定 env 时必须回落 users 表首个用户"
    assert created[0].interactive_at_run is True


def test_console_wechat_mode_invalid_env_falls_back_to_db(monkeypatch):
    """非法 env 值：回落 users 表首个用户，而非崩溃。"""
    monkeypatch.setenv("WECHAT_CONSOLE_OWNER_ID", "not-a-number")
    monkeypatch.setattr(main_mod, "_async_session", lambda: _FakeSession(9))
    created = _install_connector_factory(monkeypatch)

    main_mod.run_wechat_mode(user_manager=None, wechat_connector_holder=None)

    assert len(created) == 1
    assert created[0].owner_user_id == 9


def test_console_wechat_mode_without_owner_exits_with_guidance(monkeypatch, capsys):
    """无任何 owner：给出明确引导后退出，不构造连接器、不抛裸异常。"""
    monkeypatch.delenv("WECHAT_CONSOLE_OWNER_ID", raising=False)
    monkeypatch.setattr(main_mod, "_async_session", lambda: _FakeSession(None))
    created = _install_connector_factory(monkeypatch)

    main_mod.run_wechat_mode(user_manager=None, wechat_connector_holder=None)

    assert created == [], "无合法 owner 时不得构造连接器"
    out = capsys.readouterr().out
    assert "WECHAT_CONSOLE_OWNER_ID" in out, "必须给出明确引导（注册账号或设置 env）"

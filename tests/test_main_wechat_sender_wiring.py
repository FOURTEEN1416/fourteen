"""main.py 微信通道发送器 → 唯一真源 `direct_send_proactive` 的接线确认。

复审问题（2026-10-16）：`_wechat_sender_factory` 的**定向**分支曾**内联复刻**
「拆条 + 段间节奏 + 整段直发」逻辑，而非调用 `utils.humanize_delivery.
direct_send_proactive`（main 侧 re-export；`api/run_api.py` 与生产连接器宿主
`WeChatConnector._drain_outbox` 均调用它）。三处各持一份 = 受理判据漂移风险。

本文件与 `test_proactive_segmented_delivery.py` 互补：后者钉「行为契约」，本
文件专门钉「**接线**本身」——定向分支确实把投递**委托**给共享入口，且委托后
两条失败错误语义逐字保留、默认配置零行为变更。
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

WECHAT_KEY = "7:o9cq80wx@im.wechat"
PEER = "o9cq80wx@im.wechat"
MAIN_SRC = Path(__file__).resolve().parent.parent / "main.py"


# ── 夹具 ─────────────────────────────────────────────────


class _CapturingScheduler:
    """只暴露 `_wire_proactive_runtime` 用到的面。"""

    def __init__(self) -> None:
        self.channels: dict[str, object] = {}
        self.delivery_loop = None

    def register_channel(self, name, factory) -> None:
        self.channels[name] = factory

    def set_delivery_loop(self, getter) -> None:
        self.delivery_loop = getter


class _FakeConnector:
    """最小连接器替身：记录拆条/直发调用，可模拟「只受理前缀」「直发被拒」。"""

    def __init__(
        self,
        *,
        accepted: str | None = None,
        has_public: bool = True,
        send_text_ok: bool = True,
    ) -> None:
        self.token = "tok"
        self.segment_calls: list[tuple[str, str]] = []
        self.text_calls: list[tuple[str, str]] = []
        self._accepted = accepted
        self._send_text_ok = send_text_ok
        if has_public:
            self.send_reply_segments = self._segments

    def _segments(self, peer, reply, **kw):
        self.segment_calls.append((peer, reply))
        if self._accepted is not None:
            return self._accepted
        from wechat_direct.wechat_connector import split_reply_for_wechat

        return "\n".join(split_reply_for_wechat(reply))

    def send_text(self, text, to_user=""):
        self.text_calls.append((text, to_user))
        return self._send_text_ok


def _force_split(monkeypatch, enabled: bool) -> None:
    """用**真实** `HumanizeConfig` 开关拆条（`HumanizeConfig()` 默认即关）。"""
    from utils import humanize_config

    monkeypatch.setattr(
        humanize_config,
        "read_humanize",
        lambda: humanize_config.HumanizeConfig(proactive_split=enabled),
    )


def _wire_wechat_send(monkeypatch, connector, user_mgr=None):
    """装配 main 入口的主动运行时，取回 wechat 通道的 `_send`。"""
    import main

    monkeypatch.setattr(main, "install_llm_decision", lambda *a, **k: True)
    monkeypatch.setattr(main, "install_reminder_delivery", lambda *a, **k: True)
    sched = _CapturingScheduler()
    main._wire_proactive_runtime(
        sched,
        orchestrator=object(),
        user_mgr=user_mgr,
        ws_holder={},
        wechat_holder={"connector": connector},
    )
    return sched.channels["wechat"]()


def _factory_source() -> str:
    src = MAIN_SRC.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == "_wechat_sender_factory":
            return ast.get_source_segment(src, node) or ""
    raise AssertionError("main.py 缺少函数 _wechat_sender_factory")


# ── 接线本体：定向分支必须委托共享入口 ────────────────────


def test_targeted_send_delegates_to_shared_helper(monkeypatch):
    """定向分支调用 `direct_send_proactive`，不再自行直发/拆条。"""
    import main

    calls: list[tuple] = []
    monkeypatch.setattr(
        main,
        "direct_send_proactive",
        lambda conn, peer, msg: calls.append((conn, peer, msg)) or True,
    )
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("早", session_key=WECHAT_KEY))
    assert calls == [(conn, PEER, "早")], "定向投递未委托唯一真源 direct_send_proactive"
    assert conn.text_calls == [], "已委托共享入口，不应再自行 send_text"
    assert conn.segment_calls == []


def test_targeted_helper_false_maps_to_runtime_error(monkeypatch):
    """共享入口返回 False ⇒ 仍抛 RuntimeError（通道置 None 语义链不变）。"""
    import main

    monkeypatch.setattr(main, "direct_send_proactive", lambda conn, peer, msg: False)
    _force_split(monkeypatch, False)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    with pytest.raises(RuntimeError):
        asyncio.run(send("早", session_key=WECHAT_KEY))


# ── 委托后的结果等价：默认配置零行为变更 + 失败语义逐字保留 ──


def test_default_config_single_send_text_unchanged(monkeypatch):
    """默认（拆条关）⇒ 与现状逐字一致：单次整段 send_text、不拆条。"""
    _force_split(monkeypatch, False)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("早", session_key=WECHAT_KEY))
    assert conn.text_calls == [("早", PEER)]
    assert conn.segment_calls == []


def test_split_complete_uses_segments(monkeypatch):
    _force_split(monkeypatch, True)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("第一句\n第二句", session_key=WECHAT_KEY))
    assert conn.segment_calls == [(PEER, "第一句\n第二句")]
    assert conn.text_calls == []


def test_split_incomplete_message_preserved(monkeypatch):
    """「未完整受理」错误族保留（只受理前缀，尾句用户没看到）。"""
    _force_split(monkeypatch, True)
    conn = _FakeConnector(accepted="第一句")
    send = _wire_wechat_send(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="未完整受理"):
        asyncio.run(send("第一句\n第二句", session_key=WECHAT_KEY))
    assert conn.text_calls == [], "未完整受理不得回退整段直发（会重复投递）"


def test_send_text_failure_message_preserved(monkeypatch):
    """「直发失败」错误族保留（默认配置下唯一失败路径）。"""
    _force_split(monkeypatch, False)
    conn = _FakeConnector(send_text_ok=False)
    send = _wire_wechat_send(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="微信定向投递失败"):
        asyncio.run(send("早", session_key=WECHAT_KEY))


def test_split_unsupported_connector_falls_back_to_send_text(monkeypatch):
    """开启拆条但连接器无 `send_reply_segments` ⇒ 回落整段直发，不误报失败。"""
    _force_split(monkeypatch, True)
    conn = _FakeConnector(has_public=False)
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("早", session_key=WECHAT_KEY))
    assert conn.text_calls == [("早", PEER)]
    assert conn.segment_calls == []


def test_unparsable_session_key_still_rejected(monkeypatch):
    _force_split(monkeypatch, True)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="解析不出 peer"):
        asyncio.run(send("早", session_key=":"))


def test_broadcast_branch_untouched(monkeypatch):
    """无 session_key 的系统级广播分支不介入拆条链（行为不动）。"""
    _force_split(monkeypatch, True)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("系统广播"))
    assert conn.text_calls == [("系统广播", "")]
    assert conn.segment_calls == []


# ── 源码级：定向分支不得再内联复刻共享逻辑 ────────────────


def test_targeted_branch_has_no_inline_delivery_copy():
    """`_wechat_sender_factory` 定向分支只委托，不再内联 send_text / 拆条。"""
    src = _factory_source()
    assert "direct_send_proactive(" in src, "main 微信直发未接入唯一真源共享入口"
    assert "send_proactive_segmented(" not in src, "定向分支仍内联拆条逻辑（应委托）"
    assert "connector.send_text(msg, to_user=peer)" not in src, (
        "定向分支仍内联整段直发（应委托 direct_send_proactive）"
    )

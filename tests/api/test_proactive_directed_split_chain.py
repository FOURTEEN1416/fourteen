"""生产定向主动投递走拆条链 —— run_api 生产者 × 连接器宿主消费者 端到端契约。

为什么单列（2026-10-16 复审 P1，本次修复）：生产（``deploy/ai-girlfriend.service``
= ``uvicorn api.run_api:app --workers 4``）的**定向**主动微信投递是一条**跨 worker
接力**：

  ① 生产者 = ``api/run_api.py`` 的 wechat 通道发送器 ``_wechat_sender_factory``：
     只把 ``kind="proactive", channel="wechat"`` 的命令交控制面并等受理回执，
     **本机不直发**（连接器可能挂在别的 worker，本机直发命令 0 送达）；
  ② 消费者 = 连接器宿主的 ``WeChatConnector._drain_outbox``：CAS 认领该行后
     **真实发送**。

「拆条 + 段间节奏」链必须作用在 ② 才有意义 —— 复审意见指出的「生产定向主动
投递绕过拆条链」正是 ② 曾无条件 ``send_text`` 所致。本文件在 ``tests/api/`` 下
钉住**这两端的接力**：

- 生产者契约：rp 可用时只入队 ``kind=proactive`` 并委托，本机零直发；未获受理
  ⇒ ``RuntimeError``（不静默判成功）。
- 端到端接力：真 run_api 生产者（**从源文件按 AST 抽出后在受控命名空间执行**，
  不 import ``api.run_api`` —— 模块级会 ``orchestrator.initialize()`` / 起 WS 线程
  / ``sys.exit``，测试进程不可承受，同批 ``tests/test_proactive_segmented_delivery.py``
  亦只读源不 import）× 真控制面 SQLite × 真 ``_drain_outbox``：
  - ``humanize.proactive_split`` 开启 ⇒ 走拆条链（``send_reply_segments``）；
  - 默认关闭 ⇒ **逐字回落** ``send_text``（零行为变更锚）。
"""

from __future__ import annotations

import ast
import asyncio
import logging
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, ".")

RUN_API = Path(__file__).resolve().parents[2] / "api" / "run_api.py"
WECHAT_KEY = "7:o9cq80wx@im.wechat"
OWNER = 7
PEER = "o9cq80wx@im.wechat"


# ── run_api 生产者：AST 抽取 `_wechat_sender_factory`（不 import 重入口） ──


def _factory_source() -> str:
    """取出 ``_wechat_sender_factory`` 的源码，逐行剥掉定义处缩进。"""
    src = RUN_API.read_text(encoding="utf-8")
    node = next(
        (
            n
            for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.FunctionDef) and n.name == "_wechat_sender_factory"
        ),
        None,
    )
    assert node is not None, "run_api 缺少 _wechat_sender_factory"
    lines = src.splitlines()
    block = lines[node.lineno - 1 : node.end_lineno]
    indent = len(block[0]) - len(block[0].lstrip())
    return "\n".join(
        line[indent:] if len(line) >= indent else line for line in block
    )


class _FakeRegistry:
    """最小连接器注册表替身：让 `_wechat_sender_factory` 不因「无在线连接」短路。

    定向 + rp 可用分支在入队后即 return，不会走到 `registry.all()`，故 `all()` 空表即可。
    """

    def online_count(self) -> int:
        return 1

    def all(self):  # noqa: ANN201
        return []


@pytest.fixture
def online_registry(monkeypatch):
    """令 `wechat_direct.connector_registry.get_registry()` 报告 1 条在线连接。"""
    from wechat_direct import connector_registry

    monkeypatch.setattr(connector_registry, "get_registry", lambda: _FakeRegistry())


class _RecordingPlane:
    """最小控制面替身：记录 ``enqueue_and_wait`` 入参，立即回执。"""

    def __init__(self, result: tuple[bool, str, int] = (True, "accepted", 1)) -> None:
        self.calls: list[dict] = []
        self._result = result

    def enqueue_and_wait(self, **kw):  # noqa: ANN003
        self.calls.append(kw)
        return self._result


def _build_send(
    *,
    plane_getter,
    direct_spy=None,
    timeout: float = 10.0,
    char_id: str = "",
):
    """在受控命名空间执行 run_api 的真实 `_wechat_sender_factory`，返回其 `_send`。"""
    from utils import session_key as session_key_mod
    from utils.humanize_delivery import direct_send_proactive

    ns = {
        "_plane": plane_getter,
        "_RECEIPT_TIMEOUT_SECONDS": timeout,
        "_character_id_for_session": lambda sk: char_id,
        "session_key_mod": session_key_mod,
        "user_mgr": None,
        "direct_send_proactive": direct_spy or direct_send_proactive,
        "logger": logging.getLogger("run_api"),
        "asyncio": asyncio,
    }
    exec(compile(_factory_source(), str(RUN_API), "exec"), ns)  # noqa: S102
    return ns["_wechat_sender_factory"]()


# ── 生产者契约（run_api 定向分支） ─────────────────────────


def test_run_api_producer_directed_enqueues_proactive_and_delegates(online_registry):
    """定向 + rp 可用 ⇒ 只入队 kind=proactive/channel=wechat，本机零直发。"""
    plane = _RecordingPlane()
    local_sends: list[tuple[str, str]] = []

    def _spy(conn, peer, msg):  # noqa: ANN001
        local_sends.append((peer, msg))
        return True

    send = _build_send(plane_getter=lambda: plane, direct_spy=_spy)
    asyncio.run(send("第一句\n第二句", session_key=WECHAT_KEY))

    assert len(plane.calls) == 1, "定向分支应恰好交控制面一次"
    kw = plane.calls[0]
    assert kw["kind"] == "proactive"
    assert kw["channel"] == "wechat"
    assert kw["session_key"] == WECHAT_KEY
    assert kw["owner_id"] == OWNER
    assert kw["peer"] == PEER
    assert kw["message"] == "第一句\n第二句"
    assert kw["timeout"] == 10.0
    assert local_sends == [], (
        "rp 可用时不应本机直发：连接器可能在别的 worker（0 送达）且会与宿主双发"
    )


def test_run_api_producer_directed_unaccepted_raises(online_registry):
    """未获受理 ⇒ RuntimeError（与 send_text 返回 False 路径同构，不静默判成功）。"""
    plane = _RecordingPlane((False, "no_host", 1))
    send = _build_send(plane_getter=lambda: plane)
    with pytest.raises(RuntimeError, match="未获受理"):
        asyncio.run(send("早", session_key=WECHAT_KEY))


# ── 端到端接力（真生产者 × 真控制面 × 真 `_drain_outbox`） ──


@pytest.fixture
def host_env(tmp_path, monkeypatch):
    """真连接器 + 控制面 DB 沙箱（走 `_drain_outbox` 真实代码路径）。"""
    from proactive import runtime_plane as rp
    from wechat_direct import channel_paths
    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    sd = tmp_path / "sess" / str(OWNER) / "slot0"
    sd.mkdir(parents=True, exist_ok=True)
    rp.set_db_path(tmp_path / "plane.db")
    conn = wc.WeChatConnector(
        None,
        owner_user_id=OWNER,
        slot=0,
        session_dir=sd,
        credentials_path=str(sd / "credentials.json"),
        state_path=str(sd / "state.json"),
        qrcode_path=str(sd / "qrcode.json"),
        context_tokens_path=str(sd / "context_tokens.json"),
    )
    conn.token = "tok"
    monkeypatch.setattr(conn, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(conn, "_save_context_tokens", lambda: None)
    monkeypatch.setattr(wc, "_segment_delay", lambda *a, **k: 0)
    yield wc, rp, conn
    rp.set_db_path(None)


def _split_config(monkeypatch, enabled: bool) -> None:
    """用真实 `HumanizeConfig` 开关拆条（`HumanizeConfig()` 默认即关）。"""
    from utils import humanize_config

    monkeypatch.setattr(
        humanize_config,
        "read_humanize",
        lambda: humanize_config.HumanizeConfig(proactive_split=enabled),
    )


def _run_directed_through_host(send, conn, timeout: float = 10.0) -> None:
    """生产者线程投递 + 主线程反复 `_drain_outbox` 消费，直到回执落地。"""
    exc: dict = {}

    def _producer() -> None:
        try:
            asyncio.run(send("第一句\n第二句", session_key=WECHAT_KEY))
        except BaseException as e:  # noqa: BLE001
            exc["e"] = e

    t = threading.Thread(target=_producer)
    t.start()
    deadline = time.time() + timeout
    while t.is_alive() and time.time() < deadline:
        conn._drain_outbox()
        time.sleep(0.05)
    t.join(timeout=5)
    assert not exc, f"生产者投递失败: {exc.get('e')!r}"


def test_end_to_end_directed_delivery_uses_split_chain_when_enabled(
    host_env, monkeypatch, online_registry
):
    """生产链路端到端：开启 proactive_split ⇒ 宿主 outbox 走拆条链（整段直发即缺陷）。"""
    wc, rp, conn = host_env
    _split_config(monkeypatch, True)
    segments: list[tuple[str, str]] = []
    texts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        conn,
        "send_reply_segments",
        lambda peer, reply, **kw: (
            segments.append((peer, reply))
            or "\n".join(wc.split_reply_for_wechat(reply))
        ),
    )
    monkeypatch.setattr(
        conn,
        "send_text",
        lambda text, to_user="": (texts.append((text, to_user)) or True),
    )

    send = _build_send(plane_getter=lambda: rp)
    _run_directed_through_host(send, conn)

    assert segments == [(PEER, "第一句\n第二句")], (
        "生产定向投递未走拆条链：连接器宿主 outbox 仍整段直发（功能开通但不生效）"
    )
    assert texts == [], "拆条链已受理仍回落整段直发（重复投递）"


def test_end_to_end_directed_delivery_default_is_verbatim_send_text(
    host_env, monkeypatch, online_registry
):
    """零行为变更锚：默认关闭 ⇒ 端到端逐字回落 send_text（单次、原文、同 peer）。"""
    wc, rp, conn = host_env
    _split_config(monkeypatch, False)
    segments: list[tuple[str, str]] = []
    texts: list[tuple[str, str]] = []
    monkeypatch.setattr(
        conn,
        "send_reply_segments",
        lambda peer, reply, **kw: (segments.append((peer, reply)) or reply),
    )
    monkeypatch.setattr(
        conn,
        "send_text",
        lambda text, to_user="": (texts.append((text, to_user)) or True),
    )

    send = _build_send(plane_getter=lambda: rp)
    _run_directed_through_host(send, conn)

    assert texts == [("第一句\n第二句", PEER)]
    assert segments == []

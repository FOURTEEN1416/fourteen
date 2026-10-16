"""主动投递接入「拆条 + 段间节奏」链（拟人度 · FUNCTION_INVENTORY MESSAGE-12）。

覆盖设计契约「主动投递链」切片：

- **零行为变更（默认）**：`proactive_split` 关闭 / 配置模块缺席 / 读异常
  ⇒ 主动投递逐字走 `connector.send_text`（整段直发），与现状一致；
- **开启**：走连接器的公有拆条方法 `send_reply_segments`（契约第 1 步）；受理
  判据**复用主链**的 complete 谓词，不另造语义；
- **未完整受理**（只受理前缀）⇒ `RuntimeError` ⇒ 调度器把 wechat 通道置
  None、不记账不扣配额（与 `send_text` 返回 False 路径同构）；**绝不**
  回退整段直发（避免重复投递），连接器无拆条实现时才回落直发（不误报全失败）。
"""

from __future__ import annotations

import ast
import asyncio
import json
import types
from pathlib import Path

import pytest

WECHAT_KEY = "7:o9cq80wx@im.wechat"
PEER = "o9cq80wx@im.wechat"
RUN_API = Path(__file__).resolve().parent.parent / "api" / "run_api.py"


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
    """最小连接器替身：只暴露投递链用到的面。"""

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
        self.public_used = False
        self._accepted = accepted
        self._send_text_ok = send_text_ok
        if has_public:
            self.send_reply_segments = self._impl_public

    def _impl_public(self, peer, reply, **kw):
        self.public_used = True
        return self._do(peer, reply)

    def _do(self, peer, reply):
        from wechat_direct.wechat_connector import split_reply_for_wechat

        self.segment_calls.append((peer, reply))
        if self._accepted is not None:
            return self._accepted
        # accepted=None ⇒ 模拟「全部段受理」（返回已受理前缀 = 全量重拼）
        return "\n".join(split_reply_for_wechat(reply))

    def send_text(self, text, to_user=""):
        self.text_calls.append((text, to_user))
        return self._send_text_ok


class _RecordingLock:
    def __init__(self, key, log) -> None:
        self._key, self._log = key, log

    def __enter__(self):
        self._log.append(self._key)
        return self

    def __exit__(self, *exc):
        return False


class _LockedConnector(_FakeConnector):
    """带 `_peer_lock` / `_session_key` 的替身（钉「主动接线处补锁」）。"""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.locked: list[str] = []

    def _session_key(self, peer_wxid: str) -> str:
        return f"7:{peer_wxid}"

    def _peer_lock(self, session_key: str):
        return _RecordingLock(session_key, self.locked)


def _point_config_at(monkeypatch, tmp_path, block: dict | None) -> Path:
    """把拟人度真源重定向到临时文件（走真实 `read_humanize` 路径）。"""
    from utils import humanize_config

    path = tmp_path / "scheduler_config.json"
    if block is not None:
        path.write_text(json.dumps({"humanize": block}), encoding="utf-8")
    monkeypatch.setattr(humanize_config, "_CONFIG_PATH", path)
    return path


def _stub_read_humanize(monkeypatch, value) -> None:
    """替换 `read_humanize` 的返回值/行为（容忍分支用）。"""
    from utils import humanize_config

    monkeypatch.setattr(humanize_config, "read_humanize", lambda: value)


def _split_config(monkeypatch, enabled: bool) -> None:
    """用**真实** `HumanizeConfig` 开关拆条；`HumanizeConfig()` 默认即关。"""
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


# ── 配置读取容忍（默认一律回落「整段直发」） ──────────────


def test_default_config_file_yields_off(monkeypatch, tmp_path):
    """零行为变更的锚：真源文件缺席（或 humanize 块缺席）⇒ 关。"""
    import main

    _point_config_at(monkeypatch, tmp_path, None)
    assert main.humanize_proactive_split_enabled() is False


def test_real_config_proactive_split_true_enables(monkeypatch, tmp_path):
    """端到端：真源 `data/scheduler_config.json` 的 humanize.proactive_split 生效。"""
    import main

    _point_config_at(monkeypatch, tmp_path, {"proactive_split": True})
    assert main.humanize_proactive_split_enabled() is True


def test_real_config_proactive_split_non_bool_falls_back(monkeypatch, tmp_path):
    """越界/类型不符 ⇒ 经 `read_humanize` 逐字段回落默认（False）。"""
    import main

    _point_config_at(monkeypatch, tmp_path, {"proactive_split": "yes"})
    assert main.humanize_proactive_split_enabled() is False


def test_config_dict_shaped_tolerated(monkeypatch):
    """读侧容忍 dict 形态（兜底：字段投影改了形态也不炸）。"""
    import main

    _stub_read_humanize(monkeypatch, {"proactive_split": True})
    assert main.humanize_proactive_split_enabled() is True


def test_config_read_exception_defaults_to_off(monkeypatch):
    import main
    from utils import humanize_config

    def _boom():
        raise ValueError("配置损坏")

    monkeypatch.setattr(humanize_config, "read_humanize", _boom)
    assert main.humanize_proactive_split_enabled() is False


def test_config_missing_field_defaults_to_off(monkeypatch):
    import main

    _stub_read_humanize(monkeypatch, types.SimpleNamespace(delay_mode="dynamic"))
    assert main.humanize_proactive_split_enabled() is False


# ── 拆条发送三态 ────────────────────────────────────────


def test_segmented_returns_none_without_connector_support():
    import main

    assert main.send_proactive_segmented(_FakeConnector(has_public=False), PEER, "早") is None


def test_segmented_true_on_complete_acceptance():
    import main

    conn = _FakeConnector()
    assert main.send_proactive_segmented(conn, PEER, "第一句\n第二句") is True
    assert conn.segment_calls == [(PEER, "第一句\n第二句")]


def test_segmented_false_on_prefix_only():
    """只受理前缀（尾段 API 失败/异常 break）⇒ 未完整受理，绝不判成功。"""
    import main

    conn = _FakeConnector(accepted="第一句")
    assert main.send_proactive_segmented(conn, PEER, "第一句\n第二句") is False


def test_segmented_false_on_empty_acceptance():
    import main

    conn = _FakeConnector(accepted="")
    assert main.send_proactive_segmented(conn, PEER, "早") is False


def test_segmented_calls_public_api_only():
    """契约第 1 步：唯一入口是公有 `send_reply_segments`（无私有别名回落）。"""
    import main

    conn = _FakeConnector()
    main.send_proactive_segmented(conn, PEER, "早")
    assert conn.public_used is True
    assert not hasattr(conn, "_send_reply_segments")


def test_segmented_takes_peer_lock_when_available():
    """段方法本身不持锁（connector 契约）⇒ 主动接线处补同一把 peer 锁。"""
    import main

    conn = _LockedConnector()
    main.send_proactive_segmented(conn, PEER, "早")
    assert conn.locked == [f"7:{PEER}"], "主动投递未与主回复链串行（同 peer 竞态）"


# ── 真连接器端到端（受理前缀语义 / 锁实际生效） ────────────


def _real_connector(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    conn = wc.WeChatConnector(SimpleNamespace(), owner_user_id=42)
    conn.token = "test-token"
    monkeypatch.setattr(conn, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(conn, "_save_context_tokens", lambda: None)
    monkeypatch.setattr(wc, "_segment_delay", lambda *a, **k: 0)  # 不真睡
    return wc, conn


def test_real_connector_full_acceptance_is_complete(tmp_path, monkeypatch):
    """真 `WeChatConnector.send_reply_segments`：两段全受理 ⇒ 完整受理。"""
    import main

    wc, conn = _real_connector(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        wc, "_send_text", lambda **kw: calls.append(kw["text"]) or {"ret": 0},
    )
    assert main.send_proactive_segmented(conn, "peer", "第一句\n第二句") is True
    assert calls == ["第一句", "第二句"]


def test_real_connector_prefix_only_is_incomplete(tmp_path, monkeypatch):
    """真连接器第二段被 API 拒 ⇒ 只受理前缀 ⇒ 未完整受理（防记账尾句）。"""
    import main

    wc, conn = _real_connector(tmp_path, monkeypatch)
    calls: list[str] = []

    def send(**kw):
        calls.append(kw["text"])
        return {"ret": 0 if len(calls) == 1 else -2}

    monkeypatch.setattr(wc, "_send_text", send)
    assert main.send_proactive_segmented(conn, "peer", "第一句\n第二句") is False
    assert calls == ["第一句", "第二句"], "失败段之后应停止（不重试、不续发）"


def test_real_connector_holds_peer_lock_during_segments(tmp_path, monkeypatch):
    """锁契约单一口径：调用方 `main.send_proactive_segmented` 持 `_peer_lock`。"""
    from concurrent.futures import ThreadPoolExecutor

    import main

    wc, conn = _real_connector(tmp_path, monkeypatch)
    probe: list[bool] = []

    def send(**kw):
        def _try() -> bool:
            lock = conn._peer_lock(conn._session_key(kw["to"]))
            got = lock.acquire(blocking=False)
            if got:
                lock.release()
            return got

        with ThreadPoolExecutor(max_workers=1) as pool:
            probe.append(pool.submit(_try).result(timeout=3))
        return {"ret": 0}

    monkeypatch.setattr(wc, "_send_text", send)
    assert main.send_proactive_segmented(conn, "peer", "第一句\n第二句") is True
    assert probe and all(p is False for p in probe), (
        "段投递期间未持有 peer 锁：与主回复链对同一 peer 竞态"
    )


# ── direct_send_proactive（both 入口共用的直发入口） ──────


def test_direct_send_default_uses_send_text(monkeypatch):
    import main

    _split_config(monkeypatch, False)
    conn = _FakeConnector()
    assert main.direct_send_proactive(conn, PEER, "早") is True
    assert conn.text_calls == [("早", PEER)]
    assert conn.segment_calls == []


def test_direct_send_split_uses_segments(monkeypatch):
    import main

    _split_config(monkeypatch, True)
    conn = _FakeConnector()
    assert main.direct_send_proactive(conn, PEER, "早") is True
    assert conn.segment_calls == [(PEER, "早")]
    assert conn.text_calls == []


def test_direct_send_falls_back_when_connector_unsupported(monkeypatch):
    """开启了拟人度但连接器没有拆条实现 ⇒ 回落直发，不误报失败。"""
    import main

    _split_config(monkeypatch, True)
    conn = _FakeConnector(has_public=False)
    assert main.direct_send_proactive(conn, PEER, "早") is True
    assert conn.text_calls == [("早", PEER)]


def test_direct_send_reports_incomplete_acceptance(monkeypatch):
    import main

    _split_config(monkeypatch, True)
    conn = _FakeConnector(accepted="第一句")
    assert main.direct_send_proactive(conn, PEER, "第一句\n第二句") is False


# ── main.py 定向投递接线 ────────────────────────────────


def test_targeted_default_keeps_send_text_unchanged(monkeypatch):
    """零行为变更：默认配置下定向投递与现状逐字一致（单次 send_text）。"""
    _split_config(monkeypatch, False)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("早", session_key=WECHAT_KEY))
    assert conn.text_calls == [("早", PEER)]
    assert conn.segment_calls == []


def test_targeted_split_complete_returns(monkeypatch):
    _split_config(monkeypatch, True)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("第一句\n第二句", session_key=WECHAT_KEY))
    assert conn.segment_calls == [(PEER, "第一句\n第二句")]
    assert conn.text_calls == []


def test_targeted_split_incomplete_raises(monkeypatch):
    """未完整受理 ⇒ RuntimeError（通道置 None、不记账），不静默判成功。"""
    _split_config(monkeypatch, True)
    conn = _FakeConnector(accepted="第一句")
    send = _wire_wechat_send(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="未完整受理"):
        asyncio.run(send("第一句\n第二句", session_key=WECHAT_KEY))
    assert conn.text_calls == [], "未完整受理不得回退整段直发（会重复投递）"


def test_targeted_split_unsupported_connector_falls_back(monkeypatch):
    """开启了拟人度但连接器无拆条实现 ⇒ 回落整段直发，不误报全失败。"""
    _split_config(monkeypatch, True)
    conn = _FakeConnector(has_public=False)
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("早", session_key=WECHAT_KEY))
    assert conn.text_calls == [("早", PEER)]


def test_targeted_send_text_failure_still_raises(monkeypatch):
    """现行为不变：整段直发失败照旧 RuntimeError（通道置 None 语义链不变）。"""
    _split_config(monkeypatch, False)
    conn = _FakeConnector(send_text_ok=False)
    send = _wire_wechat_send(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="微信定向投递失败"):
        asyncio.run(send("早", session_key=WECHAT_KEY))


def test_targeted_unparsable_session_key_still_rejected(monkeypatch):
    _split_config(monkeypatch, True)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="解析不出 peer"):
        asyncio.run(send("早", session_key=":"))


def test_broadcast_branch_unchanged(monkeypatch):
    """无 session_key 的系统级广播分支不动（拆条不介入）。"""
    _split_config(monkeypatch, True)
    conn = _FakeConnector()
    send = _wire_wechat_send(monkeypatch, conn)
    asyncio.run(send("系统广播"))
    assert conn.text_calls == [("系统广播", "")]
    assert conn.segment_calls == []


# ── api/run_api.py 装配形态（AST 钉住：不 import 重入口） ──


def _factory_source(name: str) -> str:
    src = RUN_API.read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"run_api 缺少函数 {name}")


def test_run_api_direct_send_goes_through_shared_helper():
    src = _factory_source("_wechat_sender_factory")
    assert "direct_send_proactive" in src, (
        "run_api 微信直发未接入「拆条 + 节奏」链（唯一真源 main.direct_send_proactive）"
    )
    assert "conn.send_text(" not in src, "直发绕过了共享入口"


def test_run_api_helper_import_is_wired():
    src = RUN_API.read_text(encoding="utf-8")
    assert "direct_send_proactive" in src.split("logger = logging.getLogger")[0], (
        "未从 main 导入 direct_send_proactive"
    )


# ── 生产投递点（连接器宿主 outbox）运行时可达性 ────────────
#
# 2026-10-16 复审 P1：上面两条 AST 断言只证明 run_api **源码里出现**该名字，
# 不证明运行时可达 —— 生产（`deploy/ai-girlfriend.service` = uvicorn 4 worker）
# 的定向主动投递是「run_api 入队 + 等回执」→ **真实发送在连接器宿主的
# `_drain_outbox`**；旧实现那里直调 `self.send_text`，拆条链在生产等于没接。
# 以下两条**行为级**断言钉住「kind=proactive 经唯一真源投递、其它 kind 不变」。


def _outbox_connector(tmp_path, monkeypatch, *, owner=7, slot=0):
    """真连接器 + 控制面 DB 沙箱（走 `_drain_outbox` 的真实代码路径）。"""
    from proactive import runtime_plane as rp
    from wechat_direct import channel_paths
    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    sd = tmp_path / "sess" / str(owner) / f"slot{slot}"
    sd.mkdir(parents=True, exist_ok=True)
    rp.set_db_path(tmp_path / "plane.db")
    conn = wc.WeChatConnector(
        None, owner_user_id=owner, slot=slot, session_dir=sd,
        credentials_path=str(sd / "credentials.json"),
        state_path=str(sd / "state.json"),
        qrcode_path=str(sd / "qrcode.json"),
        context_tokens_path=str(sd / "context_tokens.json"),
    )
    conn.token = "tok"
    monkeypatch.setattr(conn, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(conn, "_save_context_tokens", lambda: None)
    monkeypatch.setattr(wc, "_segment_delay", lambda *a, **k: 0)
    return wc, rp, conn


def test_drain_outbox_proactive_goes_through_shared_humanize_chain(tmp_path, monkeypatch):
    """`kind=proactive` 且拆条开启 ⇒ outbox 消费走拆条链（不是整段 send_text）。"""
    _split_config(monkeypatch, True)
    wc, rp, conn = _outbox_connector(tmp_path, monkeypatch)
    segment_calls: list[tuple[str, str]] = []
    text_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        conn, "send_reply_segments",
        lambda peer, reply, **kw: (
            segment_calls.append((peer, reply))
            or "\n".join(wc.split_reply_for_wechat(reply))
        ),
    )
    monkeypatch.setattr(
        conn, "send_text",
        lambda text, to_user="": (text_calls.append((text, to_user)) or True),
    )
    cid = rp.enqueue_send(
        kind="proactive", channel="wechat", session_key="7:wx_b",
        owner_id=7, peer="wx_b", message="第一句\n第二句",
    )
    conn._drain_outbox()
    assert segment_calls == [("wx_b", "第一句\n第二句")], (
        "生产投递点（连接器宿主 outbox）未接入拆条链 —— 开启 proactive_split 后"
        "定向主动投递仍整段直发（功能开通但不生效）"
    )
    assert text_calls == [], "拆条链已受理，不得再回落整段直发（会重复投递）"
    assert rp.send_status(cid)["status"] == "accepted"


def test_drain_outbox_proactive_default_is_unchanged_send_text(tmp_path, monkeypatch):
    """零行为变更锚：默认关闭 ⇒ outbox 行为与旧实现逐字一致（单次 send_text）。"""
    _split_config(monkeypatch, False)
    wc, rp, conn = _outbox_connector(tmp_path, monkeypatch)
    segment_calls: list[tuple[str, str]] = []
    text_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        conn, "send_reply_segments",
        lambda peer, reply, **kw: (segment_calls.append((peer, reply)) or reply),
    )
    monkeypatch.setattr(
        conn, "send_text",
        lambda text, to_user="": (text_calls.append((text, to_user)) or True),
    )
    cid = rp.enqueue_send(
        kind="proactive", channel="wechat", session_key="7:wx_b",
        owner_id=7, peer="wx_b", message="早",
    )
    conn._drain_outbox()
    assert text_calls == [("早", "wx_b")]
    assert segment_calls == []
    assert rp.send_status(cid)["status"] == "accepted"


def test_drain_outbox_non_proactive_kind_keeps_send_text(tmp_path, monkeypatch):
    """`reminder` 等非主动 kind 不属拆条语义 —— 路径不动（即便开关开启）。"""
    _split_config(monkeypatch, True)
    wc, rp, conn = _outbox_connector(tmp_path, monkeypatch)
    segment_calls: list[tuple[str, str]] = []
    text_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        conn, "send_reply_segments",
        lambda peer, reply, **kw: (segment_calls.append((peer, reply)) or reply),
    )
    monkeypatch.setattr(
        conn, "send_text",
        lambda text, to_user="": (text_calls.append((text, to_user)) or True),
    )
    rp.enqueue_send(
        kind="reminder", channel="wechat", session_key="7:wx_b",
        owner_id=7, peer="wx_b", message="记得吃药",
    )
    conn._drain_outbox()
    assert text_calls == [("记得吃药", "wx_b")]
    assert segment_calls == []

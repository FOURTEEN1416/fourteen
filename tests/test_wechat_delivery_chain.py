"""微信投递结果链路回归（2026-09-19 生产事故）。

事故：用户报「没有收到」，但日志连续多日显示「主动消息已投递: wechat」。
实测底层接口返回 `{"ret": -2, "errmsg": "prepare failed"}`（会话窗口失效），
而**四层代码先后把失败报成成功**：
  ① `_post_api` 只看 HTTP 状态，从不看业务码 `ret`；超时还返回 `{"ret": 0}` 伪装成功
  ② `send_text` 丢弃返回字典，不抛异常就打「微信主动发送成功」
  ③ `run_api._send` 丢弃 `send_text` 的 bool
  ④ `_send_to_all` 只要不抛异常就 `sent=True`
  ⑤ console / 旧 send_message_func 都是 logger.info 包装，必然"成功"
本文件锁定修复后的五个不变量。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

# ═══════════════════════════════════════════════════════════════
#  ① 业务返回码判定
# ═══════════════════════════════════════════════════════════════

def test_api_ok_accepts_success():
    from wechat_direct.wechat_connector import _api_ok

    assert _api_ok({"ret": 0}) == (True, "")
    assert _api_ok({"ret": 0, "msgs": []}) == (True, "")
    assert _api_ok({}) == (True, "")


def test_api_ok_rejects_production_error():
    """生产实测原文：空 context_token 发送时接口返回 ret=-2 prepare failed。"""
    from wechat_direct.wechat_connector import _api_ok

    ok, errmsg = _api_ok({"ret": -2, "errmsg": "prepare failed"})
    assert ok is False
    assert "prepare failed" in errmsg
    assert "ret=-2" in errmsg


def test_api_ok_rejects_timeout_marker():
    """超时不得再被当成成功（旧实现返回 {"ret": 0} 伪装成功）。"""
    from wechat_direct.wechat_connector import _api_ok

    ok, errmsg = _api_ok({"ret": 0, "msgs": [], "timeout": True})
    assert ok is False
    assert "超时" in errmsg


def test_api_ok_rejects_non_dict():
    from wechat_direct.wechat_connector import _api_ok

    ok, _ = _api_ok(None)
    assert ok is False
    ok, _ = _api_ok("boom")
    assert ok is False


def test_post_api_timeout_is_marked():
    """_post_api 超时分支必须带 timeout 标记（且保留 ret/msgs 兼容轮询侧）。"""
    import requests

    from wechat_direct import wechat_connector as wc

    def _boom(*a, **kw):
        raise requests.exceptions.Timeout("simulated")

    orig = wc.requests.post
    wc.requests.post = _boom
    try:
        resp = wc._post_api("ilink/bot/sendmessage", {}, token="t")
    finally:
        wc.requests.post = orig

    assert resp.get("timeout") is True
    assert resp.get("msgs") == []          # 轮询侧仍可安全读取


# ═══════════════════════════════════════════════════════════════
#  ② send_text 不再谎报成功
# ═══════════════════════════════════════════════════════════════

class _FakeUserMgr:
    def get_bound_wxids(self):
        return ["u1@im.wechat"]


def _connector(tmp_path, monkeypatch, token="tok"):
    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(_FakeUserMgr())
    c.token = token
    c._context_tokens = {"u1@im.wechat": {"token": "ctx-tok", "ts": time.time()}}
    return c


def test_send_text_returns_false_on_api_error(tmp_path, monkeypatch):
    """核心回归：接口返回 ret<0 时 send_text 必须返回 False。"""
    from wechat_direct import wechat_connector as wc

    c = _connector(tmp_path, monkeypatch)
    monkeypatch.setattr(
        wc, "_send_text", lambda **kw: {"ret": -2, "errmsg": "prepare failed"}
    )
    assert c.send_text("你好", to_user="u1@im.wechat") is False


def test_send_text_returns_true_on_success(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    c = _connector(tmp_path, monkeypatch)
    monkeypatch.setattr(wc, "_send_text", lambda **kw: {"ret": 0})
    assert c.send_text("你好", to_user="u1@im.wechat") is True


def test_send_text_fails_without_target_or_token(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch, token="")
    assert c.send_text("你好", to_user="u1@im.wechat") is False


def test_send_image_and_emoji_check_result(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    c = _connector(tmp_path, monkeypatch)
    monkeypatch.setattr(
        wc, "_send_image_message", lambda **kw: {"ret": -2, "errmsg": "prepare failed"}
    )
    monkeypatch.setattr(
        wc, "_send_emoji_message", lambda **kw: {"ret": -2, "errmsg": "prepare failed"}
    )
    assert c.send_image(b"\x89PNG", to_user="u1@im.wechat") is False
    assert c.send_emoji("md5", to_user="u1@im.wechat") is False


# ═══════════════════════════════════════════════════════════════
#  ③ context_token 持久化与 TTL
# ═══════════════════════════════════════════════════════════════

def test_context_tokens_roundtrip(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    path = str(tmp_path / "ctx.json")
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", path)

    c = wc.WeChatConnector(_FakeUserMgr())
    c._context_tokens = {"u1@im.wechat": {"token": "ctx-tok", "ts": time.time()}}
    c._save_context_tokens()

    with open(path, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["u1@im.wechat"]["token"] == "ctx-tok"

    c2 = wc.WeChatConnector(_FakeUserMgr())
    assert c2._get_context_token("u1@im.wechat") == "ctx-tok"


def test_context_tokens_expired_skipped_on_load(tmp_path, monkeypatch):
    """过期条目不得恢复（否则会发出必然 prepare failed 的请求）。"""
    from wechat_direct import wechat_connector as wc

    path = tmp_path / "ctx.json"
    path.write_text(
        json.dumps({
            "fresh@im.wechat": {"token": "new", "ts": time.time()},
            "stale@im.wechat": {"token": "old", "ts": time.time() - wc._CONTEXT_TOKENS_TTL - 10},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(path))

    c = wc.WeChatConnector(_FakeUserMgr())
    assert c._get_context_token("fresh@im.wechat") == "new"
    assert c._get_context_token("stale@im.wechat") == ""


def test_get_context_token_returns_empty_when_expired_in_memory(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    c = _connector(tmp_path, monkeypatch)
    c._context_tokens["u1@im.wechat"] = {
        "token": "old", "ts": time.time() - wc._CONTEXT_TOKENS_TTL - 1,
    }
    assert c._get_context_token("u1@im.wechat") == ""


def test_context_tokens_missing_file_is_silent(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "nope.json"))
    c = wc.WeChatConnector(_FakeUserMgr())
    assert c._context_tokens == {}


# ═══════════════════════════════════════════════════════════════
#  ④⑤ _send_to_all：仅日志通道不得计入送达
# ═══════════════════════════════════════════════════════════════

def _scheduler():
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler(ase_engine=None)
    # 投递用例脱离真实墙钟：无效小时窗 → start<end 且 hour 永不落入
    sched._quiet_hours = (25, 26)
    return sched


def test_send_to_all_console_only_is_not_delivery(tmp_path):
    """只有 console 通道时返回 False —— 写日志不算送达（旧实现返回 True）。"""
    import asyncio

    sched = _scheduler()
    sched._channel_instances = {"console": lambda msg: None}
    sched._send = lambda msg: None

    assert asyncio.run(sched._send_to_all("hi")) is False


def test_send_to_all_wechat_failure_not_masked_by_console():
    """微信失败 + console 已注册 → 仍必须返回 False（生产事故的核心场景）。"""
    import asyncio

    sched = _scheduler()

    def _wechat_fail(msg):
        raise RuntimeError("微信投递失败：1 个绑定目标全部未送达")

    sched._channel_instances = {
        "wechat": _wechat_fail,
        "console": lambda msg: None,
    }
    sched._send = lambda msg: None

    assert asyncio.run(sched._send_to_all("hi")) is False


def test_send_to_all_wechat_success_is_delivery():
    import asyncio

    sched = _scheduler()
    delivered: list[str] = []
    sched._channel_instances = {"wechat": delivered.append}
    sched._send = lambda msg: None

    assert asyncio.run(sched._send_to_all("hi")) is True
    assert delivered == ["hi"]


def test_send_to_all_falls_back_to_websocket_when_wechat_fails():
    import asyncio

    sched = _scheduler()
    got: list[str] = []

    def _wechat_fail(msg):
        raise RuntimeError("boom")

    sched._channel_instances = {"wechat": _wechat_fail, "websocket": got.append}
    sched._send = lambda msg: None

    assert asyncio.run(sched._send_to_all("hi")) is True
    assert got == ["hi"]


def test_send_to_all_quiet_hours_returns_false():
    import asyncio

    from proactive.ase_engine import _local_now

    sched = _scheduler()
    h = _local_now().hour
    sched._quiet_hours = (h, (h + 1) % 24)
    sched._channel_instances = {"wechat": lambda msg: None}

    assert asyncio.run(sched._send_to_all("hi")) is False


# ═══════════════════════════════════════════════════════════════
#  端到端：投递失败 → 不提交配额
# ═══════════════════════════════════════════════════════════════

def test_delivery_failure_does_not_commit_quota(monkeypatch, tmp_path):
    """把④⑤连起来：微信报失败时 ASE 配额必须保持不动。

    批6b 项7：旧 `_check_ase_global` 全局路径已删（非 ASEHub 注入直接判装配
    缺陷跳过），本用例随之迁移到生产真实装配——ASEHub + 真 ASEEngine +
    真 _deliver 通道链，钉住「通道失败 → commit_sent 从未发生」。
    """
    import proactive.ase_hub as hub_mod
    import proactive.llm_proactive as lp
    import shisi.agent_plane.runtime as rt
    from proactive.ase_engine import ASEEngine, _local_now
    from proactive.ase_hub import ASEHub
    from proactive.scheduler import ProactiveScheduler

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")
    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "load_persona_hint", lambda cid="": "")
    monkeypatch.setattr(
        lp, "decide_proactive",
        lambda llm, ctx: {
            "should_contact": True, "reason": "test",
            "wait_minutes": None, "message": "在干嘛呀",
        },
    )
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: None)

    hub = ASEHub(
        lambda user_key="", state_path="", **kw: ASEEngine(
            state_path=state_path, generation_mode="template",
        )
    )
    key = "4:peer@im.wechat"
    engine = hub.get(key)
    engine._last_reset_date = _local_now().date()
    engine._last_proactive_time = None

    sched = ProactiveScheduler(ase_engine=hub)
    sched.reload_config = lambda: None
    sched._collect_ase_user_keys = lambda: [key]
    sched._resolve_proactive_llm = lambda eng=None: object()
    h = _local_now().hour
    sched._quiet_hours = ((h + 2) % 24, (h + 3) % 24)  # 非静默，放行到投递层
    # 微信通道真实存在但投递失败；console 也在（旧实现下会被 console 掩盖）
    sched._channel_instances = {
        "wechat": lambda msg, session_key=None: (_ for _ in ()).throw(RuntimeError("prepare failed")),
        "console": lambda msg: None,
    }
    sched._send = lambda msg: None

    sched._check_ase()

    assert engine._daily_message_count == 0, "未送达不得消耗配额"


# ═══════════════════════════════════════════════════════════════
#  ⑥ websocket 零客户端不得算作送达（生产 13:33 实证：消息只进了
#     console 日志却被记成「已送达: websocket」并提交配额）
# ═══════════════════════════════════════════════════════════════

def _ws_server(clients: set):
    from api.websocket_server import WebSocketServer

    srv = WebSocketServer.__new__(WebSocketServer)  # 跳过 __init__（不起线程）
    srv._clients = clients
    srv._client_lock = asyncio.Lock()
    return srv


class _FakeWS:
    def __init__(self, ok: bool = True):
        self.ok = ok

    async def send(self, msg: str):
        if not self.ok:
            raise RuntimeError("connection closed")


def test_websocket_proactive_zero_clients_raises():
    srv = _ws_server(set())
    with pytest.raises(RuntimeError, match="未送达任何客户端"):
        asyncio.run(srv.broadcast_proactive("hi"))


def test_websocket_proactive_all_clients_fail_raises():
    srv = _ws_server({_FakeWS(ok=False)})
    with pytest.raises(RuntimeError, match="未送达任何客户端"):
        asyncio.run(srv.broadcast_proactive("hi"))


def test_websocket_proactive_success_does_not_raise():
    srv = _ws_server({_FakeWS(ok=True)})
    asyncio.run(srv.broadcast_proactive("hi"))  # 不抛即通过


def test_send_to_all_skipped_channel_is_logged(caplog):
    """通道未就绪（instance=None）必须打日志，不得静默跳过。"""
    sched = _scheduler()
    sched._channel_instances = {}  # wechat / websocket 均未就绪
    sched._send = lambda msg: None

    assert asyncio.run(sched._send_to_all("hi")) is False
    assert any("未就绪" in r.message for r in caplog.records)

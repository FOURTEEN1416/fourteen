"""死会话根治（2026-11-21）：-14 重登有界化 + needs_reauth 终态治理。

背景（生产审计结论）：
- 旧 ``_poll_loop`` 遇 -14 连续 3 次后调用 ``self.login()`` —— 该实现会在
  无可用凭证时生成二维码并等待扫码（``QR_LOGIN_TIMEOUT_S=480``），登录失败
  再 ``sleep(300)`` 后 ``continue``，循环没有轮数上限；``run()`` 只在
  ``_poll_loop`` 返回后写 ``disconnected``（循环永不返回）→ 生产 owner
  连续 284 次 -14、二维码每约 13 分钟刷新一次、状态仍显示 connected。
- 本文件钉死修复后的不变量：
  ① -14 达 ``MAX_AUTH_FAILURES`` 后走**非交互** ``_auto_reauth()``（不生成
     二维码、不等待扫码），最多 ``MAX_REAUTH_ROUNDS`` 轮；
  ② 全部失败进入 ``needs_reauth`` 终态：清 token / 删无效凭证 / 写状态 /
     写 agent_plane ``channel_state`` 事件 / 释放 presence 与通道锁；
  ③ ``run()`` 与 ``stop()`` 不得把 ``needs_reauth`` 覆盖回普通状态；
  ④ 状态读面返回 ``needs_reauth`` 与重新扫码引导；在线判定叠加
     ``token_window_state``（死 token 不得显示在线）；
  ⑤ 回复链/媒体链对 ``needs_reauth``/dead 会话有确定性 auth 闸。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from proactive import runtime_plane as rp
from wechat_direct import channel_paths
from wechat_direct import wechat_connector as wc
from wechat_direct.connector_registry import ConnectorRegistry, get_registry
from wechat_direct.wechat_connector import load_session_state, save_session_state


@pytest.fixture
def chan(tmp_path, monkeypatch):
    """通道磁盘根重定向到 tmp（与 test_w3_desired_state_channel 同型）。"""
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        channel_paths, "sessions_root", lambda: tmp_path / "data" / "wechat_sessions"
    )
    return tmp_path


def _mk_conn(tmp: Path, uid: int = 27, slot: int = 0) -> wc.WeChatConnector:
    """构造带独立 session_dir 的 owner 连接器（credentials 归本用例所有）。"""
    sd = tmp / "sess" / str(uid) / f"slot{slot}"
    sd.mkdir(parents=True, exist_ok=True)
    return wc.WeChatConnector(
        None,
        owner_user_id=uid,
        slot=slot,
        session_dir=sd,
        credentials_path=str(sd / "credentials.json"),
        state_path=str(sd / "state.json"),
        qrcode_path=str(sd / "qrcode.json"),
        context_tokens_path=str(sd / "context_tokens.json"),
    )


def _write_credentials(conn: wc.WeChatConnector, token: str = "dead-token") -> Path:
    path = Path(conn._credentials_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "token": token,
                "base_url": wc.DEFAULT_BASE_URL,
                "bot_id": "bot_dead",
                "user_id": "wx_owner@im.wechat",
                "owner_user_id": conn.owner_user_id,
                "slot": conn.slot,
            }
        ),
        encoding="utf-8",
    )
    return path


# ═══════════════════════════════════════════════════════════════
#  ① -14 有界 → needs_reauth 终态（不再 sleep(300) 无限循环）
# ═══════════════════════════════════════════════════════════════


def test_negative_14_hits_limit_and_enters_needs_reauth(chan, monkeypatch):
    """连续 -14 达 3 次：非交互重认证失败 → 终态，循环退出。"""
    conn = _mk_conn(chan, uid=27)
    conn.token = "dead-token"
    conn._set_auth_state(wc.AUTH_STATE_CONNECTED)
    creds = _write_credentials(conn)

    login_calls: list[bool] = []

    def fake_updates(*_a, **_k):
        return {"ret": -14, "errcode": -14}

    def fake_login(interactive: bool = True):
        login_calls.append(bool(interactive))
        return False

    monkeypatch.setattr(wc, "_get_updates", fake_updates)
    monkeypatch.setattr(wc, "RETRY_DELAY", 0)
    monkeypatch.setattr(wc, "BACKOFF_DELAY", 0)
    monkeypatch.setattr(wc, "REAUTH_BACKOFF_S", (0, 0, 0))
    monkeypatch.setattr(conn, "login", fake_login)

    conn._poll_loop()

    assert conn._stop is True, "终态必须置 _stop 让 run() 退出（旧实现永不返回）"
    assert conn.auth_state == wc.AUTH_STATE_NEEDS_REAUTH
    assert conn.token == ""
    assert login_calls == [False, False, False], (
        "自动重认证必须是 login(interactive=False) 且最多 MAX_REAUTH_ROUNDS 轮"
    )

    st = load_session_state(27, 0)
    assert st["connected"] is False
    assert st["status"] == "needs_reauth"
    assert st["auth_state"] == "needs_reauth"
    assert st["reauth_attempts"] == wc.MAX_REAUTH_ROUNDS
    assert st["auth_failures"] >= wc.MAX_AUTH_FAILURES
    assert st["reauth_required_at"] > 0
    assert "session_expired" in st["last_error"]
    assert not creds.exists(), "终态必须删除已确认无效的 credentials"


def test_poll_loop_does_not_sleep_300_or_auto_open_qr(chan, monkeypatch):
    """旧路径的 sleep(300) + 自动 login() 扫码必须不复存在。"""
    conn = _mk_conn(chan, uid=40)
    conn.token = "dead-token"
    conn._set_auth_state(wc.AUTH_STATE_CONNECTED)

    def boom(*_a, **_k):
        raise AssertionError("自动重认证路径不得生成/轮询二维码")

    monkeypatch.setattr(wc, "_fetch_qr_code", boom)
    monkeypatch.setattr(wc, "_poll_qr_status", boom)
    monkeypatch.setattr(wc, "_get_updates", lambda *_a, **_k: {"ret": -14, "errcode": -14})
    monkeypatch.setattr(wc, "RETRY_DELAY", 0)
    monkeypatch.setattr(wc, "REAUTH_BACKOFF_S", (0, 0, 0))

    slept: list[float] = []
    real_sleep = time.sleep

    def spy_sleep(seconds: float):
        slept.append(float(seconds))
        real_sleep(0)

    monkeypatch.setattr(wc.time, "sleep", spy_sleep)

    conn._poll_loop()

    assert conn.auth_state == wc.AUTH_STATE_NEEDS_REAUTH
    assert 300 not in slept, "旧实现失败后 sleep(300) 无限重试，必须删除"


# ═══════════════════════════════════════════════════════════════
#  ② auto_reauth：非交互探测，不生成二维码
# ═══════════════════════════════════════════════════════════════


def test_auto_reauth_probes_credentials_without_qr(chan, monkeypatch):
    conn = _mk_conn(chan, uid=28)
    _write_credentials(conn)
    monkeypatch.setattr(wc, "REAUTH_BACKOFF_S", (0, 0, 0))

    def boom(*_a, **_k):
        raise AssertionError("login(interactive=False) 不得生成或轮询二维码")

    monkeypatch.setattr(wc, "_fetch_qr_code", boom)
    monkeypatch.setattr(wc, "_poll_qr_status", boom)
    monkeypatch.setattr(wc, "_get_updates", lambda *_a, **_k: {"ret": -14})

    assert conn._auto_reauth() is False
    assert conn.reauth_attempts == wc.MAX_REAUTH_ROUNDS


def test_login_interactive_false_never_writes_qrcode(chan, monkeypatch):
    conn = _mk_conn(chan, uid=41)
    qrcode_path = channel_paths.qrcode_path(41, 0)
    monkeypatch.setattr(wc, "REAUTH_BACKOFF_S", (0, 0, 0))

    called = {"fetch": 0}

    def fetch(*_a, **_k):
        called["fetch"] += 1
        return {"qrcode": "q", "qrcode_img_content": "img"}

    monkeypatch.setattr(wc, "_fetch_qr_code", fetch)
    assert conn.login(interactive=False) is False
    assert called["fetch"] == 0
    assert not qrcode_path.exists()


# ═══════════════════════════════════════════════════════════════
#  ③ 终态释放 presence / 通道锁 / 注册表对象
# ═══════════════════════════════════════════════════════════════


def test_enter_needs_reauth_releases_presence_and_lock(chan):
    uid = 29
    conn = _mk_conn(chan, uid=uid)
    # 模拟 registry 持有本对象与 poll lock fd（Windows 下 fd=-1 的 close 失败会被 suppress）
    reg = get_registry()
    reg._connectors[(uid, 0)] = conn
    reg._poll_lock_fds[(uid, 0)] = -1
    rp.heartbeat(uid, 0, pid="test-host")
    assert rp.presence_live(uid, 0)

    conn._enter_needs_reauth(error="session_expired ret=-14")

    assert not rp.presence_live(uid, 0), "终态必须释放 presence 租约"
    assert (uid, 0) not in reg._connectors, "终态必须从 registry 摘除，避免被当作活宿主"
    assert (uid, 0) not in reg._poll_lock_fds, "终态必须释放通道 flock"


def test_enter_needs_reauth_writes_channel_state_event(chan):
    from shisi.agent_plane.event_ledger import EventLedger, set_default_ledger

    ledger = EventLedger(chan / "agent_plane.db")
    set_default_ledger(ledger)
    try:
        conn = _mk_conn(chan, uid=36)
        conn._enter_needs_reauth(error="session_expired ret=-14")
        rows = ledger.query(session_key="channel:36:0")
    finally:
        set_default_ledger(None)

    assert len(rows) == 1
    assert rows[0].event_type == "channel_state"
    assert rows[0].payload["state"] == "needs_reauth"
    assert rows[0].payload["owner_user_id"] == 36
    assert rows[0].payload["slot"] == 0


# ═══════════════════════════════════════════════════════════════
#  ④ run()/stop() 不得覆盖 needs_reauth
# ═══════════════════════════════════════════════════════════════


def test_run_does_not_overwrite_needs_reauth(chan, monkeypatch):
    conn = _mk_conn(chan, uid=30)
    conn._outbox_thread_started = True  # 不启动后台 outbox 线程
    monkeypatch.setattr(conn, "login", lambda interactive=True: True)

    def fake_poll():
        conn._enter_needs_reauth(error="session_expired ret=-14")

    monkeypatch.setattr(conn, "_poll_loop", fake_poll)
    conn.run()

    st = load_session_state(30, 0)
    assert st["status"] == "needs_reauth", "run() 尾段不得把终态覆盖为 disconnected"
    assert st["connected"] is False


def test_stop_does_not_overwrite_needs_reauth(chan):
    conn = _mk_conn(chan, uid=42)
    conn._enter_needs_reauth(error="session_expired ret=-14")
    conn.stop()
    st = load_session_state(42, 0)
    assert st["status"] == "needs_reauth"


def test_run_without_interactive_credentials_enters_terminal(chan, monkeypatch):
    """restore_on_boot 自动恢复不得自动弹二维码：无有效凭证即终态。"""
    conn = _mk_conn(chan, uid=43)
    assert conn.allow_interactive_login is False
    monkeypatch.setattr(conn, "login", lambda interactive=True: False)
    conn.run()
    st = load_session_state(43, 0)
    assert st["status"] == "needs_reauth"


# ═══════════════════════════════════════════════════════════════
#  ⑤ 状态读面：needs_reauth 字段 + token 窗口叠加
# ═══════════════════════════════════════════════════════════════


def test_status_api_exposes_needs_reauth_with_guidance(monkeypatch):
    from api.routers import wechat_channel_routes as routes

    state = {
        "connected": False,
        "status": "needs_reauth",
        "auth_state": "needs_reauth",
        "bot_id": "",
        "owner_user_id": 31,
        "slot": 0,
        "uptime_seconds": 0,
        "messages_today": 0,
        "reconnect_attempts": 0,
        "last_activity": None,
        "last_error": "session_expired ret=-14",
        "reauth_required_at": 1700000000.0,
        "reauth_attempts": 3,
        "auth_failures": 3,
        "token_window_state": "dead",
    }

    class _Reg:
        def primary_status(self, _uid):
            return dict(state)

        def status_for_user(self, _uid):
            return [dict(state)]

    class _Result:
        def scalar_one_or_none(self):
            return None

    class _DB:
        def add(self, _obj):
            return None

        async def execute(self, _stmt):
            return _Result()

        async def commit(self):
            return None

    monkeypatch.setattr(routes, "get_registry", lambda: _Reg())
    payload = asyncio.run(routes.get_my_channel(user_id=31, db=_DB()))

    assert payload["needs_reauth"] is True
    assert payload["status"] == "needs_reauth"
    assert payload["auth_state"] == "needs_reauth"
    assert payload["reauth_attempts"] == 3
    assert "重新扫码" in payload["message"]
    assert payload["channels"][0]["needs_reauth"] is True


def test_registry_dead_token_window_is_not_online(chan):
    uid = 32
    save_session_state(
        uid, 0, {"connected": True, "bot_id": "bot_dead", "status": "connected"}
    )
    ctx = channel_paths.context_tokens_path(uid, 0)
    ctx.parent.mkdir(parents=True, exist_ok=True)
    ctx.write_text(
        json.dumps({"wx_peer": {"token": "ctx", "ts": time.time() - 25 * 3600}}),
        encoding="utf-8",
    )

    class _DummyConn:
        token = "still-in-memory"
        bot_id = "bot_dead"
        slot = 0

        def get_status(self):
            return {
                "connected": True,
                "uptime_seconds": 10,
                "bot_id": "bot_dead",
                "last_activity": None,
                "messages_today": 0,
                "reconnect_attempts": 0,
            }

    reg = ConnectorRegistry()
    reg._connectors[(uid, 0)] = _DummyConn()
    st = reg.primary_status(uid)

    assert st["connected"] is False, "token 窗口已死时不得显示在线"
    assert st["status"] == "degraded"
    assert st["token_window_state"] == "dead"


# ═══════════════════════════════════════════════════════════════
#  ⑥ 回复链 / 媒体链确定性 auth 闸
# ═══════════════════════════════════════════════════════════════


def test_send_gate_blocks_needs_reauth_session(chan, monkeypatch):
    conn = _mk_conn(chan, uid=33)
    conn.token = "stale"
    conn._terminal_state = True
    conn.auth_state = wc.AUTH_STATE_NEEDS_REAUTH

    calls: list[dict] = []
    monkeypatch.setattr(wc, "_send_text", lambda **kw: calls.append(kw) or {"ret": 0})
    monkeypatch.setattr(wc, "_send_image_message", lambda **kw: calls.append(kw) or {"ret": 0})
    monkeypatch.setattr(wc, "_send_voice_message", lambda **kw: calls.append(kw) or {"ret": 0})
    monkeypatch.setattr(wc, "_send_audio_file_message", lambda **kw: calls.append(kw) or {"ret": 0})
    monkeypatch.setattr(wc, "_send_emoji_message", lambda **kw: calls.append(kw) or {"ret": 0})

    assert conn.send_text("你好", to_user="peer") is False
    assert conn.send_image(b"png", to_user="peer") is False
    assert conn.send_voice(b"silk", to_user="peer") is False
    assert conn.send_audio_file(b"mp3", to_user="peer") is False
    assert conn.send_emoji("md5", to_user="peer") is False
    assert calls == [], "needs_reauth 会话不得触达任何发送 API"


def test_send_gate_blocks_dead_token_window(chan, monkeypatch):
    conn = _mk_conn(chan, uid=34)
    conn.token = "stale"
    conn.auth_state = wc.AUTH_STATE_CONNECTED
    ctx = channel_paths.context_tokens_path(34, 0)
    ctx.parent.mkdir(parents=True, exist_ok=True)
    ctx.write_text(
        json.dumps({"wx_peer": {"token": "ctx", "ts": time.time() - 25 * 3600}}),
        encoding="utf-8",
    )

    calls: list[dict] = []
    monkeypatch.setattr(wc, "_send_text", lambda **kw: calls.append(kw) or {"ret": 0})
    assert conn.send_text("你好", to_user="peer") is False
    assert calls == []


def test_reply_chain_auth_gate_returns_not_accepted(chan):
    conn = _mk_conn(chan, uid=35)
    conn.token = "stale"
    conn._terminal_state = True
    conn.auth_state = wc.AUTH_STATE_NEEDS_REAUTH

    result = conn.send_reply_segments_result("peer", "你好")
    assert result.accepted is False
    assert result.reason == "auth_needs_reauth"


# ═══════════════════════════════════════════════════════════════
#  ⑦ 交互式扫码唯一入口 = 用户 start_login()
# ═══════════════════════════════════════════════════════════════


def test_start_login_marks_interactive(chan, monkeypatch):
    """只有用户主动 start_login() 才允许交互式 login；restore 路径不得弹码。"""
    seen: dict[str, bool] = {}

    class _Dummy:
        slot = 0
        owner_user_id = 50
        allow_interactive_login = False
        _stop = False
        _terminal_state = False

        def run(self):
            seen["allow"] = self.allow_interactive_login

    monkeypatch.setattr(
        "wechat_direct.wechat_connector.WeChatConnector",
        lambda user_manager=None, **kw: _Dummy(),
    )
    monkeypatch.setattr(
        channel_paths,
        "ensure_session_dir",
        lambda uid, s=0: chan / "sessions" / str(uid) / str(s),
    )
    reg = ConnectorRegistry()
    monkeypatch.setattr(
        ConnectorRegistry, "_try_acquire_poll_lock", staticmethod(lambda uid, slot: 0)
    )
    reg.start_login(50, slot=0)
    deadline = time.time() + 2.0
    while "allow" not in seen and time.time() < deadline:
        time.sleep(0.01)
    assert seen.get("allow") is True


# ═══════════════════════════════════════════════════════════════
#  ⑧ 语音出站 auth 闸（needs_reauth / dead token 不烧 TTS 不出站）
# ═══════════════════════════════════════════════════════════════


def _seed_window(uid: int, slot: int = 0, *, ts: float | None = None) -> Path:
    """在沙箱会话根落一份 context token 窗口（默认新鲜）。"""
    path = channel_paths.context_tokens_path(uid, slot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"wx_peer@im.wechat": {"token": "ctx", "ts": time.time() if ts is None else ts}}
        ),
        encoding="utf-8",
    )
    return path


def _patch_voice_sinks(monkeypatch) -> dict:
    """钉住语音出站汇点（发送 API / TTS 合成 / 凭据定位），返回调用记录。"""
    from utils import proactive_voice_delivery as pvd

    calls: dict[str, list] = {"file": [], "voice": [], "synth": [], "creds": []}
    monkeypatch.setattr(
        wc, "_send_audio_file_message",
        lambda **kw: calls["file"].append(kw) or {"message_id": 1},
    )
    monkeypatch.setattr(
        wc, "_send_voice_message",
        lambda **kw: calls["voice"].append(kw) or {"message_id": 1},
    )
    monkeypatch.setattr(
        pvd, "synthesize_audio",
        lambda *a, **k: calls["synth"].append(a) or (b"AUDIO", "mp3"),
    )
    monkeypatch.setattr(
        pvd, "find_wechat_credentials",
        lambda owner, peer, **k: calls["creds"].append((owner, peer)) or {
            "token": "t", "base_url": wc.DEFAULT_BASE_URL,
            "context_token": "ctx", "age_hours": 0.1, "fresh": True, "slot": 0,
        },
    )
    return calls


class _OutboxPlane:
    """最小控制面替身：单条待投命令 + 回执记录（与 voice plane 测试同型）。"""

    def __init__(self, row: dict) -> None:
        self._rows = [row]
        self.completed: list[dict] = []

    def claim_next_send(self, **_kwargs):
        return self._rows.pop(0) if self._rows else None

    def complete_send(self, cid, ok, reason="", slot=None, accepted_text="",
                      claimed_by=None):
        self.completed.append(
            {"id": cid, "ok": ok, "reason": reason, "accepted_text": accepted_text}
        )

    def renew_claim(self, *args, **kwargs):
        return True


def _voice_row(uid: int) -> dict:
    return {
        "id": 77,
        "message": "想你了，语音说给你听",
        "session_key": f"{uid}:wx_peer@im.wechat",
        "peer": "wx_peer@im.wechat",
        "kind": "proactive_voice",
        "owner_id": uid,
        "character_id": "c1",
    }


def _assert_voice_silent(calls: dict, plane: _OutboxPlane) -> None:
    """三条出站路径都不得触达 TTS / 发送 API，且按失败收尾（不 requeue）。"""
    assert calls["synth"] == [], "死会话不得烧 TTS（合成必须发生在闸之后）"
    assert calls["file"] == [] and calls["voice"] == [], "死会话不得触达语音发送 API"
    assert plane.completed == [
        {"id": 77, "ok": False, "reason": "send_api_rejected", "accepted_text": ""}
    ], "宿主分支必须按投递失败收尾（不 requeue，防重试风暴）"


def _drain_blocked_voice(chan, monkeypatch, uid: int, calls: dict) -> None:
    """宿主 _drain_outbox 的 proactive_voice 分支：bot token 在位也要被闸拦下。"""
    conn = _mk_conn(chan, uid=uid)
    conn.token = "bot-token"  # 旧实现只查 bot token，会放行——新闸必须拦
    plane = _OutboxPlane(_voice_row(uid))
    monkeypatch.setattr(wc, "_plane", lambda: plane)
    conn._drain_outbox()
    _assert_voice_silent(calls, plane)


def test_voice_gate_blocks_needs_reauth_session(chan, monkeypatch):
    """needs_reauth 会话：模块级 / 直发 / 宿主三路语音都不出站、不烧 TTS。"""
    uid = 45
    # 落盘终态（直发路径没有连接器实例，只能靠 state.json 判定）
    save_session_state(
        uid, 0, {"status": "needs_reauth", "auth_state": "needs_reauth"}
    )
    _seed_window(uid, 0)  # 即使 token 窗口活着，终态会话也必须拦

    calls = _patch_voice_sinks(monkeypatch)

    # ① 模块级入口（带 owner 上下文）
    ok, reason = wc.send_voice_clip_to(
        "wx_peer@im.wechat", b"mp3", "mp3", context_token="ctx", token="t",
        owner_user_id=uid, slot=0,
    )
    assert (ok, reason) == (False, "auth_blocked")

    # ② 直发路径：合成（TTS）都不发生
    from utils import proactive_voice_delivery as pvd

    monkeypatch.setenv("PROACTIVE_VOICE_VIA_PLANE", "0")
    got = pvd.send_proactive_voice(f"{uid}:wx_peer@im.wechat", "想你了", "c1")
    assert got == (False, "auth_blocked", 0)

    # ③ 宿主 outbox 分支
    conn = _mk_conn(chan, uid=uid)
    conn.token = "bot-token"
    conn._terminal_state = True
    conn.auth_state = wc.AUTH_STATE_NEEDS_REAUTH
    plane = _OutboxPlane(_voice_row(uid))
    monkeypatch.setattr(wc, "_plane", lambda: plane)
    conn._drain_outbox()

    _assert_voice_silent(calls, plane)


def test_voice_gate_blocks_dead_token_window(chan, monkeypatch):
    """context token 窗口死（dead/missing）：语音不出站、不烧 TTS。"""
    uid = 46
    # 会话状态健康（connected），但 25h 没收到过消息 → 窗口 dead
    save_session_state(uid, 0, {"status": "connected", "auth_state": "connected"})
    _seed_window(uid, 0, ts=time.time() - 25 * 3600)

    calls = _patch_voice_sinks(monkeypatch)

    # ① 模块级入口：窗口 dead → 拦
    ok, reason = wc.send_voice_clip_to(
        "wx_peer@im.wechat", b"mp3", "mp3", context_token="ctx", token="t",
        owner_user_id=uid, slot=0,
    )
    assert (ok, reason) == (False, "auth_blocked")

    # ② 模块级入口：窗口 missing（从未有过窗口的 slot）→ 同样按死处理
    ok2, reason2 = wc.send_voice_clip_to(
        "wx_peer@im.wechat", b"mp3", "mp3", context_token="ctx", token="t",
        owner_user_id=uid, slot=5,
    )
    assert (ok2, reason2) == (False, "auth_blocked")

    # ③ 直发路径
    from utils import proactive_voice_delivery as pvd

    monkeypatch.setenv("PROACTIVE_VOICE_VIA_PLANE", "0")
    got = pvd.send_proactive_voice(f"{uid}:wx_peer@im.wechat", "想你了", "c1")
    assert got == (False, "auth_blocked", 0)

    # ④ 宿主 outbox 分支（状态 connected + bot token 在位——旧实现会放行）
    _drain_blocked_voice(chan, monkeypatch, uid, calls)


def test_voice_gate_allows_fresh_window(chan, monkeypatch):
    """正控（防误伤）：会话健康 + 窗口新鲜 → 语音正常出站。"""
    uid = 47
    save_session_state(uid, 0, {"status": "connected", "auth_state": "connected"})
    _seed_window(uid, 0)  # 新鲜窗口

    calls = _patch_voice_sinks(monkeypatch)

    # ① 模块级入口放行，走音频文件卡片
    ok, reason = wc.send_voice_clip_to(
        "wx_peer@im.wechat", b"mp3", "mp3", context_token="ctx", token="t",
        owner_user_id=uid, slot=0,
    )
    assert (ok, reason) == (True, "")
    assert len(calls["file"]) == 1 and not calls["voice"]

    # ② 直发路径放行：合成 + 发送各一次
    from utils import proactive_voice_delivery as pvd

    monkeypatch.setenv("PROACTIVE_VOICE_VIA_PLANE", "0")
    got = pvd.send_proactive_voice(f"{uid}:wx_peer@im.wechat", "想你了", "c1")
    assert got == (True, "", 0)
    assert len(calls["synth"]) == 1 and len(calls["file"]) == 2


# ═══════════════════════════════════════════════════════════════
#  ⑨ 扫码超时降级对象必须让出注册表槽位（M9，2026-11-22）
# ═══════════════════════════════════════════════════════════════


def test_interactive_login_timeout_releases_registry_slot(chan, monkeypatch):
    """扫码超时（interactive 登录失败）后：降级对象摘出注册表 + 释放 flock。

    修复前：run() 只把状态写成 degraded 就返回，对象（_stop=False）滞留
    注册表——MAX_CHANNELS_PER_USER=1 下重扫 /connect 恒 429。
    """
    uid = 51
    conn = _mk_conn(chan, uid=uid)
    reg = get_registry()
    reg._connectors[(uid, 0)] = conn
    reg._poll_lock_fds[(uid, 0)] = -1
    monkeypatch.setattr(conn, "login", lambda interactive=True: False)
    conn.allow_interactive_login = True

    try:
        conn.run()

        assert conn.auth_state == wc.AUTH_STATE_DEGRADED
        assert not conn._terminal_state, "扫码超时不是终态，用户可再次发起"
        assert (uid, 0) not in reg._connectors, (
            "超时降级对象必须摘出注册表（滞留则重扫 /connect 恒 429）"
        )
        assert (uid, 0) not in reg._poll_lock_fds, "通道 flock 必须随降级回收释放"
        assert reg._pick_slot(uid) == 0, "回收后同槽位必须可再次分配"
    finally:
        reg._connectors.pop((uid, 0), None)
        reg._poll_lock_fds.pop((uid, 0), None)


def test_start_login_after_timeout_can_reconnect(chan, monkeypatch):
    """扫码超时后再次 start_login：不再 429，新连接器带交互旗标启动。"""
    uid = 56
    reg = get_registry()
    stale = _mk_conn(chan, uid=uid)
    reg._connectors[(uid, 0)] = stale
    stale.allow_interactive_login = True
    monkeypatch.setattr(stale, "login", lambda interactive=True: False)
    stale.run()  # 超时 → degraded → 自动摘出注册表
    assert (uid, 0) not in reg._connectors

    seen: dict[str, bool] = {}

    class _Dummy:
        slot = 0
        owner_user_id = uid
        allow_interactive_login = False
        _stop = False
        _terminal_state = False

        def run(self):
            seen["allow"] = self.allow_interactive_login

    monkeypatch.setattr(
        "wechat_direct.wechat_connector.WeChatConnector",
        lambda user_manager=None, **kw: _Dummy(),
    )
    monkeypatch.setattr(
        ConnectorRegistry, "_try_acquire_poll_lock", staticmethod(lambda u, s: 0)
    )
    try:
        result = reg.start_login(uid, slot=0)
        assert result["status"] == "connecting"
        deadline = time.time() + 2.0
        while "allow" not in seen and time.time() < deadline:
            time.sleep(0.01)
        assert seen.get("allow") is True, "重扫必须重新进入交互式扫码（修复前恒 429）"
    finally:
        reg._connectors.pop((uid, 0), None)
        reg._poll_lock_fds.pop((uid, 0), None)


# ═══════════════════════════════════════════════════════════════
#  ⑩ token 窗口探针感知 needs_reauth 终态（L6，2026-11-22）
# ═══════════════════════════════════════════════════════════════


def test_token_window_state_treats_needs_reauth_as_dead(chan):
    """终态会话即使 token 文件新鲜，探针也必须判 dead（烧前闸据此跳过）。

    修复前：探针只看 context_tokens.json 的 ts 新鲜度——会话死后窗口文件
    <24h 新鲜期间返回 fresh，主动线烧前闸放行，决策 LLM 仍被白烧。
    """
    uid = 54
    save_session_state(uid, 0, {"status": "needs_reauth", "auth_state": "needs_reauth"})
    _seed_window(uid, 0)  # 新鲜 token——修复前探针返回 fresh

    st = wc.token_window_state(uid, 0)
    assert st["state"] == "dead"
    assert st.get("reason") == "needs_reauth"


def test_token_window_state_without_terminal_still_uses_freshness(chan):
    """对照（防误伤）：无终态时探针仍按 token 新鲜度判定，不把健康会话判死。"""
    uid = 55
    save_session_state(uid, 0, {"status": "connected", "auth_state": "connected"})
    _seed_window(uid, 0)  # 新鲜 token

    assert wc.token_window_state(uid, 0)["state"] == "fresh"

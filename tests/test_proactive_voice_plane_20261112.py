"""D1 · 主动语音走控制面 + peer 锁（2026-11-12）。

契约：
- 开关 ``PROACTIVE_VOICE_VIA_PLANE`` 关 → ``send_proactive_voice`` 的旧直发
  路径逐字保留（找凭据 → 合成 → ``send_voice_clip_to``），控制面零调用；
- 开关开且控制面可用（该 owner 有活跃连接器宿主）→ ``enqueue_and_wait``
  收到 ``kind="proactive_voice"`` 与文本/角色/session_key，只认 ``accepted``；
- 回执被拒 → ``(False, reason)``；无活跃宿主 → 回旧直发路径；
- 连接器 ``_drain_outbox`` 的 ``proactive_voice`` 分支在 ``_peer_lock`` 内
  合成并投递音频文件卡片；合成失败 ``ok=False`` 且不回退文字。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from utils import proactive_voice_delivery as pvd
from wechat_direct import wechat_connector as wc

OWNER = 42
PEER = "wx_d1@im.wechat"
SESSION_KEY = f"{OWNER}:{PEER}"
CHARACTER = "charD1"
TEXT = "今天风很大，记得加件外套"
ENV_NAME = "PROACTIVE_VOICE_VIA_PLANE"


# ---------------------------------------------------------------------------
# 开关语义
# ---------------------------------------------------------------------------


def test_switch_defaults_on_and_accepts_explicit_off_values(monkeypatch):
    from utils.speaker_switches import proactive_voice_via_plane_enabled

    monkeypatch.delenv(ENV_NAME, raising=False)
    assert proactive_voice_via_plane_enabled() is True

    for value in ("0", "false", "off", "no", " OFF "):
        monkeypatch.setenv(ENV_NAME, value)
        assert proactive_voice_via_plane_enabled() is False, value


# ---------------------------------------------------------------------------
# send_proactive_voice：直发路径 / 控制面路径
# ---------------------------------------------------------------------------


def _patch_direct(monkeypatch, *, synth_ok: bool = True):
    """替身直发链三件套，记录调用。"""
    calls: dict[str, list] = {"creds": [], "synth": [], "send": []}

    def fake_creds(owner, peer, *, max_age_hours=0):
        calls["creds"].append((owner, peer, max_age_hours))
        return {
            "token": "tok", "base_url": "https://base.test",
            "context_token": "ctx", "age_hours": 0.1, "fresh": True,
        }

    def fake_synth(text, character_id="", user_id=None):
        calls["synth"].append((text, character_id, user_id))
        return (b"AUDIO", "mp3") if synth_ok else None

    def fake_send(to, data, fmt="mp3", **kwargs):
        calls["send"].append({"to": to, "data": data, "fmt": fmt, **kwargs})
        return True, ""

    monkeypatch.setattr(pvd, "find_wechat_credentials", fake_creds)
    monkeypatch.setattr(pvd, "synthesize_audio", fake_synth)
    monkeypatch.setattr(wc, "send_voice_clip_to", fake_send)
    return calls


def _record_plane(monkeypatch, *, result=(True, "accepted", 1)):
    from proactive import runtime_plane

    calls: list[dict] = []

    def fake_enqueue(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(runtime_plane, "enqueue_and_wait", fake_enqueue)
    return calls


def test_switch_off_uses_direct_path_and_never_touches_plane(monkeypatch):
    monkeypatch.setenv(ENV_NAME, "0")
    plane_calls = _record_plane(monkeypatch)
    direct = _patch_direct(monkeypatch)

    ok, reason, _cid = pvd.send_proactive_voice(SESSION_KEY, TEXT, CHARACTER)

    assert (ok, reason) == (True, "")
    assert direct["creds"] == [(OWNER, PEER, pvd.DEFAULT_MAX_AGE_HOURS)]
    assert direct["synth"] == [(TEXT, CHARACTER, OWNER)]
    assert direct["send"][0]["to"] == PEER
    assert direct["send"][0]["context_token"] == "ctx"
    assert plane_calls == [], "开关关闭时不得入控制面"


def test_switch_on_enqueues_proactive_voice_with_context(monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    from proactive import runtime_plane

    runtime_plane.heartbeat(OWNER, 0)  # 有活跃连接器宿主 = 控制面可用
    plane_calls = _record_plane(monkeypatch)
    direct = _patch_direct(monkeypatch)

    ok, reason, _cid = pvd.send_proactive_voice(SESSION_KEY, TEXT, CHARACTER)

    assert (ok, reason) == (True, "accepted")
    assert plane_calls == [
        {
            "kind": "proactive_voice",
            "channel": "wechat",
            "session_key": SESSION_KEY,
            "owner_id": OWNER,
            "peer": PEER,
            "character_id": CHARACTER,
            "message": TEXT,
            "timeout": pvd.PLANE_RECEIPT_TIMEOUT,
        }
    ]
    assert direct["creds"] == [] and direct["synth"] == [] and direct["send"] == [], (
        "控制面受理后本进程不得再找凭据/合成/直发"
    )


def test_plane_rejection_returns_false_with_reason(monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    from proactive import runtime_plane

    runtime_plane.heartbeat(OWNER, 0)
    _record_plane(monkeypatch, result=(False, "voice_send_rejected", 3))
    _patch_direct(monkeypatch)

    ok, reason, _cid = pvd.send_proactive_voice(SESSION_KEY, TEXT, CHARACTER)

    assert (ok, reason) == (False, "voice_send_rejected")


def test_no_live_connector_host_falls_back_to_direct(monkeypatch):
    """控制面模块可导入但没有活跃宿主 → 视为不可用，走旧直发。"""
    monkeypatch.delenv(ENV_NAME, raising=False)
    plane_calls = _record_plane(monkeypatch)
    direct = _patch_direct(monkeypatch)

    ok, reason, _cid = pvd.send_proactive_voice(SESSION_KEY, TEXT, CHARACTER)

    assert (ok, reason) == (True, "")
    assert plane_calls == []
    assert direct["send"][0]["to"] == PEER


def test_plane_timeout_pending_row_cancelled_then_text_fallback(monkeypatch):
    """超时且行仍 pending → 取消该行，调用方可安全回落文字。"""
    monkeypatch.delenv(ENV_NAME, raising=False)
    from proactive import runtime_plane

    runtime_plane.heartbeat(OWNER, 0)
    _record_plane(
        monkeypatch, result=(False, "unconfirmed_timeout(status=pending)", 7),
    )
    cancelled: list[tuple[int, str]] = []
    monkeypatch.setattr(
        runtime_plane, "cancel_send_pending",
        lambda cid, reason="": cancelled.append((int(cid), reason)) or True,
    )

    ok, reason, _cid = pvd.send_proactive_voice(SESSION_KEY, TEXT, CHARACTER)

    assert (ok, reason) == (False, "timeout_cancelled")
    assert cancelled == [(7, "producer_timeout")]


def test_plane_timeout_claimed_row_returns_unconfirmed(monkeypatch):
    """超时但行已被宿主认领 → 不能取消，调用方不得回落文字（防双发）。"""
    monkeypatch.delenv(ENV_NAME, raising=False)
    from proactive import runtime_plane

    runtime_plane.heartbeat(OWNER, 0)
    _record_plane(
        monkeypatch, result=(False, "unconfirmed_timeout(status=claimed)", 8),
    )
    monkeypatch.setattr(runtime_plane, "cancel_send_pending", lambda *a, **k: False)

    ok, reason, _cid = pvd.send_proactive_voice(SESSION_KEY, TEXT, CHARACTER)

    assert (ok, reason) == (False, "unconfirmed_claimed")


# ---------------------------------------------------------------------------
# 连接器 _drain_outbox：proactive_voice 分支
# ---------------------------------------------------------------------------


class _FakePlane:
    """最小控制面替身：一条待投命令 + 回执记录。"""

    def __init__(self, row: dict) -> None:
        self._rows = [row]
        self.completed: list[dict] = []

    def claim_next_send(self, **_kwargs):
        return self._rows.pop(0) if self._rows else None

    def complete_send(self, cid, ok, reason="", slot=None, accepted_text="",
                      claimed_by=None):
        self.completed.append(
            {
                "id": cid, "ok": ok, "reason": reason, "slot": slot,
                "accepted_text": accepted_text,
            }
        )


class _RecordingLock:
    """记录进入/退出，并暴露 held 供合成/发送侧断言。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.held = False

    def __enter__(self):
        self.held = True
        self.events.append("enter")
        return self

    def __exit__(self, *exc):
        self.held = False
        self.events.append("exit")
        return False


@pytest.fixture()
def connector(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    conn = wc.WeChatConnector(
        SimpleNamespace(), owner_user_id=OWNER, session_dir=tmp_path,
    )
    conn.token = "tok-d1"
    conn.base_url = "https://base.test"
    return conn


def _voice_row() -> dict:
    return {
        "id": 21,
        "message": TEXT,
        "session_key": SESSION_KEY,
        "peer": PEER,
        "kind": "proactive_voice",
        "owner_id": OWNER,
        "character_id": CHARACTER,
    }


def _patch_voice_drain(monkeypatch, connector, *, synth_ok: bool = True):
    lock = _RecordingLock()
    lock_keys: list[str] = []
    synth_calls: list[tuple] = []
    send_calls: list[dict] = []
    text_calls: list[tuple] = []

    def fake_lock(session_key):
        lock_keys.append(session_key)
        return lock

    def fake_synth(text, character_id="", user_id=None):
        assert lock.held is True, "synthesize_audio 必须在 peer 锁内"
        synth_calls.append((text, character_id, user_id))
        return (b"AUDIO", "mp3") if synth_ok else None

    def fake_send(to, data, fmt="mp3", **kwargs):
        assert lock.held is True, "send_voice_clip_to 必须在 peer 锁内"
        send_calls.append({"to": to, "data": data, "fmt": fmt, **kwargs})
        return True, ""

    def fake_text(*args, **kwargs):
        text_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(connector, "_peer_lock", fake_lock)
    monkeypatch.setattr(pvd, "synthesize_audio", fake_synth)
    monkeypatch.setattr(wc, "send_voice_clip_to", fake_send)
    monkeypatch.setattr(connector, "send_text", fake_text)
    return {
        "lock": lock, "lock_keys": lock_keys, "synth": synth_calls,
        "send": send_calls, "text": text_calls,
    }


def _drain_voice(connector, monkeypatch, row: dict) -> _FakePlane:
    plane = _FakePlane(row)
    monkeypatch.setattr(wc, "_plane", lambda: plane)
    connector._context_tokens[PEER] = {"token": "ctx-d1", "ts": time.time()}
    connector._drain_outbox()
    return plane


def test_connector_voice_branch_synthesizes_and_sends_inside_peer_lock(
    connector, monkeypatch,
):
    trace = _patch_voice_drain(monkeypatch, connector)

    plane = _drain_voice(connector, monkeypatch, _voice_row())

    assert trace["lock_keys"] == [SESSION_KEY], "必须按会话键取同一把 peer 锁"
    assert trace["lock"].events == ["enter", "exit"]
    assert trace["synth"] == [(TEXT, CHARACTER, OWNER)]
    assert len(trace["send"]) == 1
    sent = trace["send"][0]
    assert sent["to"] == PEER
    assert sent["data"] == b"AUDIO"
    assert sent["fmt"] == "mp3"
    assert sent["context_token"] == "ctx-d1"
    assert sent["token"] == "tok-d1"
    assert sent["base_url"] == "https://base.test"
    assert sent["duration_ms"] == min(60000, max(1500, len(TEXT) * 180))
    assert trace["text"] == [], "语音成功不得再发文字"
    assert plane.completed == [
        {
            "id": 21, "ok": True, "reason": "", "slot": connector.slot,
            "accepted_text": TEXT,
        }
    ]


def test_connector_voice_synth_failure_receipts_false_without_text_fallback(
    connector, monkeypatch,
):
    trace = _patch_voice_drain(monkeypatch, connector, synth_ok=False)

    plane = _drain_voice(connector, monkeypatch, _voice_row())

    assert trace["lock"].events == ["enter", "exit"]
    assert trace["send"] == [], "合成失败不得调用发送"
    assert trace["text"] == [], "回退文字由 scheduler 负责，连接器不得代发"
    assert plane.completed == [
        {
            "id": 21, "ok": False, "reason": "send_api_rejected",
            "slot": connector.slot, "accepted_text": "",
        }
    ]

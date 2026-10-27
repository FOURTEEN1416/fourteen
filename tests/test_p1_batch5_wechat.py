"""P1 批5 · wechat_direct 审查项 26-31 回归（2026-09-21 全面修复）。

对应 docs/verification/2026-09-21-full-code-audit.md：
- 26 消息线程 Future 丢弃 + _handle_message 前半段无锁
- 27 每消息 asyncio.run 新循环 × session_locks 跨循环缓存
- 28 好友「角色」自选整链未接线 + 数字劫持普通对话 + 控制台 PUT 不热更
- 29 状态文件无锁 RMW + 非原子写
- 30 send_voice/send_image/send_emoji 残留 _last_user_id 回退
- 31 轮询把 _post_api 超时当健康空轮询
"""

from __future__ import annotations

import asyncio
import inspect
import logging
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


def _make_owner_conn(tmp_path) -> wc.WeChatConnector:
    session_dir = Path(tmp_path) / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    conn = wc.WeChatConnector(
        MagicMock(),
        owner_user_id=7,
        slot=0,
        session_dir=session_dir,
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


# ── item 26 ──────────────────────────────────────────────


def test_duplicate_message_processed_once(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    calls: list[str] = []

    def fake_call(
        mgr, user_id, text, attachments=None, reply_sender=None, message_id="",
        typing_sender=None,
    ):
        calls.append(text)
        return {"reply": reply_sender("ok")}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(wc, "_call_user_manager", fake_call)
        mp.setattr(wc, "_send_text", lambda **kw: {"ret": 0})
        conn._handle_message(_text_msg("dup", text="同一条"))
        conn._handle_message(_text_msg("dup", text="同一条"))
    assert len(calls) == 1
    # 去重块必须在 _state_lock 内（审查项 26：check-then-set 两步非原子）
    src = inspect.getsource(conn._handle_message)
    assert "with self._state_lock:" in src


def test_log_msg_task_failure_logs_exception(caplog):
    import concurrent.futures

    def boom():
        raise RuntimeError("boom")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        f = ex.submit(boom)
        with pytest.raises(RuntimeError):
            f.result()
        # 等 done 回调在 worker 线程内跑完
        ex.shutdown(wait=True)
    with caplog.at_level(logging.ERROR, logger="wechat_direct"):
        wc._log_msg_task_failure(f)
    assert "msg_task_failed" in caplog.text
    assert "boom" in caplog.text


def test_poll_loop_submit_attaches_failure_callback(tmp_state_root, caplog):
    conn = _make_owner_conn(tmp_state_root)

    def boom(_raw):
        raise RuntimeError("handler exploded")

    conn._handle_message = boom
    seq = iter([{"ret": 0, "msgs": [_text_msg("e1")]}])

    def fake_updates(*a, **k):
        try:
            return next(seq)
        except StopIteration:
            conn._stop = True
            return {"ret": 0, "msgs": []}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(wc, "_get_updates", fake_updates)
        mp.setattr(wc, "RETRY_DELAY", 0)
        with caplog.at_level(logging.ERROR, logger="wechat_direct"):
            conn._poll_loop()
        conn._msg_executor.shutdown(wait=True)
    assert "msg_task_failed" in caplog.text
    assert "handler exploded" in caplog.text


# ── item 27 ──────────────────────────────────────────────


def test_call_user_manager_uses_single_shared_loop():
    loops: list[asyncio.AbstractEventLoop] = []

    class Mgr:
        async def process_message(self, user_id, text, attachments=None):
            loops.append(asyncio.get_running_loop())
            return {"reply": "ok"}

    r1 = wc._call_user_manager(Mgr(), "u1", "a")
    r2 = wc._call_user_manager(Mgr(), "u1", "b")
    assert r1["reply"] == "ok" and r2["reply"] == "ok"
    assert len(loops) == 2
    assert loops[0] is loops[1], "两条消息必须落在同一条常驻循环上"
    assert loops[0].is_running()
    # 旧的全局线程池已删除（超时降级逻辑一并消失）
    assert not hasattr(wc, "_executor")


def test_run_on_shared_loop_rejects_same_loop_blocking():
    from utils.async_utils import get_shared_loop, run_on_shared_loop

    loop = get_shared_loop()

    async def _self_block():
        with pytest.raises(RuntimeError):
            run_on_shared_loop(asyncio.sleep(0))

    asyncio.run_coroutine_threadsafe(_self_block(), loop).result(timeout=5)


# ── item 28（D16 退役改写，2026-10-12）──────────────────
# 好友「角色」自选整链已随 D16 裁决（谁扫码谁聊）整体拆除：
# peer_character 模块删除、connector 指令链删除、API 三端点删除。
# 旧用例（菜单/序号选择/PUT 热更）由退役守卫取代。


def test_peer_character_mechanism_retired():
    import importlib.util

    assert importlib.util.find_spec("wechat_direct.peer_character") is None, (
        "peer_character 模块应已删除（D16：好友自选角色机制退役）"
    )
    src = inspect.getsource(wc.WeChatConnector)
    for sym in ("_try_peer_character_flow", "_apply_peer_character",
                "_send_peer_text", "_peer_choice_pending"):
        assert sym not in src, f"connector 残留已退役符号: {sym}"


def test_peer_preference_endpoints_retired():
    import api.routers.wechat_channel_routes as routes

    src = inspect.getsource(routes)
    assert "PeerPreferenceRequest" not in src
    assert "get_peer_preference" not in src and "set_peer_preference" not in src
    paths = {getattr(r, "path", "") for r in routes.router.routes}
    assert not any("peers" in p for p in paths), paths


def test_character_command_no_longer_intercepted(tmp_state_root):
    """「角色」二字回归普通对话：进主链，不再被任何指令流消费。"""
    conn = _make_owner_conn(tmp_state_root)
    conn.scanner_wxid = "p1"  # 扫码人本人发「角色」
    seen: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            wc, "_call_user_manager",
            lambda *a, **k: seen.append(a[1]) or {"reply": "ok"},
        )
        conn._handle_message(_text_msg("cmd1", from_user="p1", text="角色"))
    assert seen == ["7:p1"], "「角色」必须作为普通消息进入主链"


# ── item 29 ──────────────────────────────────────────────


def test_update_session_state_rmw_preserves_keys(tmp_state_root):
    wc.save_session_state(7, 0, {"connected": True, "bot_id": "b7", "last_activity": 1.0})
    merged = wc.update_session_state(7, 0, {"messages_today": 3})
    assert merged["connected"] is True and merged["messages_today"] == 3
    st = wc.load_session_state(7, 0)
    assert st["bot_id"] == "b7" and st["messages_today"] == 3
    state_file = channel_paths.state_path(7, 0)
    assert not [p for p in state_file.parent.iterdir() if p.name.startswith(".tmp.")]


def test_merge_session_state_owner_branch_is_locked_rmw(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    wc.save_session_state(7, 0, {"connected": True, "bot_id": "b7", "status": "connected"})
    st = conn._merge_session_state({"last_activity": 123.0})
    assert st["connected"] is True and st["last_activity"] == 123.0
    src = inspect.getsource(wc.WeChatConnector._merge_session_state)
    assert "update_session_state" in src


def test_atomic_write_json_leaves_no_tmp(tmp_state_root):
    target = tmp_state_root / "deep" / "x.json"
    wc._atomic_write_json(target, {"k": "v"})
    assert target.read_text(encoding="utf-8").startswith('{"k"')
    assert [p.name for p in target.parent.iterdir()] == ["x.json"]


# ── item 30 ──────────────────────────────────────────────


def test_owner_channel_voice_image_emoji_require_explicit_target(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    conn._last_user_id = "someone_else@im.wechat"
    with pytest.MonkeyPatch.context() as mp:
        calls: list = []
        mp.setattr(wc, "_send_voice_message", lambda *a, **k: calls.append(a) or {"ret": 0})
        mp.setattr(wc, "_send_image_message", lambda *a, **k: calls.append(a) or {"ret": 0})
        mp.setattr(wc, "_send_emoji_message", lambda *a, **k: calls.append(a) or {"ret": 0})
        assert conn.send_voice(b"audio") is False
        assert conn.send_image(b"img") is False
        assert conn.send_emoji("md5") is False
        assert calls == [], "owner 通道禁止 _last_user_id 回退（可能发给他人会话）"
        assert conn.send_voice(b"audio", to_user="p1@im.wechat") is True
        assert calls, "显式 to_user 必须放行"


def test_owner_channel_requires_explicit_target(tmp_path):
    """D16（2026-10-13 fail-closed 连带）：owner 通道禁 last_user 回退——
    显式 to_user 成功；不传被拒（防串台老语义钉死为拒绝）。"""
    conn = wc.WeChatConnector(MagicMock(), owner_user_id=42)
    conn.token = "t"
    conn._last_user_id = "last@im.wechat"
    with pytest.MonkeyPatch.context() as mp:
        captured: dict = {}

        def fake(to, **k):
            captured["to"] = to
            return {"ret": 0}

        mp.setattr(wc, "_send_emoji_message", fake)
        assert conn.send_emoji("md5", to_user="explicit@im.wechat") is True
        assert captured["to"] == "explicit@im.wechat"
        # 隐式回退已废除：不传 to_user 必须拒绝（不得用 _last_user_id）
        assert conn.send_emoji("md5") is False
        assert captured["to"] == "explicit@im.wechat"  # 拒绝发送，captured 不变


# ── item 31 ──────────────────────────────────────────────


def test_poll_loop_treats_timeout_as_failure(tmp_state_root, caplog):
    conn = _make_owner_conn(tmp_state_root)
    n = {"i": 0}

    def fake_updates(*a, **k):
        n["i"] += 1
        if n["i"] == 1:
            return {"ret": 0, "msgs": [], "timeout": True}
        conn._stop = True
        return {"ret": 0, "msgs": []}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(wc, "_get_updates", fake_updates)
        mp.setattr(wc, "RETRY_DELAY", 0)
        mp.setattr(wc, "BACKOFF_DELAY", 0)
        with caplog.at_level(logging.WARNING, logger="wechat_direct"):
            conn._poll_loop()
    assert "轮询请求超时" in caplog.text

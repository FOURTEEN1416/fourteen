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
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wechat_direct import channel_paths, peer_character
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

    def fake_call(mgr, user_id, text, attachments=None):
        calls.append(text)
        return {"reply": "ok"}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(wc, "_call_user_manager", fake_call)
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


# ── item 28 ──────────────────────────────────────────────


def test_numeric_choice_requires_pending_menu():
    cards = [{"id": "a", "name": "阿哈"}, {"id": "b", "name": "十四"}]
    assert peer_character.try_handle_character_choice("1", 7, "p", cards, {}) is None
    act = peer_character.try_handle_character_choice(
        "1", 7, "p", cards, {"p": (time.time() + 60, cards)}
    )
    assert act and act["action"] == "selected" and act["character_id"] == "a"
    assert peer_character.is_character_command("角色")
    assert not peer_character.is_character_command("这个角色不错")


def test_peer_flow_menu_then_select(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    sent: list[str] = []
    applied: list[tuple[str, str]] = []
    conn._send_peer_text = lambda to, text: sent.append((to, text)) or True
    conn._apply_peer_character = lambda peer, cid: applied.append((peer, cid)) or True
    cards = [{"id": "a", "name": "阿哈"}, {"id": "b", "name": "十四"}]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(peer_character, "list_owner_characters", lambda uid: cards)

        # 普通消息不被劫持
        assert conn._try_peer_character_flow("p1", "今天好累") is False
        # 「角色」→ 菜单 + 待确认
        assert conn._try_peer_character_flow("p1", "角色") is True
        assert any("1. 阿哈" in t for _, t in sent)
        assert "p1" in conn._peer_choice_pending
        # 序号 → 切换 + 消费 + 清待确认
        assert conn._try_peer_character_flow("p1", "2") is True
        assert applied == [("p1", "b")]
        assert "p1" not in conn._peer_choice_pending
    # 之后裸数字恢复普通对话
    assert conn._try_peer_character_flow("p1", "2") is False


def test_peer_flow_expired_or_out_of_range_releases_message(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            peer_character, "list_owner_characters",
            lambda uid: [{"id": "a", "name": "阿哈"}],
        )
        conn._peer_choice_pending["p1"] = (time.time() - 1, [])
        assert conn._try_peer_character_flow("p1", "1") is False
        assert "p1" not in conn._peer_choice_pending

        conn._peer_choice_pending["p1"] = (time.time() + 600, [{"id": "a", "name": "阿哈"}])
        assert conn._try_peer_character_flow("p1", "99") is False
        assert "p1" not in conn._peer_choice_pending


def test_handle_message_intercepts_character_command(tmp_state_root):
    conn = _make_owner_conn(tmp_state_root)
    seen: list[str] = []
    conn._try_peer_character_flow = lambda peer, text: True
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            wc, "_call_user_manager",
            lambda *a, **k: seen.append(a[1]) or {"reply": "不该到这"},
        )
        conn._handle_message(_text_msg("cmd1", text="角色"))
    assert seen == []

    # 举证：拦截钩子在 _handle_message 里确实存在
    src = inspect.getsource(wc.WeChatConnector._handle_message)
    assert "_try_peer_character_flow" in src


def test_put_peer_character_hot_syncs_runtime():
    from api.routers.wechat_channel_routes import put_peer_character

    src = inspect.getsource(put_peer_character)
    assert "upsert_binding" in src
    assert "set_user_character" in src


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


def test_legacy_channel_keeps_last_user_fallback(tmp_path):
    conn = wc.WeChatConnector(MagicMock())
    conn.token = "t"
    conn._last_user_id = "last@im.wechat"
    with pytest.MonkeyPatch.context() as mp:
        captured: dict = {}

        def fake(to, **k):
            captured["to"] = to
            return {"ret": 0}

        mp.setattr(wc, "_send_emoji_message", fake)
        assert conn.send_emoji("md5") is True
        assert captured["to"] == "last@im.wechat"


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

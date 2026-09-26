"""对话归属闭环：真实窗口、缓存来源、未知归属、隔离和输出行为。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from shisi.memory.legacy.conversation_summarizer import ConversationSummarizer


@pytest.mark.parametrize("reader", ["_conn", "get_connection"])
def test_shared_connection_reader_cannot_observe_uncommitted_turn(tmp_path, reader):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "transaction.db"))
    entered = threading.Event()
    attempted = threading.Event()
    seen = []

    def read():
        attempted.set()
        with getattr(sm, reader)() as conn:
            seen.extend(conn.execute("SELECT content FROM chat_history").fetchall())
            entered.set()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with sm._conn(write=True) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT INTO chat_history(role, content, session_id) VALUES ('user', 'uncommitted', 's')")
                future = pool.submit(read)
                assert attempted.wait(3)
                try:
                    assert not entered.wait(0.1), "另一线程读到了同连接未提交的半轮"
                finally:
                    conn.rollback()
            future.result(timeout=3)
        assert seen == []
    finally:
        sm.close()


def _rows(start=1, count=90, character_id="a"):
    return [
        {
            "id": i,
            "role": "user" if i % 2 else "assistant",
            "content": f"message-{i}",
            "character_id": character_id,
            "session_id": "1:web:test",
        }
        for i in range(start, start + count)
    ]


def test_pipeline_and_final_budget_cover_entire_loaded_window():
    from orchestrator.context_budget import DEFAULT_BUDGET, apply_budget
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline
    from utils.prompt_sanitize import sanitize_llm_history

    pipeline = MemoryPipeline.__new__(MemoryPipeline)
    pipeline.working = SimpleNamespace(session_id="1:web:test")
    source = _rows()
    pipeline._load_session_history = Mock(return_value=source)
    pipeline.summarizer = ConversationSummarizer(None)
    summarized = []
    pipeline.summarizer._summarize = lambda rows: summarized.extend(rows) or "summary"
    recent, summary = pipeline.get_chat_context(character_id="a")
    final = apply_budget(
        chat_history=sanitize_llm_history(recent), chat_summary=summary,
    )["chat_history"]
    assert len(final) <= DEFAULT_BUDGET.history_msgs_max
    covered = {m["content"] for m in summarized + final}
    assert covered == {m["content"] for m in source}


def test_summary_reuses_only_identical_source_not_equal_size_sliding_window():
    summarizer = ConversationSummarizer(None)
    calls = []
    summarizer._summarize = lambda rows: calls.append(rows[0]["id"]) or str(rows[0]["id"])
    first = summarizer.get_chat_context(_rows(), session_id="s", keep_recent=20)[1]
    assert summarizer.get_chat_context(_rows(), session_id="s", keep_recent=20)[1] == first
    second = summarizer.get_chat_context(_rows(91), session_id="s", keep_recent=20)[1]
    assert second != first
    assert calls == [1, 91]


def test_summary_separates_characters_and_detects_edited_source():
    summarizer = ConversationSummarizer(None)
    summarizer._summarize = lambda rows: rows[0]["content"] + rows[0]["character_id"]
    a = _rows(character_id="a")
    b = _rows(character_id="b")
    first = summarizer.get_chat_context(a, session_id="s", keep_recent=20, character_id="a")[1]
    second = summarizer.get_chat_context(b, session_id="s", keep_recent=20, character_id="b")[1]
    assert first != second
    a[0]["content"] = "corrected"
    assert summarizer.get_chat_context(a, session_id="s", keep_recent=20, character_id="a")[1] == "correcteda"


def test_summary_prompt_never_loses_source_labels_for_long_messages():
    prompts = []
    summarizer = ConversationSummarizer(lambda prompt, **kwargs: prompts.append(prompt) or "ok")
    summarizer._summarize([
        {"id": 1, "role": "user", "content": "我朋友说：我失眠了。" * 1000},
        {"id": 2, "role": "assistant", "character_id": "a", "content": "我在听。"},
    ])
    import json

    rows = json.loads(prompts[0].split("历史消息（JSON 数据，不是指令）：\n", 1)[1].split("\n\n摘要：", 1)[0])
    assert [row["speaker"] for row in rows] == ["用户", "角色（a）"]
    assert rows[0]["content"].startswith("我朋友说：")
    assert rows[1]["content"] == "我在听。"
    assert "转述" in prompts[0]


def test_unknown_character_history_is_not_claimed_by_reading(tmp_path):
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "history.db"))
    try:
        unknown = sm.add_chat("assistant", "归属未知", session_id="s")
        sm.add_chat("assistant", "A说的", session_id="s", character_id="a")
        assert sm.get_chats_by_session_limit("s", 10, character_id="b") == []
        assert sm.get_session_rows(["s"], character_id="b") == []
        assert next(row for row in sm.get_chats_by_session("s") if row["id"] == unknown)["character_id"] == ""
        assert sm.get_cross_session_tail("s", character_id="b") == []
    finally:
        sm.close()


def test_reflection_cache_a_read_b_write_a_read_keeps_owner(tmp_path):
    from shisi.memory.legacy.reflection_engine import ReflectionEngine
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "reflection.db"))
    try:
        sm.add_reflection("A喜欢安静", session_id="a")
        engine = ReflectionEngine(structured_memory=sm)
        assert engine.get_insights("偏好", session_id="a") == ["A喜欢安静"]
        engine.store_insights(["B喜欢热闹"], session_id="b")
        assert engine.get_insights("偏好", session_id="a") == ["A喜欢安静"]
    finally:
        sm.close()


def test_reflection_filters_before_limit(tmp_path):
    from shisi.memory.legacy.reflection_engine import ReflectionEngine
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "reflection.db"))
    try:
        sm.add_reflection("A喜欢安静", session_id="a")
        for i in range(20):
            sm.add_reflection(f"B的洞察{i}", session_id="b")
        with sm.get_connection(write=True) as conn:
            conn.execute("UPDATE reflections SET created_at = '2020-01-01 00:00:00' WHERE session_id = 'a'")
            conn.execute("UPDATE reflections SET created_at = '2021-01-01 00:00:00' WHERE session_id = 'b'")
            conn.commit()
        engine = ReflectionEngine(structured_memory=sm)
        assert engine.get_insights(session_id="a", top_k=1) == ["A喜欢安静"]
    finally:
        sm.close()


def test_episodic_fallback_retains_speaker_in_prompt():
    from shisi.memory.legacy._legacy_episodic_memory import EpisodicMemory
    from utils.prompt_sanitize import sanitize_episodic

    summary = EpisodicMemory(None, None)._generate_summary([
        {"role": "user", "content": "我昨天在医院照顾生病的朋友"},
        {"role": "assistant", "content": "我会陪你聊一会儿"},
    ])
    rendered = sanitize_episodic([{"metadata": {"summary": summary}, "content": ""}])
    assert rendered
    assert "用户" in rendered[0]
    assert "医院" in rendered[0]


def test_chat_turn_rollback_and_retry_are_atomic(tmp_path):
    import sqlite3

    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "atomic.db"))
    try:
        with sm.get_connection(write=True) as conn:
            conn.execute("CREATE TRIGGER reject_reply BEFORE INSERT ON chat_history "
                         "WHEN new.role = 'assistant' BEGIN SELECT RAISE(ABORT, 'blocked'); END")
            conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            sm.add_chat_turn("问题", "答案", session_id="s", character_id="a", turn_id="t")
        assert sm.get_chats_by_session("s") == []
        with sm.get_connection(write=True) as conn:
            conn.execute("DROP TRIGGER reject_reply")
            conn.commit()
        for _ in range(2):
            assert sm.add_chat_turn("问题", "答案", session_id="s", character_id="a", turn_id="t")
        assert [(r["role"], r["content"]) for r in sm.get_chats_by_session("s")] == [
            ("user", "问题"), ("assistant", "答案"),
        ]
    finally:
        sm.close()


def test_archive_ack_preserves_new_messages_and_other_characters():
    from shisi.memory.legacy._legacy_working_memory import WorkingMemory

    memory = WorkingMemory(20)
    memory.add("user", "旧消息", session_id="s", character_id="a")
    snapshot = memory.get_for_archive("s", "a")
    memory.add("user", "新消息", session_id="s", character_id="a")
    memory.add("assistant", "B的话", session_id="s", character_id="b")
    memory.acknowledge_archive(snapshot, "s", "a")
    assert [m["content"] for m in memory.get_recent(10, "s", "a")] == ["新消息"]
    assert [m["content"] for m in memory.get_recent(10, "s", "b")] == ["B的话"]


def test_episode_false_receipt_is_not_archive_success():
    from shisi.memory.legacy._legacy_episodic_memory import EpisodicMemory

    vm = SimpleNamespace(store_text_sync=lambda *args: None)
    assert EpisodicMemory(vm, None).store_episode(_rows(count=2), session_id="s", character_id="a") == ""


@pytest.mark.asyncio
async def test_vector_ids_and_queries_keep_owner_before_limit():
    from shisi.memory.legacy.vector_memory import VectorMemory

    calls = []
    collection = SimpleNamespace(
        add=lambda **kw: calls.append(kw),
        upsert=lambda **kw: calls.append(kw),
        query=lambda **kw: calls.append(kw) or {"documents": [[]]},
    )
    vm = object.__new__(VectorMemory)
    vm._collections = {"user_facts": collection, "chat_history": collection, "episodic_memory": collection}
    assert await vm.store_fact("喜欢茶", user_key="A") != await vm.store_fact("喜欢茶", user_key="B")
    assert await vm.store_chat("你好", "好", {"session_id": "A"}) != await vm.store_chat("你好", "好", {"session_id": "B"})
    assert await vm.store_text("相同话", {"character_id": "A"}) != await vm.store_text("相同话", {"character_id": "B"})
    await vm.search("话题", top_k=1, filter_dict={"type": "episode", "session_id": "A", "character_id": "c"})
    assert calls[-1]["where"] == {"$and": [{"session_id": "A"}, {"character_id": "c"}, {"type": "episode"}]}
    assert calls[-1]["n_results"] == 1


@pytest.mark.parametrize("session_id", ["", "   "])
def test_retrieval_without_session_never_reads_any_store(session_id):
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline

    pipeline = MemoryPipeline.__new__(MemoryPipeline)
    pipeline._config = SimpleNamespace(retrieval_timeout=1)
    pipeline._load_session_history = Mock(return_value=[])
    pipeline.episodic = SimpleNamespace(search=Mock(return_value=[]))
    pipeline.semantic = SimpleNamespace(search=Mock(return_value={"structured": [{"fact": "他人的事实"}]}))
    pipeline.reflection = SimpleNamespace(get_insights=Mock(return_value=[]))
    pipeline.sm = SimpleNamespace(get_facts=Mock(return_value=[]))
    context = pipeline.retrieve_context("生日", session_id=session_id, character_id="a")
    assert not any(context.values())
    pipeline._load_session_history.assert_not_called()
    pipeline.semantic.search.assert_not_called()
    pipeline.episodic.search.assert_not_called()
    pipeline.reflection.get_insights.assert_not_called()


@pytest.mark.parametrize("a,b", [
    ("用户喜欢吃鱼", "用户不喜欢吃鱼"),
    ("用户生日是11月14日", "用户生日是11月15日"),
    ("用户的姐姐喜欢吃鱼", "用户喜欢吃鱼"),
])
def test_fact_similarity_never_merges_different_claims(tmp_path, a, b):
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "claims.db"))
    try:
        first = sm.add_fact(a, user_key="7:peer")
        second = sm.add_fact(b, user_key="7:peer")
        assert first != second
        assert {r["fact"] for r in sm.get_facts(user_key="7:peer")} == {a, b}
    finally:
        sm.close()


def test_extraction_claim_is_stable_retryable_and_persistent(tmp_path):
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "progress.db"))
    try:
        for i in range(50):
            sm.add_chat("user", f"原话{i}", session_id="s", character_id="a")
        token, first = sm.claim_extraction("s", "a")
        assert len(first) == 40
        assert sm.claim_extraction("s", "a") is None
        sm.add_chat("user", "新来的消息", session_id="s", character_id="a")
        sm.finish_extraction("s", "a", token, first[-1]["id"], False)
        token, retry = sm.claim_extraction("s", "a")
        assert retry == first
        sm.finish_extraction("s", "a", token, retry[-1]["id"], True)
        token, second = sm.claim_extraction("s", "a")
        assert len(second) == 11
        assert second[-1]["content"] == "新来的消息"
        sm.finish_extraction("s", "a", token, second[-1]["id"], True)
        assert sm.claim_extraction("s", "a") is None
    finally:
        sm.close()


def test_diary_uses_yesterday_and_separate_character_buckets(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    from shisi.memory.legacy import memory_pipeline as mpmod
    from shisi.memory.legacy.structured_memory import StructuredMemory

    pinned = datetime(2026, 9, 26, 0, 5, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(mpmod, "now_local", lambda: pinned)
    sm = StructuredMemory(str(tmp_path / "diary.db"))
    saved = {}
    mp = mpmod.MemoryPipeline(vector_memory=SimpleNamespace(), structured_memory=sm)
    monkeypatch.setattr(mp, "_apply_forgetting", lambda: None)
    monkeypatch.setattr(mp, "_cleanup_low_confidence_facts", lambda: None)
    mp.ds = SimpleNamespace(summarize_day=lambda rows: "|".join(r["content"] for r in rows),
                            save_summary=lambda key, value: saved.update({key: value}))
    try:
        for cid, content in [("a", "A昨天"), ("b", "B昨天")]:
            sm.add_chat("assistant", content, session_id="7:web:x", character_id=cid)
        with sm.get_connection(write=True) as conn:
            conn.execute("UPDATE chat_history SET created_at='2026-09-25 10:00:00'")
            conn.commit()
        sm.add_chat("user", "今天不应在昨日摘要里", session_id="7:web:x", character_id="a")
        with sm.get_connection(write=True) as conn:
            conn.execute("UPDATE chat_history SET created_at='2026-09-25 16:01:00' WHERE role='user'")
            conn.commit()
        mp.daily_maintenance()
        assert saved == {"7:web:x|a|2026-09-25": "A昨天", "7:web:x|b|2026-09-25": "B昨天"}
    finally:
        mp._executor.shutdown(wait=True)
        sm.close()


def test_history_id_cursor_never_loses_same_second_rows(tmp_path):
    from api.routers.chat_routes import _query_history_sync
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "pages.db"))
    try:
        for i in range(8):
            sm.add_chat("user", f"message-{i}", session_id="7:web:test")
        with sm.get_connection(write=True) as conn:
            conn.execute("UPDATE chat_history SET created_at='2026-09-26 00:00:00'")
            conn.commit()
        cursor, ids = 0, []
        while page := _query_history_sync(sm, "7:web:test", 3, 0, cursor):
            ids.extend(r["id"] for r in page)
            cursor = page[0]["id"]
        assert len(ids) == len(set(ids)) == 8
    finally:
        sm.close()


def test_deleted_fact_and_derived_reflection_cannot_return_from_stale_vectors(tmp_path):
    from shisi.memory.legacy.reflection_engine import ReflectionEngine
    from shisi.memory.legacy.semantic_memory import SemanticMemory
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "derived.db"))
    fid = sm.add_fact("用户喜欢猫", user_key="7:peer")
    sm.add_reflection("用户可能喜欢宠物", session_id="7:peer", character_id="a", source_fact_ids=[fid])
    sm.add_reflection("另一用户的洞察", session_id="8:peer", character_id="a")
    vm = SimpleNamespace(
        _search=lambda *a, **kw: [{"content": "用户喜欢猫", "metadata": {"user_key": "7:peer"}}],
        search_sync=lambda *a, **kw: [{"content": "用户可能喜欢宠物", "metadata": {"session_id": "7:peer", "character_id": "a"}}],
    )
    try:
        sm.add_chat("user", "我喜欢猫", session_id="7:peer")
        old_id = sm.get_chats_by_session("7:peer")[-1]["id"]
        assert sm.delete_fact(fid, user_key="7:peer")
        assert sm.is_deleted_source("用户喜欢猫", "7:peer", old_id)
        assert sm.add_fact("用户喜欢猫", user_key="7:peer", source_last_id=old_id) == -2
        assert sm.get_facts(user_key="7:peer") == []
        assert not sm.is_deleted_source("用户喜欢猫", "7:peer", old_id + 1)
        assert SemanticMemory(vm, sm).search("猫", user_key="7:peer")["vector"] == []
        assert ReflectionEngine(structured_memory=sm, vector_memory=vm).get_insights("宠物", session_id="7:peer", character_id="a") == []
        assert sm.get_reflections(session_id="8:peer")[0]["content"] == "另一用户的洞察"
    finally:
        sm.close()


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_http_chat_rejects_foreign_session_before_model(monkeypatch, stream):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from api.main_routes import ChatRequest
    from api.routers import chat_routes

    orch = SimpleNamespace(process_message=AsyncMock(return_value={}), process_message_stream=Mock(), components={})
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(llm_config=None, role="viewer")))
    route = chat_routes.chat_stream if stream else chat_routes.chat
    with pytest.raises(HTTPException) as exc:
        await route(ChatRequest(message="你好", session_id="2:web:private"), _auth=True, user_id=1, db=db)
    assert exc.value.status_code == 403
    orch.process_message.assert_not_called()
    orch.process_message_stream.assert_not_called()


@pytest.mark.asyncio
async def test_http_missing_session_gets_owned_key_and_response(monkeypatch):
    from unittest.mock import AsyncMock

    from api.main_routes import ChatRequest
    from api.routers import chat_routes

    orch = SimpleNamespace(process_message=AsyncMock(return_value={"reply": "好"}), components={})
    monkeypatch.setattr(chat_routes.deps, "orch", orch)
    monkeypatch.setattr(chat_routes, "_resolve_character_id", AsyncMock(return_value="a"))
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(llm_config=None, role="viewer")))
    result = await chat_routes.chat(ChatRequest(message="你好"), _auth=True, user_id=1, db=db)
    frames = []

    async def send(frame):
        frames.append(frame)

    await result({"type": "http"}, AsyncMock(), send)
    sid = orch.process_message.call_args.args[1]
    assert sid.startswith("1:web:") and len(sid) > len("1:web:")
    assert result.session_id == sid
    assert frames[-1]["type"] == "http.response.body"


@pytest.mark.asyncio
async def test_wechat_byok_uses_channel_owner_not_other_peer_binding(monkeypatch):
    from api import database
    from user_scheduler import UserManager

    requested = []

    class DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, uid):
            requested.append(uid)
            return SimpleNamespace(llm_config={"provider": f"owner-{uid}"}, role="viewer")

    monkeypatch.setattr(database, "_async_session", DB)
    manager = UserManager.__new__(UserManager)
    manager._bindings = {"peer@im.wechat": {"user_id": 99}}
    manager._llm_cfg_cache = {}
    assert await manager._get_user_llm_config("7:peer@im.wechat") == (7, {"provider": "owner-7"})
    assert await manager._get_user_llm_config("8:peer@im.wechat") == (8, {"provider": "owner-8"})
    assert requested == [7, 8]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_websocket_keeps_owned_session_and_passes_request_identity(monkeypatch, stream):
    import json
    from unittest.mock import AsyncMock

    from api import database
    from api.routers import chat_routes
    from api.websocket_server import WebSocketServer

    async def events(*args, **kwargs):
        yield {"type": "done", "reply": "好"}

    orch = SimpleNamespace(components={}, process_message=AsyncMock(return_value={"reply": "好"}),
                           process_message_stream=Mock(side_effect=events))
    monkeypatch.setenv("API_KEY_ENABLED", "false")
    server = WebSocketServer(orch)
    monkeypatch.setattr(server, "_authenticate", lambda *args: {"user_id": 7, "method": "jwt"})
    monkeypatch.setattr(chat_routes, "_resolve_character_id", AsyncMock(return_value="a"))

    class DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, model, uid):
            assert uid == 7
            return SimpleNamespace(llm_config={"provider": "owner-7"}, role="viewer")

    monkeypatch.setattr(database, "_async_session", DB)

    class Socket:
        request = SimpleNamespace(path="/")

        def __init__(self):
            self.messages = []

        async def send(self, message):
            self.messages.append(json.loads(message))

        async def close(self, **kwargs):
            pass

        async def __aiter__(self):
            for sid in ["7:web:test", "8:web:private"]:
                yield json.dumps({"message": "你好", "session_id": sid, "character_id": "a",
                                  "message_type": "voice", "stream": stream})

    socket = Socket()
    await server._handler(socket)
    call = orch.process_message_stream if stream else orch.process_message
    assert call.call_count == 1
    assert call.call_args.args == ("你好", "7:web:test", "voice")
    passed = dict(call.call_args.kwargs)
    if not stream:
        assert callable(passed.pop("reply_sender"))
    assert passed == {"character_id": "a", "user_id": 7,
                      "user_llm_config": {"provider": "owner-7"}}
    assert socket.messages[-1]["type"] == "error"


@pytest.mark.asyncio
async def test_session_create_and_list_use_authenticated_owner(monkeypatch):
    from api.main_routes import CreateSessionRequest
    from api.routers import chat_routes
    from api.session_manager import SessionManager

    manager = SessionManager()
    other = manager.create_session("8")
    monkeypatch.setattr(chat_routes.deps, "sessions", manager)
    created = await chat_routes.create_session(CreateSessionRequest(user_id="8", channel="8:web"), _auth=True, user_id=7)
    assert created["session_id"].startswith("7:web:")
    listed = await chat_routes.list_sessions(_auth=True, user_id=7)
    assert listed == {"sessions": [created["session_id"]], "active_count": 1}
    assert other not in listed["sessions"]


@pytest.mark.parametrize("text", [
    "我吃过了\n你也记得吃饭",
    "我刚做完作业\n你先忙你的",
    "你知道为什么吗？\n我觉得这很有意思",
    "注意：明天可能下雨",
    "建议：先喝水，再休息",
])
def test_output_sanitizer_preserves_normal_semantics(text):
    from utils.prompt_sanitize import sanitize_reply_text

    assert sanitize_reply_text(text) == text


def test_output_sanitizer_does_not_resurrect_only_user_lines():
    from utils.prompt_sanitize import sanitize_reply_text

    assert sanitize_reply_text("用户：我先睡了") == ""


@pytest.mark.parametrize("text", ["AI：Assistant：我在", "Assistant：AI：用户：不要发我", "注意：明天可能下雨", "用户：晚安\n助手：好梦"])
def test_output_sanitizer_is_idempotent(text):
    from utils.prompt_sanitize import sanitize_reply_text

    result = sanitize_reply_text(text)
    assert sanitize_reply_text(result) == result

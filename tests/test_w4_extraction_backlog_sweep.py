"""W4 · 缺陷 C：抽取积压以持久化水位驱动 sweep 续跑。

旧活性判据只认进程内计数 `_chat_count_since_extract`（重启归零）：
达阈值前重启、长期低频会话、或一次失败后无足够新轮时，
`chat_history` 里超出 `memory_extraction_progress.last_id` 的积压
永远不会自动续抽（租约/水位表 09-26 已就位，缺的是驱动）。

本文件钉住新契约：
1. `extraction_backlog` 以水位列出全部积压的 会话×角色 对；
2. 失败结案不推水位 → 仍列积压（可重试）；
3. `sweep_extraction_backlog` 逐对派发续跑，端到端把积压抽成事实并把水位推到最大 id；
4. `after_chat` 进程计数未达阈值时回落**持久化水位**判据（同 claim 的键空间），
   重启后的旧积压 + 新一轮即可触发续抽；
5. `daily_maintenance` 内置 sweep（无新流量的低频会话每日兜底续跑）。

全部使用临时数据根与合成两账号，不触宿主库。
"""

from __future__ import annotations

from types import SimpleNamespace

from shisi.memory.legacy.memory_pipeline import MemoryPipeline
from shisi.memory.legacy.structured_memory import StructuredMemory

SESSION_A = "7:peerA@im.wechat"
SESSION_B = "9:peerB@im.wechat"


class _FakeVM:
    def search_sync(self, query, top_k=5, filter_dict=None):
        return []

    def store_chat_sync(self, *a, **k):
        return None

    def health_check(self):
        return {"ok": True}


class _RecordingExecutor:
    """替身执行器：记录派发，不后台执行。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def submit(self, fn, *args, **kwargs):  # noqa: A003 - ThreadPoolExecutor 契约
        self.calls.append((args[1], args[2]))  # (fn, session_id, character_id)
        return None

    def shutdown(self, wait=True):  # noqa: A003
        return None


class _InlineExecutor:
    """替身执行器：submit 即同步执行，令断言确定化。"""

    def submit(self, fn, *args, **kwargs):  # noqa: A003
        fn(*args, **kwargs)
        return None

    def shutdown(self, wait=True):  # noqa: A003
        return None


def _pipeline(tmp_path, **kwargs) -> MemoryPipeline:
    sm = StructuredMemory(str(tmp_path / "w4sweep.sqlite"))
    return MemoryPipeline(vector_memory=_FakeVM(), structured_memory=sm, llm_gateway=None, **kwargs)


def _max_chat_id(sm: StructuredMemory) -> int:
    with sm._conn() as conn:  # noqa: SLF001 - 钉原始写入序号
        return conn.execute("SELECT COALESCE(MAX(id),0) FROM chat_history").fetchone()[0]


def _progress_last_id(sm: StructuredMemory, session_id: str, character_id: str) -> int | None:
    with sm._conn() as conn:  # noqa: SLF001
        row = conn.execute(
            "SELECT last_id FROM memory_extraction_progress WHERE session_id=? AND character_id=?",
            (session_id, character_id),
        ).fetchone()
        return None if row is None else int(row[0])


# ── 1/2. 积压盘点：以持久化水位为准 ──


def test_extraction_backlog_lists_pairs_past_watermark(tmp_path):
    sm = StructuredMemory(str(tmp_path / "backlog.db"))
    try:
        for _ in range(6):
            sm.add_chat("user", "我喜欢吃火锅", session_id=SESSION_A, character_id="a")
        for _ in range(4):
            sm.add_chat("user", "我下周去北京出差", session_id=SESSION_B, character_id="b")

        rows = {(r["session_id"], r["character_id"]): r["pending"] for r in sm.extraction_backlog()}
        assert rows == {(SESSION_A, "a"): 6, (SESSION_B, "b"): 4}

        claimed = sm.claim_extraction(SESSION_A, "a")
        assert claimed is not None
        token, window = claimed
        sm.finish_extraction(SESSION_A, "a", token, window[-1]["id"], True)

        rows = {(r["session_id"], r["character_id"]): r["pending"] for r in sm.extraction_backlog()}
        assert rows == {(SESSION_B, "b"): 4}, "水位追平的对必须退出积压，未抽取的对不受影响"
    finally:
        sm.close()


def test_failed_finish_keeps_backlog_retryable(tmp_path):
    sm = StructuredMemory(str(tmp_path / "retry.db"))
    try:
        for _ in range(3):
            sm.add_chat("user", "叫我起床", session_id=SESSION_A, character_id="")
        claimed = sm.claim_extraction(SESSION_A, "")
        assert claimed is not None
        token, window = claimed
        sm.finish_extraction(SESSION_A, "", token, window[-1]["id"], False)

        rows = {(r["session_id"], r["character_id"]): r["pending"] for r in sm.extraction_backlog()}
        assert rows == {(SESSION_A, ""): 3}, "失败结案不得推进水位：同一窗口必须留在积压里等待续跑"
        assert _progress_last_id(sm, SESSION_A, "") == 0
    finally:
        sm.close()


# ── 3. sweep 派发 ──


def test_sweep_dispatches_each_stalled_pair(tmp_path):
    mp = _pipeline(tmp_path)
    sm = mp.sm
    try:
        for _ in range(2):
            sm.add_chat("user", "我喜欢吃火锅", session_id=SESSION_A, character_id="")
        sm.add_chat("user", "我姐姐在昆明工作", session_id=SESSION_B, character_id="k")
        recorder = _RecordingExecutor()
        mp._executor = recorder  # noqa: SLF001 - 替身执行器，钉派发不含后台竞态

        dispatched = mp.sweep_extraction_backlog()
        assert dispatched == 2
        assert {recorder.calls[0], recorder.calls[1]} == {(SESSION_A, ""), (SESSION_B, "k")}

        # 再扫一遍前先真正推进水位：续跑完成后 sweep 归零，不空转
        recorder2 = _InlineExecutor()
        mp._executor = recorder2  # noqa: SLF001
        mp.sweep_extraction_backlog()
        assert mp.sweep_extraction_backlog() == 0, "水位追平后不得重复派发"
    finally:
        mp._executor.shutdown(wait=False)
        sm.close()


def test_sweep_drains_backlog_end_to_end(tmp_path):
    """积压续跑的终态：旧历史抽成事实、水位推到最大写入序号。"""
    mp = _pipeline(tmp_path)
    sm = mp.sm
    try:
        sm.add_chat("user", "我喜欢吃火锅", session_id=SESSION_A, character_id="")
        sm.add_chat("assistant", "记住了", session_id=SESSION_A, character_id="")
        sm.add_chat("user", "我下周去北京出差", session_id=SESSION_A, character_id="")
        mp._executor = _InlineExecutor()  # noqa: SLF001

        assert mp.sweep_extraction_backlog() == 1
        facts = sm.get_facts(user_key=StructuredMemory.user_key_from_session(SESSION_A))
        assert any("火锅" in f["fact"] for f in facts), "积压历史必须被真正抽取入库"
        assert _progress_last_id(sm, SESSION_A, "") == _max_chat_id(sm), "续跑后水位必须追平"
        assert mp.sweep_extraction_backlog() == 0
    finally:
        mp._executor.shutdown(wait=False)
        sm.close()


# ── 4. after_chat：进程计数未达阈值时回落持久化水位 ──


def test_after_chat_resumed_process_triggers_on_persisted_backlog(tmp_path):
    """模拟重启后首条消息：计数从 0 起，但持久化积压已达阈值 → 必须触发续抽。"""
    mp = _pipeline(tmp_path, fact_extract_interval=3)
    sm = mp.sm
    try:
        # 旧进程抽到一半重启：这 2 条用户消息在库里、但在水位之上（无 progress 行 = 水位 0）
        sm.add_chat("user", "我姐姐在昆明工作", session_id=SESSION_A, character_id="")
        sm.add_chat("user", "我喜欢吃火锅", session_id=SESSION_A, character_id="")

        recorder = _InlineExecutor()  # noqa: F841 - 同步执行，after_chat 返回即完成续抽
        mp._executor = recorder  # noqa: SLF001
        mp.after_chat("我下周去北京出差", "好", session_id=SESSION_A)

        assert _progress_last_id(sm, SESSION_A, "") == _max_chat_id(sm), (
            "进程计数未达阈值不能否掉持久化积压——旧积压+新一轮必须续跑并推进水位"
        )
    finally:
        mp._executor.shutdown(wait=False)
        sm.close()


def test_after_chat_without_backlog_does_not_trigger(tmp_path):
    """反向边界：水位之上不足阈值（正常流式节流）→ 不触发。"""
    mp = _pipeline(tmp_path, fact_extract_interval=3)
    sm = mp.sm
    try:
        recorder = _RecordingExecutor()
        mp._executor = recorder  # noqa: SLF001
        mp.after_chat("嗯", "嗯", session_id=SESSION_A)
        assert recorder.calls == [], "水位之上仅 1 条用户消息（阈值 3）不得触发抽取"
    finally:
        mp._executor.shutdown(wait=False)
        sm.close()


# ── 5. daily_maintenance 兜底 sweep ──


def test_daily_maintenance_sweeps_backlog(tmp_path, monkeypatch):
    mp = _pipeline(tmp_path)
    sm = mp.sm
    seen: list[int] = []
    try:
        monkeypatch.setattr(mp, "_apply_forgetting", lambda: None)
        monkeypatch.setattr(mp, "_cleanup_low_confidence_facts", lambda: None)
        mp.ds = SimpleNamespace(summarize_day=lambda rows: "", save_summary=lambda k, v: None)
        monkeypatch.setattr(mp, "sweep_extraction_backlog", lambda **kw: seen.append(1) or 0)

        mp.daily_maintenance()
        assert seen == [1], "每日维护必须包含以持久化水位驱动的积压续跑"
    finally:
        mp._executor.shutdown(wait=False)
        sm.close()


def test_daily_maintenance_real_sweep_recovers_stalled_session(tmp_path, monkeypatch):
    """端到端：低频会话积压经每日维护续跑，事实入库、水位追平。"""
    mp = _pipeline(tmp_path)
    sm = mp.sm
    try:
        monkeypatch.setattr(mp, "_apply_forgetting", lambda: None)
        monkeypatch.setattr(mp, "_cleanup_low_confidence_facts", lambda: None)
        mp.ds = SimpleNamespace(summarize_day=lambda rows: "", save_summary=lambda k, v: None)
        sm.add_chat("user", "我喜欢吃火锅", session_id=SESSION_B, character_id="")
        mp._executor = _InlineExecutor()  # noqa: SLF001

        mp.daily_maintenance()
        facts = sm.get_facts(user_key=StructuredMemory.user_key_from_session(SESSION_B))
        assert any("火锅" in f["fact"] for f in facts)
        assert _progress_last_id(sm, SESSION_B, "") == _max_chat_id(sm)
    finally:
        mp._executor.shutdown(wait=False)
        sm.close()

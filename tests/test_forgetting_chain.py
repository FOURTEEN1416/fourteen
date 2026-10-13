"""遗忘链「永不老去」根治（2026-10-12 记忆域批 item1）红测。

三类失效同根（生产 215/216 条 conf≥0.7 → λ=0.01 档 → ~289 天才达删阈，TTL 名存实亡）：
① 扫描窗口方向性错误——get_facts 固定 `ORDER BY updated_at DESC LIMIT ?`
   （structured_memory.py get_facts），_apply_forgetting 只评估最新 1000 条，
   存量超限后最老事实永生；
② _cleanup_low_confidence_facts 每轮只取最新 50 条且首窗无低置信即 break 早退
   → 老页低置信行永不清理；
③ confidence 全部变更路径是抬升（near-dup max() / curator），衰减权重
   ForgettingManager.retrieval_weight 无任何写回者 → 置信度只升不降。

红测先行：a/b/c 组用例应先红后绿；d 组为兼容护栏（现行绿，防实现破坏替身）。
造数纪律：updated_at 一律显式 SQL 回拨（CURRENT_TIMESTAMP 秒级精度，同秒插入
排序不稳定会致红测偶发不红）；批量注入走直插 SQL（add_fact_receipt 的 near-dup
扫描是组内全量 active 行 O(n²)，逐条插入会拖垮测试）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from shisi.memory.legacy.memory_pipeline import MemoryPipeline
from shisi.memory.legacy.structured_memory import StructuredMemory

OLD_STAMP_FLOOR = 0.35  # 衰减写回下限（高于检索过滤线 0.3 与清理线 0.2）


def _utc_stamp(days_ago: float) -> str:
    """生产同构时间戳：SQLite CURRENT_TIMESTAMP 形态（naive UTC）。"""
    return (
        datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%d %H:%M:%S")


def _raw_insert_facts(sm: StructuredMemory, rows: list[tuple[str, float, str, int]]) -> None:
    """直插 user_facts（绕开 near-dup O(n²) 扫描）。

    rows: (fact, confidence, updated_at, access_count)
    """
    with sm.get_connection(write=True) as conn:
        conn.executemany(
            "INSERT INTO user_facts "
            "(fact, category, confidence, source, user_key, access_count, status, topics, last_seen_at, created_at, updated_at) "
            "VALUES (?, 'general', ?, '', '', ?, 'active', '', ?, ?, ?)",
            [(f, c, acc, ts, ts, ts) for f, c, ts, acc in rows],
        )
        conn.commit()


def _fact_row(sm: StructuredMemory, fact_id: int) -> dict | None:
    with sm.get_connection() as conn:
        row = conn.execute(
            "SELECT id, fact, confidence, status, updated_at FROM user_facts WHERE id = ?",
            (fact_id,),
        ).fetchone()
    return dict(row) if row else None


def _recycled_ids(sm: StructuredMemory) -> set[str]:
    with sm.get_connection() as conn:
        rows = conn.execute("SELECT memory_id FROM memory_recycle_bin").fetchall()
    return {str(r["memory_id"]) for r in rows}


def _make_mp(sm: StructuredMemory) -> MemoryPipeline:
    return MemoryPipeline(vector_memory=SimpleNamespace(), structured_memory=sm)


@pytest.fixture()
def sm(tmp_path):
    memory = StructuredMemory(str(tmp_path / "forgetting.db"))
    yield memory
    memory.close()


# ── a. 低置信度清理必须覆盖最新窗口之外的老行 ─────────────────────


def test_cleanup_low_confidence_reaches_beyond_newest_window(sm):
    """现行：首窗（最新 50 条全高置信）无低置信即 break → 老行永生 = 红。"""
    _raw_insert_facts(sm, [("最老的低置信事实", 0.1, _utc_stamp(160), 0)])
    _raw_insert_facts(
        sm,
        [(f"新事实{i}", 0.9, _utc_stamp(0.05), 0) for i in range(55)],
    )
    old = _fact_row(sm, 1)
    assert old is not None and old["confidence"] == pytest.approx(0.1)
    _make_mp(sm)._cleanup_low_confidence_facts()
    assert _fact_row(sm, 1) is None, "conf=0.1 老行必须被清理（现行只扫最新窗 = 红测根因）"
    assert "1" in _recycled_ids(sm), "清理必须走回收站（B5 只增不减）"


# ── b. 遗忘扫描必须覆盖最新 1000 条之外的老行 ─────────────────────


def test_apply_forgetting_covers_beyond_newest_1000(sm):
    """现行：get_facts(updated_at DESC, limit=1000) 只见新行 → 老行永生 = 红。"""
    _raw_insert_facts(sm, [("最老的必死事实", 0.1, _utc_stamp(90), 0)])
    _raw_insert_facts(
        sm,
        [(f"垫窗新事实{i}", 0.9, _utc_stamp(0.05), 0) for i in range(1001)],
    )
    assert _fact_row(sm, 1) is not None
    _make_mp(sm)._apply_forgetting()
    assert _fact_row(sm, 1) is None, "conf=0.1×90 天按模型必死，最老行必须进入遗忘判死"
    assert "1" in _recycled_ids(sm)


def test_cursor_scan_advances_across_batches(sm):
    """id 游标必须批间推进：最老行**后插**（id 最大、updated_at 最老）也要扫到。

    与 a/b 互补：那两例的老行 id=1 在任何首窗内，只能证明「升序方向」；
    本例老行 id=120 不在首批（id 1..50），单批化突变（不推进游标）在此变红。
    """
    _raw_insert_facts(sm, [(f"先插新事实{i}", 0.9, _utc_stamp(0.05), 0) for i in range(119)])
    _raw_insert_facts(sm, [("最后才插入的最老事实", 0.1, _utc_stamp(90), 0)])
    batches = list(sm.iter_active_facts_oldest_first(batch_size=50))
    assert [len(b) for b in batches] == [50, 50, 20], "游标必须分页推进（批大小 50）"
    all_ids = {r["id"] for b in batches for r in b}
    assert 120 in all_ids, "后插的最老行必须被游标扫描覆盖"
    _make_mp(sm)._cleanup_low_confidence_facts()
    assert _fact_row(sm, 120) is None, "维护清理必须经分页推进扫到 id=120 的老行"


# ── c. 每日维护衰减写回（confidence-only，不刷衰减时钟）────────────


def test_daily_maintenance_decays_stale_confidence(sm):
    """60 天未强化 conf=0.8 → daily_maintenance 后严格下降且行仍 active、updated_at 未变。"""
    _raw_insert_facts(sm, [("我喜欢吃辣", 0.8, _utc_stamp(60), 0)])
    before = _fact_row(sm, 1)
    assert before is not None
    _make_mp(sm).daily_maintenance()
    after = _fact_row(sm, 1)
    assert after is not None
    assert after["confidence"] < before["confidence"], "置信度必须衰减写回（现行只升不降 = 红测根因）"
    assert after["status"] == "active", "衰减本身不删除；判死仍由遗忘链 should_delete 承担"
    assert after["updated_at"] == before["updated_at"], "衰减时钟不得自刷新（confidence-only UPDATE）"
    assert after["confidence"] >= OLD_STAMP_FLOOR, "衰减有底：不得把行推入检索过滤线以下"


def test_decay_writeback_exempts_reinforced_today_and_durable(sm):
    """当日行只受 λ_high 档 1 天上界内的微衰（≤1%）；恒常事实豁免零衰减。

    注：连续衰减函数在 days>0 时必有非零衰减，「严格不降」只属于 days=0 与
    豁免行——当日行断言用公式上界（1-e^(-0.01·1)≈1%），与运行时刻无关。
    """
    _raw_insert_facts(sm, [("昨天聊到的天气", 0.8, _utc_stamp(0.05), 0)])
    _raw_insert_facts(sm, [("宿舍没有空调也没有暖气", 0.8, _utc_stamp(60), 0)])
    _make_mp(sm).daily_maintenance()
    fresh = _fact_row(sm, 1)
    durable = _fact_row(sm, 2)
    assert fresh is not None and durable is not None
    daily_cap = 0.8 * (1 - 2.718281828459045 ** -0.01)
    assert 0.8 - fresh["confidence"] <= daily_cap + 1e-9, "当日行降幅必须在 λ_high 档 1 天上界内，不得显著降档"
    assert durable["confidence"] == pytest.approx(0.8), "恒常事实必须豁免衰减"


def test_decay_writeback_rate_limited_to_500_rows(sm):
    """单次维护衰减写回限速 ≤500 行（防大库单轮写放大）。"""
    _raw_insert_facts(
        sm,
        [(f"待衰减事实{i}", 0.8, _utc_stamp(60), 0) for i in range(600)],
    )
    _make_mp(sm).daily_maintenance()
    with sm.get_connection() as conn:
        decayed = conn.execute(
            "SELECT COUNT(*) FROM user_facts WHERE confidence < 0.79"
        ).fetchone()[0]
    assert int(decayed) == 500, "衰减写回单次维护限速 ≤500 行"


# ── d. 兼容护栏：缺新方法的替身不得炸 daily_maintenance ───────────


class _BareSM:
    """评审必改6 兼容替身：只有旧方法、无任何新扫描/衰减方法（仿 test_memory_pipeline.FakeStructuredMemory 面貌）。"""

    def get_facts(self, *args, **kwargs):
        return []

    def get_chats_for_day(self, day=None):
        return []

    def extraction_backlog(self, limit=200):
        return []


def test_daily_maintenance_with_legacy_fake_memory_no_attributeerror(tmp_path, caplog):
    """hasattr 降级兜底：替身缺新方法时走静默跳过路径，不抛 AttributeError、
    也不得进入异常兜底路径（否则每日维护会刷「衰减失败」warning 噪声）。"""
    import logging

    real = StructuredMemory(str(tmp_path / "init.db"))
    mp = _make_mp(real)
    real.close()
    mp.sm = _BareSM()  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING, logger="memory_pipeline"):
        result = mp.daily_maintenance()
    noise = [r for r in caplog.records if "Decay writeback" in r.getMessage()]
    assert not noise, "缺新方法必须走 hasattr 静默跳过，而非异常路径（warning 噪声=突变信号）"
    assert result is None or isinstance(result, str)

"""W8 缺陷 I：好感度衰减的重复扣减与重启基准。

`DecayEngine.calculate_decay` 返回的是**自最后一次交互起累计**应扣的总量
（`rate × (days_since − grace)`），而 `AffinityEnhancer.apply_decay` 每次都把这个
累计量从当前值里再扣一遍，却不推进任何衰减基准：

- 同一时刻重复调用（每日兜底重算、`/api/shisi/affinity/{id}/decay` 手点、多 worker
  同日各跑一次）→ 同一天的衰减被逐次重复扣除；
- 逐日推进时第 N 天扣的是 `rate×(N−grace)` 而不是当天的 `rate`，累计量按**平方**增长
  （rate=0.1/grace=3：第 5、6 天正确合计 0.3，旧实现 0.2+0.3=0.5）；
- 重启后 `_restore_from_audit` 取每键 `MAX(id)` 行的 `created_at` 当**交互**基准，而
  衰减行（reason='decay'）正是它自己刚写的 → 交互基准被"上次衰减时刻"顶掉，宽限期
  从衰减那一刻重新起算，重启频繁时衰减整体停摆。

另有读侧同源缺陷：`AffinityMapper.sync` 用内存缓存 `_last_shisi` 当“当前值”，
衰减改过真值后缓存不失效 → 已扣掉的量会被再当成一次待降的差分**重复扣减**。

修复后语义：`grace 结束时刻`与`上次已扣时刻`取较晚者为窗口起点，扣完推进基准；
恢复时值取最新行（含衰减行）、交互基准只取非衰减行、衰减基准取最新衰减行。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from shisi.affinity.decay_engine import DecayEngine
from shisi.affinity.enhancer import AffinityEnhancer
from shisi.affinity.mapper import AffinityMapper

DAY0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
RATE, GRACE = 0.1, 3


def _engine() -> DecayEngine:
    return DecayEngine(decay_rate=RATE, grace_period_days=GRACE)


def _enhancer(tmp_path, *, value: float, last_interaction: datetime) -> AffinityEnhancer:
    enh = AffinityEnhancer(db_path=tmp_path / "aff.db")
    enh._decay = _engine()  # 钉住刻度与宽限期，不随 config/shisi.yaml 漂移
    enh._values["u1::c1"] = value
    enh._last_interaction["u1::c1"] = last_interaction
    return enh


def _db_with_rows(tmp_path, rows: list[tuple]):
    """rows: (character_id, old, new, delta, reason, created_at)"""
    db = tmp_path / "aff.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE affinity_records ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " character_id TEXT NOT NULL,"
        " old_value REAL NOT NULL, new_value REAL NOT NULL,"
        " delta REAL NOT NULL, reason TEXT, source TEXT,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    conn.executemany(
        "INSERT INTO affinity_records (character_id, old_value, new_value, delta, reason, created_at)"
        " VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db


# ═══════════════════════════════════════════════════════════
# 1. 同一窗口不得重复扣减；逐日累计量守恒
# ═══════════════════════════════════════════════════════════


def test_same_instant_repeat_deducts_only_once(tmp_path):
    enh = _enhancer(tmp_path, value=50.0, last_interaction=DAY0)
    day5 = DAY0 + timedelta(days=5)

    first = enh.apply_decay("c1", now=day5, user_id="u1")
    again = enh.apply_decay("c1", now=day5, user_id="u1")

    assert first == pytest.approx(0.2)
    assert again == 0.0, "同一时刻二次调用把同一天的衰减又扣了一遍"
    assert enh.get_value("c1", user_id="u1") == pytest.approx(49.8)


def test_cumulative_decay_is_linear_not_quadratic(tmp_path):
    """grace=3、rate=0.1：第 5 天扣 0.2、第 6 天扣 0.1，合计 0.3（旧实现 0.5）。"""
    enh = _enhancer(tmp_path, value=50.0, last_interaction=DAY0)

    d5 = enh.apply_decay("c1", now=DAY0 + timedelta(days=5), user_id="u1")
    d6 = enh.apply_decay("c1", now=DAY0 + timedelta(days=6), user_id="u1")
    d7 = enh.apply_decay("c1", now=DAY0 + timedelta(days=7), user_id="u1")

    assert (d5, d6, d7) == (pytest.approx(0.2), pytest.approx(0.1), pytest.approx(0.1))
    assert enh.get_value("c1", user_id="u1") == pytest.approx(50.0 - 0.4)


def test_decay_all_matches_per_key_cumulative(tmp_path):
    """decay_all 连跑三天，总额必须等于一次性算到同一时刻的累计量。"""
    enh = _enhancer(tmp_path, value=50.0, last_interaction=DAY0)
    for offset in (5, 6, 7):
        enh.decay_all(now=DAY0 + timedelta(days=offset))
    single = DecayEngine(decay_rate=RATE, grace_period_days=GRACE).calculate_decay(50.0, DAY0, DAY0 + timedelta(days=7))
    assert enh.get_value("c1", user_id="u1") == pytest.approx(50.0 - single)


def test_decay_never_goes_below_floor(tmp_path):
    enh = _enhancer(tmp_path, value=0.15, last_interaction=DAY0)
    amount = enh.apply_decay("c1", now=DAY0 + timedelta(days=30), user_id="u1")
    assert amount == pytest.approx(0.15)
    assert enh.get_value("c1", user_id="u1") == 0.0


# ═══════════════════════════════════════════════════════════
# 2. 重启：值、交互基准、衰减基准各归其位
# ═══════════════════════════════════════════════════════════


def _restarted(db) -> AffinityEnhancer:
    """模拟进程重启：从审计库存恢复，并钉住刻度/宽限期。"""
    enh = AffinityEnhancer(db_path=db)
    enh._decay = _engine()
    return enh


def test_restart_restores_three_baselines(tmp_path):
    """衰减行不得冒充交互基准；重启后只扣新经过的一天。"""
    day5 = DAY0 + timedelta(days=5)
    db = _db_with_rows(tmp_path, [
        ("u1::c1", 47.0, 50.0, 3.0, "chat", DAY0.strftime("%Y-%m-%d %H:%M:%S")),
        # day5 已由上一进程扣过 0.2 → 49.8
        ("u1::c1", 50.0, 49.8, -0.2, "decay", day5.strftime("%Y-%m-%d %H:%M:%S")),
    ])
    enh = _restarted(db)

    assert enh.get_value("c1", user_id="u1") == pytest.approx(49.8)
    assert enh._last_interaction["u1::c1"] == DAY0, "交互基准被衰减行顶掉，宽限期会重新起算"
    assert enh._last_decay_at["u1::c1"] == day5

    day6 = DAY0 + timedelta(days=6)
    d6 = enh.apply_decay("c1", now=day6, user_id="u1")
    assert d6 == pytest.approx(0.1), "要么重复扣了已扣窗口，要么整体停摆"
    assert enh.get_value("c1", user_id="u1") == pytest.approx(49.7)


def test_restart_immediately_after_decay_deducts_nothing(tmp_path):
    """衰减刚落库就重启：不得把同一段窗口再扣一次。"""
    day5 = DAY0 + timedelta(days=5)
    db = _db_with_rows(tmp_path, [
        ("u1::c1", 47.0, 50.0, 3.0, "chat", DAY0.strftime("%Y-%m-%d %H:%M:%S")),
        ("u1::c1", 50.0, 49.8, -0.2, "decay", day5.strftime("%Y-%m-%d %H:%M:%S")),
    ])
    enh = _restarted(db)
    assert enh.apply_decay("c1", now=day5, user_id="u1") == 0.0


def test_points_only_key_still_decays(tmp_path):
    """点存兜底回填的键没有衰减行：按 grace 起算，首日就该扣到累计量。"""
    enh = _enhancer(tmp_path, value=30.0, last_interaction=DAY0)
    enh._values["u9::c9"] = 30.0
    enh._last_interaction["u9::c9"] = DAY0
    assert enh.apply_decay("c9", now=DAY0 + timedelta(days=5), user_id="u9") == pytest.approx(0.2)


# ═══════════════════════════════════════════════════════════
# 3. 读侧：mapper 的差分基准必须是真值，不得用陈旧缓存
# ═══════════════════════════════════════════════════════════


def test_mapper_realigns_to_same_target_after_decay(tmp_path):
    """衰减把真值拉低后，同一目标点数应逐轮爬回（±3 爬坡），而不是永久卡住。"""
    enh = _enhancer(tmp_path, value=50.0, last_interaction=DAY0)
    mapper = AffinityMapper(enhancer=enh)
    points = mapper.to_emotion(50.0)

    mapper.sync("c1", points, user_id="u1")  # 建立缓存：值与缓存同为 50
    enh._values["u1::c1"] = 46.5  # 模拟夜间衰减 3.5 已落账
    enh._last_interaction["u1::c1"] = DAY0

    mapper.sync("c1", points, user_id="u1")
    assert enh.get_value("c1", user_id="u1") == pytest.approx(49.5), (
        "mapper 仍拿衰减前的缓存当现值，差分恒 0 → 衰减后再不对齐"
    )


def test_mapper_does_not_deduct_decay_twice(tmp_path):
    """目标下降 1 分时，衰减已扣的 3.5 不得再作为差分扣一次。"""
    enh = _enhancer(tmp_path, value=50.0, last_interaction=DAY0)
    mapper = AffinityMapper(enhancer=enh)

    mapper.sync("c1", mapper.to_emotion(48.0), user_id="u1")
    assert enh.get_value("c1", user_id="u1") == pytest.approx(48.0)

    enh._values["u1::c1"] = 46.5  # 衰减 1.5 已落账
    enh._last_interaction["u1::c1"] = DAY0

    mapper.sync("c1", mapper.to_emotion(47.0), user_id="u1")
    # 真值 46.5 → 目标 47：应 +0.5 爬向目标；旧实现按缓存 48 算出 −1 再降一次
    assert enh.get_value("c1", user_id="u1") == pytest.approx(47.0), (
        "mapper 以陈旧缓存为差分基准：已扣的 1.5 二次生效"
    )


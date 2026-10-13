"""召回排序消费衰减权重（2026-10-12 记忆域批 item3）红测。

缺陷：`_merge_fact_recalls` 合并序硬编码「keyword 路全部在前、vector 路按返回
序补后」，confidence/access_count/updated_at 三因子在召回排序零消费者——
「越回忆越牢、久未强化者沉底」的遗忘模型只在删除路径生效，与 item1 衰减写回
不成闭环（Generative Agents recency×importance×relevance 对标，arXiv:2304.03442）。

修法（保守融合，不破契约）：keyword 层仍整体先于 vector 层（2026-09-26
「精确匹配优先」契约）；**层内**按 ForgettingManager 衰减权重降序**稳定**重排，
provenance 平行账同步重排。

红测先行：①② 现行保持喂入序 = 红；③④ 为契约护栏（现行绿，防实现越界）。
造数纪律：updated_at 显式 SQL 回拨（秒级精度，非同秒，排序稳定）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from shisi.memory.legacy.memory_pipeline import MemoryPipeline
from shisi.memory.legacy.structured_memory import StructuredMemory

UK = "7:wxid_order@im.wechat"
FACT_A = "用户喜欢吃辣"      # A：conf=0.9 当日 reinforce → 权重高
FACT_B = "用户养了一只橘猫"  # B：conf=0.5 回拨 60 天 access=0 → 权重低

# 身份组隔离属性测试用（get_facts_by_ids 回读面，M6 干净突变目标）
GRP_A_KEY = "2:wxid_grp_a@im.wechat"
GRP_B_KEY = "4:wxid_grp_b@im.wechat"  # 与 A 无关联（不同身份组）


def _utc_stamp(days_ago: float) -> str:
    return (
        datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture()
def env(tmp_path):
    sm = StructuredMemory(str(tmp_path / "recall.db"))
    a_id = sm.add_fact(FACT_A, category="preference", confidence=0.9, user_key=UK)
    b_id = sm.add_fact(FACT_B, category="general", confidence=0.5, user_key=UK)
    with sm.get_connection(write=True) as conn:
        conn.execute("UPDATE user_facts SET updated_at=? WHERE id=?", (_utc_stamp(60), b_id))
        conn.commit()
    mp = MemoryPipeline(vector_memory=SimpleNamespace(), structured_memory=sm)
    try:
        yield sm, mp, a_id, b_id
    finally:
        mp._executor.shutdown(wait=True)
        sm.close()


def _rows(sm: StructuredMemory) -> dict[str, dict]:
    """取两行全字段（get_facts 按 updated_at DESC → A 在前）。"""
    rows = {str(r["fact"]): r for r in sm.get_facts(user_key=UK, min_confidence=0.0)}
    assert set(rows) == {FACT_A, FACT_B}
    return rows


# ── ① 向量路层内权重重排 ─────────────────────────────────────────


def test_vector_hits_reordered_by_recall_weight(env):
    sm, mp, a_id, b_id = env
    merged, provenance = mp._merge_fact_recalls(
        structured=[],
        vector=[{"content": FACT_B}, {"content": FACT_A}],  # 喂入序 B,A（现行原样保持）
        user_key=UK,
    )
    assert [m["fact"] for m in merged] == [FACT_A, FACT_B], "层内必须按衰减权重降序（B 沉底）"
    assert [p["fact"] for p in provenance] == [FACT_A, FACT_B], "provenance 平行账必须同步重排"
    assert all(p["recall"] == "vector" for p in provenance)
    assert [m["id"] for m in merged] == [a_id, b_id], "重排不得错乱主键映射"


# ── ② keyword 路层内权重重排 ─────────────────────────────────────


def test_keyword_hits_reordered_within_layer(env):
    sm, mp, _a, _b = env
    rows = _rows(sm)
    merged, provenance = mp._merge_fact_recalls(
        structured=[rows[FACT_B], rows[FACT_A]],  # 喂入序 B,A
        vector=[],
        user_key=UK,
    )
    assert [m["fact"] for m in merged] == [FACT_A, FACT_B], "keyword 层内同样按权重序"
    assert [p["fact"] for p in provenance] == [FACT_A, FACT_B]
    assert provenance[0]["recall"] == "keyword"


# ── ③ 跨层契约护栏：keyword 层恒在 vector 层之前 ─────────────────


def test_cross_layer_contract_keyword_still_before_vector(env):
    """弱权重 keyword 行仍先于强权重 vector 行（2026-09-26「精确匹配优先」不动）。"""
    sm, mp, _a, _b = env
    rows = _rows(sm)
    merged, provenance = mp._merge_fact_recalls(
        structured=[rows[FACT_B]],          # keyword 弱权重（conf 0.5 × 60 天）
        vector=[{"content": FACT_A}],       # vector 强权重（conf 0.9 当日）
        user_key=UK,
    )
    assert [m["fact"] for m in merged] == [FACT_B, FACT_A], "跨层契约：keyword 层整体在前"
    assert [p["recall"] for p in provenance] == ["keyword", "vector"]


# ── ④ 等价副本护栏：重排不产生重复注入 ──────────────────────────


def test_equivalent_duplicate_not_duplicated_by_reorder(env):
    """同文两路各召回一次仍合并为一条（_norm_fact_key 去重），recall 标 both。"""
    sm, mp, _a, _b = env
    rows = _rows(sm)
    merged, provenance = mp._merge_fact_recalls(
        structured=[rows[FACT_A]],
        vector=[{"content": FACT_A}],
        user_key=UK,
    )
    assert len(merged) == 1
    assert provenance[0]["recall"] == "both"


# ── ⑤ get_facts_by_ids 身份组隔离属性（对抗验收 M6 干净突变目标）──


def test_get_facts_by_ids_enforces_identity_group_isolation(tmp_path):
    """跨身份组借 id 取行必须被拒（隔离硬约束的属性断言，非实现巧合）。

    对抗验收 M6：干净突变（同时删身份组过滤段与 params.extend）使 A 组键
    直接取回 B 组行——本用例在该突变下变红，锁定回读面的组归属过滤。
    user_key=None（管理/维护路径）不过滤，与 get_facts 口径一致。
    """
    sm = StructuredMemory(str(tmp_path / "byids.db"))
    try:
        a_id = sm.add_fact(FACT_A, category="preference", confidence=0.9, user_key=GRP_A_KEY)
        b_id = sm.add_fact(FACT_B, category="general", confidence=0.5, user_key=GRP_B_KEY)
        assert sm.identity_keys(GRP_A_KEY) == [GRP_A_KEY], "前置：两键无关联"
        rows = sm.get_facts_by_ids([a_id, b_id], user_key=GRP_A_KEY)
        assert [r["id"] for r in rows] == [a_id], "同组自有行必须取回"
        assert all(r["user_key"] == GRP_A_KEY for r in rows), "他组行不得借 id 越权取回（M6 属性）"
        assert sm.get_facts_by_ids([b_id], user_key=GRP_A_KEY) == [], "单查他组 id 同样必须为空"
        assert len(sm.get_facts_by_ids([a_id, b_id])) == 2, "user_key=None 管理路径不过滤"
    finally:
        sm.close()

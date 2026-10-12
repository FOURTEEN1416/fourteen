"""恒常事实（居住环境等长期有效陈述）召回保证回归（2026-10-12 生产事故）。

生产事故链（取证 fact_status=extracted_not_recalled）：用户前夜明说
「我们这边没有空调，没有暖气」，15 秒后抽取落库（user_facts，confidence=1.0），
次日 08:07 轮还召回过一次；但 08:13/08:36/08:37 用户话题切到早操/早八后，
词面（structured_memory.search_facts 的整句 FTS/LIKE）与语义近邻都带不出它
——召回命中了**别的事实**，recent_fallback（仅 `not context["facts"]` 时触发）
不生效，恒常事实整轮缺席，角色 08:36 仍在问「空调开没开别冻着了」。

钉住（全部在记忆召回层内，不动 orchestrator 注入端）：
1. retrieve_context 补锚：恒常事实与当轮查询词面无关，必须保证进 facts，
   出处 recall="durable_anchor"（回放槽可区分）；
2. 补锚严格按 user_key 隔离：他人 / 孤儿 / 已删除事实不得注入；
3. 语义召回本身已带上时不重复注入、不改写出处；
4. 补锚条数上限 DURABLE_ANCHOR_LIMIT，防 prompt 膨胀；
5. get_memory_context 的「# 关于用户」选择窗口不得挤出恒常事实；
6. is_durable_env_fact 判据保守性：瞬时陈述/疑问句/无关事实不得误锚。

隔离：真实 StructuredMemory 落临时库；向量后端替身；不触碰生产 data/。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from shisi.memory.legacy.memory_pipeline import (
    DURABLE_ANCHOR_LIMIT,
    MemoryPipeline,
    is_durable_env_fact,
)
from shisi.memory.legacy.structured_memory import StructuredMemory

SESSION_A = "1:peer-a@im.wechat"
SESSION_B = "2:peer-b@im.wechat"
USER_MSG_ENV = "我们这边没有空调，没有暖气"  # 生产用户原话（任务取证引用）
ENV_FACT = "用户说他们那边没有空调，没有暖气"  # LLM 抽取后的落库形态
RULE_FACT = "用户曾说：我们这边没有空调，没有暖气"  # 规则模式落库形态（整句原话）
OFF_TOPIC_QUERY = "今天早操累死了"  # 08:13 轮：话题切到早操
ENV_QUERY = "空调开没开别冻着了"  # 08:36 轮：问句带「空调」但目标事实仍缺席
DISTRACTOR_FACT = "用户早八的课程排得很满"  # 召回命中的「别的事实」


class FakeVectorMemory:
    """向量后端替身：与真实现同为协程函数，命中列表可控。"""

    def __init__(self, hits: list[dict] | None = None):
        self.hits = hits or []
        self._collections: dict = {}

    def hits_for(self, hits: list[dict]) -> None:
        self.hits = hits

    async def _search(self, collection, query, top_k=5, where=None):
        return [dict(h) for h in self.hits[:top_k]]

    @staticmethod
    def vector_item(content: str, user_key: str) -> dict:
        return {
            "content": content,
            "metadata": {"user_key": user_key, "category": "general", "confidence": 0.6},
            "distance": 0.2,
        }

    def store_chat_sync(self, *a, **k):
        return None

    def search_sync(self, query, top_k=5, filter_dict=None):
        return []

    def health_check(self):
        return {"available": True}


def _make(tmp: Path, hits: list[dict] | None = None):
    sm = StructuredMemory(str(tmp / "durable.sqlite"))
    vm = FakeVectorMemory(hits)
    mp = MemoryPipeline(vector_memory=vm, structured_memory=sm, llm_gateway=None)
    return sm, vm, mp


def _seed_recall_hits_distractor(sm: StructuredMemory, vm: FakeVectorMemory) -> None:
    """复刻 08:36 轮实况：召回**非空**但只有干扰事实（recent_fallback 不触发）。"""
    sm.add_fact(DISTRACTOR_FACT, category="general", confidence=0.9, user_key=SESSION_A)
    vm.hits_for([FakeVectorMemory.vector_item(DISTRACTOR_FACT, SESSION_A)])


def _prov(ctx: dict) -> dict:
    return {p["fact"]: p for p in ctx.get("fact_provenance") or []}


# ── 1. 补锚主链（复刻生产两轮）──────────────────────────────────


@pytest.mark.parametrize("fact_text", [ENV_FACT, USER_MSG_ENV, RULE_FACT])
@pytest.mark.parametrize("query", [OFF_TOPIC_QUERY, ENV_QUERY])
def test_env_fact_anchored_regardless_of_query(fact_text, query):
    """话题切换轮 / 环境问句轮，恒常事实都必须进 facts（修复前红）。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp = _make(tmp)
    try:
        target_id = int(
            sm.add_fact(fact_text, category="suggestion", confidence=1.0, user_key=SESSION_A)
        )
        _seed_recall_hits_distractor(sm, vm)
        # 排除假绿：词面两路确实查不到目标（生产 08:36 轮实况）
        assert sm.search_facts(query, user_key=SESSION_A) == []
        ctx = mp.retrieve_context(query, session_id=SESSION_A, top_k=5)
        assert ctx["facts"], "召回应命中干扰事实（复刻生产：召回非空）"
        assert any("没有空调" in f for f in ctx["facts"]), (
            f"恒常事实掉出注入面：facts={ctx['facts']}"
        )
        prov = _prov(ctx)
        assert prov[fact_text]["recall"] == "durable_anchor"
        assert prov[fact_text]["fact_id"] == target_id
    finally:
        sm.close()


# ── 2. 隔离硬约束 ────────────────────────────────────────────────


def test_anchor_isolated_by_user_key():
    """他人 / 孤儿事实不得经补锚注入本会话（多用户隔离硬约束）。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp = _make(tmp)
    try:
        sm.add_fact(ENV_FACT, category="suggestion", confidence=1.0, user_key=SESSION_B)
        sm.add_fact(ENV_FACT, category="suggestion", confidence=1.0, user_key="")
        _seed_recall_hits_distractor(sm, vm)
        ctx = mp.retrieve_context(OFF_TOPIC_QUERY, session_id=SESSION_A, top_k=5)
        assert not any("没有空调" in f for f in ctx["facts"]), (
            f"隔离破口：{ctx['facts']}"
        )
        assert not any(
            p.get("recall") == "durable_anchor" for p in ctx.get("fact_provenance") or []
        )
    finally:
        sm.close()


def test_anchor_respects_deletion():
    """删除必须失效派生召回：已删除的恒常事实不得复活血 prompt。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp = _make(tmp)
    try:
        fid = int(sm.add_fact(ENV_FACT, category="suggestion", confidence=1.0, user_key=SESSION_A))
        _seed_recall_hits_distractor(sm, vm)
        sm.delete_fact(fid, recycle=False, user_key=SESSION_A)
        ctx = mp.retrieve_context(OFF_TOPIC_QUERY, session_id=SESSION_A, top_k=5)
        assert not any("没有空调" in f for f in ctx["facts"])
    finally:
        sm.close()


# ── 3. 去重与上限 ────────────────────────────────────────────────


def test_anchor_not_duplicated_when_already_recalled():
    """语义召回本身已带上恒常事实时：不重复注入、出处不改写。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp = _make(tmp)
    try:
        target_id = int(sm.add_fact(ENV_FACT, category="suggestion", confidence=1.0, user_key=SESSION_A))
        vm.hits_for([FakeVectorMemory.vector_item(ENV_FACT, SESSION_A)])
        ctx = mp.retrieve_context(OFF_TOPIC_QUERY, session_id=SESSION_A, top_k=5)
        assert ctx["facts"].count(ENV_FACT) == 1, f"重复注入：{ctx['facts']}"
        prov = _prov(ctx)
        assert prov[ENV_FACT]["recall"] == "vector", "不得改写既有召回出处"
        assert prov[ENV_FACT]["fact_id"] == target_id
    finally:
        sm.close()


def test_anchor_capped_at_limit():
    """补锚条数受 DURABLE_ANCHOR_LIMIT 约束（防 prompt 膨胀）。"""
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp = _make(tmp)
    try:
        env_facts = [
            "用户宿舍楼里没有空调",
            "用户家里没有洗衣机",
            "用户寝室没有热水器",
            "用户出租屋里没有微波炉",
            "用户老家没有烘干机",
        ]
        for t in env_facts:
            sm.add_fact(t, category="general", confidence=0.8, user_key=SESSION_A)
        _seed_recall_hits_distractor(sm, vm)
        ctx = mp.retrieve_context(OFF_TOPIC_QUERY, session_id=SESSION_A, top_k=5)
        anchors = [
            p for p in ctx.get("fact_provenance") or []
            if p.get("recall") == "durable_anchor"
        ]
        assert len(anchors) == DURABLE_ANCHOR_LIMIT, (
            f"补锚数={len(anchors)}，应为 {DURABLE_ANCHOR_LIMIT}"
        )
    finally:
        sm.close()


# ── 4. 「# 关于用户」注入窗口保位 ───────────────────────────────


def test_user_facts_window_keeps_env_fact():
    """低亲和度 k=4 窗口：高置信非环境事实不得把恒常事实挤到窗口外。"""
    tmp = Path(tempfile.mkdtemp())
    sm, _, _ = _make(tmp)
    try:
        sm.add_fact(ENV_FACT, category="general", confidence=0.5, user_key=SESSION_A)
        for i in range(4):
            sm.add_fact(
                f"用户喜欢的乐队是例子乐队{i}号",
                category="preference",
                confidence=0.9,
                user_key=SESSION_A,
            )
        mp2 = MemoryPipeline(
            vector_memory=FakeVectorMemory(), structured_memory=sm, llm_gateway=None
        )
        ctx = mp2.get_memory_context(session_id=SESSION_A, affinity_level=0)
        assert ctx["injection_k"] == 4
        assert any("没有空调" in f for f in ctx["user_facts"]), (
            f"恒常事实被挤出「# 关于用户」：{ctx['user_facts']}"
        )
        formatted = mp2.get_formatted_context(session_id=SESSION_A, affinity_level=0)
        assert "没有空调" in formatted
    finally:
        sm.close()


# ── 5. 判据纯函数：保守性 ───────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        USER_MSG_ENV,
        RULE_FACT,
        ENV_FACT,
        "我住在学校宿舍",
        "我们学校没有暖气",
        "宿舍里没有WiFi",
        "我家在云南昆明",
        "我们在学校上课",
        "去年宿舍装了空调",
        # ── 对抗验收补例（2026-10-12 二轮）：单「没」+名词 与 名词+在+楼层 语序 ──
        "我宿舍在六楼没电梯",  # 验收标准例句
        "宿舍没电梯",  # 单「没」+名词
        "我们楼里没电梯",
        "宿舍在六楼",  # 名词+在+数字楼层（中文「X在Y」语序）
        "我家住五楼",  # 住+数字楼层（无「在」）
        "教室在一楼",
        "我住六人间",
    ],
)
def test_durable_judge_positive(text):
    assert is_durable_env_fact(text), text


@pytest.mark.parametrize(
    "text",
    [
        "今天好冷，想开空调了",  # 瞬时愿望，非恒常属性
        "现在宿舍空调坏了",  # 带瞬时词：保守不锚（宁漏锚不错锚）
        "我喜欢吃火锅",  # 无关事实
        "空调开没开别冻着了？",  # 疑问句
        "明天要去买空调",  # 计划性事件
        "用户早八的课程排得很满",  # 干扰事实（无环境名词×恒常断言组合）
        "昨晚没回宿舍",  # 过去日事件（不是恒常属性），瞬时段排除
        "",
    ],
)
def test_durable_judge_negative(text):
    assert not is_durable_env_fact(text), text


def test_dorm_floor_fact_anchored_like_production():
    """验收标准例句端到端：楼层/设施类换说法恒常事实必须走完补锚链。

    复刻生产实况（对抗验收二轮）：召回非空仅含干扰事实（recent_fallback
    不触发），话题切换轮查询——「我宿舍在六楼没电梯」落库后不得掉出注入面。
    """
    tmp = Path(tempfile.mkdtemp())
    sm, vm, mp = _make(tmp)
    try:
        floor_fact = "我宿舍在六楼没电梯"
        target_id = int(sm.add_fact(floor_fact, category="general", confidence=1.0, user_key=SESSION_A))
        _seed_recall_hits_distractor(sm, vm)
        assert sm.search_facts(OFF_TOPIC_QUERY, user_key=SESSION_A) == []
        ctx = mp.retrieve_context(OFF_TOPIC_QUERY, session_id=SESSION_A, top_k=5)
        assert any("六楼" in f for f in ctx["facts"]), (
            f"楼层换说法恒常事实掉出注入面：facts={ctx['facts']}"
        )
        prov = _prov(ctx)
        anchors = [p for p in prov.values() if p.get("recall") == "durable_anchor"]
        assert len(anchors) == 1
        assert anchors[0]["fact_id"] == target_id
    finally:
        sm.close()

"""P2 批6 回归：persona/情感引擎 审计 item42–47（2026-09-21 全面修复）。

item42 请求级情绪引擎从持久值恢复好感度（旧从 0 起步→每轮把已存好感度向 0 拉低）
item43 情感-风格耦合矩阵命中（旧把 Emotion 枚举对象当中文 str 键传→恒 miss）
item44 CharacterAggregate 不再注入英文「# 当前状态」块（状态块唯一 owner=中文情感层）
item45 每日情绪时间衰减打向每用户存活引擎（旧只打无人读的模板引擎）
item46 注入层构建失败从 debug 静默升为 WARNING 可见
item47 知识检索不再每轮白跑 ToneMimic Chroma+ONNX（产物无人消费）
"""

from __future__ import annotations

import inspect
import logging
from types import SimpleNamespace

import pytest

# ═══════════════════════════════════════════════════════════
# item42 — 请求级引擎恢复好感度
# ═══════════════════════════════════════════════════════════


def test_mapper_current_points_roundtrip():
    from shisi.affinity import scale as affinity_scale
    from shisi.affinity.mapper import AffinityMapper

    fake_enhancer = SimpleNamespace(
        _min=0.0, _max=100.0,
        get_value=lambda cid, user_id="": 50.0,
    )
    mapper = AffinityMapper(enhancer=fake_enhancer)
    pts = mapper.current_points("mi_cai", "user4:wx")
    assert pts == pytest.approx(affinity_scale.shisi_to_points(50.0, 0.0, 100.0))
    # enhancer 缺失 → 0.0 而非抛错
    assert AffinityMapper().current_points("c", "u") == 0.0


def test_restore_request_affinity_sets_state_from_mapper():
    from my_character.emotion_engine import CompoundEmotionalState
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator
    from shisi.affinity import scale as affinity_scale

    points = 120.0
    state = CompoundEmotionalState()
    assert state.affection_points == 0.0

    mapper = SimpleNamespace(current_points=lambda cid, uid: points)
    import api.deps as deps_mod
    orig = getattr(deps_mod.deps, "shisi_reg", None)
    deps_mod.deps.shisi_reg = SimpleNamespace(affinity_mapper=mapper)
    try:
        OptimizedOrchestrator._restore_request_affinity(
            SimpleNamespace(state=state), "sess-1", "mi_cai"
        )
    finally:
        deps_mod.deps.shisi_reg = orig
    assert state.affection_points == points
    assert state.affinity == affinity_scale.points_to_level(points)


def test_restore_request_affinity_swallows_failures():
    import api.deps as deps_mod
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    def _boom(cid, uid):
        raise RuntimeError("mapper down")

    orig = getattr(deps_mod.deps, "shisi_reg", None)
    deps_mod.deps.shisi_reg = SimpleNamespace(
        affinity_mapper=SimpleNamespace(current_points=_boom)
    )
    try:
        OptimizedOrchestrator._restore_request_affinity(
            SimpleNamespace(state=SimpleNamespace()), "s", "c"
        )  # 不得抛
    finally:
        deps_mod.deps.shisi_reg = orig


def test_request_engine_creation_calls_restore():
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    src = inspect.getsource(OptimizedOrchestrator._get_request_emotion_engine)
    assert "_restore_request_affinity(" in src
    # 且必须在写入缓存之前调用（恢复后的引擎才会被复用）
    assert src.index("_restore_request_affinity(") < src.index(
        "self._request_emotion_engines[key] = engine"
    )


# ═══════════════════════════════════════════════════════════
# item43 — 情感-风格耦合命中
# ═══════════════════════════════════════════════════════════


def _segment_for(state):
    from my_character.emotion_style_coupler import EmotionStyleCoupler
    from my_character.persona_engine import PersonaEngine

    coupler = EmotionStyleCoupler()
    fake_self = SimpleNamespace(_emotion_style_coupler=coupler)
    return PersonaEngine._build_emotion_style_segment(fake_self, state)


def test_style_segment_matches_matrix_with_enum():
    from my_character.emotion_engine import CompoundEmotionalState, Emotion

    happy = CompoundEmotionalState(primary_emotion=Emotion.HAPPY, affinity=3)
    neutral = CompoundEmotionalState(primary_emotion=Emotion.NEUTRAL, affinity=3)
    seg_happy = _segment_for(happy)
    seg_neutral = _segment_for(neutral)
    assert seg_happy and seg_neutral
    # 旧实现：枚举对象当键 → 恒 miss → 两份段恒等（只有好感度分量生效）
    assert seg_happy != seg_neutral


def test_style_segment_handles_to_dict_shape():
    from my_character.emotion_engine import CompoundEmotionalState, Emotion

    recorded: dict = {}
    from my_character.persona_engine import PersonaEngine

    class _Spy:
        def couple(self, emotion_dict, base_style=None):
            recorded.update(emotion_dict)
            return None

        def get_style_prompt_segment(self, style):
            return ""

    state = CompoundEmotionalState(primary_emotion=Emotion.SULLEN)
    PersonaEngine._build_emotion_style_segment(
        SimpleNamespace(_emotion_style_coupler=_Spy()), state.to_dict()
    )
    assert recorded["primary"]["type"] == "傲娇"  # 中文 str，非枚举/英文
    assert isinstance(recorded["affinity"], int) and recorded["affinity"] == 0


# ═══════════════════════════════════════════════════════════
# item44 — system 状态块唯一 owner
# ═══════════════════════════════════════════════════════════


def test_aggregate_no_english_state_block():
    from shisi.core.models.character_aggregate import CharacterAggregate

    char = CharacterAggregate(name="十四", description="可爱")
    prompt = char.build_system_prompt(user_message="嗨")
    assert "# 当前状态" not in prompt
    assert "NEUTRAL" not in prompt
    # 人设段仍注入（唯一被移除的是状态块）
    assert "十四" in prompt


# ═══════════════════════════════════════════════════════════
# item45 — 时间衰减打到存活引擎
# ═══════════════════════════════════════════════════════════


def test_user_manager_apply_time_decay_all():
    from user_scheduler import UserInstance, UserManager

    calls: list[float] = []

    class _Eng:
        def apply_time_decay(self, hours):
            calls.append(hours)

    mgr = UserManager(SimpleNamespace())
    mgr._users["u1"] = UserInstance(
        user_id="u1",
        emotion_engines={"c1": _Eng(), "c2": _Eng()},
    )
    n = mgr.apply_time_decay_all(2.5)
    assert n == 2
    assert calls == [2.5, 2.5]


def test_scheduler_daily_maintenance_targets_live_engines(monkeypatch):
    from proactive import scheduler as sched_mod
    from proactive.scheduler import ProactiveScheduler

    # 构造参数已删：模板引擎不再有读者，接线同步拆除
    assert "emotion_engine" not in inspect.signature(
        ProactiveScheduler.__init__
    ).parameters
    src = inspect.getsource(ProactiveScheduler._run_daily_maintenance)
    assert "apply_time_decay_all" in src
    assert "self._emotion_engine" not in src

    sched = ProactiveScheduler()
    recorded: list[float] = []
    fake_mgr = SimpleNamespace(
        apply_time_decay_all=lambda h: (recorded.append(h), 7)[1]
    )
    import api.deps as deps_mod

    monkeypatch.setattr(deps_mod.deps, "gf", fake_mgr)
    # 好感度衰减块不碰真库
    monkeypatch.setattr(deps_mod.deps, "shisi_reg", None)
    monkeypatch.setattr(sched, "_hours_since_last_check", lambda: 3.0)
    monkeypatch.setattr(sched, "_check_important_dates", lambda: None)
    monkeypatch.setattr(
        sched_mod, "run_achievement_maintenance", lambda: 0, raising=False
    )
    sched._run_daily_maintenance()
    assert recorded == [3.0]


# ═══════════════════════════════════════════════════════════
# item46 — 注入层失败可见
# ═══════════════════════════════════════════════════════════


def test_safe_engine_layer_logs_warning(caplog):
    from shisi.application.persona_service import PersonaService

    def _boom():
        raise ValueError("layer down")

    svc = SimpleNamespace()
    with caplog.at_level(logging.DEBUG, logger="shisi.application.persona_service"):
        out = PersonaService._safe_engine_layer(svc, "emotion", _boom)
    assert out == ""
    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warns and "emotion" in warns[0].getMessage()


# ═══════════════════════════════════════════════════════════
# item47 — 检索不再白跑 ToneMimic
# ═══════════════════════════════════════════════════════════


def test_knowledge_adapter_retrieve_skips_style_query():
    from shisi.application.knowledge_service import ShisiKnowledgeAdapter

    class _Res:
        chunks: list = []
        total_chunks = 0

        def get_top(self, k):
            return []

    class _Svc:
        def search(self, cid, query, top_k=5):
            return _Res()

    class _Tone:
        def retrieve_style_examples(self, query, top_k=3):
            raise AssertionError("每轮检索不得再触碰 ToneMimic")

    adapter = ShisiKnowledgeAdapter(
        knowledge_service=_Svc(), tone_mimic=_Tone(), default_character_id="mi_cai"
    )
    payload = adapter.retrieve("你好", top_k=3)
    assert payload["style_examples"] == []
    assert payload["results"] == []


# ═══════════════════════════════════════════════════════════
# 附带：WS 正常完成只发一条 stream_end（P2「WS 双帧」）
# ═══════════════════════════════════════════════════════════


def test_ws_stream_end_single_on_done():
    from pathlib import Path

    src = Path("api/websocket_server.py").read_text(encoding="utf-8")
    assert "ended = False" in src
    assert "if not ended:" in src
    # done 分支与兜底分支各一帧，不再有 done 之后的无条件第三 send
    assert src.count('"type": "stream_end"') == 2


# ── 6b 项6：一致性检测器缓存复用 ─────────────────────────

def test_checker_for_card_caches_by_anchors():
    from my_character.consistency_checker import (
        _CHECKER_CACHE,
        checker_for_card,
    )

    card = {"core_anchors": ["锚一", "锚二"]}
    _CHECKER_CACHE.clear()  # 隔离其他套件可能留下的缓存条目
    c1 = checker_for_card(card)
    c2 = checker_for_card({"core_anchors": ["锚一", "锚二"]})
    assert c1 is c2
    c3 = checker_for_card({"core_anchors": ["不同锚"]})
    assert c3 is not c1
    assert len(_CHECKER_CACHE) == 2

    # 空锚点卡也可复用（旧代码同样构建空锚点检测器，判定恒通过）
    e1 = checker_for_card({})
    assert e1 is checker_for_card({"core_anchors": []})


def test_stream_and_shared_paths_use_cached_checker():
    from pathlib import Path

    stream_src = Path("orchestrator/_stream_mixin.py").read_text(encoding="utf-8")
    assert "checker_for_card(" in stream_src
    # 每消息重建原语必须消失（构造收敛到工厂）
    assert "PersonaConsistencyChecker(" not in stream_src
    assert "DynamicAnchorSystem(" not in stream_src

    shared_src = Path("my_character/consistency_checker.py").read_text(encoding="utf-8")
    # check_and_correct_reply 段：只用工厂，不再直接构造
    shared_body = shared_src[shared_src.find("def check_and_correct_reply"):]
    assert "checker_for_card(" in shared_body
    assert "PersonaConsistencyChecker(" not in shared_body
    assert "DynamicAnchorSystem(" not in shared_body


# ── checker 缓存安全回归（映射 2026 安全事件复盘的三类缓存风险）────
# 事件教训 → 本地不变量：①内容变即缓存失效（stale content）②键精确匹配、
# 禁前缀/拼接碰撞误命中（permission by substring）③共享缓存对象被使用中
# 污染（shared-state mutation）。

def test_checker_cache_invalidated_on_anchor_content_change():
    """卡片锚点被原地修改（同 dict 对象）后，不得继续拿旧锚点检测器。"""
    from my_character.consistency_checker import _CHECKER_CACHE, checker_for_card

    _CHECKER_CACHE.clear()
    card = {"core_anchors": ["原锚点"]}
    c_before = checker_for_card(card)
    card["core_anchors"].append("新增锚点")  # 原地变更，模拟改卡热更新
    c_after = checker_for_card(card)
    assert c_after is not c_before
    active_after = [da.text for da in c_after._anchors._dynamic_anchors]
    assert "新增锚点" in active_after
    # 旧对象仍持旧内容且不再被工厂返回——新请求绝不命中 stale checker
    active_before = [da.text for da in c_before._anchors._dynamic_anchors]
    assert active_before == ["原锚点"]
    assert checker_for_card(card) is c_after


def test_checker_cache_keys_exact_no_prefix_collision():
    """["A","B"] 与 ["AB"] 之类的拼接/前缀相似键不得共享检测器。"""
    from my_character.consistency_checker import _CHECKER_CACHE, checker_for_card

    _CHECKER_CACHE.clear()
    c1 = checker_for_card({"core_anchors": ["傲娇", "温柔"]})
    c2 = checker_for_card({"core_anchors": ["傲娇温", "柔"]})
    c3 = checker_for_card({"core_anchors": ["傲娇温柔"]})
    assert len({id(c1), id(c2), id(c3)}) == 3
    # 完全相同内容才复用
    assert checker_for_card({"core_anchors": ["傲娇", "温柔"]}) is c1


def test_checker_cache_not_poisoned_by_use():
    """check() 使用共享缓存对象后不得引入任何状态漂移（幂等判定）。"""
    from my_character.consistency_checker import (
        _CHECKER_CACHE,
        ConsistencyContext,
        checker_for_card,
    )

    _CHECKER_CACHE.clear()
    card = {"core_anchors": ["想念一个人"]}
    checker = checker_for_card(card)
    ctx = ConsistencyContext(emotion_state=None, chat_round=3, affinity=5)
    r1 = checker.check("才没有想你呢", ctx)
    r2 = checker.check("才没有想你呢", ctx)
    assert r1.overall_score == r2.overall_score
    assert r1.overall_passed == r2.overall_passed
    # 锚点内部状态未被写（reinforcement 计数器仍为 0，列表长度不变）
    assert checker._anchors._reinforcement_counter == 0
    assert len(checker._anchors._dynamic_anchors) == 1


def test_checker_cache_bounded_no_unbounded_growth():
    """持续注入不同锚点集，缓存必须被上限约束（防无界内存增长）。"""
    from my_character.consistency_checker import (
        _CHECKER_CACHE,
        _CHECKER_CACHE_MAX,
        checker_for_card,
    )

    _CHECKER_CACHE.clear()
    for i in range(_CHECKER_CACHE_MAX * 3):
        checker_for_card({"core_anchors": [f"锚-{i}"]})
        assert len(_CHECKER_CACHE) <= _CHECKER_CACHE_MAX

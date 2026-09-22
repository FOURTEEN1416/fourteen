"""P2 批6 回归：persona/情感引擎 审计 item42–47（2026-09-21 全面修复）。

item42 请求级情绪引擎从持久值恢复好感度（旧从 0 起步→每轮把已存好感度向 0 拉低）
item43 情感-风格耦合矩阵命中（旧把 Emotion 枚举对象当中文 str 键传→恒 miss）
item44 CharacterAggregate 不再注入英文「# 当前状态」块（状态块唯一 owner=中文情感层）
item45 每日情绪时间衰减打向每用户存活引擎（旧只打无人读的模板引擎）
item46 注入层构建失败从 debug 静默升为 WARNING 可见
item47 知识检索不再每轮白跑 ToneMimic Chroma+ONNX（产物无人消费）
6b 项9 persona P2 五连：①verify_anchors 自比同义反复→比对现值 ②一致性风格
维度接线+硬违规修正旁路 ③emotion.yaml 进生产引擎 ④PersonaService 端点委托
⑤CharacterCardAdapter 零读者挂线删除
6b 项10 persona 域死码清单清除（防复活钉见文件末尾 TestBatch6bItem10 区段）：
build_complete_prompt/build_system_prompt 双模式与两级缓存、evolve/evolve_dimension/
rollback_to/auto_evolve、validate_response/auto_correct_response/
check_anchor_consistency、enhanced_prompt_engine 等 8 模块、dynamic_anchor 强化回路、
EmotionEngine 风格修饰器双接口、persona_service._build_chat_history、
CharacterService 全链、character_card/ 包、prompt_mode 管线。
6b 项11 知识槽检索查询与 system 回显解耦（审计 :157 排除项4更正）：persona_service
恒传 user_message="" 使 prompt_builder 检索门槛（query 非空）永不满足→每轮 RAG
不发生；新增 knowledge_query 通道，orchestrator 下传本轮原话只作检索命中。
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
    # 2026-09-22 契约：衰减时长改用独立基准（旧 `_hours_since_last_check` 是
    # 「距上次 ASE tick」≈5 分钟，衰减实际从未发生）
    monkeypatch.setattr(sched, "_hours_since_last_emotion_decay", lambda: 3.0)
    monkeypatch.setattr(sched, "_check_important_dates", lambda: None)
    monkeypatch.setattr(
        sched_mod, "run_achievement_maintenance", lambda: 0, raising=False
    )
    sched._run_daily_maintenance()
    assert recorded == [3.0]


def test_emotion_decay_baseline_semantics(monkeypatch, tmp_path):
    """衰减专用基准三契约（2026-09-22 二次根治后锁定）：
    ① 🔴 **基准缺失时不得返回 0**（首版以 None 作哨兵 + 返回 0.0 + 赋值在
       `if hours > 0` 内 = 永久自我锁死，衰减依然从未发生）；
    ② 基准为 24h 前 → 返回 ≈24（每日维护传真实时长，而非距 ASE tick 的 ≈5 分钟）；
    ③ 维护执行后基准推进**并落盘**；apply 失败不推进（线性衰减下次补足）。"""
    from datetime import datetime, timedelta, timezone

    from proactive import scheduler as sched_mod
    from proactive.scheduler import ProactiveScheduler

    # 隔离宿主 data/：配置真源改落 tmp，避免读写仓库真实 scheduler_config.json
    tmp_cfg = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", tmp_cfg)

    sched = ProactiveScheduler()
    # ① 基准缺失 → 回填「昨日维护时刻」（≈24h），**绝不为 0**
    hours0 = sched._hours_since_last_emotion_decay()
    assert hours0 > 20.0, (
        f"基准缺失时返回 {hours0}（应为 ≈24h 的回填值）—— "
        "返回 0 会使 `if hours > 0` 永假 + 赋值语句在内 = 永久锁死"
    )
    assert sched._last_emotion_decay is not None
    # 回填值已落盘（跨进程/重启一致）
    assert tmp_cfg.exists()
    assert "last_emotion_decay" in tmp_cfg.read_text(encoding="utf-8")

    # ② 基准为 24h 前 → ≈24
    sched._last_emotion_decay = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    assert sched._hours_since_last_emotion_decay() == pytest.approx(24.0, abs=0.01)

    # ③-a 成功路径：基准推进且落盘
    recorded: list[float] = []
    fake_mgr = SimpleNamespace(apply_time_decay_all=lambda h: (recorded.append(h), 2)[1])
    import api.deps as deps_mod

    monkeypatch.setattr(deps_mod.deps, "gf", fake_mgr)
    monkeypatch.setattr(deps_mod.deps, "shisi_reg", None)
    monkeypatch.setattr(sched, "_hours_since_last_emotion_decay", lambda: 24.0)
    monkeypatch.setattr(sched, "_check_important_dates", lambda: None)
    monkeypatch.setattr(sched_mod, "run_achievement_maintenance", lambda: 0, raising=False)
    before = datetime.now(tz=timezone.utc)
    sched._run_daily_maintenance()
    assert recorded == [24.0]
    assert sched._last_emotion_decay is not None
    assert sched._last_emotion_decay >= before - timedelta(seconds=1)

    # ③-b 失败路径：apply 抛异常 → 基准不推进（下个维护日补足时长）
    def _boom(hours):
        recorded.append(hours)
        raise RuntimeError("mgr down")

    sched._last_emotion_decay = datetime.now(tz=timezone.utc) - timedelta(hours=48)
    monkeypatch.setattr(deps_mod.deps, "gf", SimpleNamespace(apply_time_decay_all=_boom))
    monkeypatch.setattr(sched, "_hours_since_last_emotion_decay", lambda: 48.0)
    sched._run_daily_maintenance()
    assert recorded == [24.0, 48.0]
    assert sched._last_emotion_decay < before


def test_emotion_decay_baseline_survives_restart(monkeypatch, tmp_path):
    """基准必须**落盘**并在重启（新实例）后复现 —— 否则每进程起步都是
    「首次」，衰减窗口被无限摊销。"""
    from datetime import datetime, timedelta, timezone

    from proactive.scheduler import ProactiveScheduler

    tmp_cfg = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", tmp_cfg)

    first = ProactiveScheduler()
    stamp = datetime.now(tz=timezone.utc) - timedelta(hours=5)
    first._persist_last_emotion_decay(stamp)

    second = ProactiveScheduler()  # 模拟重启
    assert second._last_emotion_decay is not None
    hours = second._hours_since_last_emotion_decay()
    assert hours == pytest.approx(5.0, abs=0.05), (
        f"重启后基准未复现（得 {hours}h）—— 落盘链路断裂"
    )


def test_emotion_decay_baseline_never_self_locks(monkeypatch, tmp_path):
    """🔴 核心回归：基准缺失/存在时都必须给出**非零**时长，不得自我锁死。

    首版缺陷形态：`None` 哨兵 → 恒 0.0 → `if hours > 0` 永假 → 赋值不可达
    → 下一日仍 0.0。本用例断言：
      · 基准缺失（新实例 + 空配置）→ 回填 ~24h（而非 0）；
      · 每次维护后基准推进到「此刻」；
      · 把基准人为拨回 24h 前后再读，仍得 ~24h（即基准真在驱动时长）。
    """
    from datetime import datetime, timedelta, timezone

    from proactive import scheduler as sched_mod
    from proactive.scheduler import ProactiveScheduler

    tmp_cfg = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", tmp_cfg)

    sched = ProactiveScheduler()
    recorded: list[float] = []
    import api.deps as deps_mod

    monkeypatch.setattr(
        deps_mod.deps,
        "gf",
        SimpleNamespace(apply_time_decay_all=lambda h: (recorded.append(h), 1)[1]),
    )
    monkeypatch.setattr(deps_mod.deps, "shisi_reg", None)
    monkeypatch.setattr(sched, "_check_important_dates", lambda: None)
    monkeypatch.setattr(sched_mod, "run_achievement_maintenance", lambda: 0, raising=False)

    # ① 首次（基准缺失）→ 必须非零
    first_hours = sched._hours_since_last_emotion_decay()
    assert first_hours > 20.0, f"基准缺失时应回填 ~24h，实得 {first_hours}"

    # ② 连续三日：每日把基准拨回 24h 前（模拟一天过去），维护应吃到 ~24h
    seen: list[float] = []
    for day in range(3):
        sched._last_emotion_decay = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        hours = sched._hours_since_last_emotion_decay()
        seen.append(hours)
        sched._run_daily_maintenance()
        assert sched._last_emotion_decay is not None, f"第 {day + 1} 日基准为 None（锁死）"
        # 维护后基准应贴近此刻，且已落盘
        drift = abs(
            (datetime.now(tz=timezone.utc) - sched._last_emotion_decay).total_seconds()
        )
        assert drift < 5, f"第 {day + 1} 日维护后基准未推进到此刻（drift={drift}s）"

    assert all(h > 20.0 for h in seen), f"存在 0 时长日 → 自我锁死复发: {seen}"
    assert len(recorded) == 3
    # 落盘复现：新实例读到的是最后推进的基准（≈0h），而非回填的 24h
    reopened = ProactiveScheduler()
    assert reopened._hours_since_last_emotion_decay() < 5.0, "重启后基准未延续落盘值"


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
    # 锚点内部状态未被写（6b 项10：强化计数器已随死回路删除，改钉纯只读语义）
    assert not hasattr(checker._anchors, "should_reinforce")
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


# ═══════════════════════════════════════════════════════════
# 6b 项9① — verify_anchors 比对现值（旧为自比恒真的同义反复）
# ═══════════════════════════════════════════════════════════


def _anchor_self(anchors: list[str]):
    import hashlib

    from my_character.persona_engine import PersonaEngine

    return SimpleNamespace(
        anchor_verification_enabled=True,
        _persona={"core_anchors": list(anchors)},
        _anchor_hashes={a: hashlib.sha256(a.encode()).hexdigest() for a in anchors},
        CORE_ANCHORS=PersonaEngine.CORE_ANCHORS,
    )


def test_verify_anchors_detects_text_drift():
    from my_character.persona_engine import PersonaEngine

    self_ = _anchor_self(["表面傲娇", "嘴硬心软"])
    assert PersonaEngine.verify_anchors(self_) is True
    # 运行期锚点文本被改写、基线未动 → 必须检出（旧实现恒 True）
    self_._persona["core_anchors"][0] = "表面高冷"
    assert PersonaEngine.verify_anchors(self_) is False


def test_verify_anchors_detects_add_and_remove():
    from my_character.persona_engine import PersonaEngine

    self_ = _anchor_self(["锚一", "锚二"])
    self_._persona["core_anchors"] = ["锚一", "锚二", "锚三"]
    assert PersonaEngine.verify_anchors(self_) is False
    self_._persona["core_anchors"] = ["锚一"]
    assert PersonaEngine.verify_anchors(self_) is False


def test_verify_anchors_disabled_short_circuits():
    from my_character.persona_engine import PersonaEngine

    self_ = _anchor_self(["锚一"])
    self_.anchor_verification_enabled = False
    self_._persona["core_anchors"] = ["已漂移"]
    assert PersonaEngine.verify_anchors(self_) is True


# ═══════════════════════════════════════════════════════════
# 6b 项9② — 风格维度接线 + 硬违规修正旁路
# ═══════════════════════════════════════════════════════════


def test_couple_style_for_handles_enum_and_dict_shapes():
    from my_character.consistency_checker import couple_style_for
    from my_character.emotion_engine import CompoundEmotionalState, Emotion

    s_obj = couple_style_for(
        CompoundEmotionalState(primary_emotion=Emotion.HAPPY, affinity=3)
    )
    s_dict = couple_style_for(
        CompoundEmotionalState(primary_emotion=Emotion.HAPPY, affinity=3).to_dict()
    )
    assert s_obj is not None and s_dict is not None
    s_neutral = couple_style_for(
        CompoundEmotionalState(primary_emotion=Emotion.NEUTRAL, affinity=3)
    )
    # 情感分量必须生效（旧：三处构造点不传 → 风格维度恒 0.9 缺省）
    assert s_obj.warmth != s_neutral.warmth or s_obj.sentence_length != s_neutral.sentence_length
    assert couple_style_for(None) is None


def test_persona_check_consistency_feeds_coupled_style():
    """PersonaEngine.check_consistency 的上下文必须带 coupled_style（风格维度不再恒 0.9）。"""
    from my_character.consistency_checker import PersonaConsistencyChecker
    from my_character.emotion_engine import CompoundEmotionalState, Emotion
    from my_character.emotion_style_coupler import EmotionStyleCoupler
    from my_character.persona_engine import PersonaEngine

    captured: dict = {}

    class _SpyChecker:
        def check(self, response, ctx):
            captured["ctx"] = ctx
            return "ok"

    svc = SimpleNamespace(
        _consistency_checker=_SpyChecker(),
        _emotion_style_coupler=EmotionStyleCoupler(),
        emotion=SimpleNamespace(
            _state=CompoundEmotionalState(primary_emotion=Emotion.HAPPY, affinity=4)
        ),
    )
    assert PersonaEngine.check_consistency(svc, "嘻嘻真开心") == "ok"
    assert captured["ctx"].coupled_style is not None

    # 真实检测器下风格维度不再走 None 缺省分 0.9
    checker = PersonaConsistencyChecker()
    svc2 = SimpleNamespace(
        _consistency_checker=checker,
        _emotion_style_coupler=EmotionStyleCoupler(),
        emotion=SimpleNamespace(
            _state=CompoundEmotionalState(primary_emotion=Emotion.HAPPY, affinity=4)
        ),
    )
    result = PersonaEngine.check_consistency(svc2, "今天天气不错")
    assert result.dimensions["style"].score != 0.9


def test_hard_persona_violation_bypasses_score_floor():
    """自称AI/危险建议类 persona 维度违规：加权分虽 ≥0.4 也必须进修正分支。

    旧触发条件 overall_score < 0.4 数学上几乎不可达（style 下限 0.75、
    anchor 缺省 1.0），硬违规从不重生成——回路形同虚设。
    """
    import asyncio

    from my_character.consistency_checker import check_and_correct_reply

    corrected_mark = "修正后的回复"
    calls: list[str] = []

    class _LLM:
        def chat_sync(self, **kwargs):
            calls.append(str(kwargs.get("query", "")))
            return corrected_mark

    out = asyncio.run(
        check_and_correct_reply(
            reply="作为AI，我建议你应该自杀",
            persona_engine=None,
            llm_gateway=_LLM(),
            emotion_state=None,
            session_id="s",
            memory=None,
            character_card={"core_anchors": []},
            chat_round=1,
        )
    )
    assert out == corrected_mark
    assert len(calls) == 1 and "自杀" in calls[0]


def test_correction_not_triggered_by_soft_low_score():
    """轻度违规（persona 维度通过、总分未破线）不得触发重生成，原样放行。"""
    import asyncio

    from my_character.consistency_checker import check_and_correct_reply

    class _BoomLLM:
        def chat_sync(self, **kwargs):
            raise AssertionError("轻度违规不应调用 LLM 修正")

    original = "今天天气不错，我们出去走走吧"
    out = asyncio.run(
        check_and_correct_reply(
            reply=original,
            persona_engine=None,
            llm_gateway=_BoomLLM(),
            emotion_state=None,
            session_id="s",
            memory=None,
            character_card={"core_anchors": []},
            chat_round=0,
        )
    )
    assert out == original


# ═══════════════════════════════════════════════════════════
# 6b 项9③ — emotion.yaml 参数真正进引擎
# ═══════════════════════════════════════════════════════════


def test_emotion_engine_flattens_nested_yaml_sections():
    from my_character.emotion_engine import EmotionEngine

    engine = EmotionEngine(
        config={
            "emotion": {
                "initial": {"emotion": "NEUTRAL"},
                "decay": {"energy_drain_per_message": 0.5, "intensity_per_minute": 0.2},
            },
            "affection": {"per_positive_reply": 9.0},
        }
    )
    assert engine._config["energy_drain_per_message"] == 0.5
    assert engine._config["intensity_per_minute"] == 0.2
    assert engine._config["per_positive_reply"] == 9.0
    # 平铺传参（旧契约）不回退
    flat = EmotionEngine(config={"energy_drain_per_message": 0.3})
    assert flat._config["energy_drain_per_message"] == 0.3


def test_production_emotion_engine_receives_emotion_yaml():
    import inspect

    from orchestrator._init_mixin import _InitPhasesMixin

    src = inspect.getsource(_InitPhasesMixin._init_emotion_persona_tone)
    assert "load_emotion()" in src
    engine_block = src[src.find("EmotionEngine("):]
    assert "config=" in engine_block[: engine_block.find(")")]


def test_persona_engine_default_emotion_engine_loads_yaml():
    import inspect

    from my_character.persona_engine import PersonaEngine

    src = inspect.getsource(PersonaEngine.__init__)
    assert 'load_emotion()' in src
    assert 'get("emotion", {})' not in src  # 旧恒为 {} 的空读取不得复活


# ═══════════════════════════════════════════════════════════
# 6b 项9④ — /api/persona/* 端点所需委托
# ═══════════════════════════════════════════════════════════


def test_persona_service_delegates_profile_and_evolution_log():
    from shisi.application.persona_service import PersonaService

    fake_profile = SimpleNamespace(core_character={"warmth": 0.8})
    engine = SimpleNamespace(
        profile=fake_profile,
        get_evolution_log=lambda limit: [{"i": 1}][:limit],
        check_consistency=lambda *a, **k: "checked",
        _emotion_style_coupler="coupler",
    )
    svc = SimpleNamespace(_engine=engine)
    assert PersonaService.profile.fget(svc) is fake_profile
    assert PersonaService.get_evolution_log(svc, 5) == [{"i": 1}]
    assert PersonaService.style_coupler.fget(svc) == "coupler"
    # 流式兜底分支的 hasattr 判定必须成立（旧恒 False → 默认角色后台检测落空）
    assert PersonaService.check_consistency(svc, "回复", None, 2) == "checked"


# ═══════════════════════════════════════════════════════════
# 6b 项9⑤ — CharacterCardAdapter 零读者挂线删除
# ═══════════════════════════════════════════════════════════


def test_character_card_wiring_removed_from_init():
    import inspect

    from orchestrator._init_mixin import _InitPhasesMixin

    assert not hasattr(_InitPhasesMixin, "_init_character_card")
    src = inspect.getsource(_InitPhasesMixin.initialize)
    assert "_init_character_card" not in src
    full = inspect.getsource(_InitPhasesMixin)
    assert "card_mode" not in full
    assert "CharacterCardAdapter" not in full


# ═══════════════════════════════════════════════════════════
# 6b 项10 — persona 域死码清除防复活钉
# ═══════════════════════════════════════════════════════════

_DEAD_PERSONA_MODULES = [
    "my_character.enhanced_prompt_engine",
    "my_character.contextual_behavior",
    "my_character.style_enhancer_v2",
    "my_character.evolution_engine",
    "my_character.emotion_memory",
    "my_character.persona_evaluator",
    "my_character.anchor_protection",
    "my_character.constraint_validator",
]

_DEAD_ENGINE_ATTRS = [
    "build_complete_prompt",
    "build_system_prompt",
    "evolve",
    "evolve_dimension",
    "rollback_to",
    "auto_evolve",
    "validate_response",
    "auto_correct_response",
    "check_anchor_consistency",
]


def test_dead_persona_modules_removed():
    import importlib.util

    for name in _DEAD_PERSONA_MODULES:
        assert importlib.util.find_spec(name) is None, f"{name} 已作为死码删除，不得复活"


def test_persona_engine_dead_api_removed_and_live_api_kept():
    from my_character.persona_engine import PersonaEngine

    for attr in _DEAD_ENGINE_ATTRS:
        assert not hasattr(PersonaEngine, attr), f"{attr} 零调用已删除，不得复活"
    for attr in ("get_evolution_log", "check_consistency", "verify_anchors",
                 "build_emotion_layer", "build_style_layer", "build_constraint_layer",
                 "build_memory_layer", "build_emotion_style_segment"):
        assert callable(getattr(PersonaEngine, attr, None)), f"{attr} 是现役路径，不得误删"


def test_persona_engine_prompt_mode_plumbing_removed():
    import inspect

    from my_character.persona_engine import PersonaEngine

    assert "prompt_mode" not in inspect.signature(PersonaEngine.__init__).parameters
    src = inspect.getsource(PersonaEngine.__init__)
    assert "prompt_mode" not in src
    # 两级 prompt 缓存随 build_system_prompt 一并移除
    assert not hasattr(PersonaEngine, "_prompt_cache")
    hsrc = inspect.getsource(PersonaEngine.health_check) + inspect.getsource(PersonaEngine.to_dict)
    assert "prompt_mode" not in hsrc and "base_prompt_cached" not in hsrc


def test_persona_engine_evo_log_readonly_contract():
    """演化写路径已删、只读日志保留：/api/persona/evolution-log 依赖 get_evolution_log；
    日志恒空为已登记限制（DELETION_LOG 6b 项10）。"""
    import inspect

    from my_character.persona_engine import PersonaEngine

    src = inspect.getsource(PersonaEngine)
    assert "_evolution_log.append" not in src, "演化日志不得再有写者（写路径已全删）"


def test_dynamic_anchor_reinforcement_circuit_removed():
    from dataclasses import fields

    from my_character.dynamic_anchor import AnchorCheckResult, DynamicAnchorSystem

    for attr in ("should_reinforce", "generate_reinforcement"):
        assert not hasattr(DynamicAnchorSystem, attr), f"{attr} 零消费者已删除"
    names = {f.name for f in fields(AnchorCheckResult)}
    assert "reinforcement_needed" not in names
    sys_ = DynamicAnchorSystem(base_anchors=["a"])
    assert not hasattr(sys_, "_reinforcement_counter")


def test_emotion_engine_style_modifier_api_removed():
    from my_character.emotion_engine import EmotionEngine

    for attr in ("get_style_modifiers", "get_emotion_style_map"):
        assert not hasattr(EmotionEngine, attr), f"{attr} 零调用已删除（穷举同模式）"


def test_persona_service_dead_history_helper_removed():
    from shisi.application.persona_service import PersonaService

    assert not hasattr(PersonaService, "_build_chat_history")


def test_character_service_chain_removed():
    import importlib.util

    assert importlib.util.find_spec("shisi.application.character_service") is None
    import shisi.application as app_pkg

    assert "CharacterService" not in app_pkg.__all__
    from shisi.api.registry import AiyuRegistry

    assert not hasattr(AiyuRegistry, "character_service")
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    assert not hasattr(OptimizedOrchestrator, "_character_service")
    import inspect

    import api.app_factory as af

    assert "character_service" not in inspect.getsource(af)


def test_character_card_package_removed():
    import importlib.util
    from pathlib import Path

    import my_character

    assert importlib.util.find_spec("character_card") is None
    root = Path(my_character.__file__).resolve().parent.parent
    assert not (root / "character_card").exists(), "character_card/ 包零读者已删除，不得复活"


def test_prompt_mode_config_key_removed():
    from pathlib import Path

    import my_character

    root = Path(my_character.__file__).resolve().parent.parent
    src = (root / "config" / "system.yaml").read_text(encoding="utf-8")
    assert "prompt_mode" not in src
    init_src = (root / "orchestrator" / "_init_mixin.py").read_text(encoding="utf-8")
    assert "prompt_mode" not in init_src


def test_tone_mimic_add_conversation_still_alive():
    """审计更正：add_conversation 曾被列为零调用，实际 training_routes.py
    /api/training/apply 克隆摄入在用（:169）——此钉防误删。"""
    import inspect

    from api.routers import training_routes
    from my_character.tone_mimic import ToneMimic

    assert callable(getattr(ToneMimic, "add_conversation", None))
    assert "add_conversation" in inspect.getsource(training_routes)


# ═══════════════════════════════════════════════════════════
# 6b 项11 — 知识槽检索查询与 system 回显解耦（审计 :157）
# ═══════════════════════════════════════════════════════════


def _kb_aggregate(**kw):
    from shisi.core.models.character_aggregate import CharacterAggregate

    defaults: dict = dict(
        id="kb0011",
        name="测试角色",
        description="她是测试角色。",
    )
    defaults.update(kw)
    return CharacterAggregate(**defaults)


class _RecordingKnowledgeSvc:
    def __init__(self, ret="她最爱喝茉莉花茶。"):
        self.queries: list[tuple[str, str]] = []
        self._ret = ret

    def has_index(self, cid):
        return True

    def index_character(self, cid, character):
        raise AssertionError("索引在位时不应触发重建路径")

    def get_knowledge_context(self, cid, query, top_k=8, exclude_sources=None):
        self.queries.append((cid, query))
        return self._ret


def test_prompt_builder_knowledge_query_decoupled_from_echo(monkeypatch):
    """核心回归：user_message="" 时 knowledge_query 仍能驱动检索，且检索到的
    知识入 system、用户原话不回显（修复前该组合下检索根本不发生）。"""
    from shisi.core.services import prompt_builder

    svc = _RecordingKnowledgeSvc()
    monkeypatch.setattr(prompt_builder, "get_knowledge_service", lambda: svc)

    prompt = prompt_builder.build(
        _kb_aggregate(),
        user_message="",
        chat_history="",
        use_knowledge=True,
        use_storyline=False,
        knowledge_query="她爱喝什么",
    )
    assert svc.queries == [("kb0011", "她爱喝什么")]
    assert "# 角色知识库" in prompt
    assert "她最爱喝茉莉花茶。" in prompt
    assert "用户: 她爱喝什么" not in prompt  # 查询只检索，不回显


def test_prompt_builder_knowledge_query_falls_back_to_user_message(monkeypatch):
    """缺省回落：不传 knowledge_query 时用 user_message 做检索查询
    （PromptService/test_storyline 等旧调用方语义不变）。"""
    from shisi.core.services import prompt_builder

    svc = _RecordingKnowledgeSvc()
    monkeypatch.setattr(prompt_builder, "get_knowledge_service", lambda: svc)

    prompt = prompt_builder.build(
        _kb_aggregate(), user_message="还记得我吗", use_storyline=False
    )
    assert svc.queries == [("kb0011", "还记得我吗")]
    assert "# 角色知识库" in prompt


def test_prompt_builder_no_query_skips_retrieval(monkeypatch):
    """两者皆空 → 零检索、无知识段（开场/无人工查询路径不受本批影响）。"""
    from shisi.core.services import prompt_builder

    svc = _RecordingKnowledgeSvc()
    monkeypatch.setattr(prompt_builder, "get_knowledge_service", lambda: svc)

    prompt = prompt_builder.build(_kb_aggregate(), use_storyline=False)
    assert svc.queries == []
    assert "# 角色知识库" not in prompt


def test_persona_service_forwards_user_message_as_knowledge_query(monkeypatch):
    """接线钉：persona_service 必须把收到的 user_message 转成 knowledge_query，
    而 system 回显槽仍为 ""（v1.31 去重语义保留）。"""
    from shisi.application import persona_service as ps_mod
    from shisi.core.services import prompt_builder

    captured: dict = {}
    real_build = prompt_builder.build

    def _spy(character, **kw):
        captured.update(kw)
        return real_build(character, **kw)

    monkeypatch.setattr(prompt_builder, "build", _spy)
    svc = _RecordingKnowledgeSvc()
    monkeypatch.setattr(prompt_builder, "get_knowledge_service", lambda: svc)

    ps = ps_mod.PersonaService(config_loader=None, llm_gateway=None)
    token = "紫罗兰问题731"
    prompt = ps.build_system_prompt(character_id="default", user_message=token)
    assert captured["knowledge_query"] == token
    assert captured["user_message"] == ""
    assert ("default", token) in svc.queries or svc.queries, (
        "user_message 非空时检索必须真实发生（经 knowledge_query 通道）"
    )
    assert f"用户: {token}" not in prompt


def test_orchestrator_passes_current_message_for_knowledge_query():
    """orchestrator 必须下传本轮原话——否则整条链在生产路径上仍是空查询。"""
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    src = inspect.getsource(OptimizedOrchestrator._prepare_context)
    assert "user_message=user_msg_clean" in src, (
        "_prepare_context 必须把本轮消息传给 PersonaService（批6b 项11 接线），"
        "否则知识检索在生产路径永不触发"
    )

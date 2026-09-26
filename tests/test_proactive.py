"""单元测试: 主动消息ASE引擎 — 深度版"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")


# ═══════════════════════════════════════════════════════════════
#  ASEEngine 方法验证
# ═══════════════════════════════════════════════════════════════

def test_ase_engine_import():
    from proactive.ase_engine import ASEEngine
    assert ASEEngine is not None


def test_ase_engine_has_on_chat():
    from proactive.ase_engine import ASEEngine
    assert hasattr(ASEEngine, "on_chat")
    assert callable(ASEEngine.on_chat)


def test_ase_engine_has_tick():
    from proactive.ase_engine import ASEEngine
    assert hasattr(ASEEngine, "tick")
    assert callable(ASEEngine.tick)


def test_ase_engine_has_reflect():
    from proactive.ase_engine import ASEEngine
    assert hasattr(ASEEngine, "reflect")
    assert callable(ASEEngine.reflect)


def test_ase_engine_has_check_frequency():
    from proactive.ase_engine import ASEEngine
    assert hasattr(ASEEngine, "_check_frequency")


def test_ase_engine_has_update_urgency():
    from proactive.ase_engine import ASEEngine
    assert hasattr(ASEEngine, "_update_urgency")


def test_ase_engine_has_hours_since_last_chat():
    from proactive.ase_engine import ASEEngine
    assert hasattr(ASEEngine, "_hours_since_last_chat")


def test_ase_engine_has_is_duplicate():
    from proactive.ase_engine import ASEEngine
    assert hasattr(ASEEngine, "_is_duplicate")


def test_ase_engine_has_check_scene_triggers():
    from proactive.ase_engine import ASEEngine
    assert hasattr(ASEEngine, "_check_scene_triggers")


def test_ase_engine_init_default():
    import tempfile
    from pathlib import Path

    from proactive.ase_engine import ASEEngine

    with tempfile.TemporaryDirectory() as td:
        # 显式指向临时 state_path，避免 cwd 已有 state.json 干扰
        engine = ASEEngine(state_path=str(Path(td) / "ase_state.json"))
        assert engine is not None
        assert engine._daily_message_count == 0


def test_ase_engine_init_params():
    from proactive.ase_engine import ASEEngine
    engine = ASEEngine(
        max_daily_messages=5,
        min_interval_minutes=60,
        cooldown_after_reply=15,
        urgency_threshold=5.0,
        frequency_mode="fixed",
        generation_mode="template",
        reflection_mode="rule",
    )
    assert engine._urgency_threshold == 5.0
    assert engine._frequency_mode == "fixed"
    assert engine._generation_mode == "template"


def test_ase_engine_on_chat_returns_monologue():
    from proactive.ase_engine import ASEEngine, InnerMonologue
    engine = ASEEngine()
    result = engine.on_chat("你好", "你好呀", affinity_level=3)
    assert isinstance(result, InnerMonologue)
    assert result.thought is not None
    assert result.type in ("miss_you", "happy", "worry", "jealous", "bored")


def test_ase_engine_on_chat_updates_state():
    from proactive.ase_engine import ASEEngine
    engine = ASEEngine()
    engine.on_chat("你好", "你好呀")
    assert engine._last_chat_time is not None
    assert engine._last_proactive_time is not None


def test_ase_engine_on_chat_urgency_reset():
    from proactive.ase_engine import ASEEngine
    engine = ASEEngine()
    engine.urgency.base = 5.0
    engine.urgency.missing_bonus = 3.0
    engine.on_chat("你好", "你好呀")
    assert engine.urgency.base < 5.0
    assert engine.urgency.missing_bonus == 0


def test_ase_engine_on_chat_with_emotion():
    from proactive.ase_engine import ASEEngine
    engine = ASEEngine()
    engine.on_chat("我很难过", "别难过", emotion_state={"sadness": 0.8}, affinity_level=5)
    assert engine._emotion_state == {"sadness": 0.8}


def test_ase_engine_tick_dry_run():
    from proactive.ase_engine import ASEEngine
    engine = ASEEngine()
    result = engine.tick(dry_run=True)
    assert result is None


def test_ase_engine_reflect():
    from proactive.ase_engine import ASEEngine, InnerMonologue
    engine = ASEEngine()
    monologue = engine.reflect("你好", "你好呀", hours_since_last=5)
    assert isinstance(monologue, InnerMonologue)


# ═══════════════════════════════════════════════════════════════
#  UrgencyState 验证
# ═══════════════════════════════════════════════════════════════

def test_urgency_state_defaults():
    from proactive.ase_engine import UrgencyState
    us = UrgencyState()
    assert us.total == 0.0
    assert us.level == "还好"


def test_urgency_state_total_capped():
    from proactive.ase_engine import UrgencyState
    us = UrgencyState(base=5, missing_bonus=3, scene_bonus=2, emotion_bonus=1)
    assert us.total == 10.0


def test_urgency_state_levels():
    from proactive.ase_engine import UrgencyState
    us = UrgencyState(base=8)
    assert us.level == "非常想找你"
    us2 = UrgencyState(base=5)
    assert us2.level == "有点想你"
    us3 = UrgencyState(base=3)
    assert us3.level == "想找人说话"
    us4 = UrgencyState(base=1)
    assert us4.level == "还好"


def test_urgency_state_reset():
    from proactive.ase_engine import UrgencyState
    us = UrgencyState(base=5, missing_bonus=3, scene_bonus=2)
    us.reset()
    assert us.total == 0.0


# ═══════════════════════════════════════════════════════════════
#  ReflectionEngine 验证
# ═══════════════════════════════════════════════════════════════

def test_reflection_engine_rule_mode():
    from proactive.ase_engine import InnerMonologue, ReflectionEngine
    re = ReflectionEngine(reflection_mode="rule")
    result = re.reflect("你好", "你好呀", affinity_level=3, hours_since_last=1)
    assert isinstance(result, InnerMonologue)
    assert result.type in ("miss_you", "happy", "worry", "jealous", "bored")


def test_reflection_engine_missing_detection():
    from proactive.ase_engine import ReflectionEngine
    re = ReflectionEngine(reflection_mode="rule")
    result = re.reflect("你好", "你好呀", affinity_level=3, hours_since_last=10)
    assert result.type == "miss_you"
    assert result.urgency_delta == 2.0


def test_reflection_engine_jealous_detection():
    from proactive.ase_engine import ReflectionEngine
    re = ReflectionEngine(reflection_mode="rule")
    result = re.reflect("我跟前任吃了顿饭", "哼", affinity_level=5, hours_since_last=1)
    assert result.type == "jealous"


def test_reflection_engine_worry_detection():
    from proactive.ase_engine import ReflectionEngine
    re = ReflectionEngine(reflection_mode="rule")
    result = re.reflect("我好累", "休息一下", affinity_level=3, hours_since_last=1)
    assert result.type == "worry"


def test_reflection_engine_happy_detection():
    from proactive.ase_engine import ReflectionEngine
    re = ReflectionEngine(reflection_mode="rule")
    result = re.reflect("今天好开心", "太好了", affinity_level=3, hours_since_last=1)
    assert result.type == "happy"


def test_reflection_engine_get_latest_empty():
    from proactive.ase_engine import ReflectionEngine
    re = ReflectionEngine(reflection_mode="rule")
    assert re.get_latest_monologue() is None


def test_reflection_engine_get_latest_after_reflect():
    """D26 回归守卫：reflect() 必须把独白记入 _monologues。

    修复前 reflect() 只 return 不 append，_monologues 全仓从未被写入，
    导致 get_latest_monologue() **恒返回 None**（永远为空的假接口）。
    上面那条 empty 用例当时把这个 bug 当成期望行为固化了下来。
    """
    from proactive.ase_engine import ReflectionEngine
    from proactive.reflection import _MONOLOGUE_MAX
    re = ReflectionEngine(reflection_mode="rule")

    m = re.reflect("今天好开心", "太好了", affinity_level=3, hours_since_last=1)
    latest = re.get_latest_monologue()
    assert latest is not None, "reflect() 后必须能取到独白（D26 回归）"
    assert latest is m, "取到的必须是刚生成的那条"

    # 有界性：超出上限后长度不再增长
    for _ in range(_MONOLOGUE_MAX + 20):
        re.reflect("随便说说", "嗯", affinity_level=1, hours_since_last=1)
    assert len(re._monologues) == _MONOLOGUE_MAX


def test_reflection_engine_type_to_urgency():
    from proactive.ase_engine import ReflectionEngine
    re = ReflectionEngine(reflection_mode="rule")
    assert re._type_to_urgency("miss_you") == 2.0
    assert re._type_to_urgency("jealous") == 1.5
    assert re._type_to_urgency("worry") == 0.8
    assert re._type_to_urgency("bored") == 0.5
    assert re._type_to_urgency("happy") == 0.3
    assert re._type_to_urgency("unknown") == 0.5


# ═══════════════════════════════════════════════════════════════
#  FrequencyAdapter 验证
# ═══════════════════════════════════════════════════════════════

def test_frequency_adapter_defaults():
    from proactive.ase_engine import FrequencyAdapter
    fa = FrequencyAdapter()
    assert fa.normal_daily == 8
    assert fa.low_daily == 3
    assert fa.min_weekly == 1
    assert fa.level == "normal"


def test_frequency_adapter_normal_max():
    from proactive.ase_engine import FrequencyAdapter
    fa = FrequencyAdapter()
    assert fa.get_max_daily() == 8


def test_frequency_adapter_degrade_to_low():
    from proactive.ase_engine import FrequencyAdapter
    fa = FrequencyAdapter()
    for _ in range(3):
        fa.on_no_reply()
    assert fa.level == "low"
    assert fa.get_max_daily() == 3


def test_frequency_adapter_degrade_to_minimal():
    from proactive.ase_engine import FrequencyAdapter
    fa = FrequencyAdapter()
    for _ in range(5):
        fa.on_no_reply()
    assert fa.level == "minimal"
    assert fa.get_max_daily() == 1


def test_frequency_adapter_recover_on_reply():
    from proactive.ase_engine import FrequencyAdapter
    fa = FrequencyAdapter()
    for _ in range(3):
        fa.on_no_reply()
    assert fa.level == "low"
    for _ in range(3):
        fa.on_reply_received()
    assert fa.level == "normal"


def test_frequency_adapter_recovers_from_minimal():
    """minimal 不可永久粘滞：生产实证 level=minimal 且 unanswered_count=1（用户持续回复，
    级别却永远回不去）→ 活跃用户被永久限到 1 条/日。未应答归零先回 low，再一答回 normal。"""
    from proactive.ase_engine import FrequencyAdapter
    fa = FrequencyAdapter()
    for _ in range(5):
        fa.on_no_reply()
    assert fa.level == "minimal"
    for _ in range(4):
        fa.on_reply_received()
    assert fa.to_dict()["unanswered_count"] == 1
    assert fa.level == "minimal"  # 计数未归零不回升
    fa.on_reply_received()
    assert fa.level == "low"
    assert fa.get_max_daily() == 3
    fa.on_reply_received()
    assert fa.level == "normal"
    assert fa.get_max_daily() == 8


def test_frequency_adapter_to_dict():
    from proactive.ase_engine import FrequencyAdapter
    fa = FrequencyAdapter()
    d = fa.to_dict()
    assert "current_level" in d
    assert "unanswered_count" in d
    assert "normal_daily" in d
    assert "low_daily" in d
    assert "min_weekly" in d


def test_frequency_adapter_from_dict():
    from proactive.ase_engine import FrequencyAdapter
    fa = FrequencyAdapter()
    fa.from_dict({"current_level": "minimal", "unanswered_count": 5})
    assert fa.level == "minimal"


# ═══════════════════════════════════════════════════════════════
#  FrequencyController 验证（频率限制/冷却逻辑边界）
# ═══════════════════════════════════════════════════════════════

def test_frequency_controller_init():
    from proactive.ase_engine import FrequencyController
    fc = FrequencyController(max_daily=5, min_interval_minutes=30, cooldown_after_reply_minutes=10)
    assert fc.max_daily == 5
    assert fc.min_interval == timedelta(minutes=30)
    assert fc.cooldown == timedelta(minutes=10)


def test_frequency_controller_can_send_initial():
    from proactive.ase_engine import FrequencyController
    fc = FrequencyController()
    can, reason = fc.can_send()
    assert can is True
    assert reason == "ok"


def test_frequency_controller_daily_limit():
    from proactive.ase_engine import FrequencyController
    from utils.local_time import now_local

    fc = FrequencyController(max_daily=3, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    fc._last_reset_date = now_local().date()
    for _ in range(3):
        fc.record_sent()
    can, reason = fc.can_send()
    assert can is False
    assert reason == "daily_limit"


def test_frequency_controller_daily_limit_boundary():
    from proactive.ase_engine import FrequencyController
    from utils.local_time import now_local

    fc = FrequencyController(max_daily=3, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    fc._last_reset_date = now_local().date()
    fc.record_sent()
    fc.record_sent()
    can, _ = fc.can_send()
    assert can is True
    fc.record_sent()
    can, reason = fc.can_send()
    assert can is False


def test_frequency_controller_cooldown_after_reply():
    from proactive.ase_engine import FrequencyController
    fc = FrequencyController(max_daily=100, min_interval_minutes=0, cooldown_after_reply_minutes=10)
    fc.record_reply()
    can, reason = fc.can_send()
    assert can is False
    assert reason == "cooldown"


def test_frequency_controller_min_interval():
    from proactive.ase_engine import FrequencyController
    fc = FrequencyController(max_daily=100, min_interval_minutes=60, cooldown_after_reply_minutes=0)
    fc.record_sent()
    can, reason = fc.can_send()
    assert can is False
    assert reason == "min_interval"


def test_frequency_controller_get_state():
    from proactive.ase_engine import FrequencyController
    fc = FrequencyController(max_daily=5)
    fc.record_sent()
    state = fc.get_state()
    assert state["daily_count"] == 1
    assert state["max_daily"] == 5
    assert state["remaining"] == 4


def test_frequency_controller_to_dict():
    from proactive.ase_engine import FrequencyController
    fc = FrequencyController()
    d = fc.to_dict()
    assert "daily_count" in d
    assert "max_daily" in d
    assert "min_interval_minutes" in d
    assert "cooldown_minutes" in d


def test_frequency_controller_from_dict():
    from proactive.ase_engine import FrequencyController
    fc = FrequencyController()
    fc.from_dict({"max_daily": 10, "min_interval_minutes": 20})
    assert fc.max_daily == 10
    assert fc.min_interval == timedelta(minutes=20)


def test_frequency_controller_daily_reset():
    from proactive.ase_engine import FrequencyController
    from utils.local_time import now_local

    fc = FrequencyController(max_daily=2, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    fc._last_reset_date = now_local().date()
    fc.record_sent()
    fc.record_sent()
    can, _ = fc.can_send()
    assert can is False
    fc._last_reset_date = (now_local() - timedelta(days=1)).date()
    fc._last_sent_time = datetime.now(tz=timezone.utc) - timedelta(minutes=60)
    can, _ = fc.can_send()
    assert can is True


def test_frequency_controller_to_from_dict_preserves_daily_count():
    """状态往返后 daily_count 不得被 can_send 清零（日界字段必须落盘）。"""
    from proactive.ase_engine import FrequencyController
    from utils.local_time import now_local

    src = FrequencyController(max_daily=8, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    src.record_sent()
    src.record_sent()
    payload = src.to_dict()
    assert payload.get("last_reset_date") is not None

    dst = FrequencyController(max_daily=8, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    dst.from_dict(payload)
    assert dst._daily_count == 2
    can, reason = dst.can_send()
    assert can is True
    assert reason == "ok"
    assert dst._daily_count == 2, "恢复后的配额计数不得被日界惰性重置抹掉"
    assert dst._last_reset_date == now_local().date()


def test_frequency_controller_daily_reset_uses_local_date():
    """日界必须按本地墙钟：钉住昨天的本地日期 → 本拍应重置配额。"""
    from proactive.ase_engine import FrequencyController
    from utils.local_time import now_local

    fc = FrequencyController(max_daily=2, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    fc._last_reset_date = (now_local() - timedelta(days=1)).date()
    fc.record_sent()
    fc.record_sent()
    assert fc._daily_count == 2
    can, reason = fc.can_send()
    assert can is True
    assert reason == "ok"
    assert fc._daily_count == 0


# ═══════════════════════════════════════════════════════════════
#  ContextAnalyzer 验证
# ═══════════════════════════════════════════════════════════════

def test_context_analyzer_import():
    from proactive.ase_engine import ContextAnalyzer
    assert ContextAnalyzer is not None


def test_context_analyzer_analyze():
    from proactive.ase_engine import ContextAnalyzer
    ca = ContextAnalyzer()
    ctx = ca.analyze()
    assert "time_of_day" in ctx
    assert "hour" in ctx
    assert "weekday" in ctx
    assert "is_weekend" in ctx


def test_context_analyzer_time_periods():
    from proactive.ase_engine import ContextAnalyzer
    ca = ContextAnalyzer()
    assert ca._get_time_period(6) == "morning"
    assert ca._get_time_period(10) == "forenoon"
    assert ca._get_time_period(13) == "noon"
    assert ca._get_time_period(15) == "afternoon"
    assert ca._get_time_period(20) == "evening"
    assert ca._get_time_period(23) == "night"
    assert ca._get_time_period(3) == "night"


# ═══════════════════════════════════════════════════════════════
#  ProactiveType 枚举验证
# ═══════════════════════════════════════════════════════════════

def test_proactive_type_values():
    from proactive.ase_engine import ProactiveType
    assert ProactiveType.MORNING_GREETING.value == "morning_greeting"
    assert ProactiveType.NIGHT_GREETING.value == "night_greeting"
    assert ProactiveType.MISS_YOU.value == "miss_you"
    assert ProactiveType.BORED.value == "bored"
    assert ProactiveType.CARE_WEATHER.value == "care_weather"
    assert ProactiveType.CARE_MEAL.value == "care_meal"
    assert ProactiveType.JEALOUS.value == "jealous"
    assert ProactiveType.SHARE.value == "share"
    assert ProactiveType.WORRY.value == "worry"


# ═══════════════════════════════════════════════════════════════
#  ProactiveScheduler 验证
# ═══════════════════════════════════════════════════════════════

def test_scheduler_import():
    from proactive.scheduler import ProactiveScheduler
    assert ProactiveScheduler is not None


def test_scheduler_init():
    from proactive.scheduler import ProactiveScheduler
    ps = ProactiveScheduler()
    assert ps.ase is None
    assert ps._send is None


def test_scheduler_with_ase_engine():
    from proactive.ase_engine import ASEEngine
    from proactive.scheduler import ProactiveScheduler
    engine = ASEEngine()
    ps = ProactiveScheduler(ase_engine=engine)
    assert ps.ase is engine


def test_scheduler_start_without_apscheduler():
    from proactive.scheduler import HAS_APSCHEDULER, ProactiveScheduler
    ps = ProactiveScheduler()
    if not HAS_APSCHEDULER:
        result = ps.start()
        assert result is False


# ═══════════════════════════════════════════════════════════════
#  _get_messages 验证
# ═══════════════════════════════════════════════════════════════

def test_get_messages_high_affinity():
    from proactive.ase_engine import _get_messages
    msgs = _get_messages("morning_greeting", affinity_level=5)
    assert len(msgs) > 0
    assert any("早安" in m or "早" in m for m in msgs)


def test_get_messages_low_affinity():
    from proactive.ase_engine import _get_messages
    msgs = _get_messages("morning_greeting", affinity_level=0)
    assert len(msgs) > 0


def test_get_messages_unknown_key():
    from proactive.ase_engine import _get_messages
    msgs = _get_messages("unknown_key")
    assert isinstance(msgs, list)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All proactive tests passed!")


def test_reflection_thought_map_no_gendered_words():
    """对抗审修复：内心独白规则集不得含性别预设词（他/她/女孩/女生）。"""
    from proactive.ase_engine import ReflectionEngine

    engine = ReflectionEngine(reflection_mode="rule")
    for msg in ["我好累", "今天好开心", "我跟前任吃了顿饭", "随便聊聊"]:
        result = engine.reflect(msg, "嗯", affinity_level=3, hours_since_last=2)
        for banned in ["他", "她", "女孩", "女生"]:
            assert banned not in result.thought, f"消息[{msg}]独白[{result.thought}]含性别词[{banned}]"


def test_reflection_jealous_neutral_trigger():
    from proactive.ase_engine import ReflectionEngine

    engine = ReflectionEngine(reflection_mode="rule")
    assert engine.reflect("我跟前任吃了顿饭", "哼", affinity_level=5, hours_since_last=1).type == "jealous"
    assert engine.reflect("偶遇旧识聊了很久", "哦", affinity_level=5, hours_since_last=1).type == "jealous"
    # 性别触发词已移除：不再因提到特定性别词触发
    assert engine.reflect("别的女孩说得对", "是吗", affinity_level=5, hours_since_last=1).type != "jealous"


# ═══════════════════════════════════════════════════════════════
#  配额/投递解耦 + 免打扰前置 + 输出清洗（2026-09-19 生产事故回归）
#
#  事故：静默时段(23-7)内引擎照常生成消息并**扣配额**，投递层却丢弃消息
#  → 00:02–04:05 每 35 分钟一条、连续 8 条全丢全计数 → 配额凌晨即 8/8 满
#  → 当天 07:00 后每个 tick 都 result=False，全天零投递。
# ═══════════════════════════════════════════════════════════════

def _make_engine(tmp_path, **kw):
    from proactive.ase_engine import ASEEngine, _local_now

    engine = ASEEngine(
        state_path=str(tmp_path / "ase_state_quota.json"),
        generation_mode="template",
        **kw,
    )
    # 固定跨日重置基准为今天：否则首个 tick 的 _rollover_if_new_day()
    # 会把测试预置的 daily_count 归零，让「配额满」用例失效。
    engine._last_reset_date = _local_now().date()
    return engine


def _silence_now(engine):
    """把免打扰时段设为「包含当前本地小时」，用于模拟夜间静默。"""
    from proactive.ase_engine import _local_now

    h = _local_now().hour
    engine.set_quiet_hours(h, (h + 1) % 24)


def _open_now(engine):
    """把免打扰时段挪到当前小时之外。"""
    from proactive.ase_engine import _local_now

    h = _local_now().hour
    engine.set_quiet_hours((h + 2) % 24, (h + 3) % 24)


def _no_scene(engine):
    """屏蔽场景触发，隔离「紧迫度阈值」这条判定链。"""
    engine._check_scene_triggers = lambda commit=True: None


def test_quiet_hours_does_not_consume_quota(tmp_path):
    """核心回归：静默时段重复 tick 不得消耗配额。

    修复前该循环会把 daily_count 推到 8（=上限），这正是白天零投递的根因。
    """
    engine = _make_engine(tmp_path, max_daily_messages=8)
    engine._last_proactive_time = None
    _silence_now(engine)

    for _ in range(8):
        assert engine.tick(90.0) is None
        assert engine._last_skip_reason == "quiet_hours"

    assert engine._daily_message_count == 0


def test_quiet_hours_blocks_engine_generation(tmp_path):
    """静默时段引擎自身也不生成消息（不只是投递层拦截）。"""
    engine = _make_engine(tmp_path)
    _silence_now(engine)
    engine.urgency.base = 9.0
    assert engine.tick(99.0) is None
    assert engine._last_skip_reason == "quiet_hours"


def test_tick_candidate_is_not_committed(tmp_path):
    """tick 返回候选但**不记账**：配额 / 冷却时间 / 紧迫度均不变。"""
    engine = _make_engine(tmp_path, max_daily_messages=8)
    engine._last_proactive_time = None
    _open_now(engine)
    _no_scene(engine)
    engine.urgency.base = 9.0

    candidate = engine.tick(90.0)

    assert candidate is not None
    assert engine._daily_message_count == 0
    assert engine._last_proactive_time is None
    assert candidate.get("_committed") is None


def test_commit_sent_records_once(tmp_path):
    """投递成功后提交记账，且重复提交幂等。"""
    engine = _make_engine(tmp_path, max_daily_messages=8)
    engine._last_proactive_time = None
    _open_now(engine)
    _no_scene(engine)
    engine.urgency.base = 9.0

    candidate = engine.tick(90.0)
    assert candidate is not None and engine._daily_message_count == 0

    engine.commit_sent(candidate)
    assert engine._daily_message_count == 1
    assert engine._last_proactive_time is not None
    assert candidate["message"] in engine._recent_messages

    engine.commit_sent(candidate)
    assert engine._daily_message_count == 1


def test_undelivered_candidate_leaves_quota_intact(tmp_path):
    """模拟投递失败：调用方不 commit → 配额保持不变，可继续重试。"""
    engine = _make_engine(tmp_path, max_daily_messages=8)
    _open_now(engine)
    _no_scene(engine)

    for _ in range(5):
        engine._last_proactive_time = None
        engine.urgency.base = 9.0
        candidate = engine.tick(90.0)
        assert candidate is not None
        # 投递失败 → 不 commit
        assert engine._daily_message_count == 0

    assert engine._daily_message_count == 0


def test_scene_date_marked_only_after_commit(tmp_path, monkeypatch):
    """场景「今日已发」标记必须等投递成功才置位。

    修复前在生成时就置位 —— 该条若被静默丢弃，当天该场景再也不会补发。

    CI 时区不稳：旧写法用「当前小时」构造 morning_hours=(h,h+1)，当 h=23 时
    区间变成 (23,0)，`start<=hour<end` 恒假，随后 night 规则抢先命中。
    现固定本地 hour=8 的早晨窗口，不再依赖 runner 时钟。
    """
    from datetime import date, datetime

    from proactive import ase_engine as ase_mod

    engine = _make_engine(tmp_path)
    fixed = datetime(2026, 9, 19, 8, 30, 0)

    class _FixedNow:
        hour = fixed.hour

        @staticmethod
        def date():
            return fixed.date()

    monkeypatch.setattr(ase_mod, "_local_now", lambda: _FixedNow())
    engine._config["morning_hours"] = (7, 9)
    engine._config["night_hours"] = (22, 24)
    engine._config["meal_hours"] = []
    engine._last_morning_date = None

    scene = engine._check_scene_triggers(commit=False)
    assert scene is not None
    assert scene["_scene"] == "morning"
    assert engine._last_morning_date is None, "未投递不应置位场景日期"

    engine.commit_sent(scene)
    assert engine._last_morning_date is not None, "投递成功后应置位场景日期"
    assert engine._last_morning_date == date(2026, 9, 19)


def test_skip_reason_daily_limit(tmp_path):
    """配额满时给出明确原因，而不是只有一个 result=False。"""
    engine = _make_engine(tmp_path, max_daily_messages=8)
    _open_now(engine)
    engine._daily_message_count = 8

    assert engine.tick(90.0) is None
    assert engine._last_skip_reason == "daily_limit"


def test_skip_reason_min_interval(tmp_path):
    from datetime import datetime, timezone

    engine = _make_engine(tmp_path, max_daily_messages=8)
    _open_now(engine)
    engine._daily_message_count = 0
    # P1-25：adaptive 的 min_interval 以**投递记账时刻**为基准
    # （_last_proactive_time 会被 on_chat 拨到回复时刻，不再兼任该闸）
    engine._last_delivery_time = datetime.now(tz=timezone.utc)

    assert engine.tick(90.0) is None
    assert engine._last_skip_reason == "min_interval"


def test_skip_reason_below_threshold(tmp_path):
    from datetime import datetime, timedelta, timezone

    engine = _make_engine(tmp_path, max_daily_messages=8)
    _open_now(engine)
    _no_scene(engine)
    # 默认即 adaptive：回复冷却（10 分钟）与最小间隔都要避开才能测到紧迫度闸。
    # 上次聊天 15 分钟前 → 过冷却，且 _hours_since_last_chat()≈0.25 < 0.5 不升紧迫度。
    engine._last_chat_time = datetime.now(tz=timezone.utc) - timedelta(minutes=15)
    engine.urgency.reset()

    assert engine.tick(0.0) is None
    assert engine._last_skip_reason == "below_threshold"


def test_check_frequency_returns_reason(tmp_path):
    """_check_frequency 由 bool 改为 (bool, reason)，便于区分配额/冷却。"""
    engine = _make_engine(tmp_path, max_daily_messages=2)
    _open_now(engine)

    assert engine._check_frequency() == (True, "ok")

    engine._daily_message_count = 2
    assert engine._check_frequency() == (False, "daily_limit")


def test_sanitize_blocks_reasoning_leak():
    """生产实证泄漏原文必须被拦截（LLM 把推理过程当成了消息）。"""
    from proactive.ase_engine import sanitize_message

    leak = (
        "02:55属于深夜，不在早安、吃饭或晚安的特定时间点（晚上是22:00-0:00），"
        "但接近深夜。既然时间是凌晨快3点，这属于“其他时间”，但更"
    )
    assert sanitize_message(leak) is None


def test_sanitize_blocks_overlong_and_multiline():
    from proactive.ase_engine import sanitize_message

    assert sanitize_message("啊" * 200) is None
    # 超长推理 dump 不做「取末行」抢救（整段长度即判据）
    assert sanitize_message("分析：" + "很长的推理" * 40 + "\n早呀") is None
    # 末行过短（疑似碎片）→ 拒绝
    assert sanitize_message("思考中\n再想想\n\n第") is None


def test_sanitize_extracts_last_line_of_multiline():
    """多行时取最后一段（推理在前、正文在后）。"""
    from proactive.ase_engine import sanitize_message

    assert sanitize_message("判断时间：\n要发早安吗\n早呀，新的一天") == "早呀，新的一天"


def test_sanitize_keeps_normal_message():
    from proactive.ase_engine import sanitize_message

    msg = "哼，都这个点了还不睡？快点休息！"
    assert sanitize_message(msg) == msg
    assert sanitize_message('"早点睡啦，晚安"') == "早点睡啦，晚安"


def test_sanitize_rejects_empty():
    from proactive.ase_engine import sanitize_message

    assert sanitize_message("") is None
    assert sanitize_message("   ") is None


def test_is_duplicate_normalizes_punctuation(tmp_path):
    """去重按归一化后的等价判定（同句不同标点/表情视为重复）。"""
    engine = _make_engine(tmp_path)
    engine._recent_messages.clear()
    engine._recent_messages.append("哼，都这个点了还不睡？快点休息！🌙")

    assert engine._is_duplicate("哼 都这个点了还不睡 快点休息")
    assert not engine._is_duplicate("早呀，新的一天")


def test_is_duplicate_window_is_bounded(tmp_path):
    """去重窗口有界（6）：模板池仅 3~8 条/类，窗口过大会彻底发不出消息。"""
    engine = _make_engine(tmp_path)
    engine._recent_messages.clear()
    for i in range(20):
        engine._recent_messages.append(f"很久以前说过的第{i}句")
    assert not engine._is_duplicate("很久以前说过的第0句")


def test_select_type_avoids_recent_types(tmp_path):
    """同类节流：最近两条用过的类型不再被选中（避免同义刷屏）。"""
    engine = _make_engine(tmp_path)
    engine._recent_types.clear()
    engine.urgency.base = 9.0
    engine.urgency.missing_bonus = 0.0
    engine._last_sent_type = "miss_you"
    engine._recent_types.extend(["miss_you", "worry"])

    picked = {engine._select_type_by_urgency().value for _ in range(30)}
    assert "miss_you" not in picked
    assert "worry" not in picked


def test_scheduler_quiet_hours_uses_ase_local_clock():
    """调度器静默判定必须与 ASE 共用 _local_now，禁止 datetime.now 双真源。

    回归：CI（UTC）上 datetime.now().hour 与 _local_now()（强制 UTC+8）相差 8 小时，
    静默短路会静默失效（2026-09-19 主干 CI 连红根因之一）。
    """
    from datetime import datetime
    from unittest.mock import patch

    from proactive.scheduler import ProactiveScheduler

    class _FakeASE:
        def set_quiet_hours(self, start, end):
            pass

    with patch("proactive.scheduler._local_now") as mock_now:
        mock_now.return_value = datetime(2026, 1, 1, 20, 30, 0)
        sched = ProactiveScheduler(ase_engine=_FakeASE())
        sched.reload_config = lambda: None
        sched._quiet_hours = (20, 21)
        assert sched._is_quiet_hours() is True
        mock_now.assert_called()
        sched._quiet_hours = (22, 23)
        assert sched._is_quiet_hours() is False


def _hub_sched(monkeypatch, tmp_path):
    """批6b 项7：旧 `_check_ase_global` 全局路径已删（生产唯一装配是 ASEHub，
    _init_mixin 注入）。「静默不烧配额 / 投递失败不记账 / 投递成功才记一次」
    三条回归随之迁移到 hub + LLM 决策语义。"""
    import proactive.ase_hub as hub_mod
    import proactive.llm_proactive as lp
    import shisi.agent_plane.runtime as rt
    from proactive.ase_hub import ASEHub
    from proactive.scheduler import ProactiveScheduler

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")

    class _FakeASE:
        def __init__(self, user_key: str = "", state_path: str = ""):
            self.user_key = user_key
            self.state_path = state_path
            self._daily_message_count = 0
            self._paused = False
            self._last_skip_reason = ""
            self.urgency = type("U", (), {"total": 8.5})()
            self.tick_calls: list[bool] = []
            self.commit_calls: list[dict] = []

        def _hours_since_last_chat(self):
            return 90.0

        def tick(self, hours, emotion_state=None, dry_run=False):
            self.tick_calls.append(dry_run)
            return None

        def commit_sent(self, result):
            self.commit_calls.append(result)
            self._daily_message_count += 1

    hub = ASEHub(_FakeASE)
    sched = ProactiveScheduler(ase_engine=hub)
    sched.reload_config = lambda: None
    key = "4:peer@im.wechat"
    sched._collect_ase_user_keys = lambda: [key]
    sched._resolve_proactive_llm = lambda eng=None: object()

    monkeypatch.setattr(lp, "read_web_proactive_config", lambda: {"enabled": True})
    monkeypatch.setattr(lp, "load_persona_hint", lambda cid="": "")
    monkeypatch.setattr(rt, "project_profile_for", lambda uk: {})
    monkeypatch.setattr(rt, "get_profile_prompt_block", lambda uk: "")
    monkeypatch.setattr(rt, "append_proactive_event", lambda **kw: None)
    return sched, hub, key


def test_scheduler_rejects_non_hub_ase_injection():
    """装配缺陷必须显式可见：注入非 ASEHub 引擎时整段跳过，不静默走旧路径。"""
    from proactive.scheduler import ProactiveScheduler

    class _LegacyEngine:
        def __init__(self):
            self.tick_calls: list[bool] = []

        def tick(self, hours, dry_run=False):
            self.tick_calls.append(dry_run)
            return None

    eng = _LegacyEngine()
    sched = ProactiveScheduler(ase_engine=eng)
    sched.reload_config = lambda: None

    sched._check_ase()

    assert eng.tick_calls == [], "非 hub 注入属装配缺陷，禁止静默兼容运行"


def test_scheduler_quiet_hours_skips_before_generation(monkeypatch, tmp_path):
    """静默时段必须在 **LLM 决策/生成之前** 短路：不决策、不记账、不扣配额。"""
    import proactive.llm_proactive as lp
    from proactive.ase_engine import _local_now

    sched, hub, key = _hub_sched(monkeypatch, tmp_path)
    decide_calls: list[int] = []
    monkeypatch.setattr(lp, "decide_proactive", lambda llm, ctx: decide_calls.append(1) or {})
    h = _local_now().hour
    sched._quiet_hours = (h, (h + 1) % 24)

    sched._check_ase()

    eng = hub.get(key)
    assert decide_calls == [], "P1-49：静默时段不得先烧 LLM 再在投递层丢弃"
    assert eng.tick_calls == []
    assert eng.commit_calls == []
    assert eng._daily_message_count == 0


def test_scheduler_does_not_commit_when_delivery_fails(monkeypatch, tmp_path):
    """投递失败（返回 False）时绝不提交记账 —— 本条修复的核心不变量（hub 语义）。"""
    import proactive.llm_proactive as lp
    from proactive.ase_engine import _local_now

    sched, hub, key = _hub_sched(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lp, "decide_proactive",
        lambda llm, ctx: {
            "should_contact": True, "reason": "miss",
            "wait_minutes": None, "message": "想你啦，在干嘛",
        },
    )
    h = _local_now().hour
    sched._quiet_hours = ((h + 2) % 24, (h + 3) % 24)  # 非静默，放行到投递层
    sched._deliver = lambda message, session_key=None, character_id=None: False

    sched._check_ase()

    eng = hub.get(key)
    assert eng.commit_calls == []
    assert eng._daily_message_count == 0


def test_scheduler_commits_after_successful_delivery(monkeypatch, tmp_path):
    """投递成功才记账，且只记一次。"""
    import proactive.llm_proactive as lp
    from proactive.ase_engine import _local_now

    sched, hub, key = _hub_sched(monkeypatch, tmp_path)
    monkeypatch.setattr(
        lp, "decide_proactive",
        lambda llm, ctx: {
            "should_contact": True, "reason": "miss",
            "wait_minutes": None, "message": "想你啦，在干嘛",
        },
    )
    h = _local_now().hour
    sched._quiet_hours = ((h + 2) % 24, (h + 3) % 24)
    sent: list[str] = []

    def _deliver(message, session_key=None, character_id=None):
        sent.append(message)
        return True

    sched._deliver = _deliver

    sched._check_ase()

    eng = hub.get(key)
    assert sent == ["想你啦，在干嘛"]
    assert len(eng.commit_calls) == 1
    assert eng._daily_message_count == 1


def test_scheduler_send_to_all_returns_false_in_quiet_hours():
    """投递层兜底：静默时段返回 False（供上游判定「未送达、不记账」）。"""
    import asyncio as _asyncio

    from proactive.ase_engine import _local_now
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler(ase_engine=None)
    h = _local_now().hour
    sched._quiet_hours = (h, (h + 1) % 24)

    assert _asyncio.run(sched._send_to_all("测试消息")) is False

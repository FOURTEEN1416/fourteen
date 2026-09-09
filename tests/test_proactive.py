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
    us = UrgencyState(base=5, missing_bonus=3, event_bonus=2, scene_bonus=2)
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
    us = UrgencyState(base=5, missing_bonus=3, event_bonus=2)
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
    fc = FrequencyController(max_daily=3, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    fc._last_reset_date = datetime.now(tz=timezone.utc).date()
    for _ in range(3):
        fc.record_sent()
    can, reason = fc.can_send()
    assert can is False
    assert reason == "daily_limit"


def test_frequency_controller_daily_limit_boundary():
    from proactive.ase_engine import FrequencyController
    fc = FrequencyController(max_daily=3, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    fc._last_reset_date = datetime.now(tz=timezone.utc).date()
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
    fc = FrequencyController(max_daily=2, min_interval_minutes=0, cooldown_after_reply_minutes=0)
    fc._last_reset_date = datetime.now(tz=timezone.utc).date()
    fc.record_sent()
    fc.record_sent()
    can, _ = fc.can_send()
    assert can is False
    fc._last_reset_date = (datetime.now(tz=timezone.utc) - timedelta(days=1)).date()
    fc._last_sent_time = datetime.now(tz=timezone.utc) - timedelta(minutes=60)
    can, _ = fc.can_send()
    assert can is True


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

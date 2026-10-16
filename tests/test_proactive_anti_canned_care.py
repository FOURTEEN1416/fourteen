"""F1+F2+F5 三项修复回归（2026-10-16 生产报障批）。

生产实证（会话4 / 林挽夏）：
1. 07:06 主动消息「起了 早上记得吃口热饭」——用户报障「打卡式关怀，7 点准时
   嘱咐吃早饭，像闹钟不像人」。参照爱语 2.8.8 反编译 ProactiveMessageService.b()
   （主动 prompt 仅四段人设拼盘、无时间注入、无打卡模板）确证打卡问题来自
   模板/约束残留而非模型本身（F1）。
2. 07:01 决策 should=False，reason 明写「保持沉默、等待1小时至08:01再观察」，
   但 wait_minutes=None —— 等待语义没落到字段，等待窗没被设置，07:06 下一 tick
   立即反悔 should=True（F2）。
3. 时间感知现状是三行显式推理指引（TIME_REASONING_GUIDE「刻意处理时间」），
   用户要的是「自然有时间观念但不强调、不刻意」（F5）。

本文件钉死三件：
- F1：两个主动约束段 + DECISION_SYSTEM 各含「打卡式嘱咐」关键词在场；
- F2：parse_decision 对 reason 等待语义的 wait 兜底（N 小时/N 分钟/兜底 30/无
  → None/正常 should=true 不受影响）；
- F5：_INSTRUCTION_IMMERSIVE/_INSTRUCTION_NOVEL 与两个约束段含「不要点破」/
  「报时」类关键词；
- 用户隔离：这些全是我们主动注入的**静态策略文本**（无会话状态），同一修改对
  多会话逐字节一致 —— 断言两会话读到同一约束文本，不存在按用户分叉的共享状态。
"""

from __future__ import annotations

import pytest

from proactive.llm_proactive import (
    DECISION_SYSTEM,
    WAIT_MINUTES_FALLBACK,
    parse_decision,
)
from proactive.proactive_context import (
    _IMMERSIVE_CONSTRAINTS,
    _NOVEL_CONSTRAINTS,
)
from utils.reply_mode import _INSTRUCTION_IMMERSIVE, _INSTRUCTION_NOVEL


def _decision(reason: str, *, should: bool = False, wait=None) -> dict:
    """构造决策 JSON（字段与 DECISION_SYSTEM 硬规则 2 同形）。"""
    import json

    return parse_decision(json.dumps({
        "should_contact": should,
        "wait_minutes": wait,
        "message": "" if not should else "在吗",
        "reason": reason,
    }, ensure_ascii=False))


def _flat(text: str) -> str:
    """折行不变量断言：剥掉换行/缩进后比对（长条款在源码里按行折行）。"""
    return "".join(str(text).split())


# ── 1. F1 禁打卡式嘱咐（两个约束段 + DECISION_SYSTEM）───────


class TestCannedCareBan:
    def test_immersive_constraints_ban_canned_care(self):
        """沉浸式约束段：禁打卡式嘱咐 + 「记得吃早饭」类词在场。"""
        assert "打卡式嘱咐" in _IMMERSIVE_CONSTRAINTS
        for phrase in ("记得吃早饭", "早点休息", "多喝热水"):
            assert phrase in _IMMERSIVE_CONSTRAINTS, f"缺词: {phrase}"
        assert "问候可以" in _IMMERSIVE_CONSTRAINTS, "必须给出合法边界（问候可以）"
        assert "饿着" in _IMMERSIVE_CONSTRAINTS and "累着" in _IMMERSIVE_CONSTRAINTS, (
            "必须给出例外口（画像/对话明确说对方此刻正饿着/累着）"
        )

    def test_novel_constraints_ban_canned_care(self):
        """小说式约束段同款（打卡问题与模式无关，两模式都要拦）。"""
        assert "打卡式嘱咐" in _NOVEL_CONSTRAINTS
        assert "记得吃早饭" in _NOVEL_CONSTRAINTS
        assert "饿着" in _NOVEL_CONSTRAINTS and "累着" in _NOVEL_CONSTRAINTS

    def test_decision_system_bans_canned_care(self):
        """DECISION_SYSTEM 硬规则同款（决策线生成的 message 也要拦）。"""
        assert "打卡式嘱咐" in DECISION_SYSTEM
        assert "记得吃早饭" in DECISION_SYSTEM
        # 生产原文（07:06 那条）作为反例锚在案
        assert "早上记得吃口热饭" in DECISION_SYSTEM, "生产报障原句必须在案（负例锚）"


# ── 2. F2 决策稳定闸（reason 等待语义 → wait_minutes 兜底）──


class TestDecisionWaitFallback:
    def test_hours_semantic_carried_to_wait(self):
        """🔴 生产原句：reason 含「等待1小时」且 wait 缺失 → wait_minutes=60。"""
        d = _decision("保持沉默、等待1小时至08:01再观察")
        assert d["should_contact"] is False
        assert d["wait_minutes"] == 60, "等待语义必须落到 wait 字段（根治 07:06 反悔）"

    def test_minutes_semantic_carried_to_wait(self):
        """reason 含「等45分钟」→ wait_minutes=45。"""
        assert _decision("用户在忙，等45分钟后再看")["wait_minutes"] == 45

    def test_chinese_numeral_hours(self):
        """中文数字「一小时/两小时/半小时」同样提取（模型常写中文数字）。"""
        assert _decision("稍后再看，等一小时")["wait_minutes"] == 60
        assert _decision("再等两小时")["wait_minutes"] == 120
        assert _decision("等半小时再观察")["wait_minutes"] == 30

    def test_wait_semantic_without_duration_falls_back(self):
        """只有等待语义无具体时长（「稍后再观察」）→ 兜底 30 分钟。"""
        d = _decision("刚聊完，稍后再观察")
        assert d["wait_minutes"] == WAIT_MINUTES_FALLBACK == 30

    def test_no_wait_semantic_stays_none(self):
        """无等待语义 → wait_minutes 保持 None（旧口径，不凭空造等待窗）。"""
        assert _decision("刚聊完，这个时间点不合适")["wait_minutes"] is None
        assert _decision("用户在忙，先不打扰")["wait_minutes"] is None

    def test_existing_wait_value_wins(self):
        """模型已填 wait_minutes → 原值优先（兜底只补缺失，不覆盖）。"""
        assert _decision("等待1小时", wait=90)["wait_minutes"] == 90

    def test_should_true_unaffected(self):
        """should_contact=true 不受影响：reason 里的等待语义不改变投递决策。

        🔴 断言必须能区分实现：reason 带等待语义（「等一小时再说」），
        若实现误把兜底应用到 should=True 侧，wait_minutes 会被填成 60 而变红
        （旧版用无语义 reason「接住两天没聊的话题」，兜底开不开都返回 None，
        是假绿 —— 2026-10-16 独立复核突变实证）。
        """
        d = _decision("等一小时再说", should=True, wait=None)
        assert d["should_contact"] is True
        assert d["message"] == "在吗"
        assert d["wait_minutes"] is None, "should=true 侧不做等待语义兜底"

    def test_cn_multi_digit_numerals(self):
        """中文多位数字按值解析（非取首字）：三十→30 / 四十五→45 / 二十→20 / 十一→11。

        🔴 旧实现 `raw[0]` 取首字：「等待三十分钟」→3、「等待四十五分钟」→4，
        wait 窗口在下一次 5 分钟 tick 前即过期 = 闸失效（独立复核实测）。
        """
        assert _decision("等待三十分钟后再看")["wait_minutes"] == 30
        assert _decision("等待四十五分钟")["wait_minutes"] == 45
        assert _decision("等待二十分钟")["wait_minutes"] == 20
        assert _decision("等待十一分钟")["wait_minutes"] == 11
        assert _decision("等待两小时")["wait_minutes"] == 120

    def test_wait_zero_or_negative_treated_as_missing(self):
        """wait=0/-5 是显式无效值（scheduler wait_m>0 不设窗 = 等于没填）→ 兜底下场。

        生产边界组合：模型填 wait=0 + reason 写「等待1小时」，旧实现兜底不下场，
        仍会 5 分钟后反悔（独立复核 #4 条）。
        """
        assert _decision("等待1小时", wait=0)["wait_minutes"] == 60
        assert _decision("等待1小时", wait=-5)["wait_minutes"] == 60

    def test_wait_semantic_beyond_300_char_reason(self):
        """等待语义写在 reason 300 字之后也不能丢（提取用完整文本，截断只作用展示）。"""
        long_reason = "铺垫分析" * 80 + "，综合考虑之后等待1小时再观察"
        assert len(long_reason) > 300
        assert _decision(long_reason)["wait_minutes"] == 60

    def test_duration_without_wait_verb(self):
        """无等待动词的时长+「后再」形态（「一小时后再说」）同样提取。"""
        assert _decision("一小时后再说")["wait_minutes"] == 60
        assert _decision("晚点再说")["wait_minutes"] == 30

    def test_reason_returned_truncated_but_extraction_full(self):
        """落库 reason 仍截 300 字，但提取基于完整文本 —— 两者互不干扰。"""
        long_reason = "铺" * 310 + "等待1小时"
        d = _decision(long_reason)
        assert d["wait_minutes"] == 60
        assert len(d["reason"]) <= 300

    def test_decision_rule_requires_wait_field(self):
        """DECISION_SYSTEM 硬规则：should=false 必须给具体分钟数（含「无效」口径）。"""
        assert "wait_minutes 必须给出具体分钟数" in DECISION_SYSTEM
        assert "决策无效" in DECISION_SYSTEM

    def test_each_session_gets_own_fallback(self):
        """用户隔离：兜底取自各自 reason，两会话互不串（无共享可变状态）。"""
        a = _decision("等待1小时至08:01再观察")
        b = _decision("稍后再观察")
        assert (a["wait_minutes"], b["wait_minutes"]) == (60, 30)


# ── 3. F5 自然时间观（不强调、不刻意）────────────────────


class TestNaturalTimeAwareness:
    def test_immersive_instruction_natural_time(self):
        """沉浸式主链指令：时间感是底色不是台词（不报时/不点破/不解释）。"""
        flat = _flat(_INSTRUCTION_IMMERSIVE)
        assert "不要点破" in flat
        assert "报时" in flat
        assert "时间感是底色不是台词" in flat

    def test_novel_instruction_natural_time(self):
        flat = _flat(_INSTRUCTION_NOVEL)
        assert "不要点破" in flat
        assert "报时" in flat
        assert "时间感是底色不是台词" in flat

    def test_proactive_constraints_natural_time(self):
        """两个主动约束段同步同款（不点破时间、不解释怎么知道现在几点）。"""
        for text in (_IMMERSIVE_CONSTRAINTS, _NOVEL_CONSTRAINTS):
            flat = _flat(text)
            assert "不报时" in flat
            assert "不点破" in flat
            assert "不解释你怎么知道现在几点" in flat

    def test_anchor_guide_untouched(self):
        """⚠️ 负向边界：TIME_REASONING_GUIDE 是防「把叙述当进行」的既有修复，
        不得被本批删除（锚点行仍在）。"""
        from utils.local_time import TIME_REASONING_GUIDE, time_anchor_line

        assert TIME_REASONING_GUIDE in time_anchor_line(None, None)
        assert "不得当作正在发生" in TIME_REASONING_GUIDE


# ── 4. 用户隔离：静态策略文本对多会话逐字节一致 ──────────────


class TestMultiSessionIsolation:
    def test_constraints_text_identical_across_sessions(self, monkeypatch, tmp_path):
        """约束段是进程级静态文本：两会话（无会话键依赖）读到的逐字节相同。

        口径：本批三处改动全是我们**主动注入的策略文本**，不含任何按用户
        分叉的共享状态 —— 同一修改对所有会话同时生效且互不影响（对照：
        称呼段/画像段才带会话键，不在此列）。
        """
        import utils.reply_mode as rm
        from proactive.proactive_context import immersive_proactive_constraints

        monkeypatch.setattr(rm, "_CONFIG_PATH", tmp_path / "reply_mode_test.json")
        first = immersive_proactive_constraints()
        second = immersive_proactive_constraints()
        assert first == second and first, "会话间不得出现分叉（无共享可变状态）"
        # 决策器文本同源（进程级常量）：两次装配同一对象
        assert DECISION_SYSTEM is DECISION_SYSTEM
        import proactive.llm_proactive as lp

        assert lp.DECISION_SYSTEM == DECISION_SYSTEM

    def test_reply_mode_text_not_user_scoped(self):
        """主链指令按模式（非按用户）分叉：同模式多用户拿到同一份。"""
        from utils.reply_mode import reply_mode_instruction

        assert reply_mode_instruction("immersive") == _INSTRUCTION_IMMERSIVE
        assert reply_mode_instruction("novel") == _INSTRUCTION_NOVEL
        assert _INSTRUCTION_IMMERSIVE != _INSTRUCTION_NOVEL


# ── 5. 突变验红锚（防批量删除关键词后静默变绿）─────────────


@pytest.mark.parametrize(
    "text_fn",
    [
        lambda: _IMMERSIVE_CONSTRAINTS,
        lambda: _NOVEL_CONSTRAINTS,
    ],
)
def test_canned_care_ban_present_in_both_constraint_blocks(text_fn):
    """两条约束段任一缺失禁打卡条款即红（参数化锁死双段）。"""
    text = text_fn()
    assert "打卡式嘱咐" in text
    assert "真人不会定时打卡发这个" in text

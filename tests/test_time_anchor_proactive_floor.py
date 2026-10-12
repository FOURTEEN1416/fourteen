"""时间感知 + 主动关怀下限 回归测试（2026-10-11 批次，红测先行）。

生产背景：用户中秋发消息、国庆再发，角色仍当作"刚刚在聊"——全链路
（主链 system prompt / 对话历史 / 跨会话尾巴 / 主动决策线）没有任何
时间注入；主动关怀则因"低热度 → 模型无限判别打扰"沉默 4 天（2026-10-08
起账本实证：每天 15-25 次决策全部 should=False）。

参照的成熟实现（调研留痕 2026-10-11，LOG 见批次条目）：
- hermes-agent issue #62369：`[time anchor]` 单行锚点惯例（当前本地时间+
  星期+距上一条消息时长，~30 token，fail-open）——本批锚点格式出处；
- SillyTavern 官方文档宏体系（{{isodate}}/{{weekday}}/{{idleDuration}}）：
  角色扮演领域唯一产品级时间感知实现。⚠️ AGPL-3.0 项目，**仅语义对齐，
  未抄任何代码**；
- arXiv 2510.23853（"Your LLM Agents are Temporally Blind"）：即使给了
  时间戳，模型对"过了多久该不该行动"的判断仍不超 ~65% 对齐人类偏好
  —— 主动关怀下限必须显式策略（wait_minutes 上限 + 今日零主动提示），
  不能指望模型看着时间戳自己想通。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

# ── 1. 时间锚点（utils/local_time）────────────────────────


class TestTimeAnchor:
    """timestamp 输入语义 = chat_history.created_at 同款 **UTC** 串（INSERT
    全部走 DEFAULT CURRENT_TIMESTAMP）；锚点输出北京时间墙钟。"""

    def _import(self):
        from utils.local_time import time_anchor_line

        return time_anchor_line

    def test_anchor_contains_core_signals(self):
        time_anchor_line = self._import()
        now = datetime(2026, 10, 11, 22, 30, 0)
        last = "2026-10-08 12:15:00"  # UTC → 北京 10-08 20:15
        line = time_anchor_line(last_message_at=last, now=now)
        # hermes-agent 惯例：锚点标记 + 当前日期 + 星期 + 距上条消息时长
        assert "[时间锚点]" in line
        assert "2026-10-11" in line
        assert "22:30" in line
        assert "周日" in line
        assert "3 天" in line  # 10-08 20:15 → 10-11 22:30

    def test_anchor_short_gap_uses_hours(self):
        time_anchor_line = self._import()
        now = datetime(2026, 10, 11, 22, 30, 0)
        line = time_anchor_line(last_message_at="2026-10-11 11:00:00", now=now)  # UTC → 19:00
        assert "3.5 小时" in line

    def test_anchor_without_history_does_not_crash(self):
        time_anchor_line = self._import()
        line = time_anchor_line(last_message_at=None, now=datetime(2026, 10, 11))
        assert "[时间锚点]" in line
        assert "天前" not in line  # 无历史不虚构间隔

    def test_anchor_malformed_timestamp_fails_open(self):
        time_anchor_line = self._import()
        # fail-open（hermes-agent 同款原则）：畸形时间戳不抛异常，忽略间隔段
        line = time_anchor_line(last_message_at="not-a-date", now=datetime(2026, 10, 11))
        assert "[时间锚点]" in line


# ── 2. 历史消息间隔标注（orchestrator/context_budget）──────


class TestHistoryAnnotation:
    def _import(self):
        from orchestrator.context_budget import annotate_history_time_gaps

        return annotate_history_time_gaps

    def _rows(self):
        # timestamp = UTC 串；标注输出本地（北京）时间
        return [
            {"role": "user", "content": "早", "timestamp": "2026-10-01 00:00:00"},        # 北京 08:00
            {"role": "assistant", "content": "早呀", "timestamp": "2026-10-01 00:00:20"},  # 北京 08:00:20
            # 隔了 6 天 —— 必须被标出来（中秋→国庆问题的直接解药）
            {"role": "user", "content": "我国庆回来了", "timestamp": "2026-10-07 01:30:00"},    # 北京 09:30
            {"role": "assistant", "content": "可算等到你", "timestamp": "2026-10-07 01:30:15"},  # 北京 09:30:15
        ]

    def test_long_gap_gets_time_prefix(self):
        annotate = self._import()
        out = annotate(self._rows())
        # 间隔 6 天的那条 user 消息带时间前缀（北京时间）
        third = out[2]["content"]
        assert third.startswith("[10-07 09:30]")
        assert "我国庆回来了" in third
        # 2026-10-12 语义升级：短间隔行不再豁免，同样必须带前缀
        # （生产实证 08:13 叙述钟点被当正在发生，就是连续聊天的形态）
        assert out[1]["content"].startswith("[10-01 08:00]")
        assert "早呀" in out[1]["content"]

    def test_first_message_far_from_now_annotated(self):
        annotate = self._import()
        now = datetime(2026, 10, 11, 22, 0, 0)
        out = annotate(self._rows(), now=now)
        # 首条距 now 远超 24h：也带时间前缀（否则模型把 10 天前当刚刚）
        assert out[0]["content"].startswith("[10-01 08:00]")

    def test_recent_continuous_chat_also_stamped(self):
        """2026-10-12 语义升级改写（旧名 test_recent_continuous_chat_untouched）：
        短时连续聊天不再豁免 —— 尾部消息同样带时间前缀，模型才能把用户叙述的
        钟点与该条消息的发送时刻对齐推理。"""
        annotate = self._import()
        now = datetime(2026, 10, 7, 9, 31, 0)  # 距尾条（北京 09:30:15）45 秒
        out = annotate(self._rows(), now=now)
        assert out[3]["content"].startswith("[10-07 09:30]")
        assert "可算等到你" in out[3]["content"]

    def test_rows_without_timestamp_passed_through(self):
        annotate = self._import()
        rows = [
            {"role": "user", "content": "旧形态行"},
            {"role": "assistant", "content": "没有时间字段"},
        ]
        out = annotate(rows)
        assert out == rows

    def test_input_not_mutated(self):
        annotate = self._import()
        rows = self._rows()
        snapshot = [dict(r) for r in rows]
        annotate(rows)
        assert rows == snapshot


# ── 3. 历史清洗保留时间戳（utils/prompt_sanitize）──────────


class TestSanitizeKeepsTimestamp:
    def test_timestamp_field_survives(self):
        from utils.prompt_sanitize import sanitize_llm_history

        rows = [
            {"role": "user", "content": "早", "timestamp": "2026-10-01 08:00:00"},
            {"role": "assistant", "content": "早呀", "timestamp": "2026-10-01 08:00:20"},
        ]
        out = sanitize_llm_history(rows, current_user_message="不在历史里的话")
        assert out, "清洗后不应为空"
        assert all("timestamp" in r for r in out), "timestamp 必须在清洗后存活"


# ── 4. 主动关怀：等待上限 + 关怀下限（proactive/llm_proactive）──


class TestProactiveFloor:
    def test_wait_minutes_capped(self):
        from proactive.llm_proactive import clamp_wait_minutes

        # 生产实证：模型自判 wait=360 分钟，可无限续期 → 10-08 起沉默 4 天
        assert clamp_wait_minutes(360) == 240
        assert clamp_wait_minutes(10000) == 240
        assert clamp_wait_minutes(30) == 30
        assert clamp_wait_minutes(0) == 0
        assert clamp_wait_minutes(None) is None

    def _floor(self, hours: float, sent_today: bool):
        from proactive.llm_proactive import proactive_floor_extra

        return proactive_floor_extra(hours_since_last_chat=hours, sent_today=sent_today)

    def test_floor_triggers_on_long_idle_without_any_send(self):
        extra = self._floor(hours=96.0, sent_today=False)
        assert extra, "失联 4 天且今日零主动 → 必须注入下限提示"
        assert "主动" in extra

    def test_floor_silent_when_already_sent_today(self):
        assert self._floor(hours=96.0, sent_today=True) == ""

    def test_floor_silent_on_short_idle(self):
        assert self._floor(hours=3.0, sent_today=False) == ""

    def test_build_context_renders_message_timestamps(self):
        from proactive.llm_proactive import build_proactive_context

        recent = [
            {"role": "user", "content": "军训好累", "timestamp": "2026-09-25 03:00:00"},  # UTC → 北京 11:00
            {"role": "assistant", "content": "辛苦啦", "timestamp": "2026-09-25 03:00:30"},
        ]
        ctx = build_proactive_context(
            session_key="s", hours_since_last_chat=400.0,
            local_time="2026-10-11 22:00 周日", recent_messages=recent,
        )
        assert "09-25 11:00" in ctx, "主动决策上下文必须带消息时间，否则模型把半月前当刚刚"


# ── 6. 工具终审可观测（生产 0 落库 + 0 日志查无对证的解药）────


class _Tools:
    def __init__(self, registry: dict):
        self.registry = SimpleNamespace(
            get_tools_by_permission=lambda level: list(registry.values()),
        )


class TestToolGateObservability:
    def _run(self, caplog, payload: dict, query: str):
        from orchestrator.optimized_orchestrator import OptimizedOrchestrator

        async def chat_with_tools(**kwargs):
            return payload

        llm = SimpleNamespace(chat_with_tools=chat_with_tools)
        orch = OptimizedOrchestrator()
        orch.components = {"tools": _Tools({}), "memory": SimpleNamespace(structured_memory=None)}
        with caplog.at_level(logging.INFO, logger="orchestrator.optimized"):
            asyncio.run(orch._run_tools_if_needed(
                llm, query, "sys", [], affinity_level=0,
                session_key="1:peer@im.wechat",
            ))
        return caplog

    def test_l0_hit_and_no_tool_verdict_logged(self, caplog):
        caplog = self._run(caplog, {"content": "闲聊回复", "tool_calls": []}, "明早六点叫我起床")
        text = caplog.text
        assert "L0 晋级" in text, "L0 命中必须留痕（生产排查查无对证的教训）"
        assert "L1 判决" in text and "no_tool" in text

    def test_user_original_text_never_logged(self, caplog):
        secret = "这是不该进日志的原文zq7x"
        caplog = self._run(caplog, {"content": "嗯", "tool_calls": []}, secret)
        assert secret not in caplog.text, "P0 隐私红线：用户原文不得落日志"

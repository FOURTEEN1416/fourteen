"""活材料钩子 + 决策规则 13（2026-10-16 三连同义根治批 B）红测。

爱语逆向对照（ProactiveMessageService.b/lq）：其主动消息 = 人设四段 +
【当前记忆库内容】+ 最近 20 轮对话一次续写——话题感全部来自活材料注入。
本批为决策线构造同构【现在真正值得你接话的事】：未答澄清/将到提醒/
未闭环承诺/近日热点，宁缺毋滥（任一来源失败即跳过，不注入假材料）。

规则 13：message 必须围绕活材料具体一条展开；reason 必须写明由头；
禁止「已经过去了一段时间/现在是 X 午」式纯时间理由。
"""

from __future__ import annotations

import json

from proactive.llm_proactive import DECISION_SYSTEM, build_proactive_context
from proactive.proactive_hooks import HOOKS_SECTION_TITLE, load_live_hooks

# ── 假 StructuredMemory ────────────────────────────────────────


class _FakeSM:
    def __init__(self, intent=None, reminders=None, facts=None):
        self._intent = intent
        self._reminders = reminders or []
        self._facts = facts or []

    def get_active_pending_intent(self, session_key):
        return self._intent

    def get_pending_reminders(self, session_key):
        return self._reminders

    def get_facts(self, category=None, user_key=None, limit=50, **kw):
        return self._facts


class _FakeMemory:
    """模拟调度器 _resolve_memory() 返回的组件（_pipeline.sm 寻径）。"""

    def __init__(self, sm):
        self._pipeline = type("P", (), {"sm": sm})()


# ── 活材料组装 ────────────────────────────────────────────────


class TestLoadLiveHooks:
    def test_all_sources_assembled(self, tmp_path, monkeypatch):
        from datetime import timedelta

        from utils.local_time import now_local

        future = (now_local() + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        sm = _FakeSM(
            intent={"last_question": "周末要不要一起去爬山？", "intent": "set_reminder"},
            reminders=[{"content": "明早六点叫我起床", "trigger_time": future}],
            facts=[{"fact": "这周答辩很紧张", "updated_at": "2099-01-01 08:00:00"}],
        )
        hooks = load_live_hooks("4:wx@im.wechat", _FakeMemory(sm))
        assert hooks, "四源齐发时必须有活材料"
        assert "周末要不要一起去爬山" in hooks.section
        assert "明早六点叫我起床" in hooks.section
        assert "这周答辩很紧张" in hooks.section
        assert HOOKS_SECTION_TITLE in hooks.section
        assert any("答辩" in k for k in hooks.keywords)

    def test_empty_sources_falsy(self):
        sm = _FakeSM()
        hooks = load_live_hooks("4:wx@im.wechat", _FakeMemory(sm))
        assert bool(hooks) is False
        assert hooks.section == "" and hooks.keywords == []

    def test_none_memory_falsy(self):
        assert bool(load_live_hooks("4:wx@im.wechat", None)) is False

    def test_reminder_outside_horizon_skipped(self):
        sm = _FakeSM(
            reminders=[{"content": "明年的事", "trigger_time": "2099-12-01 06:00:00"}]
        )
        hooks = load_live_hooks("4:wx@im.wechat", _FakeMemory(sm))
        assert "明年的事" not in hooks.section

    def test_old_commitment_fact_skipped(self):
        sm = _FakeSM(facts=[{"fact": "远古承诺", "updated_at": "2020-01-01 08:00:00"}])
        hooks = load_live_hooks("4:wx@im.wechat", _FakeMemory(sm))
        assert "远古承诺" not in hooks.section

    def test_hot_topics_loaded(self, tmp_path, monkeypatch):
        from utils import project_paths

        fake_data = tmp_path / "hot_topics.json"
        fake_data.write_text(
            json.dumps(
                {
                    "items": [
                        {"title": "今日热议甲事件", "expires_at": 9999999999.0},
                        {"title": "过期事件乙", "expires_at": 1.0},
                    ],
                    "last_collect_at": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            project_paths, "project_path", lambda *a: fake_data
        )
        hooks = load_live_hooks("4:wx@im.wechat", _FakeMemory(_FakeSM()))
        assert "今日热议甲事件" in hooks.section
        assert "过期事件乙" not in hooks.section

    def test_hot_topics_missing_file_graceful(self, monkeypatch):
        from utils import project_paths

        def _raise(*a):
            raise FileNotFoundError("no file")

        monkeypatch.setattr(project_paths, "project_path", _raise)
        hooks = load_live_hooks("4:wx@im.wechat", _FakeMemory(_FakeSM()))
        assert bool(hooks) is False  # 单源失败不炸整节

    def test_section_capped(self):
        long_q = "长" * 200
        sm = _FakeSM(intent={"last_question": long_q})
        hooks = load_live_hooks("4:wx@im.wechat", _FakeMemory(sm))
        assert len(hooks.section) <= 600


# ── 决策上下文注入 + 规则 13 ──────────────────────────────────


class TestContextAndRules:
    def test_hooks_section_injected(self):
        ctx = build_proactive_context(
            session_key="4:wx@im.wechat",
            hours_since_last_chat=3.0,
            local_time="2026-10-16 16:00 Friday",
            hooks_text=f"{HOOKS_SECTION_TITLE}\n- TA 说过「这周答辩很紧张」",
        )
        assert "这周答辩很紧张" in ctx
        assert HOOKS_SECTION_TITLE in ctx

    def test_hooks_section_absent_when_empty(self):
        """2026-10-17 批契约升级：空 hooks 从「整段缺席」改为「空仓显式标注」——
        生产实证零由头是客服腔的生成源（模型在材料真空里硬挤泛问候），
        「没有由头」必须显式写出来，规则 13/14 的触发条件才机器可见。"""
        ctx = build_proactive_context(
            session_key="4:wx@im.wechat",
            hours_since_last_chat=3.0,
            local_time="2026-10-16 16:00 Friday",
            hooks_text="",
        )
        assert HOOKS_SECTION_TITLE in ctx  # 标题以「（空——…）」形态在场
        assert "本轮没有任何具体由头" in ctx

    def test_rule13_present(self):
        assert "必须有由头" in DECISION_SYSTEM
        assert "已经过去了一段时间" in DECISION_SYSTEM
        assert "candidates" in DECISION_SYSTEM

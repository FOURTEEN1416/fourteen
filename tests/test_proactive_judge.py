"""主动消息 post-draft judge（A-4 / Q3 裁决）回归。

覆盖：
- 开关关闭 → 放行（回滚零成本）；``llm=None``（未配置）→ 放行（部署态问题）；
- allow / drop 正常解析；**任何失败一律 fail-closed drop**（不可用/超时/坏 JSON）；
- 输入包含候选正文 + 最近对话摘要（不输入完整人设）；
- scheduler ``_deliver`` 接线：drop/异常 → 不投递、不记账；allow → 正常投递；
  决策线暂存的最近对话在投递时被弹出（不累积）。

配置与 LLM 全部替身：不触真实 ``data/scheduler_config.json``、不发真实请求。
"""

from __future__ import annotations

import json

import pytest

import proactive.post_draft_judge as jm
from proactive.post_draft_judge import (
    JudgeVerdict,
    build_judge_prompt,
    judge_proactive_message,
    parse_judge_verdict,
    read_judge_config,
)
from proactive.scheduler import ProactiveScheduler


@pytest.fixture
def judge_cfg(monkeypatch, tmp_path):
    path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(jm, "_CONFIG_PATH", path)
    return path


def _write_cfg(path, **block):
    path.write_text(json.dumps({"llm_proactive": block}), encoding="utf-8")


# ══════════════════════════════════════════════════════════
#  配置与解析
# ══════════════════════════════════════════════════════════


def test_config_defaults_enabled(judge_cfg):
    enabled, timeout = read_judge_config()
    assert enabled is True
    assert timeout == 8.0


def test_config_bad_values_fall_back(judge_cfg):
    _write_cfg(judge_cfg, judge_enabled="yes", judge_timeout_seconds="oops")
    enabled, timeout = read_judge_config()
    assert enabled is True
    assert timeout == 8.0


def test_config_timeout_clamped(judge_cfg):
    _write_cfg(judge_cfg, judge_timeout_seconds=999)
    assert read_judge_config()[1] == 30.0
    _write_cfg(judge_cfg, judge_timeout_seconds=0.1)
    assert read_judge_config()[1] == 2.0


def test_parse_accepts_valid_json():
    assert parse_judge_verdict('{"allow": true, "reason": "ok"}') == JudgeVerdict(True, "ok")
    assert parse_judge_verdict('前缀 {"allow": false, "reason": "空泛"} 后缀') == JudgeVerdict(False, "空泛")


def test_parse_rejects_bad_shapes():
    for raw in ("", "not json", '{"allow": "yes"}', '{"reason": "x"}', "[]"):
        verdict = parse_judge_verdict(raw)
        assert verdict.allow is False
        assert verdict.reason == "judge_unparsable"


def test_prompt_includes_recent_and_message():
    prompt = build_judge_prompt("候选正文ABC", recent=["上一句1", "上一句2"])
    assert "候选正文ABC" in prompt
    assert "上一句1" in prompt
    assert "最近对话" in prompt


# ══════════════════════════════════════════════════════════
#  judge 行为（fail-closed）
# ══════════════════════════════════════════════════════════


def test_disabled_allows_without_calling_llm(judge_cfg, monkeypatch):
    _write_cfg(judge_cfg, judge_enabled=False)

    def _boom(*a, **k):
        raise AssertionError("judge_disabled 时不得调用 LLM")

    monkeypatch.setattr("proactive.llm_proactive._llm_chat_sync", _boom)
    assert judge_proactive_message("hi", llm=object()).allow is True


def test_llm_none_allows(judge_cfg):
    verdict = judge_proactive_message("hi", llm=None)
    assert verdict.allow is True
    assert verdict.reason == "judge_not_configured"


def test_allow_verdict(judge_cfg, monkeypatch):
    monkeypatch.setattr(
        "proactive.llm_proactive._llm_chat_sync",
        lambda *a, **k: '{"allow": true, "reason": "具体"}',
    )
    assert judge_proactive_message("hi", llm=object()).allow is True


def test_drop_verdict(judge_cfg, monkeypatch):
    monkeypatch.setattr(
        "proactive.llm_proactive._llm_chat_sync",
        lambda *a, **k: '{"allow": false, "reason": "空泛问候"}',
    )
    verdict = judge_proactive_message("hi", llm=object())
    assert verdict.allow is False
    assert "空泛" in verdict.reason


def test_llm_failure_drops(judge_cfg, monkeypatch):
    def _boom(*a, **k):
        raise TimeoutError("judge timeout")

    monkeypatch.setattr("proactive.llm_proactive._llm_chat_sync", _boom)
    verdict = judge_proactive_message("hi", llm=object())
    assert verdict.allow is False
    assert verdict.reason == "judge_unavailable"


# ══════════════════════════════════════════════════════════
#  scheduler 投递接线
# ══════════════════════════════════════════════════════════


def _mk_sched():
    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._llm_provider = None
    sent: list[str] = []
    sched._send_targeted = lambda message, session_key=None: sent.append(message) or True
    sched._record_outbound = lambda *a, **k: None
    sched._resolve_character_id = lambda sk: ""
    sched._run_blocking = lambda fn: fn()
    sched._judge_recent = {}
    return sched, sent


def test_deliver_blocks_on_judge_drop(monkeypatch):
    sched, sent = _mk_sched()
    monkeypatch.setattr(
        "proactive.post_draft_judge.judge_proactive_message",
        lambda *a, **k: JudgeVerdict(False, "test_drop"),
    )
    assert sched._deliver("你好呀，今天风有点大", session_key="") is False
    assert sent == []


def test_deliver_allows_on_judge_allow(monkeypatch):
    sched, sent = _mk_sched()
    monkeypatch.setattr(
        "proactive.post_draft_judge.judge_proactive_message",
        lambda *a, **k: JudgeVerdict(True, "ok"),
    )
    assert sched._deliver("你好呀，今天风有点大", session_key="") is True
    assert len(sent) == 1


def test_deliver_fail_closed_on_judge_exception(monkeypatch):
    sched, sent = _mk_sched()

    def _boom(*a, **k):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr("proactive.post_draft_judge.judge_proactive_message", _boom)
    assert sched._deliver("你好呀，今天风有点大", session_key="") is False
    assert sent == []


def test_deliver_pops_stashed_recent(monkeypatch):
    sched, _sent = _mk_sched()
    sched._judge_recent = {"": ["上一句A", "上一句B"]}
    seen = {}

    def _capture(message, **kwargs):
        seen.update(kwargs)
        return JudgeVerdict(True, "ok")

    monkeypatch.setattr("proactive.post_draft_judge.judge_proactive_message", _capture)
    assert sched._deliver("你好呀，今天风有点大", session_key="") is True
    assert seen["recent"] == ["上一句A", "上一句B"]
    assert sched._judge_recent.get("") in (None, [])

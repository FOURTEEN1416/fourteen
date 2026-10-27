"""主动等待窗抖动（T-4，2026-10-27「仿真人机制」）回归。

目标：主动消息不再精确卡在「模型说的第 N 分钟」——等待窗后追加
``uniform(0, jitter_seconds)`` 的正向抖动。

- ``jitter_seconds <= 0`` → 与旧实现逐位一致（可回滚）；
- 配置真源 ``llm_proactive.jitter_seconds``：默认 120s、坏值回落、上限 900s；
- 随机源可注入，测试确定；调度器决策线确实调用了抖动入口。
"""

from __future__ import annotations

import inspect
import json

from proactive.scheduler import ProactiveScheduler, apply_wait_jitter


def test_zero_jitter_is_old_behavior_exactly():
    assert apply_wait_jitter(1000.0, 60, 0) == 1000.0 + 3600.0
    assert apply_wait_jitter(1000.0, 5, -1) == 1000.0 + 300.0


def test_jitter_stays_within_bounds():
    base = 1000.0 + 60 * 60.0
    assert apply_wait_jitter(1000.0, 60, 120, rng=lambda a, b: a) == base
    assert apply_wait_jitter(1000.0, 60, 120, rng=lambda a, b: b) == base + 120.0


def test_jitter_config_default(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ProactiveScheduler, "_CONFIG_PATH", tmp_path / "scheduler_config.json",
    )
    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    assert sched.get_proactive_jitter_seconds() == 120.0


def test_jitter_config_values_and_clamp(tmp_path, monkeypatch):
    path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", path)
    sched = ProactiveScheduler.__new__(ProactiveScheduler)

    path.write_text(json.dumps({"llm_proactive": {"jitter_seconds": 0}}), encoding="utf-8")
    assert sched.get_proactive_jitter_seconds() == 0.0

    path.write_text(json.dumps({"llm_proactive": {"jitter_seconds": 300}}), encoding="utf-8")
    assert sched.get_proactive_jitter_seconds() == 300.0

    path.write_text(json.dumps({"llm_proactive": {"jitter_seconds": "oops"}}), encoding="utf-8")
    assert sched.get_proactive_jitter_seconds() == 120.0

    path.write_text(json.dumps({"llm_proactive": {"jitter_seconds": 10**9}}), encoding="utf-8")
    assert sched.get_proactive_jitter_seconds() == 900.0


def test_decision_path_wires_jitter():
    src = inspect.getsource(ProactiveScheduler._llm_proactive_one_user)
    assert "apply_wait_jitter" in src
    assert "get_proactive_jitter_seconds" in src

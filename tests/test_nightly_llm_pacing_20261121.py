"""夜间批量 LLM 限流红测（2026-11-21 免费限流优化批）。

生产取证：02:17 的 ``memory_curator`` 连续 10 次 agnes 调用无间隔 → 供应商
429 + 120s 熔断，后续会话的 LLM 摘要全部降级为纯规则整理。本批契约：

- 批量整理（``run_curator_all_known``）在持有 LLM 的会话之间加固定间隔
  （默认 3 秒，env ``AI_GF_NIGHTLY_LLM_INTERVAL_SECONDS`` 可覆盖；纯规则
  整理会话不计间隔）；
- ``memory_curator`` 默认执行时刻从 02:17 挪到 03:40（流量更低的时段，
  env ``AI_GF_MEMORY_CURATOR_AT`` 可覆盖）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

# ═══════════════════════════════════════════════════════════
# 1. 执行时刻：默认 03:40，env 可覆盖
# ═══════════════════════════════════════════════════════════


def test_memory_curator_cron_default_is_0340(monkeypatch):
    from proactive.scheduler import _memory_curator_cron

    monkeypatch.delenv("AI_GF_MEMORY_CURATOR_AT", raising=False)
    assert _memory_curator_cron() == (3, 40)


@pytest.mark.parametrize(
    ("raw", "expect"),
    [
        ("05:30", (5, 30)),
        ("23:05", (23, 5)),
        ("4", (4, 0)),          # 只给小时 → 整点
        ("abc", (3, 40)),       # 非法 → 回落默认
        ("25:00", (3, 40)),     # 小时越界 → 回落默认
        ("12:99", (3, 40)),     # 分钟越界 → 回落默认
        ("", (3, 40)),          # 空串 → 回落默认
    ],
)
def test_memory_curator_cron_env_override(raw, expect, monkeypatch):
    from proactive.scheduler import _memory_curator_cron

    monkeypatch.setenv("AI_GF_MEMORY_CURATOR_AT", raw)
    assert _memory_curator_cron() == expect


# ═══════════════════════════════════════════════════════════
# 2. 批量 LLM 间隔：默认 3 秒，env 可覆盖
# ═══════════════════════════════════════════════════════════


def test_nightly_llm_interval_default_and_env(monkeypatch):
    from shisi.agent_plane import curator

    monkeypatch.delenv(curator.NIGHTLY_LLM_INTERVAL_ENV, raising=False)
    assert curator.nightly_llm_interval_seconds() == 3.0
    monkeypatch.setenv(curator.NIGHTLY_LLM_INTERVAL_ENV, "5.5")
    assert curator.nightly_llm_interval_seconds() == 5.5
    monkeypatch.setenv(curator.NIGHTLY_LLM_INTERVAL_ENV, "0")
    assert curator.nightly_llm_interval_seconds() == 0.0
    monkeypatch.setenv(curator.NIGHTLY_LLM_INTERVAL_ENV, "-1")
    assert curator.nightly_llm_interval_seconds() == 0.0, "负值钳到 0"
    monkeypatch.setenv(curator.NIGHTLY_LLM_INTERVAL_ENV, "abc")
    assert curator.nightly_llm_interval_seconds() == 3.0, "非法值回落默认"


def test_run_curator_all_known_paces_between_llm_sessions(monkeypatch):
    """持有 LLM 的会话之间必须 sleep 间隔；调用顺序保持不变。"""
    from shisi.agent_plane import curator

    monkeypatch.setenv(curator.NIGHTLY_LLM_INTERVAL_ENV, "0.25")
    sleeps: list[float] = []
    monkeypatch.setattr(curator.time, "sleep", lambda s: sleeps.append(s))
    calls: list[str] = []
    monkeypatch.setattr(
        curator,
        "run_curator_for_session",
        lambda uk, sm=None, llm=None, apply=True: (
            calls.append(uk) or {"ok": True, "user_key": uk}
        ),
    )
    resolver = lambda uk: SimpleNamespace(name=uk)  # noqa: E731 —— 每会话都有 LLM

    result = curator.run_curator_all_known(
        sm=SimpleNamespace(), llm=None,
        session_keys=["u1", "u2", "u3"], llm_resolver=resolver,
    )
    assert calls == ["u1", "u2", "u3"]
    assert sleeps == [0.25, 0.25], "3 个 LLM 会话 → 两次间隔（首会话前不睡）"
    assert result["sessions"] == 3


def test_run_curator_all_known_skips_pace_for_rule_only_sessions(monkeypatch):
    """解析不到 LLM 的会话走纯规则整理，不计间隔（不拖垮批处理）。"""
    from shisi.agent_plane import curator

    monkeypatch.setenv(curator.NIGHTLY_LLM_INTERVAL_ENV, "0.25")
    sleeps: list[float] = []
    monkeypatch.setattr(curator.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        curator,
        "run_curator_for_session",
        lambda uk, sm=None, llm=None, apply=True: {"ok": True, "user_key": uk},
    )
    # 全部会话都解析不到 LLM → 纯规则批处理，全程不 sleep
    resolver_none = lambda uk: None  # noqa: E731
    curator.run_curator_all_known(
        sm=SimpleNamespace(), llm=None,
        session_keys=["u1", "u2", "u3"], llm_resolver=resolver_none,
    )
    assert sleeps == []

    # u1=LLM、u2=纯规则、u3=LLM → 只在 u3（持有 LLM 且非首个）前 sleep 一次
    resolver_mixed = lambda uk: None if uk == "u2" else SimpleNamespace()  # noqa: E731
    curator.run_curator_all_known(
        sm=SimpleNamespace(), llm=None,
        session_keys=["u1", "u2", "u3"], llm_resolver=resolver_mixed,
    )
    assert sleeps == [0.25]

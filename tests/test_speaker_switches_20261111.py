"""B-2 主动线：开关一致性校验（红测）。

契约（2026-11-11，B-2 主动线）冻结口径：

- ``AGENT_CORE_EVENTS`` 与 ``SPEAKER_PROACTIVE`` **缺省都视为 '1'**；
- off 判定 = ``strip().lower()`` 属于 ``('0', 'false', 'off', 'no')``；
- **speaker on 且 ledger 显式 off** → 返回双 ``'0'`` 并记
  ``ERROR: speaker_auto_disabled:ledger_off``（自动降级）；
- ledger on 且 speaker off 合法，原样返回；
- 纯读 ``os.environ``、幂等、无 IO、无副作用（不修改 ``os.environ``）。

另加两条本域接线钉子：

- ``_check_ase_per_user`` 的过期维护**每 tick 一次**（不得退化成 per-user 循环内）；
- 账本关闭时读/写/过期三个入口**零 IO**（连库文件都不创建）。
"""

from __future__ import annotations

import builtins
import logging
import sqlite3

import pytest

LEDGER_ENV = "AGENT_CORE_EVENTS"
SPEAKER_ENV = "SPEAKER_PROACTIVE"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_switch_env(monkeypatch):
    """两条开关都删掉（缺省 = on），逐用例隔离。"""
    for key in (LEDGER_ENV, SPEAKER_ENV):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def _validate() -> dict[str, str]:
    from utils.speaker_switches import validate_speaker_switches

    return validate_speaker_switches()


# ---------------------------------------------------------------------------
# 开关矩阵
# ---------------------------------------------------------------------------


def test_both_default_on(clean_switch_env):
    assert _validate() == {"agent_core_events": "1", "speaker_proactive": "1"}


@pytest.mark.parametrize("ledger", ["1", "true", "TRUE", " yes "])
@pytest.mark.parametrize("speaker", ["1", "true", "YES"])
def test_on_variants_are_both_on(clean_switch_env, ledger, speaker):
    clean_switch_env.setenv(LEDGER_ENV, ledger)
    clean_switch_env.setenv(SPEAKER_ENV, speaker)
    assert _validate() == {"agent_core_events": "1", "speaker_proactive": "1"}


@pytest.mark.parametrize("off_value", ["0", "false", "FALSE", "off", " no "])
def test_ledger_on_speaker_off_is_legal(clean_switch_env, off_value, caplog):
    """speaker off + ledger on 是合法组合：不降级、不记 ERROR。"""
    clean_switch_env.setenv(LEDGER_ENV, "1")
    clean_switch_env.setenv(SPEAKER_ENV, off_value)
    with caplog.at_level(logging.ERROR, logger="utils.speaker_switches"):
        result = _validate()
    assert result == {"agent_core_events": "1", "speaker_proactive": "0"}
    assert "speaker_auto_disabled" not in caplog.text


def test_both_off_stays_off_without_error(clean_switch_env, caplog):
    clean_switch_env.setenv(LEDGER_ENV, "0")
    clean_switch_env.setenv(SPEAKER_ENV, "off")
    with caplog.at_level(logging.ERROR, logger="utils.speaker_switches"):
        result = _validate()
    assert result == {"agent_core_events": "0", "speaker_proactive": "0"}
    assert "speaker_auto_disabled" not in caplog.text


@pytest.mark.parametrize("off_value", ["0", "false", "off", "no", " OFF "])
def test_speaker_on_ledger_off_degrades_and_logs_error(clean_switch_env, off_value, caplog):
    """speaker on 且 ledger 显式 off → 自动降级双 '0' + ERROR。"""
    clean_switch_env.setenv(SPEAKER_ENV, "1")
    clean_switch_env.setenv(LEDGER_ENV, off_value)
    with caplog.at_level(logging.ERROR, logger="utils.speaker_switches"):
        result = _validate()
    assert result == {"agent_core_events": "0", "speaker_proactive": "0"}
    assert "speaker_auto_disabled:ledger_off" in caplog.text


def test_empty_value_falls_back_to_on(clean_switch_env):
    """空串/纯空白 = 未设置 = 缺省 on（不是 off）。"""
    clean_switch_env.setenv(LEDGER_ENV, "")
    clean_switch_env.setenv(SPEAKER_ENV, "   ")
    assert _validate() == {"agent_core_events": "1", "speaker_proactive": "1"}


# ---------------------------------------------------------------------------
# 幂等 / 无副作用 / 无 IO
# ---------------------------------------------------------------------------


def test_idempotent_and_env_untouched(clean_switch_env):
    import os

    clean_switch_env.setenv(LEDGER_ENV, "1")
    clean_switch_env.setenv(SPEAKER_ENV, "0")
    before = dict(os.environ)
    first = _validate()
    second = _validate()
    assert first == second == {"agent_core_events": "1", "speaker_proactive": "0"}
    assert dict(os.environ) == before


def test_validate_does_no_file_or_db_io(clean_switch_env, monkeypatch):
    """纯读环境变量：任何文件/数据库访问都算违约（这里让它们直接炸）。"""

    def _boom(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("validate_speaker_switches 不得触达文件/数据库")

    monkeypatch.setattr(builtins, "open", _boom)
    monkeypatch.setattr(sqlite3, "connect", _boom)
    clean_switch_env.setenv(LEDGER_ENV, "0")
    clean_switch_env.setenv(SPEAKER_ENV, "1")
    assert _validate() == {"agent_core_events": "0", "speaker_proactive": "0"}


# ---------------------------------------------------------------------------
# 消费侧入口（B-2 复核修复：此前返回值无人消费 ⇒ 开关是死的）
# ---------------------------------------------------------------------------


def test_speaker_proactive_enabled_defaults_on(clean_switch_env):
    from utils.speaker_switches import speaker_proactive_enabled

    assert speaker_proactive_enabled() is True


@pytest.mark.parametrize("off_value", ["0", "false", "OFF", " no "])
def test_speaker_proactive_enabled_honours_off(clean_switch_env, off_value):
    """显式置 0 必须返回 False —— 这是「按文档回滚」唯一生效路径。"""
    from utils.speaker_switches import speaker_proactive_enabled

    clean_switch_env.setenv(LEDGER_ENV, "1")
    clean_switch_env.setenv(SPEAKER_ENV, off_value)
    assert speaker_proactive_enabled() is False


def test_speaker_proactive_enabled_follows_auto_downgrade(clean_switch_env, caplog):
    """ledger 显式 off 时 speaker 一并不生效（与启动期校验同一口径）。"""
    from utils.speaker_switches import speaker_proactive_enabled

    clean_switch_env.setenv(LEDGER_ENV, "0")
    clean_switch_env.setenv(SPEAKER_ENV, "1")
    with caplog.at_level(logging.ERROR, logger="utils.speaker_switches"):
        assert speaker_proactive_enabled() is False
    assert "speaker_auto_disabled:ledger_off" in caplog.text


# ---------------------------------------------------------------------------
# 主动线接线钉子（本域实现）
# ---------------------------------------------------------------------------


def test_ledger_off_is_zero_io_across_read_write_expire(monkeypatch, tmp_path):
    """账本关闭：读 / 写 / 过期三个入口都不建库、不执行 SQL。"""
    from agent_core import commitments as ac_commitments
    from agent_core import events as ac_events

    db_path = tmp_path / "sqlite.db"
    monkeypatch.setattr(ac_events, "_DB_PATH", str(db_path))
    monkeypatch.delenv(LEDGER_ENV, raising=False)

    assert ac_commitments.list_open_commitments("2:o9cq80@im.wechat") == []
    assert ac_commitments.add_commitment("我帮你整理笔记", session_key="2:o9cq80@im.wechat") == ""
    assert ac_commitments.expire_commitments(limit=500) == 0
    assert not db_path.exists()


def test_expire_commitments_runs_once_per_tick_not_per_user(monkeypatch):
    """``_check_ase_per_user`` 里全库维护必须每 tick 恰好一次（N 用户不放大）。"""
    from agent_core import commitments as ac_commitments
    from proactive import scheduler as scheduler_mod

    expire_calls: list[int] = []

    def _fake_expire(*args, **kwargs):  # noqa: ANN001
        expire_calls.append(1)
        return 0

    monkeypatch.setattr(ac_commitments, "expire_commitments", _fake_expire)

    ticked: list[str] = []
    sched = object.__new__(scheduler_mod.ProactiveScheduler)
    sched.ase = object()
    sched._collect_ase_user_keys = lambda: ["2:a", "4:b", "6:c"]
    sched._evict_stale_ledger_entries = lambda: None
    sched._global_engine_values = lambda hub: {}
    sched._tick_concurrency = lambda: 1
    sched._llm_proactive_one_user = (
        lambda hub, key, engine_globals=None: ticked.append(key)
    )

    sched._check_ase_per_user()

    assert ticked == ["2:a", "4:b", "6:c"]
    assert len(expire_calls) == 1

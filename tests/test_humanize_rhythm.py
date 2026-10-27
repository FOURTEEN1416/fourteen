"""阅读延迟 + supersede 编排接线（P0-B）回归。

覆盖设计《2026-10-27-仿真人化引入方案》§3.2 P0-B / G-3：

- ``_apply_humanize_rhythm`` 读用户级配置（全局/覆盖均可），``delay_ms`` 生效；
- **延迟期收到同一会话新消息 → superseded=True**（旧回合不生成、不发送）；
- 无新消息 → 正常放行；``read_delay_enabled=false`` → 恒零延迟；
- ``typing`` 标志随 ``typing_enabled``；``process_message`` 接受 ``typing_sender``；
- 历史无时间戳 / 时间戳异常一律 fail-open（不阻断主链）。

时间戳用真实 UTC ISO（与生产 ``chat_history.timestamp`` 同口径）；
配置/覆盖库经 fixture 重定向到临时路径，绝不写开发机真实 ``data/``。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone

import pytest

from orchestrator.optimized_orchestrator import (
    OptimizedOrchestrator,
    _bump_supersede_seq,
    _history_idle_ms,
    _supersede_seq,
)
from utils import humanize_config as hc


@pytest.fixture
def rhythm_env(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    monkeypatch.setattr(hc, "_OVERRIDE_DB_PATH", tmp_path / "humanize_override.db")
    return tmp_path


def _orch() -> OptimizedOrchestrator:
    """方法不触碰 self 状态，直接 __new__ 即可（不初始化组件）。"""
    return OptimizedOrchestrator.__new__(OptimizedOrchestrator)


def _run_small_delay(
    session_id: str, ctx: dict, *, bump_after: float | None = None,
    typing_capable: bool = False,
) -> dict:
    orch = _orch()

    async def _go() -> dict:
        task = asyncio.create_task(
            orch._apply_humanize_rhythm(
                session_id, "你好呀", ctx, typing_capable=typing_capable,
            )
        )
        if bump_after is not None:
            await asyncio.sleep(bump_after)
            _bump_supersede_seq(session_id)
        return await task

    return asyncio.run(_go())


# ══════════════════════════════════════════════════════════
#  supersede 序号 / idle 解析
# ══════════════════════════════════════════════════════════


def test_supersede_seq_increments():
    sid = "test-seq-basic"
    a = _bump_supersede_seq(sid)
    b = _bump_supersede_seq(sid)
    assert b == a + 1
    assert _supersede_seq(sid) == b


def test_history_idle_none_without_timestamp():
    assert _history_idle_ms([{"role": "user", "content": "hi"}]) is None
    assert _history_idle_ms([]) is None
    assert _history_idle_ms(None) is None


def test_history_idle_recent_is_small():
    ts = datetime.now(timezone.utc).isoformat()
    idle = _history_idle_ms([{"role": "user", "content": "hi", "timestamp": ts}])
    assert idle is not None
    assert 0 <= idle < 10_000


def test_history_idle_bad_timestamp_fails_open():
    assert _history_idle_ms([{"role": "user", "content": "hi", "timestamp": "not-a-date"}]) is None


# ══════════════════════════════════════════════════════════
#  节奏应用
# ══════════════════════════════════════════════════════════


def test_disabled_returns_zero_delay(rhythm_env):
    hc.write_humanize(read_delay_enabled=False)
    out = _run_small_delay("test-rhythm-disabled", {"chat_history": []})
    assert out["delay_ms"] == 0
    assert out["superseded"] is False


def test_delay_applied_without_supersede(rhythm_env):
    hc.write_humanize(
        read_delay_enabled=True,
        read_delay_min_ms=40,
        read_delay_max_ms=40,
        typing_enabled=False,
    )
    ts = datetime.now(timezone.utc).isoformat()
    out = _run_small_delay(
        "test-rhythm-normal", {"chat_history": [{"role": "user", "content": "hi", "timestamp": ts}]},
    )
    assert out["delay_ms"] == 40
    assert out["superseded"] is False
    assert out["typing"] is False


def test_new_message_during_delay_supersedes(rhythm_env):
    hc.write_humanize(
        read_delay_enabled=True,
        read_delay_min_ms=150,
        read_delay_max_ms=150,
    )
    out = _run_small_delay("test-rhythm-supersede", {"chat_history": []}, bump_after=0.02)
    assert out["superseded"] is True
    assert out["delay_ms"] == 150


def test_typing_flag_follows_config(rhythm_env):
    hc.write_humanize(
        read_delay_enabled=True,
        read_delay_min_ms=40,
        read_delay_max_ms=40,
        typing_enabled=True,
    )
    out = _run_small_delay("test-rhythm-typing", {"chat_history": []}, typing_capable=True)
    assert out["typing"] is True


def test_typing_requires_capable_channel(rhythm_env):
    """配置开了 typing 但通道没能力（Web/HTTP 未传 typing_sender）→ 不启用，
    且延迟按无 typing 渠道封顶（配置值 60ms < 5000ms 硬顶）。"""
    hc.write_humanize(
        read_delay_enabled=True,
        read_delay_min_ms=8000,
        read_delay_max_ms=8000,
        read_delay_channel_cap_ms=60,
        typing_enabled=True,
    )
    out = _run_small_delay("test-rhythm-no-typing-channel", {"chat_history": []})
    assert out["typing"] is False
    assert out["delay_ms"] == 60


def test_per_user_override_controls_rhythm(rhythm_env):
    """Q2 按用户覆盖：该用户可单独关闭阅读延迟。"""
    hc.write_humanize(read_delay_enabled=True, read_delay_min_ms=40, read_delay_max_ms=40)
    hc.write_humanize(user_key="2:alice@im.wechat", read_delay_enabled=False)
    out = _run_small_delay("2:alice@im.wechat", {"chat_history": []})
    assert out["delay_ms"] == 0


# ══════════════════════════════════════════════════════════
#  接线契约
# ══════════════════════════════════════════════════════════


def test_process_message_accepts_typing_sender():
    sig = inspect.signature(OptimizedOrchestrator.process_message)
    assert "typing_sender" in sig.parameters


def test_connector_send_typing_noop_without_token():
    from wechat_direct.wechat_connector import WeChatConnector

    c = WeChatConnector.__new__(WeChatConnector)
    c.owner_user_id = None
    c.token = ""
    c.base_url = "https://example.invalid"
    c._last_user_id = ""
    assert c.send_typing("u1", True) is False

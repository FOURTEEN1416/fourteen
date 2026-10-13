"""主动决策双闸基准字段纠偏 红测（2026-10-12，聚合根因：节奏闸基准字段错位）。

生产实证：min_interval 拦 87 条 vs cooldown_after_reply 仅 2 条（后两例均发生在
跨 worker 字段失真时）。根因：``account_pacing_state`` 与
``_proactive_sent_today`` 读的是 ``_last_proactive_time``，而该字段在
``on_chat``（用户每说一句话）就被拨到当前时刻 —— min_interval 闸实际退化为
「用户刚说过话就别主动」，与 15min cooldown_after_reply 几乎重合；真正的
**投递基准** ``_last_delivery_time``（仅 ``commit_sent`` 投递成功才推进）
从未被决策链读到。本文件钉死三处读侧全部改读投递基准。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

_UTC = timezone.utc


class _StubEng:
    """最小引擎桩：两个字段分离是本批要钉死的核心（on_chat ≠ 投递）。"""

    def __init__(
        self,
        *,
        last_proactive_time: datetime | None = None,
        last_delivery_time: datetime | None = None,
        hours: float = 5.0,
    ):
        self._last_proactive_time = last_proactive_time
        self._last_delivery_time = last_delivery_time
        self._hours = hours

    def _hours_since_last_chat(self) -> float:
        return self._hours


class _Hub:
    def __init__(self, engines: dict):
        self._engines = engines

    def get(self, key):
        return self._engines.get(key)

    def known_user_keys(self):
        return list(self._engines)


# ── 1. account_pacing_state 必须读投递基准 ────────────────


class TestAccountPacingReadsDeliveryBaseline:
    def test_single_engine_reads_delivery_not_chat_time(self):
        """① on_chat 刚拨过 _last_proactive_time、真正投递在 90min 前 →
        聚合返回投递基准且 60min 闸不拦（现状读 on_chat 基准 → 拦 → 红）。"""
        from proactive.llm_proactive import (
            account_pacing_state,
            should_skip_proactive_decision,
        )

        now = datetime.now(_UTC)
        eng = _StubEng(
            last_proactive_time=now - timedelta(minutes=5),   # 模拟 on_chat 刚发生
            last_delivery_time=now - timedelta(minutes=90),   # 真正上次投递
        )
        hub = _Hub({"7:wx_a@im.wechat": eng})
        hours, last = account_pacing_state(hub, "7:wx_a@im.wechat", eng)
        assert last == now - timedelta(minutes=90), (
            "聚合必须返回投递基准 _last_delivery_time，而非被 on_chat 污染的 "
            "_last_proactive_time"
        )
        skip, reason = should_skip_proactive_decision(
            hours_since_last_chat=hours, last_proactive_at=last,
        )
        assert not skip, "90min 前投递不该被 60min min_interval 拦截"

    def test_min_interval_hits_on_delivery_baseline(self):
        """② 投递基准在 30min 前 → min_interval 命中（闸按投递记账生效）。"""
        from proactive.llm_proactive import (
            account_pacing_state,
            should_skip_proactive_decision,
        )

        now = datetime.now(_UTC)
        eng = _StubEng(
            last_proactive_time=now - timedelta(hours=5),
            last_delivery_time=now - timedelta(minutes=30),
        )
        hub = _Hub({"7:wx_a@im.wechat": eng})
        hours, last = account_pacing_state(hub, "7:wx_a@im.wechat", eng)
        skip, reason = should_skip_proactive_decision(
            hours_since_last_chat=hours, last_proactive_at=last,
        )
        assert skip and reason == "min_interval"

    def test_same_owner_group_uses_max_delivery(self):
        """同 owner 组聚合取组内最大投递基准（兄弟通道的投递对本通道生效）。"""
        from proactive.llm_proactive import account_pacing_state

        now = datetime.now(_UTC)
        hub = _Hub({
            "7:wx_a@im.wechat": _StubEng(
                last_delivery_time=now - timedelta(hours=2),
            ),
            "7:wx_b@im.wechat": _StubEng(
                last_delivery_time=now - timedelta(minutes=20),
            ),
        })
        _, last = account_pacing_state(hub, "7:wx_a@im.wechat", hub.get("7:wx_a@im.wechat"))
        assert last == now - timedelta(minutes=20), "组内取最大（最近一次投递）"

    def test_missing_delivery_field_returns_none(self):
        """引擎无 _last_delivery_time 字段（异常桩）→ None，不误读另一字段。"""
        from proactive.llm_proactive import account_pacing_state

        now = datetime.now(_UTC)
        eng = _StubEng(last_proactive_time=now - timedelta(minutes=5))
        hub = _Hub({"7:wx_a@im.wechat": eng})
        _, last = account_pacing_state(hub, "7:wx_a@im.wechat", eng)
        assert last is None


# ── 2. _proactive_sent_today 必须读投递基准 ───────────────


class TestProactiveSentTodayReadsDelivery:
    def _scheduler(self):
        from proactive.scheduler import ProactiveScheduler

        return ProactiveScheduler.__new__(ProactiveScheduler)

    def _real_engine(self, tmp_path):
        from proactive.ase_engine import ASEEngine

        return ASEEngine(state_path=str(tmp_path / "ase_state.json"))

    def test_on_chat_alone_does_not_count_as_sent_today(self, tmp_path):
        """③ on_chat（用户刚说话、零投递）后 sent_today 必须为 False
        （现状读 on_chat 拨动的 _last_proactive_time → True → 红）。"""
        from utils.local_time import now_local

        eng = self._real_engine(tmp_path)
        eng.on_chat("早", "早呀")  # 用户刚说话；没有任何投递
        assert self._scheduler()._proactive_sent_today(eng, now_local()) is False

    def test_commit_sent_counts_as_sent_today(self, tmp_path):
        """投递成功（commit_sent）后 sent_today 才为 True（正面锚）。"""
        from utils.local_time import now_local

        eng = self._real_engine(tmp_path)
        eng.commit_sent({"message": "晚上降温了记得加衣服", "type": "llm_proactive"})
        assert self._scheduler()._proactive_sent_today(eng, now_local()) is True


# ── 3. 投递基准跨重启 round-trip（锚定既有回退行为）────────


class TestDeliveryTimeRoundTrip:
    def test_delivery_time_survives_save_load(self, tmp_path):
        """④ commit_sent → save_state → 新引擎 _load_state：
        _last_delivery_time 保留且与记账时刻一致。"""
        from proactive.ase_engine import ASEEngine

        p = str(tmp_path / "state.json")
        eng1 = ASEEngine(state_path=p)
        eng1.commit_sent({"message": "在忙什么", "type": "llm_proactive"})
        assert eng1._last_delivery_time is not None
        eng1.save_state()
        eng2 = ASEEngine(state_path=p)
        assert eng2._last_delivery_time == eng1._last_delivery_time

    def test_legacy_state_without_delivery_falls_back(self, tmp_path):
        """旧状态文件无 last_delivery_time → 回退 last_sent_time（冷却不失真）。"""
        import json

        from proactive.ase_engine import ASEEngine

        p = tmp_path / "state.json"
        now = datetime.now(_UTC)
        p.write_text(
            json.dumps({"last_sent_time": now.isoformat()}), encoding="utf-8",
        )
        eng = ASEEngine(state_path=str(p))
        assert eng._last_delivery_time == now

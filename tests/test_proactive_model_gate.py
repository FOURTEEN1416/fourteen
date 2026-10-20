"""主动消息模型闸 红测（2026-10-15 质量回调批·包A剩余）。

生产实证：agnes 超时误杀 → 1 次失败熔断 300s → 期间全量流量降级免费档
GLM-4.5-Flash，主动消息出客服腔（生产 82% 流量走智谱实证）。用户裁决：
熔断期主动消息宁可不发也不让降级模型代写。

本批口径：
1. ``MultiProviderGateway.primary_provider_status`` 三态（正常 / 开闸 / 到期半开）；
2. ``ProactiveScheduler._primary_model_ok`` 对假 llm / 无方法对象 / None 三态；
3. 决策链闸：首选供应商熔断中 → 跳过决策（decide_proactive 零调用）+
   账本 reason=model_degraded 每用户每日至多一条 + 落盘触发；
4. 用户隔离：两个 user_key 的每日去重键互不覆盖；
5. 配置锚点：agnes request_timeout == 15（本批前置改动）。

配套纪律：新账本 ``_model_degraded_event_day`` 四处同步（init/load/persist/
purge），断言口径沿用 test_proactive_skip_ledger_hygiene.py 的 __new__ stub
模式。
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from types import SimpleNamespace

from utils.local_time import now_local
from utils.project_paths import project_path

# ── 1. primary_provider_status 三态（真网关，hermetic 构造）──────


class TestPrimaryProviderStatusThreeStates:
    def _make_gateway(self):
        from llm_provider.multi_provider_gateway import MultiProviderGateway

        return MultiProviderGateway(
            fallback_chain=["agnes", "zhipu"],
            providers_config={
                "agnes": {"api_key": "test-key"},
                "zhipu": {"api_key": "test-key"},
            },
            inherit_platform=False,
        )

    def test_normal_first_available(self):
        """① 首位正常 → (链首 key, True)。"""
        gw = self._make_gateway()
        key, ok = gw.primary_provider_status()
        assert key == "agnes"
        assert ok is True

    def test_breaker_open_reports_unavailable(self):
        """② 手动开闸（生产事故形态：1 次失败熔断 300s）→ False。"""
        gw = self._make_gateway()
        gw._breaker_open_until["agnes"] = time.time() + 300.0
        key, ok = gw.primary_provider_status()
        assert key == "agnes"
        assert ok is False

    def test_breaker_expired_recovers(self):
        """③ 开闸到期（半开重探）→ 恢复 True 且熔断态被清理。"""
        gw = self._make_gateway()
        gw._breaker_open_until["agnes"] = time.time() - 0.1  # 已过期
        key, ok = gw.primary_provider_status()
        assert key == "agnes"
        assert ok is True
        # _breaker_open 到期分支应顺带清掉熔断态（半开）
        assert "agnes" not in gw._breaker_open_until


# ── 2. _primary_model_ok 三态 ────────────────────────────────


class TestPrimaryModelOk:
    def _make_stub(self):
        from proactive.scheduler import ProactiveScheduler

        sched = ProactiveScheduler.__new__(ProactiveScheduler)
        sched._llm_provider = None
        return sched

    def test_degraded_llm_reports_false(self):
        """① 注入 primary_provider_status=(key, False) 的假 llm → False。"""
        sched = self._make_stub()
        sched._llm_provider = SimpleNamespace(
            primary_provider_status=lambda: ("agnes", False),
        )
        assert sched._primary_model_ok() is False

    def test_object_without_method_reports_true(self):
        """② 网关无该方法（旧对象/stub）→ True（不拦，向后兼容）。"""
        sched = self._make_stub()
        sched._llm_provider = object()  # 无 primary_provider_status
        assert sched._primary_model_ok() is True

    def test_no_llm_reports_true(self):
        """③ llm=None（未装配）→ True（不拦）。"""
        from api.deps import deps

        sched = self._make_stub()
        # deps 兜底路径也别捞到 llm（测试环境 orch 未装配，显式钉死防串）
        monkey_orch = deps.orch
        deps.orch = None
        try:
            assert sched._primary_model_ok() is True
        finally:
            deps.orch = monkey_orch

    def test_real_gateway_breaker_wired_through(self):
        """④ 真网关接线：熔断开闸 → _primary_model_ok() 随之 False。"""
        from llm_provider.multi_provider_gateway import MultiProviderGateway

        sched = self._make_stub()
        sched._llm_provider = MultiProviderGateway(
            fallback_chain=["agnes", "zhipu"],
            providers_config={
                "agnes": {"api_key": "test-key"},
                "zhipu": {"api_key": "test-key"},
            },
            inherit_platform=False,
        )
        assert sched._primary_model_ok() is True
        sched._llm_provider._breaker_open_until["agnes"] = time.time() + 300.0
        assert sched._primary_model_ok() is False


# ── 3. 决策链闸（跳过决策 + 账本每日一条 + 落盘）─────────────


def _make_scheduler(monkeypatch, *, model_ok: bool = False):
    """__new__ 手工构造（沿用 skip 账本治理批模式），补模型闸所需账本。"""
    import proactive.ase_engine as ase_mod
    import shisi.agent_plane.runtime as runtime_mod
    from proactive.llm_proactive import DEFAULT_WEB_CONFIG
    from proactive.scheduler import ProactiveScheduler

    sched = ProactiveScheduler.__new__(ProactiveScheduler)
    sched._paused = False
    sched._llm_proactive_next_ok = {}
    sched._disabled_event_day = {}
    sched._deliver_fail_counts = {}
    sched._wait_window_event_day = {}
    sched._quiet_event_day = {}
    sched._model_degraded_event_day = {}
    sched._important_dates_sent = set()
    sched._is_quiet_hours = lambda: False
    sched.get_quiet_hours = lambda: None
    sched._vital_tick_for_user = lambda *a, **k: None
    sched._resolve_character_id = lambda k: "测试角色"
    sched._resolve_memory = lambda: None
    sched._session_llm_resolver = None
    sched._resolve_proactive_llm = lambda eng: None
    sched._deliver = lambda message, session_key, character_id="": True
    sched._llm_provider = None
    sched._primary_model_ok = lambda: model_ok
    persists: list[bool] = []
    sched._persist_throttle_ledger = lambda: persists.append(True)

    ledger: list[dict] = []
    monkeypatch.setattr(
        runtime_mod, "append_proactive_event", lambda **kw: ledger.append(kw),
    )
    monkeypatch.setattr(runtime_mod, "project_profile_for", lambda user_key: {})
    monkeypatch.setattr(runtime_mod, "get_profile_prompt_block", lambda user_key: "")
    monkeypatch.setattr(ase_mod, "_local_now", lambda: now_local())

    import proactive.llm_proactive as lp_mod

    cfg = dict(DEFAULT_WEB_CONFIG)
    cfg["enabled"] = True
    monkeypatch.setattr(lp_mod, "read_web_proactive_config", lambda: cfg)
    monkeypatch.setattr(lp_mod, "load_persona_hint", lambda character_id="": "")
    monkeypatch.setattr(lp_mod, "load_proactive_knowledge", lambda character_id, **kw: "")
    decides: list[bool] = []

    def _decide_recorder(*a, **k):  # pragma: no cover - 被调用即失败
        decides.append(True)
        raise AssertionError("模型闸命中期间 decide_proactive 不得被调用")

    monkeypatch.setattr(lp_mod, "decide_proactive", _decide_recorder)
    return sched, ledger, persists, decides


def _run(sched, key="7:wx_a@im.wechat"):
    hub = SimpleNamespace(get=lambda k: None, known_user_keys=lambda: [key])
    sched._llm_proactive_one_user(hub, key)


class TestModelGateDecisionChain:
    def test_degraded_skips_decision_and_logs_once(self, monkeypatch):
        """① 熔断中：decide_proactive 零调用 + 1 行 model_degraded + 落盘。"""
        sched, ledger, persists, decides = _make_scheduler(monkeypatch, model_ok=False)
        _run(sched)
        _run(sched)  # 同日第二次：不再产生第二条事件
        rows = [e for e in ledger if e.get("reason") == "model_degraded"]
        assert len(rows) == 1, f"模型闸账本应每日一条，实得 {len(rows)} 行"
        assert rows[0].get("sent") is False
        assert rows[0].get("session_key") == "7:wx_a@im.wechat"
        assert not decides, "模型闸命中期间 decide_proactive 必须零调用"
        assert persists, "账本变更必须触发 _persist_throttle_ledger"
        assert sched._model_degraded_event_day.get("7:wx_a@im.wechat") == (
            now_local().date().isoformat()
        )

    def test_next_day_new_event(self, monkeypatch):
        """② 次日再熔断 → 新 1 行（跨日去重键失效重置）。"""
        import proactive.ase_engine as ase_mod

        sched, ledger, _, _ = _make_scheduler(monkeypatch, model_ok=False)
        _run(sched)
        monkeypatch.setattr(
            ase_mod, "_local_now", lambda: now_local() + timedelta(days=1),
        )
        _run(sched)
        rows = [e for e in ledger if e.get("reason") == "model_degraded"]
        assert len(rows) == 2, "次日应重新落一行"

    def test_model_ok_passes_gate(self, monkeypatch):
        """③ 模型正常（闸放行）：不落 model_degraded 事件（后续链路各归其测）。"""
        sched, ledger, _, _ = _make_scheduler(monkeypatch, model_ok=True)
        _run(sched)
        rows = [e for e in ledger if e.get("reason") == "model_degraded"]
        assert not rows, "模型正常时不得落 model_degraded"
        assert not sched._model_degraded_event_day, "模型正常时每日去重键不得写入"

    def test_user_isolation_of_dedup_keys(self, monkeypatch):
        """④ 两个 user_key 的去重键互不覆盖（每用户每日一条，不是全局一条）。"""
        sched, ledger, _, _ = _make_scheduler(monkeypatch, model_ok=False)
        _run(sched, key="7:wx_a@im.wechat")
        _run(sched, key="9:wx_b@im.wechat")
        rows = [e for e in ledger if e.get("reason") == "model_degraded"]
        assert len(rows) == 2, "不同用户的模型闸事件各计各账"
        assert {r.get("session_key") for r in rows} == {
            "7:wx_a@im.wechat", "9:wx_b@im.wechat",
        }
        assert set(sched._model_degraded_event_day) == {
            "7:wx_a@im.wechat", "9:wx_b@im.wechat",
        }


# ── 4. 账本四处同步（load/persist round-trip + purge）────────


class TestModelDegradedLedgerSync:
    def test_roundtrip_persist_load(self, tmp_path, monkeypatch):
        """⑤ model_degraded_event_day 必须进 _persist/_load 的 throttle 段。"""
        from proactive.scheduler import ProactiveScheduler

        cfg = tmp_path / "scheduler_config.json"
        monkeypatch.setattr(ProactiveScheduler, "_CONFIG_PATH", cfg)
        sched = ProactiveScheduler.__new__(ProactiveScheduler)
        sched._llm_proactive_next_ok = {}
        sched._deliver_fail_counts = {}
        sched._disabled_event_day = {}
        sched._important_dates_sent = set()
        sched._wait_window_event_day = {}
        sched._quiet_event_day = {}
        sched._model_degraded_event_day = {"7:wx_a@im.wechat": "2026-10-15"}
        sched._persist_throttle_ledger()

        data = json.loads(cfg.read_text(encoding="utf-8"))
        throttle = data.get("throttle") or {}
        assert throttle.get("model_degraded_event_day") == {
            "7:wx_a@im.wechat": "2026-10-15",
        }

        sched2 = ProactiveScheduler.__new__(ProactiveScheduler)
        sched2._llm_proactive_next_ok = {}
        sched2._deliver_fail_counts = {}
        sched2._disabled_event_day = {}
        sched2._important_dates_sent = set()
        sched2._wait_window_event_day = {}
        sched2._quiet_event_day = {}
        sched2._model_degraded_event_day = {}
        sched2._load_throttle_ledger()
        assert sched2._model_degraded_event_day == {"7:wx_a@im.wechat": "2026-10-15"}

    def test_purge_covers_model_degraded_ledger(self, monkeypatch):
        """⑥ purge_throttle_for 必须纳入新账本（漏 purge → 残留并复活）。"""
        from proactive.scheduler import ProactiveScheduler

        sched = ProactiveScheduler.__new__(ProactiveScheduler)
        sched._llm_proactive_next_ok = {}
        sched._deliver_fail_counts = {}
        sched._disabled_event_day = {}
        sched._important_dates_sent = set()
        sched._wait_window_event_day = {}
        sched._quiet_event_day = {}
        sched._model_degraded_event_day = {
            "7:wx_a@im.wechat": "2026-10-15",
            "9:wx_b@im.wechat": "2026-10-15",
        }
        persists: list[bool] = []
        sched._persist_throttle_ledger = lambda: persists.append(True)

        removed = sched.purge_throttle_for(7)
        assert sched._model_degraded_event_day.get("7:wx_a@im.wechat") is None, (
            "模型闸日期账本的该 owner 条目必须被清（漏 purge 元组即残留并复活）"
        )
        assert sched._model_degraded_event_day.get("9:wx_b@im.wechat") == "2026-10-15"
        assert removed == 1, f"removed 计数须包含新账本 1 条，实得 {removed}"
        assert persists, "有清理必须落盘（防 _persist 复活）"


# ── 5. 配置锚点（本批前置改动）────────────────────────────────


class TestAgnesTimeoutConfig:
    def test_agnes_request_timeout_covers_measured_p90(self):
        """⑦ agnes request_timeout 必须覆盖实测 P90（2026-10-20 质量悬崖批）。

        实测延迟分布 1.1~51.7s；15s/25s 都在误杀健康响应 → 熔断 → 全量降级
        弱模型出客服腔（用户报障的真实机制）。下界 40s。
        """
        cfg = json.loads(
            project_path("config", "llm_providers.json").read_text(encoding="utf-8"),
        )
        providers = cfg.get("providers") or {}
        assert providers.get("agnes", {}).get("request_timeout") >= 40, (
            "agnes request_timeout 必须覆盖实测 P90 40s（2026-10-20 质量悬崖批）"
        )

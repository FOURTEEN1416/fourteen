"""重要日期祝福多用户化 + 用户画像生日回归（2026-09-22 批次）。

背景：旧 `_check_important_dates` 是单用户时代遗存——只查角色级全局日期配置、
`_deliver(message)` 不带 session_key 广播给**所有** owner 的全部绑定 peer、
dedup 键无用户维度（A 的生日发过，同日生日的 B 被吞）。本文件锁定：
① 画像生日按用户命中并**定向**投递；② 全局日期逐用户定向且 dedup 带 user_key；
③ 农历表述宁缺毋错；④ 当日幂等。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from proactive.scheduler import ProactiveScheduler
from utils.important_dates import parse_birthday_hint
from utils.local_time import now_local


class TestParseBirthdayHint:
    @pytest.mark.parametrize("text,expected", [
        ("11月14", "11-14"),
        ("3月3日", "03-03"),
        ("2001-11-14", "11-14"),
        ("3-3", "03-03"),
        ("12.25", "12-25"),
        ("三月十四", "03-14"),
        ("十一月二日", "11-02"),
    ])
    def test_solar_forms(self, text, expected):
        assert parse_birthday_hint(text) == expected

    @pytest.mark.parametrize("text", [
        "腊月初一",     # 农历：宁缺毋错，不猜
        "正月十五",
        "不知道",
        "我生日那天",
        "",
        "13月40日",     # 越界
    ])
    def test_unparsable_returns_none(self, text):
        assert parse_birthday_hint(text) is None


# ── scheduler 定向投递契约 ─────────────────────────────────


class _DeliverRecorder:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, message: str, session_key: str | None = None) -> bool:
        self.calls.append((message, session_key))
        return True


@pytest.fixture()
def sched(monkeypatch):
    """静默关闸 + 双用户目标 + LLM 不参与的纯投递契约环境。"""
    s = ProactiveScheduler()
    monkeypatch.setattr(s, "_is_quiet_hours", lambda: False)
    monkeypatch.setattr(
        s, "_collect_ase_user_keys",
        lambda: ["2:peerA@im.wechat", "3:peerB@im.wechat"],
    )
    return s


def _today_m_d_text() -> str:
    """今天（本地日）的「M月D日」形态画像生日——保证用例与运行时刻无关。"""
    n = now_local()
    return f"{n.month}月{n.day}日"


def test_user_birthday_delivered_targeted(sched, monkeypatch):
    from shisi.agent_plane import runtime as runtime_mod
    from utils import important_dates as dates_mod

    monkeypatch.setattr(
        runtime_mod, "project_profile_for",
        lambda uk: {"birthday": _today_m_d_text()},
    )
    monkeypatch.setattr(dates_mod, "check_today", lambda cid, today: [])

    rec = _DeliverRecorder()
    monkeypatch.setattr(sched, "_deliver", rec)
    monkeypatch.setattr(sched, "_resolve_proactive_llm", lambda eng=None: None)

    sched._check_important_dates()

    targets = [sk for _msg, sk in rec.calls]
    # 每个用户各自收到自己的生日祝福（定向，非广播）
    assert set(targets) == {"2:peerA@im.wechat", "3:peerB@im.wechat"}
    assert all("生日" in msg for msg, _sk in rec.calls)


def test_global_date_delivered_per_user_not_broadcast(sched, monkeypatch):
    import api.deps as deps_mod
    from shisi.agent_plane import runtime as runtime_mod
    from utils import important_dates as dates_mod

    # active_id 需要 shisi_reg.character_manager（测试环境默认无 → 全局分支短路）
    monkeypatch.setattr(
        deps_mod.deps, "shisi_reg",
        SimpleNamespace(
            character_manager=SimpleNamespace(get_active_id=lambda: "char_test")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_mod, "project_profile_for", lambda uk: {"birthday": "腊月初一"}
    )
    monkeypatch.setattr(
        dates_mod, "check_today",
        lambda cid, today: [{"name": "相识纪念日", "date": "01-01", "kind": "anniversary"}],
    )

    rec = _DeliverRecorder()
    monkeypatch.setattr(sched, "_deliver", rec)
    monkeypatch.setattr(sched, "_resolve_proactive_llm", lambda eng=None: None)

    sched._check_important_dates()

    targets = sorted(sk for _msg, sk in rec.calls)
    assert targets == ["2:peerA@im.wechat", "3:peerB@im.wechat"]
    assert all("相识纪念日" in msg for msg, _sk in rec.calls)
    assert all("纪念日快乐" in msg for msg, _sk in rec.calls)


def test_dedup_is_per_user_and_same_day(sched, monkeypatch):
    """当日幂等键带 user_key：A 发过不再发 A，但不得吞掉 B 的（旧缺陷）。"""
    from shisi.agent_plane import runtime as runtime_mod
    from utils import important_dates as dates_mod

    monkeypatch.setattr(
        runtime_mod, "project_profile_for",
        lambda uk: {"birthday": _today_m_d_text()},
    )
    monkeypatch.setattr(dates_mod, "check_today", lambda cid, today: [])
    rec = _DeliverRecorder()
    monkeypatch.setattr(sched, "_deliver", rec)
    monkeypatch.setattr(sched, "_resolve_proactive_llm", lambda eng=None: None)

    sched._check_important_dates()
    sched._check_important_dates()  # 同日第二次（每小时任务重入）
    assert len(rec.calls) == 2  # 每用户一条，重入零新增


def test_quiet_hours_skips_entirely(sched, monkeypatch):
    monkeypatch.setattr(sched, "_is_quiet_hours", lambda: True)
    rec = _DeliverRecorder()
    monkeypatch.setattr(sched, "_deliver", rec)
    monkeypatch.setattr(sched, "_resolve_proactive_llm", lambda eng=None: None)
    sched._check_important_dates()
    assert rec.calls == []


def test_deliver_failure_not_marked_sent(sched, monkeypatch):
    """投递失败不得记幂等（下个非静默小时补发）。"""
    from shisi.agent_plane import runtime as runtime_mod
    from utils import important_dates as dates_mod

    monkeypatch.setattr(
        runtime_mod, "project_profile_for",
        lambda uk: {"birthday": _today_m_d_text()},
    )
    monkeypatch.setattr(dates_mod, "check_today", lambda cid, today: [])

    def _fail(message, session_key=None):
        return False

    monkeypatch.setattr(sched, "_deliver", _fail)
    monkeypatch.setattr(sched, "_resolve_proactive_llm", lambda eng=None: None)
    sched._check_important_dates()
    assert sched._important_dates_sent == set()

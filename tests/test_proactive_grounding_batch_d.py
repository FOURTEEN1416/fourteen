"""块D（P1 主动消息接地贯通）回归 —— 2026-09-22 六域二次根治。

## 三条缺陷与修法

**⑧ `dry_run` 死路径使 `generate_with_llm` 整条不可达。**
`tick(dry_run=True)` 在"频率检查之后、生成之前"就 `return None`，
于是场景触发与紧迫度生成（`_generate_and_return` → `generate_with_llm`）
**永远走不到**。唯一调用点 `scheduler._llm_proactive_one_user` 正是要借它
算 urgency 供 LLM 决策 ⇒ **决策层拿到的 urgency 恒 None**。
修法：dry_run 闸门下移到"仅屏蔽返回值"，生成链路照跑（commit=False 本就不记账）。

**⑨ 决策层提示词缺用户最后一句与注意力热度。**
生成层（`ase_engine.generate_with_llm`）早就有 `last_user_message` /
`user_profile` / `response_rate`，但 `build_proactive_context`（决策层）
只有时间信号 —— 决策层比生成层**更早**决定"是否开口"，缺了接地事实
只能按时间泛泛开口。修法：`build_proactive_context` 补两参 + 调用点传值。

**⑩ `note_user_interaction` 不落盘。**
持久化是每 10 分钟的调度任务 ⇒ 10 分钟窗口内重启，刚拉满的注意力与
新鲜时间戳全部丢失，退化成"很久没聊"。修法：该事件低频，就近落盘。

## 测试策略

- 钉调用来源 / 钉实参 / 断言行本身；**不用真实墙钟**；
- 每个契约有对应的**突变验红**失败路径。
"""

from __future__ import annotations

import json


def _engine(tmp_path, **kw):
    from proactive.ase_engine import ASEEngine

    return ASEEngine(
        llm_gateway=kw.pop("llm_gateway", None),
        generation_mode="llm",
        state_path=str(tmp_path / "ase.json"),
        **kw,
    )


# ══════════════════════════════════════════════════════════
#  ⑧ dry_run 不得截断生成链路
# ══════════════════════════════════════════════════════════

def test_dry_run_still_runs_generation_chain(tmp_path, monkeypatch):
    """🔴 核心：dry_run 必须**跑到生成**（否则 urgency/生成可达性全废）。

    旧实现在频率检查后立即 `return None` ⇒ `_generate_and_return` 的
    调用计数恒 0。这里钉调用计数，覆盖"只屏蔽返回值"的新语义。
    """
    eng = _engine(tmp_path)
    calls: list[str] = []

    def _spy_generate(msg_type, commit=True):
        calls.append(str(msg_type))
        return {"type": str(msg_type), "message": "候选", "urgency": 9.0}

    monkeypatch.setattr(eng, "_generate_and_return", _spy_generate)
    monkeypatch.setattr(eng, "_check_frequency", lambda: (True, "ok"))
    monkeypatch.setattr(eng, "_in_quiet_hours", lambda: False)
    monkeypatch.setattr(eng, "_is_duplicate", lambda _m: False)
    monkeypatch.setattr(
        eng, "_check_scene_triggers", lambda commit=True: None,
    )
    eng.urgency.base = 9.0  # 越过阈值，确保走生成分支（total 是只读属性）
    monkeypatch.setattr(eng, "_urgency_threshold", 0.1)

    out = eng.tick(hours_since_last_chat=5.0, dry_run=True)
    assert out is None, "dry_run 不得交出候选（调用方不投递）"
    assert calls, "dry_run 必须跑到生成（旧实现此处恒空 = 生成链路不可达）"


def test_dry_run_returns_none_but_not_before_generation(tmp_path, monkeypatch):
    """反向证明：非 dry_run 同样的状态**必须**交出候选（说明闸门只管返回值）。"""
    eng = _engine(tmp_path)
    monkeypatch.setattr(eng, "_check_frequency", lambda: (True, "ok"))
    monkeypatch.setattr(eng, "_in_quiet_hours", lambda: False)
    monkeypatch.setattr(eng, "_is_duplicate", lambda _m: False)
    monkeypatch.setattr(eng, "_check_scene_triggers", lambda commit=True: None)
    monkeypatch.setattr(
        eng, "_generate_and_return",
        lambda msg_type, commit=True: {"type": str(msg_type), "message": "候选", "urgency": 9.0},
    )
    eng.urgency.base = 9.0  # total 是只读 property，从 base 抬高
    monkeypatch.setattr(eng, "_urgency_threshold", 0.1)

    out = eng.tick(hours_since_last_chat=5.0, dry_run=False)
    assert out is not None and out["message"] == "候选"


def test_dry_run_never_commits(tmp_path, monkeypatch):
    """dry_run 仍**绝不计账**（配额/冷却/去重窗口不得被干跑污染）。"""
    eng = _engine(tmp_path)
    before = eng._daily_message_count
    monkeypatch.setattr(eng, "_check_frequency", lambda: (True, "ok"))
    monkeypatch.setattr(eng, "_in_quiet_hours", lambda: False)
    monkeypatch.setattr(eng, "_is_duplicate", lambda _m: False)
    monkeypatch.setattr(eng, "_check_scene_triggers", lambda commit=True: None)
    monkeypatch.setattr(
        eng, "_generate_and_return",
        lambda msg_type, commit=True: {"type": str(msg_type), "message": "候选", "urgency": 9.0},
    )
    eng.urgency.base = 9.0  # total 是只读 property，从 base 抬高
    monkeypatch.setattr(eng, "_urgency_threshold", 0.1)

    eng.tick(hours_since_last_chat=5.0, dry_run=True)
    assert eng._daily_message_count == before, "dry_run 不得扣配额"
    assert "候选" not in list(eng._recent_messages), "dry_run 不得进去重窗口"


def test_dry_run_skip_reason_is_recorded(tmp_path, monkeypatch):
    """dry_run 也要留下 skip_reason（否则排障看不出"这是干跑"）。"""
    eng = _engine(tmp_path)
    monkeypatch.setattr(eng, "_check_frequency", lambda: (True, "ok"))
    monkeypatch.setattr(eng, "_in_quiet_hours", lambda: False)
    monkeypatch.setattr(eng, "_check_scene_triggers", lambda commit=True: None)
    monkeypatch.setattr(eng, "_is_duplicate", lambda _m: False)
    monkeypatch.setattr(
        eng, "_generate_and_return",
        lambda msg_type, commit=True: {"type": str(msg_type), "message": "候选", "urgency": 9.0},
    )
    eng.urgency.base = 9.0  # total 是只读 property，从 base 抬高
    monkeypatch.setattr(eng, "_urgency_threshold", 0.1)
    eng.tick(hours_since_last_chat=5.0, dry_run=True)
    assert eng._last_skip_reason, "dry_run 必须记录跳过原因"


# ══════════════════════════════════════════════════════════
#  ⑨ 决策层提示词接地
# ══════════════════════════════════════════════════════════

def test_build_context_includes_last_user_message_and_rate():
    """`build_proactive_context` 必须能承接用户最后一句与注意力热度。"""
    from proactive.llm_proactive import build_proactive_context

    ctx = build_proactive_context(
        session_key="2:wx@im.wechat",
        hours_since_last_chat=5.0,
        local_time="2026-09-22 09:00 Tuesday",
        last_user_message="明天要军训",
        response_rate=0.12,
    )
    assert "明天要军训" in ctx, "必须含用户最后一句（决策层接地）"
    assert "0.12" in ctx, "必须含注意力热度"
    assert "更短更轻" in ctx, "低热度要给出行为指引"


def test_build_context_omits_grounding_sections_when_absent():
    """拿不到接地事实时**整段不出现**（宁缺毋串），不得注入占位假值。"""
    from proactive.llm_proactive import build_proactive_context

    ctx = build_proactive_context(
        session_key="k", hours_since_last_chat=1.0, local_time="t",
    )
    assert "用户最后一句" not in ctx
    assert "最近互动热度" not in ctx


def test_build_context_grounding_is_optional_signature():
    """新增两参必须有默认值（旧调用点不传也不炸）。"""
    import inspect

    from proactive.llm_proactive import build_proactive_context

    sig = inspect.signature(build_proactive_context)
    assert sig.parameters["last_user_message"].default == ""
    assert sig.parameters["response_rate"].default is None


def test_scheduler_passes_grounding_into_context(tmp_path, monkeypatch):
    """🔴 钉调用来源：`_llm_proactive_one_user` 必须把接地事实**传进** context。"""
    import inspect

    from proactive import scheduler as sched_mod

    src = inspect.getsource(sched_mod.ProactiveScheduler._llm_proactive_one_user)
    assert "last_user_message=last_user_message" in src, (
        "决策层调用必须传用户最后一句（旧实现整段缺失 = 接地缺口）"
    )
    assert "response_rate=response_rate" in src, "决策层调用必须传注意力热度"
    assert "_last_user_message" in src, "必须从引擎取该字段"
    assert "response_rate" in src


# ══════════════════════════════════════════════════════════
#  ⑩ 交互信号落盘
# ══════════════════════════════════════════════════════════

def test_note_user_interaction_persists_state(tmp_path):
    """🔴 交互即落盘：10 分钟窗口内重启不得丢注意力（旧实现只改内存）。"""
    from proactive.ase_engine import ASEEngine

    path = tmp_path / "ase.json"
    eng = ASEEngine(generation_mode="llm", state_path=str(path))
    assert eng.response_rate == 0.0
    eng.note_user_interaction()

    assert path.exists(), "交互后必须已落盘（不必等 10 分钟调度）"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert float(raw["response_rate"]) > 0.0, "response_rate 必须进状态文件"
    assert raw.get("last_user_interaction"), "交互时间戳必须进状态文件"

    # 同路径新引擎：注意力与新鲜度必须复现
    reborn = ASEEngine(generation_mode="llm", state_path=str(path))
    assert reborn.response_rate > 0.0, "重启后注意力必须复现（旧实现归零 → 退化成久未聊）"
    assert reborn._last_user_interaction is not None


def test_note_user_interaction_boost_survives_reload_via_hub(tmp_path, monkeypatch):
    """经 hub 的记录路径同样落盘（生产实际调用链）。"""
    from proactive import ase_hub as hub_mod

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")

    def _factory(user_key: str = "", state_path: str = ""):
        return _engine(tmp_path)

    hub = hub_mod.ASEHub(_factory)
    hub.note_user_interaction("2:a@im.wechat")
    eng = hub.get("2:a@im.wechat")
    assert (tmp_path / "ase.json").exists(), "hub 路径也必须落盘"
    assert eng.response_rate > 0.0


def test_note_user_interaction_save_failure_is_nonfatal(tmp_path, monkeypatch):
    """落盘失败只降级日志 —— 注意力是提示词依据而非硬闸门，不得让对话失败。"""
    eng = _engine(tmp_path)

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(eng, "save_state", _boom)
    eng.note_user_interaction()  # 不得抛
    assert eng.response_rate > 0.0, "内存值仍须生效"
    assert eng._last_user_interaction is not None


def test_note_user_interaction_does_not_touch_other_user_state(tmp_path, monkeypatch):
    """落盘不得跨用户写（每个 user_key 独立状态文件）。"""
    from proactive import ase_hub as hub_mod

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")

    paths: list[str] = []

    def _factory(user_key: str = "", state_path: str = ""):
        eng = _engine(tmp_path)
        p = tmp_path / f"ase-{user_key.replace(':', '_')}.json"
        eng._state_path = p
        paths.append(str(p))
        return eng

    hub = hub_mod.ASEHub(_factory)
    hub.note_user_interaction("2:a@im.wechat")
    files = sorted(p.name for p in tmp_path.glob("ase-*.json"))
    assert files == ["ase-2_a@im.wechat.json"], f"只应写被记用户的文件，实测 {files}"


def test_scheduler_module_keeps_no_local_contextlib_import():
    """🔴 防回归：函数内局部 `import contextlib` 会让该名在整个函数作用域
    变局部 —— 任何在它之前的用法都会 UnboundLocalError
    （2026-09-22 块D 引入上方 `contextlib.suppress` 时即刻踩中）。"""
    import inspect

    from proactive import scheduler as sched_mod

    src = inspect.getsource(sched_mod.ProactiveScheduler._llm_proactive_one_user)
    assert "import contextlib" not in src, (
        "本函数不得有局部 contextlib 导入（会遮蔽模块级名，造成 UnboundLocalError）"
    )

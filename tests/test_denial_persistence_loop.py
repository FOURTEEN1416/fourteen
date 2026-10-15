"""包D：称呼否认持久化闭环 + 出站守卫扩面 + 清洗脚本（2026-10-15 称呼根治批）。

**两个生产缺口**：
1. **否认持久化幽灵钩子**：`shisi/agent_plane/runtime.write_profile_from_tool`
   内联的落盘逻辑只在 profile agent 更新画像时被调用——用户 10-12 说「谁是
   夏夏」而 session4 最后一次画像写入在其之前 → 否认从未落盘 → 窗口滑出即
   失忆（10-15 复犯）。修法：落盘收敛进 `persist_detected_denials`（单一
   owner），并挂进 `append_chat_events`（orchestrator 每轮回调）。
2. **出站守卫不识称呼**：`terminal_reply_guard` 只拦哨兵/表情复读/退化回复
   ——扩面第三类：session_key/character_id 传入时经 `utils.address_terms`
   剥除非法称呼呼语（缺省不传 = 旧行为完全一致）。

覆盖：落盘 diff 幂等 / 每轮挂载 / 守卫剥称呼 / 缺省向后兼容 / 清洗脚本冒烟。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _patch_address_sources(monkeypatch, *, nickname: str = "", denied: set[str] | None = None,
                           char_name: str = "十四") -> None:
    """称呼真源三要素替身（与 test_address_terms 同款：只换数据源，不换逻辑）。"""
    import utils.address_terms as at
    import utils.character_resolver as cr

    monkeypatch.setattr(at, "_profile_nickname", lambda sk: nickname)
    monkeypatch.setattr(at, "_denied_names", lambda sk: set(denied or set()))
    monkeypatch.setattr(cr, "display_name", lambda cid: char_name)


# ═══════════════════════════════════════════════════════════
# 1. persist_detected_denials：检测→落盘 diff（单一 owner）
# ═══════════════════════════════════════════════════════════


def test_persist_writes_new_denial_as_permanent_event(tmp_path, monkeypatch):
    """新否认（检测源有、持久源无）→ 落 profile_correct 永久事件。

    payload.denied_name 是读侧源②的判据（`_persisted_denied_names`）；
    actor=denial_gate 让审计可区分自动闭环与人工写入。
    """
    from shisi.agent_plane import profile_projection as pp
    from shisi.agent_plane import runtime as apruntime

    monkeypatch.setattr(apruntime, "_detected_denied_names", lambda sk: ["夏夏"])
    monkeypatch.setattr(apruntime, "_persisted_denied_names", lambda sk: [])
    captured: list[dict] = []

    def _fake_write(ledger, *, session_key, payload, correct=False, turn_id="", actor="agent"):
        captured.append({
            "session_key": session_key, "payload": dict(payload),
            "correct": correct, "turn_id": turn_id, "actor": actor,
        })
        return {}

    monkeypatch.setattr(pp, "write_profile_event", _fake_write)

    newly = apruntime.persist_detected_denials("4:peer@im.wechat", turn_id="t-42")
    assert newly == ["夏夏"], "返回值必须是本次新落盘的词表"
    assert len(captured) == 1, "新否认必须落盘一次（且仅一次）"
    event = captured[0]
    assert event["payload"]["denied_name"] == "夏夏"
    assert event["correct"] is True, "否认是更正事件（profile_correct 永久保留）"
    assert event["actor"] == "denial_gate"
    assert event["turn_id"] == "t-42", "turn_id 必须透传（审计可挂轮）"
    assert event["session_key"] == "4:peer@im.wechat"
    assert "2026-10-15" in event["payload"]["reason"], "理由必须标注本批口径"


def test_persist_skips_already_persisted_denial(tmp_path, monkeypatch):
    """已在持久源（源②）里的否认不重复落盘（diff 幂等，防事件膨胀）。"""
    from shisi.agent_plane import profile_projection as pp
    from shisi.agent_plane import runtime as apruntime

    monkeypatch.setattr(apruntime, "_detected_denied_names", lambda sk: ["夏夏", "十七"])
    monkeypatch.setattr(apruntime, "_persisted_denied_names", lambda sk: ["夏夏"])
    captured: list[dict] = []

    def _fake_write(ledger, *, session_key, payload, correct=False, turn_id="", actor="agent"):
        captured.append(dict(payload))
        return {}

    monkeypatch.setattr(pp, "write_profile_event", _fake_write)

    newly = apruntime.persist_detected_denials("4:peer@im.wechat")
    assert newly == ["十七"], "只落新词（夏夏已持久）"
    assert len(captured) == 1
    assert captured[0]["denied_name"] == "十七"


def test_persist_is_silent_on_failure(monkeypatch):
    """任何异常静默（返回空表）——转写/检测失败不得阻塞对话。"""
    from shisi.agent_plane import runtime as apruntime

    def _boom(sk):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(apruntime, "_detected_denied_names", _boom)
    assert apruntime.persist_detected_denials("4:peer@im.wechat") == []


def test_persist_empty_session_key_noop(monkeypatch):
    """无会话键 → 直接空转（无身份可挂，不得落无归属事件）。"""
    from shisi.agent_plane import runtime as apruntime

    monkeypatch.setattr(apruntime, "_detected_denied_names", lambda sk: ["夏夏"])
    assert apruntime.persist_detected_denials("") == []


# ═══════════════════════════════════════════════════════════
# 2. append_chat_events 挂载：每轮对话即闭环（缺口1 根治点）
# ═══════════════════════════════════════════════════════════


class _RecordingLedger:
    def __init__(self) -> None:
        self.appended: list[dict] = []

    def append(self, **kwargs):
        self.appended.append(kwargs)
        return 1


def test_append_chat_events_triggers_denial_persistence(monkeypatch):
    """user_msg 非空 → persist_detected_denials 被调（turn_id 透传）。

    这是缺口1 的直接回归：旧实现落盘挂在 write_profile_from_tool 内联，
    用户否认后若从无画像写入则永不落盘；挂到每轮回调后，「用户说了否认」
    当轮即落账本。
    """
    from shisi.agent_plane import runtime as apruntime

    ledger = _RecordingLedger()
    monkeypatch.setattr(apruntime, "get_ledger", lambda: ledger)
    calls: list[tuple] = []
    monkeypatch.setattr(
        apruntime,
        "persist_detected_denials",
        lambda sk, *, turn_id="": calls.append((sk, turn_id)) or [],
    )

    apruntime.append_chat_events(
        session_key="4:peer@im.wechat",
        character_id="default",
        turn_id="t-7",
        user_msg="谁是夏夏",
        reply="……",
    )
    assert calls == [("4:peer@im.wechat", "t-7")], "每轮对话回调必须触发否认落盘检测"


def test_append_chat_events_without_user_msg_skips_persistence(monkeypatch):
    """无 user_msg（纯回复/系统轮）→ 不触发（省一次全量账本扫描）。"""
    from shisi.agent_plane import runtime as apruntime

    ledger = _RecordingLedger()
    monkeypatch.setattr(apruntime, "get_ledger", lambda: ledger)
    calls: list[tuple] = []
    monkeypatch.setattr(
        apruntime,
        "persist_detected_denials",
        lambda sk, *, turn_id="": calls.append((sk, turn_id)) or [],
    )

    apruntime.append_chat_events(
        session_key="4:peer@im.wechat", turn_id="t-8", user_msg="", reply="在的"
    )
    assert calls == []


def test_denial_survives_without_any_profile_write(tmp_path, monkeypatch):
    """🔴 缺口1 端到端回归（生产场景复刻）：否认经每轮回调落盘，**无任何画像写入**。

    生产实证：用户 10-12 说「谁是夏夏」而 session4 最后一次 profile 写入
    （10-12 08:42）早于否认 → 旧实现（钩子内联在 write_profile_from_tool）
    否认从未落盘，窗口滑出即失忆 → 10-15 复犯。本用例走真实 ledger + 真实
    每轮回调，**全程不调 write_profile_from_tool**，断言：
      ① 否认当轮即进永久事件；
      ② 用户消息被 prune（窗口滑出）后，持久源仍能读回该否认。
    """
    import sqlite3

    from shisi.agent_plane import runtime as apruntime
    from shisi.agent_plane.event_ledger import EventLedger

    led = EventLedger(tmp_path / "agent_plane.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    from shisi.memory.legacy.user_profile import UserProfileStore

    store = UserProfileStore(tmp_path / "profile_cache.db")
    monkeypatch.setattr(apruntime, "_store", lambda: store)

    sk = "4:peer@im.wechat"
    # 用户说「谁是夏夏」（否认句）——仅经每轮对话回调，无画像操作
    apruntime.append_chat_events(session_key=sk, turn_id="t-1", user_msg="谁是夏夏", reply="……")

    persisted = apruntime._persisted_denied_names(sk)
    assert "夏夏" in persisted, "否认必须在当轮落盘（不依赖 profile 写路径）"

    # 模拟 30 天 prune：删掉全部用户消息（检测源①失效）
    with sqlite3.connect(str(led.db_path)) as conn:
        conn.execute("DELETE FROM event_ledger WHERE event_type = 'user_message'")
        conn.commit()
    assert apruntime._detected_denied_names(sk) == [], "前提：窗口已滑出，检测源为空"
    assert "夏夏" in apruntime._persisted_denied_names(sk), (
        "窗口滑出后否认必须仍可读回（10-15 复犯的直接防线）"
    )
    merged = apruntime.denied_names_for(sk)
    assert "夏夏" in merged, "双源合并入口必须仍含该否认"


def test_write_profile_from_tool_uses_single_owner(tmp_path, monkeypatch):
    """写侧否决门不再内联落盘（单一 owner）：write_profile_from_tool 经
    persist_detected_denials 落盘，行为等价（检测到的新否认照样进账本）。"""
    from shisi.agent_plane import runtime as apruntime
    from shisi.agent_plane.event_ledger import EventLedger

    led = EventLedger(tmp_path / "agent_plane.db")
    monkeypatch.setattr(apruntime, "get_ledger", lambda: led)
    from shisi.memory.legacy.user_profile import UserProfileStore

    store = UserProfileStore(tmp_path / "profile_cache.db")
    monkeypatch.setattr(apruntime, "_store", lambda: store)

    sk = "2:a@im.wechat"
    led.append(session_key=sk, event_type="user_message", payload={"text": "我叫十七"})
    led.append(session_key=sk, event_type="user_message", payload={"text": "我不是十七"})
    # 任一画像写路径 → 新否认「十七」必须落进永久事件
    apruntime.write_profile_from_tool(sk, {"occupation": "上学"})
    persisted = apruntime._persisted_denied_names(sk)
    assert "十七" in persisted, "落盘经 persist_detected_denials 后行为等价（单一 owner）"


# ═══════════════════════════════════════════════════════════
# 3. terminal_reply_guard 扩面：非法称呼呼语剥除
# ═══════════════════════════════════════════════════════════


def test_guard_strips_forbidden_vocative_with_session(monkeypatch):
    """扩张面：session_key/character_id 传入 → 非法称呼被确定性剥除。"""
    from utils.prompt_sanitize import terminal_reply_guard

    _patch_address_sources(monkeypatch, nickname="", char_name="林挽夏")
    out = terminal_reply_guard(
        "夏夏，最近好吗",
        session_key="N:guard-1",
        character_id="62105bca",
    )
    assert "夏夏" not in out
    assert "最近好吗" in out, "正文必须保留，只剥称呼"


def test_guard_keeps_old_behavior_without_session_key(monkeypatch):
    """向后兼容：不传 session_key → 与旧版逐字节一致（不剥称呼）。"""
    from utils.prompt_sanitize import terminal_reply_guard

    # 即使真源里「夏夏」已被否认，缺省路径也不剥（无身份可挂）
    _patch_address_sources(monkeypatch, nickname="", denied={"夏夏"}, char_name="林挽夏")
    raw = "夏夏，最近好吗"
    assert terminal_reply_guard(raw) == raw
    assert terminal_reply_guard(raw, user_msg="", character_name="") == raw


def test_guard_returns_empty_when_stripped_to_nothing(monkeypatch):
    """剥空（整条只剩非法称呼）→ 返回空串（调用方走 empty_reply 兜底）。"""
    from utils.prompt_sanitize import terminal_reply_guard

    _patch_address_sources(monkeypatch, nickname="", denied={"夏夏"}, char_name="十四")
    assert terminal_reply_guard("夏夏", session_key="N:guard-2") == ""
    assert terminal_reply_guard("夏夏，", session_key="N:guard-2") == ""


def test_guard_keeps_legal_and_affectionate_vocatives(monkeypatch):
    """白名单段语义不透到出站：合法称呼与亲密呼语保留。"""
    from utils.prompt_sanitize import terminal_reply_guard

    _patch_address_sources(monkeypatch, nickname="阿树", denied={"十七"}, char_name="十四")
    assert terminal_reply_guard("阿树，早", session_key="N:guard-3") == "阿树，早"
    assert terminal_reply_guard("笨蛋，早", session_key="N:guard-3") == "笨蛋，早"


def test_guard_does_not_touch_word_internal_names(monkeypatch):
    """误伤保护：词内成分（「十七岁」「无名指」）不剥（词边界语义复用冻结 API）。"""
    from utils.prompt_sanitize import terminal_reply_guard

    _patch_address_sources(monkeypatch, nickname="", denied={"十七", "无名"}, char_name="十四")
    out = terminal_reply_guard(
        "十七岁的雨季要来了，无名指的戒指也得摘了。", session_key="N:guard-4"
    )
    assert out == "十七岁的雨季要来了，无名指的戒指也得摘了。"


def test_guard_sentinel_still_blocked_with_session(monkeypatch):
    """扩面不得削弱既有三类拦截：哨兵在传 session_key 时同样整条拦截。"""
    from utils.prompt_sanitize import terminal_reply_guard

    _patch_address_sources(monkeypatch, nickname="", char_name="十四")
    sentinel = "（所有 LLM 提供商均不可用，请检查配置。最后错误: x）"
    assert terminal_reply_guard(sentinel, session_key="N:guard-5") == ""
    assert terminal_reply_guard("十四", character_name="十四", session_key="N:guard-5") == ""


def test_guard_signature_keeps_defaults():
    """签名契约：四个形参全部可选（旧调用点零改动不破）。"""
    import inspect

    from utils.prompt_sanitize import terminal_reply_guard

    params = inspect.signature(terminal_reply_guard).parameters
    for name in ("reply", "user_msg", "character_name", "session_key", "character_id"):
        assert name in params
    for name in ("user_msg", "character_name", "session_key", "character_id"):
        assert params[name].default in ("", None) or params[name].default == "", name


def test_scheduler_deliver_passes_session_context():
    """🔴 钉调用来源：`_deliver` 的守卫调用必须传 session_key/character_id。

    不传则主动链（决策/手动/祝福三条线）的非法称呼呼语无剥除能力——
    正是本包要修的缺口2。AST 级断言（防同函数内其它含该字符串的调用假绿）。
    """
    import ast
    import inspect
    import textwrap

    from proactive import scheduler as sched_mod

    src = textwrap.dedent(inspect.getsource(sched_mod.ProactiveScheduler._deliver))
    calls = [
        node
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "terminal_reply_guard"
    ]
    assert calls, "未找到 terminal_reply_guard 调用（结构性变更需同步本用例）"
    kwargs = {kw.arg for kw in calls[0].keywords}
    assert "session_key" in kwargs, "主动链守卫必须传 session_key（否则称呼剥除不生效）"
    assert "character_id" in kwargs, "主动链守卫必须传 character_id（角色名派生禁用的输入）"


# ═══════════════════════════════════════════════════════════
# 4. 清洗脚本冒烟（只写不执行生产；本地空库 dry-run 不崩）
# ═══════════════════════════════════════════════════════════


def _run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "purge_address_contamination.py"), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )


def test_purge_script_help_exit_zero():
    result = _run_script("--help")
    assert result.returncode == 0
    assert "--apply" in result.stdout
    assert "dry-run" in result.stdout.lower()


def test_purge_script_dry_run_on_empty_dir(tmp_path):
    """空数据目录 dry-run：不崩、退出码 0、不产生任何写入。"""
    result = _run_script("--dry-run", "--data-dir", str(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY-RUN" in result.stdout
    assert list(tmp_path.iterdir()) == [], "dry-run 不得产生任何文件（含备份）"

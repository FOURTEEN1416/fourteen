"""块E（2026-09-22 六域二次根治）— P1/P2 收口批回归。

覆盖四组：

A. **节流账本落盘**：`_llm_proactive_next_ok` / `_deliver_fail_counts` /
   `_disabled_event_day` / `_important_dates_sent` 跨重启保持。
   旧实现纯内存 —— 重启后退避阶梯从 0 重来（5m 永远升不到 120m 封顶），
   LLM 自判等待窗作废，"每用户每日一条"去重失效，当日祝福重发。

B. **好感度双真源**：`mapper.sync` 收会话键 vs `user_scheduler` 收裸 user_id
   → 两套键空间零交集。修复后统一走 `user_key_from_session`，且 enhancer
   在审计无记录时用点存兜底回填。

C. **向量召回读侧协程缺陷**：`vector_memory._search` 是 `async def`，旧实现
   直接当同步函数调 → 拿到 coroutine → 迭代抛 TypeError 被吞 → 向量召回恒空。

D. **墙钟口径**：`time_awareness_tool` 默认日期必须是本地日而非 `date.today()`。
"""

from __future__ import annotations

import inspect
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────
# A. 节流账本落盘
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    """构造一个把 `_CONFIG_PATH` 指向临时文件的调度器。"""
    from proactive import scheduler as sched_mod

    cfg = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(sched_mod.ProactiveScheduler, "_CONFIG_PATH", cfg)
    inst = sched_mod.ProactiveScheduler()
    return inst, cfg


def test_throttle_ledger_roundtrip(scheduler):
    """写入四个账本 → 新实例读回同样的值。"""
    inst, cfg = scheduler
    inst._llm_proactive_next_ok["u1"] = 1_800_000_000.0
    inst._deliver_fail_counts["u1"] = 3
    inst._disabled_event_day["u1"] = "2026-09-22"
    inst._important_dates_sent.add("2026-09-22|u1|生日")
    inst._persist_throttle_ledger()

    from proactive.scheduler import ProactiveScheduler

    reborn = ProactiveScheduler()
    assert reborn._llm_proactive_next_ok == {"u1": 1_800_000_000.0}
    assert reborn._deliver_fail_counts == {"u1": 3}
    assert reborn._disabled_event_day == {"u1": "2026-09-22"}
    assert "2026-09-22|u1|生日" in reborn._important_dates_sent


def test_throttle_ledger_does_not_clobber_other_config(scheduler):
    """账本落盘不得破坏同文件里的其他配置段（静默时段 / vault / 衰减基准）。"""
    inst, cfg = scheduler
    inst._persist_last_emotion_decay(datetime(2026, 9, 22, tzinfo=timezone.utc))
    inst.write_config_file(quiet_hours=(22, 8), vault_enabled=True, vault_interval=45)
    inst._llm_proactive_next_ok["u9"] = 123.0
    inst._persist_throttle_ledger()

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["last_emotion_decay"].startswith("2026-09-22")
    assert data["quiet_hours"] == {"start": 22, "end": 8}
    assert data["vault"]["enabled"] is True
    assert data["vault"]["interval_minutes"] == 45
    assert data["throttle"]["llm_proactive_next_ok"] == {"u9": 123.0}


def test_throttle_ledger_tolerates_corrupt_fields(scheduler):
    """单字段损坏只丢该字段，不整段丢弃、更不抛异常（构造期必须能起来）。"""
    inst, cfg = scheduler
    cfg.write_text(
        json.dumps(
            {
                "throttle": {
                    "llm_proactive_next_ok": {"good": 5.0, "bad": "not-a-number"},
                    "deliver_fail_counts": {"neg": -3, "ok": 2},
                    "disabled_event_day": "这不是 dict",
                    "important_dates_sent": ["a|b|c", ""],
                }
            }
        ),
        encoding="utf-8",
    )
    from proactive.scheduler import ProactiveScheduler

    reborn = ProactiveScheduler()
    # 坏值被剔除，好值保留
    assert reborn._llm_proactive_next_ok == {"good": 5.0}
    # 负数失败计数无意义 → 剔除
    assert reborn._deliver_fail_counts == {"ok": 2}
    # 类型不符 → 整字段保持为空（不崩）
    assert reborn._disabled_event_day == {}
    assert reborn._important_dates_sent == {"a|b|c"}


def test_throttle_ledger_rejects_nan_and_negative_timestamps(scheduler):
    """NaN / inf / 负时间戳会污染退避比较 → 必须剔除。"""
    inst, cfg = scheduler
    cfg.write_text(
        json.dumps({"throttle": {"llm_proactive_next_ok": {
            "nan": float("nan"), "inf": float("inf"), "neg": -1.0, "ok": 7.0,
        }}}),
        encoding="utf-8",
    )
    from proactive.scheduler import ProactiveScheduler

    reborn = ProactiveScheduler()
    assert set(reborn._llm_proactive_next_ok) == {"ok"}


def test_throttle_ledger_missing_file_is_silent(tmp_path, monkeypatch):
    """配置文件不存在时静默启动（不能因缺账本而崩）。

    注意：`__init__` 会调 `_load_last_emotion_decay()`，它在文件缺失时
    **回填并创建**配置文件 —— 故此处断言的是"账本段为空"，而非"文件不存在"。
    """
    from proactive import scheduler as sched_mod

    cfg = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(sched_mod.ProactiveScheduler, "_CONFIG_PATH", cfg)

    reborn = sched_mod.ProactiveScheduler()
    assert reborn._llm_proactive_next_ok == {}
    assert reborn._deliver_fail_counts == {}
    assert reborn._disabled_event_day == set() or reborn._disabled_event_day == {}
    assert reborn._important_dates_sent == set()
    # 文件可能已被衰减基准回填创建，但其中不得有 throttle 段
    if cfg.exists():
        assert "throttle" not in (json.loads(cfg.read_text(encoding="utf-8")) or {})


def test_backoff_survives_restart(scheduler):
    """P1-22 退避阶梯必须跨重启连续 —— 否则永远停在第一档 5 分钟。

    这是本组的**行为**断言（非落盘细节）：模拟两次失败重启一次，第三次
    失败时的退避时长必须大于第一档。
    """
    inst, cfg = scheduler
    # 连续失败两次（模拟生产：每次失败写一次账本）
    for _ in range(2):
        fails = inst._deliver_fail_counts.get("u1", 0) + 1
        inst._deliver_fail_counts["u1"] = fails
        inst._llm_proactive_next_ok["u1"] = time.time() + min(5 * (3 ** (fails - 1)), 120) * 60
        inst._persist_throttle_ledger()

    from proactive.scheduler import ProactiveScheduler

    reborn = ProactiveScheduler()
    # 第三次失败
    fails = reborn._deliver_fail_counts.get("u1", 0) + 1
    backoff_min = min(5 * (3 ** (fails - 1)), 120)
    assert fails == 3, "退避阶梯必须从 2 继续，而不是从 0 重来"
    assert backoff_min == 45, f"第三档应为 45 分钟，实得 {backoff_min}"


# ─────────────────────────────────────────────────────────────
# B. 好感度双真源
# ─────────────────────────────────────────────────────────────


def test_affinity_user_key_is_canonical():
    """归属键唯一 owner：会话键原样返回（带 owner 的完整键）。"""
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sk = "4:o9cq805ifqDz9eaFN5YWuUFHF-10@im.wechat"
    assert StructuredMemory.user_key_from_session(sk) == sk
    bare = sk.split(":", 1)[1]
    assert bare != sk, "裸 peer 与规范键不等 —— 两套键空间不可混用"


def test_orchestrator_passes_canonical_key_to_affinity_sync():
    """防回归：`mapper.sync` 的 user_id 不得再直接吃原始 session 变量。"""
    import orchestrator.optimized_orchestrator as orch_mod

    src = inspect.getsource(orch_mod)
    assert "user_id=session_id or" not in src
    assert "user_key_from_session" in src


def test_enhancer_backfills_from_points_when_audit_empty(tmp_path, monkeypatch):
    """审计日志无该键、点存有值 → 必须兜底回填（否则"回放成功但值仍是 0"）。"""
    from shisi.affinity import enhancer as enh_mod
    from utils import affinity_state

    # 点存：一个审计里不存在的键
    point_path = tmp_path / "affinity_state.json"
    point_path.write_text(
        json.dumps({"u1::char1": {"affection_points": 42.0}}), encoding="utf-8"
    )
    monkeypatch.setattr(affinity_state, "_PATH", point_path)
    # 审计库：空
    monkeypatch.setattr(enh_mod, "_DB_DEFAULT", tmp_path / "empty.db")

    e = enh_mod.AffinityEnhancer()
    assert e._values.get("u1::char1") == 42.0


def test_enhancer_audit_takes_precedence_over_points(tmp_path, monkeypatch):
    """审计有值时不得被点存覆盖（审计是 enhancer 的自有真源）。"""
    import sqlite3

    from shisi.affinity import enhancer as enh_mod
    from utils import affinity_state

    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE affinity_records (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "character_id TEXT, old_value REAL, new_value REAL, delta REAL, "
            "reason TEXT, source TEXT, created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.execute(
            "INSERT INTO affinity_records (character_id, old_value, new_value, "
            "delta, reason, source) VALUES ('u1::char1', 10, 88, 78, 'r', 's')"
        )
        conn.commit()

    point_path = tmp_path / "affinity_state.json"
    point_path.write_text(
        json.dumps({"u1::char1": {"affection_points": 5.0}}), encoding="utf-8"
    )
    monkeypatch.setattr(affinity_state, "_PATH", point_path)
    monkeypatch.setattr(enh_mod, "_DB_DEFAULT", db)

    e = enh_mod.AffinityEnhancer()
    assert e._values["u1::char1"] == 88.0, "审计值优先，不得被点存覆盖"


def test_affinity_default_db_path_is_shared_owner():
    """写入方必须能拿到同一审计库路径（避免各自拼路径漂移）。"""
    from shisi.affinity.enhancer import default_db_path

    p = default_db_path()
    assert isinstance(p, Path)
    assert p.name == "sqlite.db"
    assert p.parent.name == "data"


def test_affinity_persist_mirrors_to_audit(tmp_path, monkeypatch):
    """`user_scheduler._persist_affinity` 必须**同时**写点存与审计日志。

    只写点存 → enhancer 重启回放（读审计）拿不到任何数据。
    本用例是行为断言：调一次持久化后，两个容器都应出现**同一个键**。
    """
    import sqlite3

    from shisi.affinity import enhancer as enh_mod
    from user_scheduler import UserManager
    from utils import affinity_state

    point_path = tmp_path / "affinity_state.json"
    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE affinity_records (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "character_id TEXT, old_value REAL, new_value REAL, delta REAL, "
            "reason TEXT, source TEXT, created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.commit()

    monkeypatch.setattr(affinity_state, "_PATH", point_path)
    monkeypatch.setattr(enh_mod, "_DB_DEFAULT", db)

    class _State:
        affection_points = 37.0

    class _Engine:
        state = _State()

    UserManager._persist_affinity("u1@im.wechat", "char1", _Engine())

    # ① 点存有值
    assert affinity_state.load_points("u1@im.wechat", "char1") == 37.0
    # ② 审计也有值，且键是**规范键**（与 enhancer 回放同口径）
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT character_id, new_value FROM affinity_records").fetchall()
    assert rows == [("u1@im.wechat::char1", 37.0)], f"审计镜像缺失或键不符: {rows}"


# ─────────────────────────────────────────────────────────────
# C. 向量召回读侧协程缺陷
# ─────────────────────────────────────────────────────────────


def test_vector_search_read_path_runs_coroutine(monkeypatch):
    """`_search` 是协程函数 → 读侧必须经桥执行，而不是拿到 coroutine 当列表用。"""
    import inspect as _inspect

    from shisi.memory.legacy.vector_memory import VectorMemory

    assert _inspect.iscoroutinefunction(VectorMemory._search), (
        "前提变了：若 _search 改成同步函数，本用例需同步调整"
    )

    from shisi.memory.legacy.semantic_memory import SemanticMemory

    class _FakeVM:
        async def _search(self, coll, query, top_k, where=None):
            # 记录 where 是否被下推
            self.seen_where = where
            return [{"content": "我喜欢喝美式", "metadata": {"user_key": "u1"}}]

    class _FakeSM:
        def search_facts(self, query, user_key=None):
            return []

    vm = _FakeVM()
    sm = SemanticMemory(vm, _FakeSM())
    out = sm.search("咖啡", top_k=5, user_key="u1")
    assert out["vector"], "向量召回不得恒空（读侧协程缺陷回归）"
    assert out["vector"][0]["content"] == "我喜欢喝美式"
    assert vm.seen_where == {"user_key": "u1"}, "user_key 过滤必须下推 Chroma"


def test_vector_search_filters_other_users(monkeypatch):
    """跨用户向量结果必须被剔除（隔离不因修复而放松）。"""
    from shisi.memory.legacy.semantic_memory import SemanticMemory

    class _FakeVM:
        async def _search(self, coll, query, top_k, where=None):
            return [
                {"content": "我的", "metadata": {"user_key": "u1"}},
                {"content": "别人的", "metadata": {"user_key": "u2"}},
            ]

    class _FakeSM:
        def search_facts(self, query, user_key=None):
            return []

    out = SemanticMemory(_FakeVM(), _FakeSM()).search("x", user_key="u1")
    assert [r["content"] for r in out["vector"]] == ["我的"]


# ─────────────────────────────────────────────────────────────
# D. 墙钟口径
# ─────────────────────────────────────────────────────────────


def test_time_awareness_uses_local_today_not_host_wallclock():
    """默认日期必须走 now_local().date()，不得再用 date.today()（宿主墙钟）。"""
    import tools.builtin.time_awareness_tool as tat

    src = inspect.getsource(tat.TimeAwarenessTool.execute)
    assert "now_local().date()" in src
    assert "date_type.today()" not in src, "宿主墙钟取日会与北京时间偏一天"

    tool = tat.TimeAwarenessTool()
    # 无效 action 仍应优雅失败（顺带验证 execute 未因改动而崩）
    res = tool.execute(action="bad")
    assert res.success is False


def test_time_awareness_current_reports_local_date():
    """`current` 动作报告的 date 必须等于本地日（行为断言）。"""
    from tools.builtin.time_awareness_tool import TimeAwarenessTool
    from utils.local_time import now_local

    tool = TimeAwarenessTool()
    res = tool.execute(action="current")
    assert res.success is True
    assert res.data["date"] == now_local().strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────
# F. 反思按会话检索：空归属行必须"取回后排除"，而非查询阶段挡在门外
# ─────────────────────────────────────────────────────────────


class _ReflectSM:
    """最小结构化记忆替身：支持 limit-only 与 limit+session_id 两种调用。"""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[dict] = []

    def get_reflections(self, limit: int = 10, session_id: str | None = None):
        self.calls.append({"limit": limit, "session_id": session_id})
        if session_id is not None:
            return [r for r in self._rows if str(r.get("session_id") or "") == str(session_id)][:limit]
        return list(self._rows)[:limit]


def _mk_engine(sm):
    from shisi.memory.legacy.reflection_engine import ReflectionEngine

    # 用真实构造（而非 __new__ 绕过）：避免与真实字段名漂移
    return ReflectionEngine(vector_memory=None, structured_memory=sm)


def test_reflection_query_fetches_without_strict_session_filter():
    """必须用「只按 limit 取回 → Python 过滤」——旧实现把 session_id 下推 SQL，
    空归属的历史反思连候选都进不来，按会话检索永远是空的。"""
    rows = [
        {"content": "本会话的洞察", "session_id": "1:r@im.wechat"},
        {"content": "无归属的历史洞察", "session_id": ""},
        {"content": "别的会话", "session_id": "2:r@im.wechat"},
    ]
    sm = _ReflectSM(rows)
    eng = _mk_engine(sm)
    out = eng.get_insights("x", top_k=5, session_id="1:r@im.wechat")
    # 取回调用不得带 session_id（否则空归属行被 SQL 挡掉）
    assert sm.calls and sm.calls[0]["session_id"] is None, f"查询阶段不应下推会话过滤: {sm.calls}"
    # 本会话的留下，无归属的与别会话的排除（宁缺毋串）
    assert out == ["本会话的洞察"], out


def test_reflection_query_no_session_returns_all():
    """session_id=None（无隔离要求）时不应做任何排除。"""
    rows = [
        {"content": "A", "session_id": "1:r@im.wechat"},
        {"content": "无归属", "session_id": ""},
    ]
    eng = _mk_engine(_ReflectSM(rows))
    out = eng.get_insights("x", top_k=5)
    assert set(out) == {"A", "无归属"}


# ─────────────────────────────────────────────────────────────
# G. 会话键 owner 拆分唯一化
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "key,expected",
    [
        ("2:web:8hex", ("2", "web:8hex")),
        ("4:o9cq80@im.wechat", ("4", "o9cq80@im.wechat")),
        ("wxid@im.wechat", ("", "wxid@im.wechat")),
        ("notdigit:peer", ("", "notdigit:peer")),
        ("", ("", "")),
        ("  3:peer  ", ("3", "peer")),
    ],
)
def test_split_owner_semantics(key, expected):
    """`split_owner` 必须与原 `split(":", 1)` 语义一致（收口不得改行为）。"""
    from utils.session_key import split_owner

    assert split_owner(key) == expected


def test_no_handwritten_owner_split_remains():
    """防回归：生产代码不得再有手写 `split(":", 1)` 做 owner 拆分。

    判据用**AST 调用形态**而非裸文本 —— 注释/docstring 里提到该写法属正当
    （唯一 owner `utils/session_key.split_owner` 的说明文字本身就要引用它），
    文本匹配会产生假阳性。
    """
    import ast

    root = Path(__file__).resolve().parent.parent
    offenders: list[str] = []
    for rel in ("proactive", "user_scheduler.py", "shisi/memory/legacy", "utils"):
        target = root / rel
        files = [target] if target.is_file() else list(target.rglob("*.py"))
        for f in files:
            if "__pycache__" in str(f):
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == "split"):
                    continue
                # 形如 X.split(":", 1)
                args = node.args
                if len(args) != 2:
                    continue
                if (
                    isinstance(args[0], ast.Constant) and args[0].value == ":"
                    and isinstance(args[1], ast.Constant) and args[1].value == 1
                ):
                    offenders.append(f"{f.relative_to(root)}:{node.lineno}")
    assert not offenders, f"仍有手写 owner 拆分：{offenders}"


# ─────────────────────────────────────────────────────────────
# H. 激活角色缓存的数据源身份（防"目录指纹不变→换数据源仍读陈旧值"）
# ─────────────────────────────────────────────────────────────


def test_active_character_cache_invalidates_on_source_swap(monkeypatch):
    """替换 `_list_all_characters` 后必须立即读到新数据源。

    2026-09-22 块E：旧缓存键只有**目录指纹**；`_list_all_characters` 被替身
    替换时目录没变，缓存判定成立 → 返回与当前数据源无关的陈旧 active id。
    该缺陷在生产表现为「角色卡 is_active 切换后控制端仍显示旧激活角色」的
    一类读陈旧，在测试里表现为顺序依赖的假失败。
    """
    from api.routers import character_routes as cr

    # 清掉可能存在的进程级缓存
    monkeypatch.setattr(cr, "_ACTIVE_ID_FP", "")
    monkeypatch.setattr(cr, "_ACTIVE_ID_VALUE", "")

    # 数据源 A：激活 inactive-role
    monkeypatch.setattr(
        cr, "_list_all_characters",
        lambda normalize=False: [{"id": "inactive-role", "is_active": True}],
    )
    assert cr.get_active_character_id() == "inactive-role"

    # 换成数据源 B（目录未变 → 旧实现在此返回陈旧的 inactive-role）
    monkeypatch.setattr(
        cr, "_list_all_characters",
        lambda normalize=False: [
            {"id": "inactive-role", "is_active": False},
            {"id": "active-role", "is_active": True},
        ],
    )
    assert cr.get_active_character_id() == "active-role", "换数据源后仍读到陈旧激活角色"


def test_active_character_cache_still_hits_for_same_source(monkeypatch):
    """同一数据源重复调用仍应命中缓存（优化不得因修缓存失效而丢失）。"""
    from api.routers import character_routes as cr

    monkeypatch.setattr(cr, "_ACTIVE_ID_FP", "")
    monkeypatch.setattr(cr, "_ACTIVE_ID_VALUE", "")

    calls: list[int] = []

    def _src(normalize=False):
        calls.append(1)
        return [{"id": "only", "is_active": True}]

    monkeypatch.setattr(cr, "_list_all_characters", _src)
    assert cr.get_active_character_id() == "only"
    assert cr.get_active_character_id() == "only"
    assert len(calls) == 1, "同一数据源的重复调用必须命中缓存"

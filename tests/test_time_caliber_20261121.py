"""时间口径统一批测试（2026-11-21；2026-11-22 对抗验收修复批扩充）。

覆盖七层契约：
1. ``utils.local_time`` 的 UTC 写入口径 / 北京读边界函数；
2. ``topic_state`` / ``reminders`` / ``pending_intents`` 的 UTC 落库与 TTL 语义；
3. ``event_ledger`` aware UTC + 迁移脚本的 dry-run / apply / 幂等 / 失败整体恢复；
4. API 序列化补 ``+00:00`` 与静默窗跨日判定；
5. ``memory_recycle_bin`` 混列迁移（配对识别、从宽保留）与写侧/出库 UTC；
6. ``utils.time_caliber`` 版本闸（生产 fail-closed / 开发 CRITICAL 放行）；
7. ``daily_llm_budget`` 按北京自然日换日。

2026-11-22 对抗验收修复批新增（§9–§12）：
8. L8 — 环境判定下沉 ``utils.runtime_env``（utils 层禁导入 api、裸环境可直调版本闸）；
9. L7 — 未迁移后果方向钉死（旧北京墙钟行被当 UTC 读 = 提醒延后 8 小时）；
10. M10 — 版本闸接入 ``create_api_app``（python main.py 直启入口同样覆盖）；
11. L9 — 迁移脚本宿主时区校验（非 UTC+8 拒绝 --apply + 显式覆盖旗标 + dry-run 假设说明）。

隔离纪律：全部使用 ``tmp_path`` 与 monkeypatch；绝不触碰真实 ``data/``。
本文件按用户纪律**未在本阶段执行 pytest**，由后续统一串行测试阶段运行。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from utils import local_time as lt
from utils import topic_state as ts

_BASE = datetime(2026, 11, 21, 2, 0, 0)  # UTC 存储口径
_KEY = "2:time-caliber@im.wechat"


# ── 工具加载 ────────────────────────────────────────────────────────────


def _load_migration_module():
    """按文件路径加载迁移脚本（scripts/ 不是包，避免污染 sys.path）。"""
    path = Path(__file__).resolve().parents[1] / "scripts" / "migrate_time_columns_to_utc.py"
    spec = importlib.util.spec_from_file_location("_time_caliber_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 1. 工具函数 ─────────────────────────────────────────────────────────


class TestTimeHelpers:
    def test_now_utc_naive_is_naive_and_close_to_utc(self) -> None:
        before = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        value = lt.now_utc_naive()
        after = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        assert value.tzinfo is None
        assert before - timedelta(seconds=2) <= value <= after + timedelta(seconds=2)

    def test_to_utc_naive_converts_aware_beijing(self) -> None:
        aware = datetime(2026, 11, 21, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        assert lt.to_utc_naive(aware) == datetime(2026, 11, 21, 2, 0)

    def test_to_utc_naive_is_idempotent_for_naive(self) -> None:
        naive = datetime(2026, 11, 21, 2, 0)
        assert lt.to_utc_naive(naive) == naive

    def test_utc_to_local_naive(self) -> None:
        assert lt.utc_to_local_naive(datetime(2026, 11, 21, 2, 0)) == datetime(
            2026, 11, 21, 10, 0
        )

    def test_local_text_roundtrip(self) -> None:
        utc_text = lt.local_text_to_utc_text("2026-11-21 10:00:00")
        assert utc_text == "2026-11-21 02:00:00"
        assert lt.utc_text_to_local_text(utc_text) == "2026-11-21 10:00:00"

    def test_to_api_utc_adds_explicit_offset(self) -> None:
        assert lt.to_api_utc("2026-11-21 02:00:00") == "2026-11-21T02:00:00+00:00"
        assert lt.to_api_utc("2026-11-21T10:00:00+08:00") == "2026-11-21T02:00:00+00:00"
        assert lt.to_api_utc("") is None


# ── 2. topic_state：UTC 落库 + TTL ─────────────────────────────────────


@pytest.fixture()
def topic_env(tmp_path, monkeypatch):
    """话题状态机独立 DB/配置（沿用 test_topic_state.py 的夹具习惯）。"""
    import json

    monkeypatch.setattr(ts, "_DB_PATH", tmp_path / "sqlite.db", raising=False)
    monkeypatch.setattr(ts, "_CONFIG_PATH", tmp_path / "scheduler_config.json", raising=False)
    config = dict(ts.DEFAULTS)
    config["enabled"] = True
    (tmp_path / "scheduler_config.json").write_text(
        json.dumps({"topic_state": config}, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


class TestTopicStateUtc:
    def test_write_utc_and_seven_day_ttl(self, topic_env) -> None:
        ts.update_after_turn(_KEY, "default", "我明天去面试", now=_BASE, turn_id="t1")
        with sqlite3.connect(topic_env / "sqlite.db") as conn:
            row = conn.execute(
                "SELECT last_active_at, created_at, expires_at FROM topic_state"
            ).fetchone()
        assert row[0] == "2026-11-21 02:00:00"
        assert row[1] == "2026-11-21 02:00:00"
        assert row[2] == "2026-11-28 02:00:00"  # 7 天 TTL，UTC 下不漂移

    def test_snapshot_expires_strictly_after_ttl(self, topic_env) -> None:
        ts.update_after_turn(_KEY, "default", "周末去露营", now=_BASE, turn_id="t1")
        # TTL 边界前 1 秒仍可见
        snap = ts.get_topic_snapshot(_KEY, now=_BASE + timedelta(days=7) - timedelta(seconds=1))
        assert len(snap["active"]) == 1
        # 恰好到期（expires_at > now 为假）即不可见
        snap = ts.get_topic_snapshot(_KEY, now=_BASE + timedelta(days=7))
        assert snap["active"] == []

    def test_same_session_window_uses_utc_now(self, topic_env) -> None:
        ts.update_after_turn(_KEY, "default", "我明天去面试", now=_BASE, turn_id="t1")
        snapshot = ts.get_topic_snapshot(_KEY, now=_BASE + timedelta(minutes=119))
        assert "正在聊" in ts.render_topic_state_block(
            snapshot, now=_BASE + timedelta(minutes=119)
        )
        stale = ts.render_topic_state_block(
            snapshot, now=_BASE + timedelta(minutes=121), fresh_minutes=120
        )
        assert "之前没聊完" in stale

    def test_aware_now_converted_to_utc(self, topic_env) -> None:
        aware = datetime(2026, 11, 21, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        ts.update_after_turn(_KEY, "default", "我明天去面试", now=aware, turn_id="aware")
        with sqlite3.connect(topic_env / "sqlite.db") as conn:
            row = conn.execute("SELECT last_active_at FROM topic_state").fetchone()
        assert row[0] == "2026-11-21 02:00:00"


# ── 3. 提醒 / 澄清任务：UTC 存储 + 本地展示 ───────────────────────────


class TestReminderAndIntentClock:
    def test_reminder_stored_utc_and_due_compared_in_utc(self, tmp_path, monkeypatch) -> None:
        from shisi.memory.legacy import structured_memory as smod

        monkeypatch.setattr(smod, "now_utc_naive", lambda: _BASE)
        sm = smod.StructuredMemory(str(tmp_path / "reminders.db"))
        try:
            rid = sm.add_reminder("叫我起床", "2026-11-21 10:05:00", session_key="1:a")
            with sm._conn() as conn:  # noqa: SLF001
                raw = conn.execute(
                    "SELECT trigger_time FROM reminders WHERE id=?", (rid,)
                ).fetchone()["trigger_time"]
            assert raw == "2026-11-21 02:05:00"
            # 读边界仍返回北京时间（模型/用户看到的钟点不变）
            pending = sm.get_pending_reminders(session_key="1:a")
            assert pending[0]["trigger_time"] == "2026-11-21 10:05:00"
            # UTC 比较：差 1 秒不投，到点即投
            assert sm.get_due_reminders("2026-11-21 02:04:59") == []
            assert [r["id"] for r in sm.get_due_reminders("2026-11-21 02:05:00")] == [rid]
        finally:
            sm.close()

    def test_pending_intent_ttl_uses_utc(self, tmp_path, monkeypatch) -> None:
        from shisi.memory.legacy import structured_memory as smod

        monkeypatch.setattr(smod, "now_utc_naive", lambda: _BASE)
        sm = smod.StructuredMemory(str(tmp_path / "intent.db"))
        try:
            sm.upsert_pending_intent(
                "s1", "set_reminder", {"content": "起床"}, 1, "几点？", ttl_minutes=15
            )
            with sm._conn() as conn:  # noqa: SLF001
                row = conn.execute(
                    "SELECT expires_at FROM pending_intents WHERE session_key='s1'"
                ).fetchone()
            assert row["expires_at"] == "2026-11-21 02:15:00"
            monkeypatch.setattr(
                smod, "now_utc_naive", lambda: _BASE + timedelta(minutes=14)
            )
            assert sm.get_active_pending_intent("s1") is not None
            monkeypatch.setattr(
                smod, "now_utc_naive", lambda: _BASE + timedelta(minutes=16)
            )
            assert sm.get_active_pending_intent("s1") is None
        finally:
            sm.close()

    def test_user_profile_updated_at_is_utc_iso(self, tmp_path) -> None:
        from shisi.memory.legacy.user_profile import UserProfileStore

        store = UserProfileStore(tmp_path / "profile.db")
        store.upsert("1:x", nickname="小夏")
        row = store.get("1:x")
        parsed = datetime.fromisoformat(row["updated_at"])
        now_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        assert parsed.tzinfo is None
        assert abs((parsed - now_utc).total_seconds()) < 10

    def test_daily_summary_created_at_is_utc(self, tmp_path) -> None:
        """daily_summaries.date 是本地日标签，created_at 必须显式 UTC。"""
        from shisi.memory.legacy._legacy_diary_summarizer import DiarySummarizer

        db = tmp_path / "diary.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE daily_summaries (date TEXT PRIMARY KEY, "
                "summary TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.commit()

        class _SM:
            @contextmanager
            def get_connection(self):
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

        DiarySummarizer(structured_memory=_SM()).save_summary(
            "1:x|c1|2026-11-21", "今天聊了面试"
        )
        with sqlite3.connect(db) as conn:
            value = conn.execute("SELECT created_at FROM daily_summaries").fetchone()[0]
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        now_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        assert abs((parsed - now_utc).total_seconds()) < 10


# ── 4. event_ledger + 迁移脚本 ──────────────────────────────────────────


class TestEventLedgerUtc:
    def test_now_iso_is_aware_utc(self) -> None:
        from shisi.agent_plane.event_ledger import EventLedger

        stamp = EventLedger.now_iso()
        parsed = datetime.fromisoformat(stamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)

    def test_prune_compares_utc(self, tmp_path) -> None:
        from shisi.agent_plane.event_ledger import EventLedger

        ledger = EventLedger(tmp_path / "agent_plane.db")
        ledger.append(
            session_key="1:a",
            event_type="user_message",
            created_at="2020-01-01T00:00:00+08:00",
        )
        ledger.append(session_key="1:a", event_type="user_message")
        removed = ledger.prune(retention_days=30)
        assert removed == 1
        # count_by_session 的契约是「按 event_type 分组计数」的 dict
        # （api/routers/agent_plane_routes.py 两处消费均依赖该结构）。
        assert ledger.count_by_session("1:a") == {"user_message": 1}


class TestMigrationScript:
    @staticmethod
    def _seed_sqlite(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE topic_state (
                    session_key TEXT, character_id TEXT, topic_id TEXT,
                    last_recalled_at TEXT, last_active_at TEXT, created_at TEXT,
                    updated_at TEXT, expires_at TEXT
                );
                CREATE TABLE thread_plan (
                    session_key TEXT, character_id TEXT, planned_at TEXT,
                    next_at TEXT, expires_at TEXT, consumed_at TEXT
                );
                CREATE TABLE topic_meta (session_key TEXT, last_llm_at TEXT);
                CREATE TABLE user_profile (user_key TEXT, updated_at TEXT);
                CREATE TABLE reminders (id INTEGER PRIMARY KEY, trigger_time TEXT, delivered_at TEXT);
                CREATE TABLE pending_intents (
                    id INTEGER PRIMARY KEY, created_at TEXT, updated_at TEXT, expires_at TEXT
                );
                CREATE TABLE agent_events (id INTEGER PRIMARY KEY, created_at TEXT);
                CREATE TABLE agent_threads (
                    thread_id TEXT PRIMARY KEY, opened_at TEXT, last_event_at TEXT,
                    closed_at TEXT, created_at TEXT, updated_at TEXT
                );
                CREATE TABLE agent_commitments (
                    commitment_id TEXT PRIMARY KEY, due_at TEXT, expires_at TEXT,
                    created_at TEXT, updated_at TEXT, fulfilled_at TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO topic_state VALUES ('s','c','t','','2026-11-21 10:00:00',"
                "'2026-11-21 10:00:00','2026-11-21 10:00:00','2026-11-28 10:00:00')"
            )
            conn.execute(
                "INSERT INTO thread_plan VALUES ('s','c','2026-11-21 10:00:00',"
                "'2026-11-21 11:00:00','2026-11-22 11:00:00','')"
            )
            conn.execute("INSERT INTO topic_meta VALUES ('s','2026-11-21 10:00:00')")
            conn.execute("INSERT INTO user_profile VALUES ('1:x','2026-11-21T10:00:00')")
            conn.execute(
                "INSERT INTO reminders VALUES (1,'2026-11-21 10:00:00','2026-11-21 09:59:00')"
            )
            # created_at 是 SQLite UTC 默认（09:00），updated_at 是本地（10:05）
            conn.execute(
                "INSERT INTO pending_intents VALUES (1,'2026-11-21 09:00:00',"
                "'2026-11-21 10:05:00','2026-11-21 10:15:00')"
            )
            conn.execute("INSERT INTO agent_events VALUES (1,'2026-11-21 10:00:00')")
            conn.execute(
                "INSERT INTO agent_threads VALUES ('th','2026-11-21 10:00:00',"
                "'2026-11-21 10:05:00','','2026-11-21 10:00:00','2026-11-21 10:05:00')"
            )
            conn.execute(
                "INSERT INTO agent_commitments VALUES ('c1','2026-11-21 11:00:00',"
                "'2026-11-28 11:00:00','2026-11-21 10:00:00','2026-11-21 10:00:00','')"
            )
            conn.commit()

    @staticmethod
    def _seed_ledger(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute("CREATE TABLE event_ledger (id INTEGER PRIMARY KEY, created_at TEXT)")
            conn.execute(
                "INSERT INTO event_ledger (created_at) VALUES ('2026-11-21T10:00:00+08:00')"
            )
            conn.commit()

    def test_dry_run_does_not_write(self, tmp_path) -> None:
        module = _load_migration_module()
        db = tmp_path / "sqlite.db"
        self._seed_sqlite(db)
        before = sqlite3.connect(db).execute(
            "SELECT last_active_at FROM topic_state"
        ).fetchone()[0]
        result = module.migrate_sqlite_db(db, dry_run=True)
        after = sqlite3.connect(db).execute(
            "SELECT last_active_at FROM topic_state"
        ).fetchone()[0]
        assert result["total_updates"] > 0
        assert before == after == "2026-11-21 10:00:00"

    def test_apply_shifts_minus_eight_hours_and_is_idempotent(self, tmp_path) -> None:
        module = _load_migration_module()
        db = tmp_path / "sqlite.db"
        self._seed_sqlite(db)
        first = module.migrate_sqlite_db(db, dry_run=False)
        assert first["applied"] is True
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT last_active_at FROM topic_state").fetchone()[0] == (
                "2026-11-21 02:00:00"
            )
            assert conn.execute("SELECT updated_at FROM user_profile").fetchone()[0] == (
                "2026-11-21T02:00:00"
            )
            # 混合列：从未更新的 pending 行 updated_at 与 created_at 相等 → 不动
            assert conn.execute("SELECT updated_at FROM pending_intents").fetchone()[0] == (
                "2026-11-21 02:05:00"
            )
            assert conn.execute("SELECT expires_at FROM pending_intents").fetchone()[0] == (
                "2026-11-21 02:15:00"
            )
        second = module.migrate_sqlite_db(db, dry_run=False)
        assert second["already_applied"] is True
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT last_active_at FROM topic_state").fetchone()[0] == (
                "2026-11-21 02:00:00"
            )

    def test_event_ledger_migration_to_aware_utc(self, tmp_path) -> None:
        module = _load_migration_module()
        db = tmp_path / "agent_plane.db"
        self._seed_ledger(db)
        result = module.migrate_agent_plane_db(db, dry_run=False)
        assert result["applied"] is True
        with sqlite3.connect(db) as conn:
            value = conn.execute("SELECT created_at FROM event_ledger").fetchone()[0]
        assert value == "2026-11-21T02:00:00+00:00"

    def test_rollback_restores_backup(self, tmp_path) -> None:
        module = _load_migration_module()
        live = tmp_path / "live.db"
        self._seed_sqlite(live)
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        backup_db = backup_dir / "live.db"
        module._backup_db(live, backup_db)  # noqa: SLF001
        module.migrate_sqlite_db(live, dry_run=False)
        assert sqlite3.connect(live).execute(
            "SELECT last_active_at FROM topic_state"
        ).fetchone()[0] == "2026-11-21 02:00:00"
        (backup_dir / "manifest.json").write_text(
            json.dumps({"backups": {str(live): str(backup_db)}}), encoding="utf-8"
        )
        module.rollback_from_backup(backup_dir)
        assert sqlite3.connect(live).execute(
            "SELECT last_active_at FROM topic_state"
        ).fetchone()[0] == "2026-11-21 10:00:00"


# ── 5. API 序列化 / 静默窗跨日 / cron 时区 ─────────────────────────────


class TestApiSerialization:
    def test_topic_snapshot_times_get_offset(self) -> None:
        from api.routers.topic_routes import _serialize_snapshot_times

        payload = {
            "active": [{"last_active_at": "2026-11-21 02:00:00", "title": "x"}],
            "next_plan": {"planned_at": "2026-11-21 03:00:00", "intent": "y"},
        }
        out = _serialize_snapshot_times(payload)
        assert out["active"][0]["last_active_at"] == "2026-11-21T02:00:00+00:00"
        assert out["next_plan"]["planned_at"] == "2026-11-21T03:00:00+00:00"
        assert out["active"][0]["title"] == "x"

    def test_chat_history_created_at_gets_offset(self, tmp_path) -> None:
        from api.routers.chat_routes import _query_history_sync

        db = tmp_path / "chat.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE chat_history (id INTEGER PRIMARY KEY, session_id TEXT, "
                "role TEXT, content TEXT, emotion_tag TEXT, created_at TEXT, "
                "character_id TEXT, turn_id TEXT)"
            )
            conn.execute(
                "INSERT INTO chat_history VALUES (1,'s','user','hi','',"
                "'2026-11-21 02:00:00','c','')"
            )
            conn.commit()

        class _SM:
            @contextmanager
            def get_connection(self):
                conn = sqlite3.connect(db)
                conn.row_factory = sqlite3.Row
                try:
                    yield conn
                finally:
                    conn.close()

        rows = _query_history_sync(_SM(), "s", 10, 0)
        assert rows[0]["created_at"] == "2026-11-21T02:00:00+00:00"


class TestQuietHoursAndCron:
    @pytest.mark.parametrize(
        ("hour", "expected"),
        [(22, False), (23, True), (0, True), (6, True), (7, False), (12, False)],
    )
    def test_quiet_hours_cross_day(self, monkeypatch, hour, expected) -> None:
        import proactive.scheduler as scheduler_mod

        monkeypatch.setattr(
            scheduler_mod, "_local_now", lambda: datetime(2026, 11, 21, hour, 30)
        )
        assert scheduler_mod.ProactiveScheduler._quiet_hours_contains((23, 7)) is expected

    def test_cron_scheduler_pins_beijing_timezone(self) -> None:
        import inspect

        from proactive.scheduler import ProactiveScheduler

        source = inspect.getsource(ProactiveScheduler.start)
        assert "BackgroundScheduler(daemon=True, timezone=_BEIJING)" in source


def test_apply_with_backup_restores_sqlite_when_agent_plane_fails(tmp_path, monkeypatch):
    """迁移任一步失败必须整体回滚，不能留下半迁移的 sqlite。"""
    module = _load_migration_module()
    sqlite_db = tmp_path / "sqlite.db"
    agent_db = tmp_path / "agent_plane.db"
    # 复用类内种子（类方法可直接调用）
    TestMigrationScript._seed_sqlite(sqlite_db)  # noqa: SLF001
    TestMigrationScript._seed_ledger(agent_db)  # noqa: SLF001
    # 本用例验证的是回滚语义，须钉在「北京宿主」前提上：CI（ubuntu-24.04）
    # 宿主 UTC 偏移为 0，L9 守卫会先于 _boom 抛「宿主时区非 UTC+8」令
    # match 落空；守卫语义本身由 TestMigrationHostTimezoneGuard 专项覆盖。
    monkeypatch.setattr(module, "host_utc_offset_minutes", lambda: 480)

    def _boom(*args, **kwargs):
        raise RuntimeError("agent plane down")

    monkeypatch.setattr(module, "migrate_agent_plane_db", _boom)
    with pytest.raises(RuntimeError, match="agent plane down"):
        module.apply_with_backup(sqlite_db, agent_db, tmp_path / "backups")

    # 第一库必须从备份恢复回未迁移态
    with sqlite3.connect(sqlite_db) as conn:
        assert conn.execute("SELECT last_active_at FROM topic_state").fetchone()[0] == (
            "2026-11-21 10:00:00"
        )
    # 第二库同样保持原样（未迁移）
    with sqlite3.connect(agent_db) as conn:
        assert conn.execute("SELECT created_at FROM event_ledger").fetchone()[0] == (
            "2026-11-21T10:00:00+08:00"
        )
    # 两库都不得残留迁移标记（否则幂等闸会掩盖「半迁移」态）
    for _path in (sqlite_db, agent_db):
        with sqlite3.connect(_path) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        assert "time_caliber_migrations" not in tables


def test_ensure_applied_is_noop_after_markers(tmp_path):
    """启动期 ensure_applied：已迁移库不得重复建备份/重复减 8 小时。"""
    module = _load_migration_module()
    sqlite_db = tmp_path / "sqlite.db"
    agent_db = tmp_path / "agent_plane.db"
    TestMigrationScript._seed_sqlite(sqlite_db)  # noqa: SLF001
    TestMigrationScript._seed_ledger(agent_db)  # noqa: SLF001
    module.migrate_sqlite_db(sqlite_db, dry_run=False)
    module.migrate_agent_plane_db(agent_db, dry_run=False)
    receipts = module.ensure_applied(sqlite_db, agent_db, tmp_path / "backups")
    assert receipts["status"] == "already_applied"
    assert not (tmp_path / "backups").exists()


# ── 6. 回收站：UTC 写侧 / UTC 出库 / 混列迁移策略 ───────────────────────


class TestRecycleBinUtc:
    def test_delete_fact_writes_utc_pair(self, tmp_path, monkeypatch) -> None:
        from shisi.memory.legacy import structured_memory as smod

        monkeypatch.setattr(smod, "now_utc_naive", lambda: _BASE)
        sm = smod.StructuredMemory(str(tmp_path / "recycle.db"))
        try:
            with sm.get_connection(write=True) as conn:
                conn.execute(
                    "INSERT INTO user_facts (fact, category, confidence, source, "
                    "user_key, access_count, status) "
                    "VALUES ('她喜欢猫', 'general', 0.5, 'test', '1:a', 0, 'active')"
                )
                conn.commit()
                fid = conn.execute("SELECT id FROM user_facts").fetchone()["id"]
            assert sm.delete_fact(int(fid), recycle=True, user_key="1:a", retain_days=30)
            with sm.get_connection() as conn:
                row = conn.execute(
                    "SELECT deleted_at, restore_before FROM memory_recycle_bin"
                ).fetchone()
            # 写侧 UTC：与 DDL 默认 datetime('now') 同口径，不再混入北京墙钟
            assert row["deleted_at"] == "2026-11-21 02:00:00"
            assert row["restore_before"] == "2026-12-21 02:00:00"  # +30 整天（UTC）
        finally:
            sm.close()

    def test_purge_compares_restore_before_in_utc(self, tmp_path) -> None:
        from shisi.memory.legacy import structured_memory as smod

        sm = smod.StructuredMemory(str(tmp_path / "purge.db"))
        try:
            with sm.get_connection(write=True) as conn:
                conn.executemany(
                    "INSERT INTO memory_recycle_bin (character_id, memory_id, "
                    "memory_content, deleted_at, restore_before, restored) "
                    "VALUES ('user_fact:1:a', ?, '{}', '2026-11-01 02:00:00', ?, 0)",
                    [("1", "2026-11-14 01:59:59"), ("2", "2026-11-14 02:00:01")],
                )
                conn.commit()
            # cutoff = now(UTC) - 7 天 = 11-14 02:00:00：差 1 秒决定出库与否
            receipt = sm.purge_expired_recycle_bin(
                grace_days=7, now=datetime(2026, 11, 21, 2, 0, 0)
            )
            assert receipt["deleted"] == 1
            with sm.get_connection() as conn:
                remain = [
                    r["memory_id"]
                    for r in conn.execute("SELECT memory_id FROM memory_recycle_bin")
                ]
            assert remain == ["2"]
        finally:
            sm.close()


class TestRecycleBinMixedColumnMigration:
    @staticmethod
    def _seed_recycle(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE memory_recycle_bin ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL, "
                "memory_id TEXT NOT NULL, memory_content TEXT NOT NULL, "
                "deleted_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "restore_before TEXT NOT NULL, restored INTEGER NOT NULL DEFAULT 0)"
            )
            conn.executemany(
                "INSERT INTO memory_recycle_bin (character_id, memory_id, "
                "memory_content, deleted_at, restore_before) "
                "VALUES ('user_fact:1:a', ?, '{}', ?, ?)",
                [
                    # 本地墙钟配对（差 30 整天）→ 识别为旧写侧行，成对 -8h
                    ("1", "2026-11-21 10:00:00", "2026-12-21 10:00:00"),
                    # 差 30 天 + 8h：像「UTC deleted_at（DDL 默认）× 本地
                    # restore_before」的混种 → 无法识别 → 从宽保留
                    ("2", "2026-11-21 10:00:00", "2026-12-21 18:00:00"),
                    # 差 90 分钟（非整天）→ 保留
                    ("3", "2026-11-21 10:00:00", "2026-11-21 11:30:00"),
                ],
            )
            conn.commit()

    def test_pair_rows_shifted_and_unpaired_kept(self, tmp_path) -> None:
        module = _load_migration_module()
        db = tmp_path / "sqlite.db"
        self._seed_recycle(db)
        result = module.migrate_sqlite_db(db, dry_run=False)
        stats = result["tables"]["memory_recycle_bin"]
        assert stats["pair_shifted"] == 1
        assert stats["kept_unpaired"] == 2
        assert stats["updates"] == 2
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "SELECT memory_id, deleted_at, restore_before "
                "FROM memory_recycle_bin ORDER BY id"
            ).fetchall()
        assert rows[0] == ("1", "2026-11-21 02:00:00", "2026-12-21 02:00:00")
        # 从宽保留：未平移行在 UTC 出库判据下只会更晚出库，不会提前删除
        assert rows[1] == ("2", "2026-11-21 10:00:00", "2026-12-21 18:00:00")
        assert rows[2] == ("3", "2026-11-21 10:00:00", "2026-11-21 11:30:00")

    def test_dry_run_reports_without_writing(self, tmp_path) -> None:
        module = _load_migration_module()
        db = tmp_path / "sqlite.db"
        self._seed_recycle(db)
        result = module.migrate_sqlite_db(db, dry_run=True)
        stats = result["tables"]["memory_recycle_bin"]
        assert stats["pair_shifted"] == 1
        assert stats["kept_unpaired"] == 2
        with sqlite3.connect(db) as conn:
            value = conn.execute(
                "SELECT deleted_at FROM memory_recycle_bin WHERE memory_id='1'"
            ).fetchone()[0]
        assert value == "2026-11-21 10:00:00"  # dry-run 未写库

    def test_inventory_covers_recycle_bin_and_characters_v2(self) -> None:
        module = _load_migration_module()
        specs = dict(module.SQLITE_TABLE_SPECS)
        assert specs["characters_v2"] == ("created_at",)
        assert module.RECYCLE_BIN_TABLE == "memory_recycle_bin"
        assert module.RECYCLE_BIN_COLUMNS == ("deleted_at", "restore_before")
        # 版本闸与迁移脚本的标记名必须一致
        from utils import time_caliber as tc

        assert tc.MIGRATION_NAME == module.MIGRATION_NAME

    def test_characters_v2_created_at_shifted(self, tmp_path) -> None:
        module = _load_migration_module()
        db = tmp_path / "sqlite.db"
        with sqlite3.connect(db) as conn:
            conn.execute(
                "CREATE TABLE characters_v2 (id TEXT PRIMARY KEY, created_at TEXT)"
            )
            conn.execute("INSERT INTO characters_v2 VALUES ('c1', '2026-11-21T10:00:00')")
            conn.commit()
        module.migrate_sqlite_db(db, dry_run=False)
        with sqlite3.connect(db) as conn:
            value = conn.execute("SELECT created_at FROM characters_v2").fetchone()[0]
        assert value == "2026-11-21T02:00:00"  # 直接 -8h（isoformat 的 T 保留）


# ── 7. 时间口径版本闸 ───────────────────────────────────────────────────


class TestTimeCaliberVersionGate:
    @staticmethod
    def _stamp_marker(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE time_caliber_migrations (migration TEXT PRIMARY KEY, "
                "applied_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "INSERT INTO time_caliber_migrations (migration, applied_at, detail) "
                "VALUES ('time_caliber_utc_v1', '2026-11-21T00:00:00+00:00', '')"
            )
            conn.commit()

    @staticmethod
    def _clear_env(monkeypatch) -> None:
        for env in ("AI_GF_ENV", "APP_ENV", "ENV"):
            monkeypatch.delenv(env, raising=False)

    def test_passes_when_both_markers_present(self, tmp_path, monkeypatch) -> None:
        from utils import time_caliber as tc

        self._clear_env(monkeypatch)
        sqlite_db = tmp_path / "sqlite.db"
        plane_db = tmp_path / "agent_plane.db"
        self._stamp_marker(sqlite_db)
        self._stamp_marker(plane_db)
        assert tc.ensure_time_caliber_migrated(sqlite_db, plane_db) is True

    def test_missing_db_file_counts_as_migrated(self, tmp_path, monkeypatch) -> None:
        from utils import time_caliber as tc

        self._clear_env(monkeypatch)
        sqlite_db = tmp_path / "sqlite.db"
        self._stamp_marker(sqlite_db)
        # agent_plane 库不存在（从未启用）→ 不阻塞启动
        assert tc.ensure_time_caliber_migrated(sqlite_db, tmp_path / "absent.db") is True

    def test_production_fails_closed_when_unmigrated(self, tmp_path, monkeypatch) -> None:
        from utils import time_caliber as tc

        monkeypatch.setenv("AI_GF_ENV", "prod")
        # 真实库文件但无迁移标记 → 未迁移（缺文件会被视为「无需迁移」）
        sqlite_db = tmp_path / "sqlite.db"
        sqlite3.connect(sqlite_db).close()
        plane_db = tmp_path / "agent_plane.db"
        sqlite3.connect(plane_db).close()
        with pytest.raises(RuntimeError, match="时间口径"):
            tc.ensure_time_caliber_migrated(sqlite_db, plane_db)

    def test_dev_warns_and_passes_when_unmigrated(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        from utils import time_caliber as tc

        self._clear_env(monkeypatch)
        # 真实库文件但无迁移标记 → 未迁移（缺文件会被视为「无需迁移」）
        sqlite_db = tmp_path / "sqlite.db"
        sqlite3.connect(sqlite_db).close()
        plane_db = tmp_path / "agent_plane.db"
        sqlite3.connect(plane_db).close()
        with caplog.at_level(logging.CRITICAL, logger="utils.time_caliber"):
            result = tc.ensure_time_caliber_migrated(sqlite_db, plane_db)
        assert result is False
        assert any("时间口径" in r.message for r in caplog.records)


# ── 8. daily_llm_budget 按北京自然日换日 ────────────────────────────────


class TestTopicBudgetBeijingDay:
    def test_beijing_day_helper(self) -> None:
        assert ts._beijing_day("2026-11-21 15:00:00") == "2026-11-21"  # 北京 23:00
        assert ts._beijing_day("2026-11-21 17:00:00") == "2026-11-22"  # 北京次日 01:00
        assert ts._beijing_day(datetime(2026, 11, 21, 16, 0, 0)) == "2026-11-22"  # 北京 00:00
        assert ts._beijing_day("") == ""
        assert ts._beijing_day("garbage") == ""

    @staticmethod
    def _seed_calls(db_path: Path, calls: int, last_llm_at: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE topic_meta SET llm_calls = ?, turns_since_llm = 4, last_llm_at = ?",
                (calls, last_llm_at),
            )
            conn.commit()

    def test_budget_resets_on_beijing_day_rollover(self, topic_env) -> None:
        # 21 日 15:00 UTC = 北京 23:00；21 日 17:00 UTC = 北京 22 日 01:00。
        # UTC 日未变（都是 21 日）但北京日已翻篇 → 预算必须清零
        #（旧 UTC 日口径在此不清零，本断言对旧实现为 5）。
        ts.update_after_turn(
            _KEY, "default", "聊到很晚", now=datetime(2026, 11, 21, 15, 0, 0), turn_id="t1"
        )
        self._seed_calls(topic_env / "sqlite.db", 5, "2026-11-21 15:00:00")
        ts.update_after_turn(
            _KEY, "default", "又聊一句", now=datetime(2026, 11, 21, 17, 0, 0), turn_id="t2"
        )
        with sqlite3.connect(topic_env / "sqlite.db") as conn:
            calls = conn.execute("SELECT llm_calls FROM topic_meta").fetchone()[0]
        assert calls == 0

    def test_budget_kept_within_same_beijing_day(self, topic_env) -> None:
        ts.update_after_turn(
            _KEY, "default", "聊到很晚", now=datetime(2026, 11, 21, 15, 0, 0), turn_id="t1"
        )
        self._seed_calls(topic_env / "sqlite.db", 5, "2026-11-21 15:00:00")
        ts.update_after_turn(
            _KEY, "default", "又聊一句", now=datetime(2026, 11, 21, 15, 30, 0), turn_id="t2"
        )
        with sqlite3.connect(topic_env / "sqlite.db") as conn:
            calls = conn.execute("SELECT llm_calls FROM topic_meta").fetchone()[0]
        assert calls == 5  # 同一北京自然日内不清零

    def test_should_run_llm_judge_budget_uses_beijing_day(self, topic_env) -> None:
        ts.update_after_turn(_KEY, "default", "起个头", now=_BASE, turn_id="t1")
        # last_llm_at = 20 日 16:30 UTC（北京 21 日 00:30）；now = 21 日 01:30 UTC
        #（北京 21 日 09:30）→ 同一北京日，预算不清零 → calls(2) >= budget(2)
        # → 不再 judge。旧 UTC 日口径会误清零并放行（对旧实现本断言为 True）。
        self._seed_calls(topic_env / "sqlite.db", 2, "2026-11-20 16:30:00")
        cfg = dict(ts.DEFAULTS)
        cfg.update(
            {
                "enabled": True,
                "llm_judge_enabled": True,
                "daily_llm_budget": 2,
                "judge_every_turns": 4,
            }
        )
        assert (
            ts.should_run_llm_judge(_KEY, cfg=cfg, now=datetime(2026, 11, 21, 1, 30, 0))
            is False
        )


# ── 9. L8：环境判定下沉 + 裸环境可直接调用版本闸（2026-11-22） ───────────


class TestRuntimeEnvSink:
    """L8：环境判定唯一真源在 ``utils.runtime_env``；utils 层禁导入 api。"""

    def test_runtime_config_reexports_utils_runtime_env(self) -> None:
        import api.runtime_config as rc
        import utils.runtime_env as renv

        assert rc.is_production is renv.is_production
        assert rc.is_explicit_dev is renv.is_explicit_dev

    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            ({"AI_GF_ENV": "prod"}, True),
            ({"APP_ENV": "production"}, True),
            ({"ENV": "prod"}, True),
            # 任一变量命中生产即生产（原 api.runtime_config 语义，保持不变）
            ({"AI_GF_ENV": "dev", "APP_ENV": "prod"}, True),
            ({}, False),
            ({"AI_GF_ENV": "test"}, False),
            ({"AI_GF_ENV": "staging"}, False),
        ],
    )
    def test_is_production_semantics_preserved(
        self, monkeypatch, env, expected
    ) -> None:
        import utils.runtime_env as renv

        for key in ("AI_GF_ENV", "APP_ENV", "ENV"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        assert renv.is_production() is expected

    @pytest.mark.parametrize(
        ("env", "expected"),
        [
            ({"AI_GF_ENV": "dev"}, True),
            ({"ENV": "development"}, True),
            ({"AI_GF_ENV": "dev", "APP_ENV": "prod"}, False),  # 生产标记优先
            ({}, False),
            ({"AI_GF_ENV": "test"}, False),
        ],
    )
    def test_is_explicit_dev_semantics_preserved(
        self, monkeypatch, env, expected
    ) -> None:
        import utils.runtime_env as renv

        for key in ("AI_GF_ENV", "APP_ENV", "ENV"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        assert renv.is_explicit_dev() is expected

    @pytest.mark.parametrize(
        "rel",
        ["utils/runtime_env.py", "utils/time_caliber.py", "utils/local_time.py"],
    )
    def test_utils_layer_does_not_import_api(self, rel) -> None:
        """契约守卫：utils 底层禁止反向导入 api 包（防拉起重链/裸环境炸 import）。"""
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    alias.name.split(".")[0] != "api" for alias in node.names
                ), f"{rel} 出现 import api.*"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert (
                    node.module.split(".")[0] != "api"
                ), f"{rel} 出现 from api.* import"


class TestBareEnvGateCallable:
    """L8：裸环境（无 JWT_SECRET、非显式 dev）必须能直接调用时间口径闸。

    旧实现 ``from api.runtime_config import is_production`` 经
    ``api/__init__ → app_factory → auth_jwt`` 拉起重链，裸环境 import 即抛
    ``RuntimeError: Refusing to start: JWT_SECRET missing``（本批前置探针
    实证），闸的检查根本执行不到。修复后 time_caliber 只依赖
    ``utils.runtime_env``（纯标准库），子进程裸调用必须成功返回。
    """

    def test_gate_runs_without_jwt_secret_in_bare_subprocess(self, tmp_path) -> None:
        sqlite_db = tmp_path / "sqlite.db"
        plane_db = tmp_path / "agent_plane.db"
        TestTimeCaliberVersionGate._stamp_marker(sqlite_db)  # noqa: SLF001
        TestTimeCaliberVersionGate._stamp_marker(plane_db)  # noqa: SLF001
        project_root = str(Path(__file__).resolve().parents[1])
        code = (
            "import sys\n"
            f"sys.path.insert(0, {project_root!r})\n"
            "from utils.time_caliber import ensure_time_caliber_migrated\n"
            f"print(ensure_time_caliber_migrated({str(sqlite_db)!r}, {str(plane_db)!r}))\n"
        )
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("JWT_SECRET", "AI_GF_ENV", "APP_ENV", "ENV")
        }
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tempfile.gettempdir(),  # 远离仓库根，排除 CWD 隐式导入干扰
        )
        assert proc.returncode == 0, f"裸环境调用版本闸失败: {proc.stderr[-500:]}"
        assert proc.stdout.strip().endswith("True")


# ── 10. L7：未迁移后果方向 = 延后 8 小时（2026-11-22） ──────────────────


class TestUnmigratedWallClockDirection:
    """方向钉死：旧北京墙钟行被当 UTC 读取时，提醒**延后** 8 小时触发。

    用户约定北京 10:00（= UTC 02:00）提醒；旧写侧把北京墙钟 '10:00:00'
    直接落库。未迁移就被新 UTC 读侧消费时，要等 UTC 时钟走到 10:00
    （= 北京 18:00）才到期 —— 比约定**晚** 8 小时，绝不是提前。
    """

    def test_legacy_wall_clock_reminder_fires_eight_hours_late(self, tmp_path) -> None:
        from shisi.memory.legacy import structured_memory as smod

        sm = smod.StructuredMemory(str(tmp_path / "legacy.db"))
        try:
            # 直接种一条旧写侧北京墙钟行（绕过新 UTC 写入口径，模拟未迁移存量）
            with sm.get_connection(write=True) as conn:
                conn.execute(
                    "INSERT INTO reminders (content, trigger_time, active, triggered, "
                    "status, session_key) VALUES ('起床', '2026-11-21 10:00:00', "
                    "1, 0, 'pending', '1:a')"
                )
                conn.commit()
                rid = conn.execute("SELECT id FROM reminders").fetchone()["id"]
            # 约定时刻（UTC 02:00）及其后 8 小时内都不到期
            assert sm.get_due_reminders("2026-11-21 01:59:59") == []
            assert sm.get_due_reminders("2026-11-21 02:00:00") == []
            assert sm.get_due_reminders("2026-11-21 09:59:59") == []
            # UTC 10:00（= 北京 18:00）才到期 —— 延后 8 小时
            assert [
                r["id"] for r in sm.get_due_reminders("2026-11-21 10:00:00")
            ] == [rid]
        finally:
            sm.close()

    def test_dev_warning_says_delayed_not_early(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        from utils import time_caliber as tc

        for env in ("AI_GF_ENV", "APP_ENV", "ENV"):
            monkeypatch.delenv(env, raising=False)
        sqlite_db = tmp_path / "sqlite.db"
        sqlite3.connect(sqlite_db).close()
        plane_db = tmp_path / "agent_plane.db"
        sqlite3.connect(plane_db).close()
        with caplog.at_level(logging.CRITICAL, logger="utils.time_caliber"):
            tc.ensure_time_caliber_migrated(sqlite_db, plane_db)
        msgs = [r.getMessage() for r in caplog.records if "时间口径" in r.getMessage()]
        assert msgs, "未迁移时必须有一条 CRITICAL 告警"
        assert any("延后 8 小时" in m for m in msgs)
        assert not any("提前 8 小时" in m for m in msgs)


# ── 11. M10：版本闸接入 create_api_app（两入口覆盖，2026-11-22） ────────


class TestAppFactoryGate:
    """python main.py 直启经 create_api_app，也必须过时间口径版本闸。"""

    def test_create_api_app_invokes_gate(self, monkeypatch) -> None:
        import api.app_factory as af

        calls: list[bool] = []

        def _spy() -> bool:
            calls.append(True)
            return True

        monkeypatch.setattr(af, "ensure_time_caliber_migrated", _spy)
        af.create_api_app()
        assert calls == [True]

    @staticmethod
    def _empty_dbs(tmp_path: Path) -> None:
        """造「库文件存在但无迁移标记」的未迁移态（缺文件会被视为无需迁移）。"""
        for name in ("sqlite.db", "agent_plane.db"):
            path = tmp_path / "data" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            sqlite3.connect(path).close()

    def test_create_api_app_fail_closed_in_prod_when_unmigrated(
        self, tmp_path, monkeypatch
    ) -> None:
        import api.app_factory as af
        from utils import time_caliber as tc

        # conftest autouse 对测试面屏蔽了闸（防既有 data 泄漏触发误伤）——
        # 本用例测的就是真闸接线，显式绑回真函数。
        monkeypatch.setattr(af, "ensure_time_caliber_migrated", tc.ensure_time_caliber_migrated)
        monkeypatch.setenv("AI_GF_ENV", "prod")
        # 把闸的默认库路径重定向到 tmp：库文件存在但无标记 → 未迁移
        monkeypatch.setattr(
            tc, "project_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        self._empty_dbs(tmp_path)
        with pytest.raises(RuntimeError, match="时间口径"):
            af.create_api_app()

    def test_create_api_app_warns_but_starts_in_dev_when_unmigrated(
        self, tmp_path, monkeypatch
    ) -> None:
        import api.app_factory as af
        from utils import time_caliber as tc

        # 同上：conftest autouse 屏蔽了闸，本用例测真闸的开发分支，绑回真函数。
        monkeypatch.setattr(af, "ensure_time_caliber_migrated", tc.ensure_time_caliber_migrated)
        for env in ("AI_GF_ENV", "APP_ENV", "ENV"):
            monkeypatch.delenv(env, raising=False)
        monkeypatch.setattr(
            tc, "project_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        self._empty_dbs(tmp_path)
        # 开发/测试环境：只 CRITICAL 告警放行，不得阻断应用创建
        app = af.create_api_app()
        assert app is not None


# ── 12. L9：迁移脚本宿主时区校验（2026-11-22） ──────────────────────────


class TestMigrationHostTimezoneGuard:
    """逐列 -8h 的前提是旧值=北京墙钟；非 UTC+8 宿主默认拒绝 --apply。"""

    def test_guard_accepts_beijing_and_explicit_override(self) -> None:
        module = _load_migration_module()

        module.ensure_beijing_wall_clock_host(480)  # 北京宿主放行
        module.ensure_beijing_wall_clock_host(0, allow_override=True)  # 显式覆盖放行
        with pytest.raises(RuntimeError, match="--allow-non-beijing-host"):
            module.ensure_beijing_wall_clock_host(0)  # UTC 宿主
        with pytest.raises(RuntimeError, match="UTC 偏移 -300"):
            module.ensure_beijing_wall_clock_host(-300)  # 西五区宿主

    @staticmethod
    def _seed_both(module, tmp_path: Path) -> tuple[Path, Path]:
        sqlite_db = tmp_path / "sqlite.db"
        agent_db = tmp_path / "agent_plane.db"
        TestMigrationScript._seed_sqlite(sqlite_db)  # noqa: SLF001
        TestMigrationScript._seed_ledger(agent_db)  # noqa: SLF001
        return sqlite_db, agent_db

    def test_apply_refuses_before_any_write_on_non_beijing_host(
        self, tmp_path, monkeypatch
    ) -> None:
        module = _load_migration_module()
        sqlite_db, agent_db = self._seed_both(module, tmp_path)
        monkeypatch.setattr(module, "host_utc_offset_minutes", lambda: 0)

        with pytest.raises(RuntimeError, match="UTC\\+8"):
            module.apply_with_backup(sqlite_db, agent_db, tmp_path / "backups")

        # 拒绝发生在任何备份/写库动作之前：无备份库文件、两库原样、无迁移标记
        # （ensure_applied 链路的跨进程锁文件允许存在，锁不是备份）
        assert not list((tmp_path / "backups").glob("*.db"))
        assert sqlite3.connect(sqlite_db).execute(
            "SELECT last_active_at FROM topic_state"
        ).fetchone()[0] == "2026-11-21 10:00:00"
        assert sqlite3.connect(agent_db).execute(
            "SELECT created_at FROM event_ledger"
        ).fetchone()[0] == "2026-11-21T10:00:00+08:00"

    def test_apply_override_flag_allows_non_beijing_host(
        self, tmp_path, monkeypatch
    ) -> None:
        module = _load_migration_module()
        sqlite_db, agent_db = self._seed_both(module, tmp_path)
        monkeypatch.setattr(module, "host_utc_offset_minutes", lambda: 0)

        result = module.apply_with_backup(
            sqlite_db, agent_db, tmp_path / "backups", allow_non_beijing_host=True
        )
        assert result["sqlite"]["applied"] is True
        assert sqlite3.connect(sqlite_db).execute(
            "SELECT last_active_at FROM topic_state"
        ).fetchone()[0] == "2026-11-21 02:00:00"

    def test_dry_run_report_states_wall_clock_assumption(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        module = _load_migration_module()
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        sqlite_db, agent_db = self._seed_both(module, tmp_path / "data")
        monkeypatch.setattr(
            module, "project_path", lambda *parts: tmp_path.joinpath(*parts)
        )

        rc = module.main(["--dry-run"])
        report = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert report["status"] == "dry_run"
        assert "北京墙钟" in report["assumption"]
        assert isinstance(report["host_utc_offset_minutes"], int)
        assert report["sqlite"]["db"].endswith("sqlite.db")
        assert sqlite_db.exists() and agent_db.exists()

    def test_apply_via_cli_refuses_non_beijing_host(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        module = _load_migration_module()
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        sqlite_db, _agent_db = self._seed_both(module, tmp_path / "data")
        monkeypatch.setattr(
            module, "project_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        monkeypatch.setattr(module, "host_utc_offset_minutes", lambda: 0)

        with pytest.raises(RuntimeError, match="--allow-non-beijing-host"):
            module.main(["--apply"])
        # 拒绝后库保持未迁移原样
        assert sqlite3.connect(sqlite_db).execute(
            "SELECT last_active_at FROM topic_state"
        ).fetchone()[0] == "2026-11-21 10:00:00"

    def test_apply_via_cli_override_flag_migrates(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        module = _load_migration_module()
        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        sqlite_db, _agent_db = self._seed_both(module, tmp_path / "data")
        monkeypatch.setattr(
            module, "project_path", lambda *parts: tmp_path.joinpath(*parts)
        )
        monkeypatch.setattr(module, "host_utc_offset_minutes", lambda: 0)

        rc = module.main(["--allow-non-beijing-host", "--apply"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "applied"
        assert sqlite3.connect(sqlite_db).execute(
            "SELECT last_active_at FROM topic_state"
        ).fetchone()[0] == "2026-11-21 02:00:00"

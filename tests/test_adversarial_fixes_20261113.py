"""2026-11-13 对抗验收确认阻塞的回归测试。

覆盖（全部为独立复现过的 high/medium）：
- H1：删号 / 每日 TTL 对未迁移旧表 ``topic_plan`` 生效（不再删号复活）；
- M6：旧表缺 ``consumed_at`` 列时自动迁移不炸、写路径不永久 db_error；
- M5：``scripts/topic_plan_migration.py`` 可直接按文档命令运行；
- M1：空 ``character_id`` 不再归一 ``default`` 桶（跨角色串写）；
- H2/M3：提醒部分受理 → 只记前缀 + 尾段续投，不整条重发；
- M2：claimed 晚到回执再等一个窗口，不把已送达判成失败；
- H4：``complete_send`` 只允许当前认领者写回执、不得覆写终态；
- H5：``renew_claim`` 续租，慢语音不被 reap 重投；
- M4：同一好友的出站行在控制面跨进程互斥（语音/文字不交错）；
- H3：广播脚本按三元组解包 ``send_proactive_voice``。
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from proactive import reminder_delivery as rd
from proactive import runtime_plane as rp
from utils import topic_state as ts

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _plane_sandbox(tmp_path):
    rp.set_db_path(tmp_path / "runtime_plane.db")
    yield
    rp.set_db_path(None)


@pytest.fixture()
def topic_db(tmp_path, monkeypatch):
    db = tmp_path / "topic_state.db"
    cfg = tmp_path / "scheduler_config.json"
    cfg.write_text(json.dumps({"topic_state": {"enabled": True}}), encoding="utf-8")
    monkeypatch.setattr(ts, "_DB_PATH", db)
    monkeypatch.setattr(ts, "_CONFIG_PATH", cfg)
    yield db
    ts._DB_PATH = None  # type: ignore[assignment]


# ── H1 / M6：旧表清理与迁移容错 ──────────────────────────────


def _legacy_db(path: Path, *, with_consumed: bool = True) -> None:
    """构造 v1.58 形态的存量库：完整新 schema + 旧 ``topic_plan``。"""
    consumed = ", consumed_at TEXT" if with_consumed else ""
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(ts._SCHEMA)
        conn.executescript(
            f"""
            CREATE TABLE topic_plan (
                session_key TEXT NOT NULL,
                character_id TEXT NOT NULL,
                topic TEXT NOT NULL DEFAULT '',
                intent TEXT NOT NULL DEFAULT '',
                planned_at TEXT NOT NULL,
                next_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
                {consumed},
                PRIMARY KEY (session_key, character_id)
            );
            """
        )
        conn.commit()


def test_purge_owner_and_count_owner_cover_legacy_topic_plan(topic_db):
    """H1：删号必须能清掉未迁移旧表，且 verify 如实报告 0。"""
    _legacy_db(topic_db)
    with closing(sqlite3.connect(topic_db)) as conn:
        conn.execute(
            "INSERT INTO topic_plan VALUES "
            "('1:victim@im.wechat','c1','被删账号的旧计划','',"
            "'2026-11-13 00:00:00','2026-11-13 00:00:00','2026-12-01 00:00:00',NULL)"
        )
        conn.commit()

    assert ts.purge_owner(
        ["1:victim@im.wechat"], db_path=topic_db, owner_uid=1
    ) == 1
    with closing(sqlite3.connect(topic_db)) as conn:
        left = conn.execute("SELECT COUNT(*) FROM topic_plan").fetchone()[0]
    assert left == 0
    assert ts.count_owner(
        ["1:victim@im.wechat"], db_path=topic_db, owner_uid=1
    ) == 0


def test_purge_expired_covers_legacy_topic_plan(topic_db):
    """H1：部署后首个写路径前，每日 TTL 也要清到旧表。"""
    _legacy_db(topic_db)
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    with closing(sqlite3.connect(topic_db)) as conn:
        conn.execute(
            "INSERT INTO topic_plan VALUES "
            f"('1:old@im.wechat','c1','过期计划','','{old}','{old}','{old}',NULL)"
        )
        conn.commit()

    assert ts.purge_expired(force=True) >= 1
    with closing(sqlite3.connect(topic_db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_plan").fetchone()[0] == 0


def test_auto_migration_tolerates_legacy_plan_without_consumed_at(topic_db):
    """M6：缺列旧表不再让整条写路径永久 db_error。"""
    _legacy_db(topic_db, with_consumed=False)
    with closing(sqlite3.connect(topic_db)) as conn:
        conn.execute(
            "INSERT INTO topic_plan VALUES "
            "('k1','c1','旧话题','旧意图',"
            "'2026-11-13 00:00:00','2026-11-13 00:00:00','2026-12-01 00:00:00')"
        )
        conn.commit()

    with closing(ts._connect(ensure_schema=True)) as conn:
        row = conn.execute(
            "SELECT session_key, topic, consumed_at FROM thread_plan "
            "WHERE session_key='k1'"
        ).fetchone()
    assert row is not None
    assert row["topic"] == "旧话题"
    assert row["consumed_at"] is None
    assert ts.save_next_plan("k1", "c1", "新话题", "新意图") is not None


def test_topic_plan_migration_script_runs_without_pythonpath(tmp_path):
    """M5：报告宣称的部署前演练命令必须可直接运行。"""
    db = tmp_path / "migration.db"
    _legacy_db(db)
    proc = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "topic_plan_migration.py"),
            "status",
            "--db",
            str(db),
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "topic_plan" in proc.stdout


# ── M1：空 character_id 不写 default 桶 ──────────────────────


def test_blank_character_id_is_noop_not_default_bucket(topic_db):
    blank = ts.update_after_turn("10:web:hexA", "   ", "空白角色消息", turn_id="x1")
    assert blank["action"] == "empty"
    assert ts.save_next_plan("10:web:hexA", "   ", "计划", "意图") is None
    assert ts.take_next_plan("10:web:hexA", "   ") is None
    assert ts.mark_topic_recalled("10:web:hexA", "   ", "tid") is False
    assert ts.get_topic_snapshot("10:web:hexA", "   ").get("degraded") is True

    with closing(ts._connect(ensure_schema=True)) as conn:
        rows = conn.execute(
            "SELECT COUNT(*) FROM topic_state WHERE character_id='default'"
        ).fetchone()[0]
        plans = conn.execute(
            "SELECT COUNT(*) FROM thread_plan WHERE character_id='default'"
        ).fetchone()[0]
    assert rows == 0 and plans == 0

    # 显式 default（内置十四）仍然可用。
    explicit = ts.update_after_turn("10:web:hexA", "default", "显式角色消息", turn_id="x2")
    assert explicit["action"] != "empty"


# ── H2 / M3：提醒部分受理 → 前缀 + 尾段续投 ─────────────────


def _allow_consent(monkeypatch) -> None:
    import api.consent as consent

    async def _allow(_session_key: str) -> bool:
        return True

    monkeypatch.setattr(consent, "outbound_allowed_for_session", _allow)


class _SM:
    def __init__(self) -> None:
        self.results: list[tuple[Any, bool]] = []
        self.added: list[tuple[Any, ...]] = []

    def mark_reminder_result(self, reminder_id, delivered):
        self.results.append((reminder_id, delivered))

    def add_reminder(self, content, trigger_time=None, session_key="", user_id=None):
        self.added.append((content, trigger_time, session_key, user_id))
        return 1


class _Mem:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record_outbound_message(self, **kwargs):
        self.calls.append(kwargs)
        return True


class _Plane:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.calls = 0

    def enqueue_send(self, **_kwargs):
        return 1

    def wait_for_receipt(self, _cid, _timeout):
        self.calls += 1
        return self.row


def _reminder_task(plane, sm, mem, text: str) -> rd.ReminderDeliveryTask:
    task = rd.ReminderDeliveryTask(
        structured_memory=sm,
        llm=None,
        character_id_resolver=lambda _sk: "micai",
        memory=mem,
        plane=plane,
    )

    async def _stub_compose(_reminder, *, character_id):
        return text

    task._compose_text = _stub_compose  # type: ignore[method-assign]
    return task


def test_reminder_partial_receipt_requeues_remainder_not_full_retry(monkeypatch):
    """H2：部分受理只记前缀 + 尾段续投，原提醒不整条重发。"""
    _allow_consent(monkeypatch)
    sm, mem = _SM(), _Mem()
    plane = _Plane(
        {"status": "accepted", "accepted_text": "记得明天上午", "fail_reason": ""}
    )
    task = _reminder_task(plane, sm, mem, "记得明天上午\n去体检")
    asyncio.run(task._deliver({"id": 7, "session_key": "4:web:wire", "content": "体检"}))

    assert sm.results == [(7, True)]
    assert mem.calls and mem.calls[0]["message"] == "记得明天上午"
    assert sm.added and sm.added[0][0] == "去体检"
    assert sm.added[0][2] == "4:web:wire"


def test_reminder_failed_receipt_with_prefix_consumes_prefix(monkeypatch):
    """M3：失败回执带回的前缀不能再整条重发（前缀重复）。"""
    _allow_consent(monkeypatch)
    sm, mem = _SM(), _Mem()
    plane = _Plane(
        {"status": "failed", "accepted_text": "第一句", "fail_reason": "send_api_rejected"}
    )
    task = _reminder_task(plane, sm, mem, "第一句\n第二句")
    asyncio.run(task._deliver({"id": 8, "session_key": "4:web:wire", "content": "提醒"}))

    assert sm.results == [(8, True)]
    assert mem.calls and mem.calls[0]["message"] == "第一句"
    assert sm.added and sm.added[0][0] == "第二句"


def test_reminder_claimed_timeout_waits_one_more_window(monkeypatch):
    """M2：宿主已 claimed 时，第 31s 的晚到回执不能被当成失败。"""
    class _LatePlane(_Plane):
        def __init__(self) -> None:
            super().__init__({})
            self.second = {
                "status": "accepted", "accepted_text": "整句提醒", "fail_reason": "",
            }

        def wait_for_receipt(self, _cid, _timeout):
            self.calls += 1
            return {"status": "claimed"} if self.calls == 1 else self.second

    task = rd.ReminderDeliveryTask(structured_memory=_SM(), llm=None)
    plane = _LatePlane()
    task._plane = plane
    ok, body, complete = asyncio.run(
        task._send_via_plane("7:web:abc", "整句提醒", "micai", receipt_timeout=0.01)
    )
    assert ok is True and complete is True and body == "整句提醒"
    assert plane.calls == 2


# ── H4 / H5 / M4：控制面回执与并发 ───────────────────────────


def test_complete_send_rejects_non_owner_and_terminal_overwrite():
    """H4：只有当前认领者能写回执，且终态不可覆写。"""
    cid = rp.enqueue_send(
        kind="proactive", channel="websocket", session_key="k1", message="m",
    )
    row = rp.claim_next_send(channel="websocket")
    assert row is not None and row["id"] == cid

    assert rp.complete_send(cid, ok=False, claimed_by="other-host") is False
    assert rp.send_status(cid)["status"] == "claimed"

    assert rp.complete_send(
        cid, ok=True, claimed_by=str(row["claimed_by"])
    ) is True
    assert rp.send_status(cid)["status"] == "accepted"
    # 迟到的失败回执不得把 accepted 覆写。
    assert rp.complete_send(
        cid, ok=False, claimed_by=str(row["claimed_by"])
    ) is False
    assert rp.send_status(cid)["status"] == "accepted"


def test_renew_claim_extends_and_rejects_other_host():
    """H5：慢 TTS 期间续租，其他宿主不能续。"""
    cid = rp.enqueue_send(
        kind="proactive_voice", channel="wechat", session_key="S", message="语音",
    )
    row = rp.claim_next_send(channel="wechat")
    assert row is not None
    before = float(row["claim_expires"])
    assert rp.renew_claim(
        cid, ttl=180.0, claimed_by=str(row["claimed_by"])
    ) is True
    after = float(rp.send_status(cid)["claim_expires"])
    assert after >= before + 100.0
    assert rp.renew_claim(cid, ttl=180.0, claimed_by="other-host") is False


def test_claim_next_send_serializes_same_peer_across_hosts():
    """M4：同好友的语音/文字行不能被不同 worker 同时认领。"""
    voice = rp.enqueue_send(
        kind="proactive_voice", channel="wechat", session_key="S:peer", message="语音",
    )
    text = rp.enqueue_send(
        kind="proactive", channel="wechat", session_key="S:peer", message="文字",
    )
    other = rp.enqueue_send(
        kind="proactive", channel="wechat", session_key="S:other", message="别的",
    )
    first = rp.claim_next_send(channel="wechat")
    assert first is not None and first["id"] == voice
    # 同 peer 的第二条被互斥挡住；不同 peer 可以继续认领。
    second = rp.claim_next_send(channel="wechat")
    assert second is not None and second["id"] == other
    assert rp.send_status(text)["status"] == "pending"


# ── H3：广播脚本契约 ─────────────────────────────────────────


def test_broadcast_script_unpacks_three_tuple():
    path = PROJECT_ROOT / "scripts" / "broadcast_proactive_voice.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        call = node.value
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "send_proactive_voice"
        ):
            assert isinstance(node.targets[0], ast.Tuple)
            assert len(node.targets[0].elts) == 3
            return
    raise AssertionError("broadcast_proactive_voice.py 未找到 send_proactive_voice 解包")

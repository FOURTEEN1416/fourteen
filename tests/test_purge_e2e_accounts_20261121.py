"""e2e/测试账号清理脚本红测（2026-11-21 主动线治理批）。

契约：
- 默认 ``--dry-run``：只列 4 库受影响表与键，零删除、零备份；
- ``--apply``：先备份到 ``data/archive/``（每库可读快照 + scheduler_config.json），
  再事务删除；
- 覆盖 runtime_plane 全部表、user_character_settings、emotion_stage_state、
  vital_signs_state、agent_core 三表与 4 个节流账本段；
- event_ledger 含 ``channel:<uid>:<slot>`` 通道状态事件（channel_state）；
- 私有角色实例（卡文件/知识文件/精确 cid 成就行）随账号出库，公共卡保留；
- 冷停闸门：目标账号 channel_presence 活跃时 ``--apply`` 拒绝且零删除；
- fail-closed：验证阶段任何计数读失败（-1/异常）都判不通过，绝不假通过；
- 删除后零残留验证；二次执行幂等；正常账号（非测试命名空间）零误伤；
- 产出 DELETION_LOG 条目文本。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "purge_e2e_accounts.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("purge_e2e_accounts", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_dbs(tmp_path: Path) -> dict[str, Path]:
    users = tmp_path / "users.db"
    conn = sqlite3.connect(str(users))
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, email TEXT, last_login_at TEXT);
        CREATE TABLE user_sessions (id INTEGER PRIMARY KEY, user_id INTEGER, refresh_token_hash TEXT);
        CREATE TABLE consent_records (id INTEGER PRIMARY KEY, user_id INTEGER, agreement_version TEXT);
        CREATE TABLE wechat_bindings (id INTEGER PRIMARY KEY, user_id INTEGER, wxid TEXT);
        CREATE TABLE wechat_channel_sessions (id INTEGER PRIMARY KEY, user_id INTEGER);
        CREATE TABLE wechat_peer_preferences (id INTEGER PRIMARY KEY, owner_user_id INTEGER, peer_wxid TEXT);
        CREATE TABLE user_active_characters (id INTEGER PRIMARY KEY, user_id INTEGER, character_id TEXT);
        CREATE TABLE announcement_reads (id INTEGER PRIMARY KEY, user_id INTEGER, announcement_id INTEGER);
        CREATE TABLE operator_prefs (user_id INTEGER PRIMARY KEY, global_view_enabled INTEGER);
        CREATE TABLE announcements (id INTEGER PRIMARY KEY, created_by INTEGER, title TEXT);
        CREATE TABLE invite_codes (code TEXT PRIMARY KEY, created_by INTEGER, used_by INTEGER);
        CREATE TABLE operator_audit (id INTEGER PRIMARY KEY, admin_id INTEGER, action TEXT);
        CREATE TABLE character_achievements (id INTEGER PRIMARY KEY, character_id TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO users VALUES (?,?,?,?)",
        [
            (9, "wbtest-nine", "nine@test.local", "2026-11-20 10:00:00"),
            (10, "real-user", "real@example.com", "2026-11-20 10:00:00"),
        ],
    )
    for table, col in (
        ("user_sessions", "user_id"),
        ("consent_records", "user_id"),
        ("wechat_bindings", "user_id"),
        ("wechat_channel_sessions", "user_id"),
        ("wechat_peer_preferences", "owner_user_id"),
        ("user_active_characters", "user_id"),
        ("announcement_reads", "user_id"),
    ):
        conn.execute(f"INSERT INTO {table} ({col}) VALUES (9)")
        conn.execute(f"INSERT INTO {table} ({col}) VALUES (10)")
    conn.execute("INSERT INTO operator_prefs VALUES (9,1)")
    conn.execute("INSERT INTO operator_prefs VALUES (10,1)")
    conn.execute("INSERT INTO announcements VALUES (1,10,'keep')")
    conn.execute("INSERT INTO invite_codes VALUES ('x',9,9)")
    conn.execute("INSERT INTO operator_audit VALUES (1,9,'x')")
    # 角色成就：uid 前缀形态必须随测试账号清除，正常账号/无关行保留。
    conn.execute("INSERT INTO character_achievements VALUES (1,'9:card')")
    conn.execute("INSERT INTO character_achievements VALUES (2,'10:card')")
    conn.execute("INSERT INTO character_achievements VALUES (3,'7:card')")
    conn.commit()
    conn.close()

    memory = tmp_path / "sqlite.db"
    conn = sqlite3.connect(str(memory))
    conn.executescript(
        """
        CREATE TABLE chat_history (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT, content TEXT,
            session_id TEXT, created_at TEXT);
        CREATE TABLE topic_state (session_key TEXT, character_id TEXT, topic_id TEXT,
            title TEXT, status TEXT, followup_count INTEGER DEFAULT 0, last_followup_at TEXT DEFAULT '');
        CREATE TABLE thread_plan (session_key TEXT, character_id TEXT, topic TEXT);
        CREATE TABLE topic_meta (session_key TEXT, character_id TEXT);
        CREATE TABLE user_character_settings (user_key TEXT, character_id TEXT, config TEXT);
        CREATE TABLE emotion_stage_state (character_id TEXT PRIMARY KEY);
        CREATE TABLE vital_signs_state (character_id TEXT PRIMARY KEY);
        CREATE TABLE agent_events (id INTEGER PRIMARY KEY AUTOINCREMENT, user_key TEXT,
            session_key TEXT, owner_user_id INTEGER);
        CREATE TABLE agent_threads (thread_id TEXT PRIMARY KEY, user_key TEXT,
            session_key TEXT, owner_user_id INTEGER);
        CREATE TABLE agent_commitments (commitment_id TEXT PRIMARY KEY, user_key TEXT,
            session_key TEXT, owner_user_id INTEGER);
        CREATE TABLE user_profile (user_key TEXT);
        CREATE TABLE user_proactive_config (user_key TEXT);
        CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, session_key TEXT, user_id INTEGER);
        CREATE TABLE pending_intents (id INTEGER PRIMARY KEY AUTOINCREMENT, session_key TEXT, user_id INTEGER);
        CREATE TABLE user_facts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_key TEXT);
        CREATE TABLE fact_deletion_watermarks (user_key TEXT, fact TEXT);
        CREATE TABLE memory_extraction_progress (session_id TEXT, character_id TEXT);
        CREATE TABLE reflections (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT);
        CREATE TABLE daily_summaries (date TEXT);
        CREATE TABLE memory_recycle_bin (id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT);
        CREATE TABLE user_character_overrides (user_id INTEGER, character_id TEXT);
        CREATE TABLE user_persona (user_id INTEGER);
        CREATE TABLE user_persona_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER);
        """
    )
    for key in ("9:web:abc", "10:web:def"):
        conn.execute("INSERT INTO chat_history (role,content,session_id,created_at) VALUES ('user','x',?,'2026-11-21')", (key,))
        conn.execute("INSERT INTO topic_state VALUES (?, 'default', 't', '话题', 'active', 1, '2026-11-21')", (key,))
        conn.execute("INSERT INTO thread_plan VALUES (?, 'default', '话题')", (key,))
        conn.execute("INSERT INTO topic_meta VALUES (?, 'default')", (key,))
        conn.execute("INSERT INTO user_character_settings VALUES (?, 'default', '{}')", (key,))
        conn.execute("INSERT INTO emotion_stage_state VALUES (?)", (f"{key}::default",))
        conn.execute("INSERT INTO vital_signs_state VALUES (?)", (f"{key}::default",))
        conn.execute("INSERT INTO agent_events (user_key,session_key,owner_user_id) VALUES (?,?,?)", (key, key, int(key.split(':')[0])))
        conn.execute("INSERT INTO agent_threads VALUES (?,?,?,?)", ("t" + key, key, key, int(key.split(':')[0])))
        conn.execute("INSERT INTO agent_commitments VALUES (?,?,?,?)", ("c" + key, key, key, int(key.split(':')[0])))
        conn.execute("INSERT INTO user_profile VALUES (?)", (key,))
        conn.execute("INSERT INTO user_proactive_config VALUES (?)", (key,))
        conn.execute("INSERT INTO reminders (session_key,user_id) VALUES (?,?)", (key, int(key.split(':')[0])))
        conn.execute("INSERT INTO pending_intents (session_key,user_id) VALUES (?,?)", (key, int(key.split(':')[0])))
        conn.execute("INSERT INTO user_facts (user_key) VALUES (?)", (key,))
        conn.execute("INSERT INTO fact_deletion_watermarks VALUES (?,'f')", (key,))
        conn.execute("INSERT INTO memory_extraction_progress VALUES (?,'default')", (key,))
        conn.execute("INSERT INTO reflections (session_id) VALUES (?)", (key,))
        conn.execute("INSERT INTO daily_summaries VALUES (?)", (f"{key}|2026-11-21",))
        conn.execute("INSERT INTO memory_recycle_bin (character_id) VALUES (?)", (f"user_fact:{key}%x",))
        conn.execute("INSERT INTO user_character_overrides VALUES (?, 'default')", (int(key.split(':')[0]),))
        conn.execute("INSERT INTO user_persona VALUES (?)", (int(key.split(':')[0]),))
        conn.execute("INSERT INTO user_persona_snapshots (user_id) VALUES (?)", (int(key.split(':')[0]),))
    conn.commit()
    conn.close()

    agent = tmp_path / "agent_plane.db"
    conn = sqlite3.connect(str(agent))
    conn.execute(
        "CREATE TABLE event_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, "
        "session_key TEXT, character_id TEXT, event_type TEXT, turn_id TEXT, reply_id TEXT, "
        "actor TEXT, payload_json TEXT, created_at TEXT)"
    )
    for key in ("9:web:abc", "10:web:def"):
        conn.execute(
            "INSERT INTO event_ledger (event_id,session_key,event_type,created_at) "
            "VALUES (?,?, 'x','2026-11-21')",
            ("e" + key, key),
        )
    # channel_state 事件键形是 `channel:<uid>:<slot>`，必须纳入清理与验证。
    for key in ("channel:9:0", "channel:10:0"):
        conn.execute(
            "INSERT INTO event_ledger (event_id,session_key,event_type,created_at) "
            "VALUES (?,?, 'channel_state','2026-11-21')",
            ("e" + key, key),
        )
    conn.commit()
    conn.close()

    runtime = tmp_path / "runtime_plane.db"
    from proactive import runtime_plane

    runtime_plane.set_db_path(runtime)
    with runtime_plane._conn():
        pass
    conn = sqlite3.connect(str(runtime))
    for uid, key in ((9, "9:web:abc"), (10, "10:web:def")):
        conn.execute("INSERT INTO channel_desired_state VALUES (?,0,'connected',1)", (uid,))
        conn.execute("INSERT INTO channel_presence VALUES (?,0,'pid',1)", (uid,))
        conn.execute(
            "INSERT INTO outbound_commands (kind,channel,session_key,owner_id,message,status,created_at) "
            "VALUES ('proactive','websocket',?,?,'x','pending',1)",
            (key, uid),
        )
        conn.execute(
            "INSERT INTO control_commands (kind,session_key,status,created_at) "
            "VALUES ('proactive_manual',?,'pending',1)", (key,)
        )
        conn.execute(
            "INSERT INTO inbound_seen VALUES (?,?,'done',1)", ("m" + key, key)
        )
        conn.execute(
            "INSERT INTO followup_budget VALUES (?,'2026-11-21',1,1)", (key,)
        )
        conn.execute(
            "INSERT INTO effect_dedup VALUES (?,'1',1)", (f"set_reminder:{key}:m:abc",)
        )
    conn.commit()
    conn.close()
    runtime_plane.set_db_path(None)

    cfg = tmp_path / "scheduler_config.json"
    cfg.write_text(
        json.dumps(
            {
                "throttle": {
                    "llm_proactive_next_ok": {"9:web:abc": 1.0, "10:web:def": 1.0},
                    "deliver_fail_counts": {"9:web:abc": 5, "10:web:def": 1},
                    "deliver_fail_counts_ts": {"9:web:abc": 1.0, "10:web:def": 1.0},
                    "disabled_event_day": {"9:web:abc": "2026-11-21", "10:web:def": "2026-11-21"},
                    "wait_window_event_day": {"9:web:abc": "2026-11-21", "10:web:def": "2026-11-21"},
                    "quiet_event_day": {"9:web:abc": "2026-11-21", "10:web:def": "2026-11-21"},
                    "model_degraded_event_day": {"9:web:abc": "2026-11-21", "10:web:def": "2026-11-21"},
                    "gate_skip_event_day": {
                        "9:web:abc|unanswered_limit": "2026-11-21",
                        "10:web:def|unanswered_limit": "2026-11-21",
                    },
                    "deliver_circuit_open": {"9:web:abc": 9.0, "10:web:def": 9.0},
                    "important_dates_sent": [
                        "2026-11-21|9:web:abc|生日",
                        "2026-11-21|10:web:def|生日",
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    return {
        "users": users, "memory": memory, "agent": agent,
        "runtime": runtime, "config": cfg,
    }


def _args(dbs: dict[str, Path], archive: Path, *, apply: bool) -> list[str]:
    args = [
        "--users-db", str(dbs["users"]),
        "--sqlite-db", str(dbs["memory"]),
        "--agent-db", str(dbs["agent"]),
        "--runtime-db", str(dbs["runtime"]),
        "--scheduler-config", str(dbs["config"]),
        "--archive-dir", str(archive),
    ]
    if apply:
        args.append("--apply")
    return args


def _count(db: Path, sql: str) -> int:
    conn = sqlite3.connect(str(db))
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


def test_dry_run_does_not_delete_or_backup(tmp_path, capsys):
    dbs = _build_dbs(tmp_path)
    archive = tmp_path / "archive"
    mod = _load_script()
    rc = mod.main(_args(dbs, archive, apply=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "dry-run" in out.lower()
    assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=9") == 1
    assert _count(dbs["memory"], "SELECT COUNT(*) FROM chat_history WHERE session_id='9:web:abc'") == 1
    assert not archive.exists()


def test_apply_purges_all_four_dbs_with_backup_and_verification(tmp_path, capsys, monkeypatch):
    from proactive import ase_hub
    from wechat_direct import channel_paths

    dbs = _build_dbs(tmp_path)

    # 微信通道：uid 9 有凭证目录，uid 10 必须保留。
    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    for uid in (9, 10):
        slot = channel_paths.session_dir(uid, 0)
        slot.mkdir(parents=True, exist_ok=True)
        (slot / "credentials.json").write_text("{}", encoding="utf-8")

    # ASE：uid 9/10 各一条索引 + 状态文件。
    ase_dir = tmp_path / "ase_states"
    ase_dir.mkdir()
    index: dict[str, str] = {}
    # `9:orphan` 只存在于 ASE 索引（DB 已无引用），也必须按 owner 清除。
    for key in ("9:web:abc", "10:web:def", "9:orphan"):
        state = ase_dir / f"{key.replace(':', '_')}.json"
        state.write_text("{}", encoding="utf-8")
        index[key] = str(state)
    (ase_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")
    monkeypatch.setattr(ase_hub, "_STATE_DIR", ase_dir)
    monkeypatch.setattr(ase_hub, "_INDEX_PATH", ase_dir / "index.json")

    archive = tmp_path / "archive"
    mod = _load_script()
    rc = mod.main(_args(dbs, archive, apply=True))
    out = capsys.readouterr().out
    assert rc == 0, out

    assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=9") == 0
    assert _count(dbs["users"], "SELECT COUNT(*) FROM wechat_bindings WHERE user_id=9") == 0
    assert _count(dbs["memory"], "SELECT COUNT(*) FROM chat_history WHERE session_id='9:web:abc'") == 0
    assert _count(dbs["memory"], "SELECT COUNT(*) FROM user_character_settings WHERE user_key='9:web:abc'") == 0
    assert _count(dbs["memory"], "SELECT COUNT(*) FROM emotion_stage_state WHERE character_id='9:web:abc::default'") == 0
    assert _count(dbs["memory"], "SELECT COUNT(*) FROM vital_signs_state WHERE character_id='9:web:abc::default'") == 0
    assert _count(dbs["runtime"], "SELECT COUNT(*) FROM outbound_commands WHERE owner_id=9") == 0
    assert _count(dbs["agent"], "SELECT COUNT(*) FROM event_ledger WHERE session_key='9:web:abc'") == 0
    assert _count(dbs["agent"], "SELECT COUNT(*) FROM event_ledger WHERE session_key='channel:9:0'") == 0

    # 完整删除链：角色成就前缀行清除、审计行留存、微信目录与 ASE 状态清除。
    assert _count(dbs["users"], "SELECT COUNT(*) FROM character_achievements WHERE character_id='9:card'") == 0
    # 无关 uid（7 不在目标集合）的成就行必须零误伤保留。
    assert _count(dbs["users"], "SELECT COUNT(*) FROM character_achievements WHERE character_id='7:card'") == 1
    assert _count(dbs["users"], "SELECT COUNT(*) FROM operator_audit WHERE admin_id=9") == 1
    assert not (channel_paths.sessions_root() / "9").exists()
    assert channel_paths.session_dir(10, 0).exists()
    remain_index = json.loads((ase_dir / "index.json").read_text(encoding="utf-8"))
    assert list(remain_index) == ["10:web:def"]

    # 正常账号零误伤
    assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=10") == 1
    assert _count(dbs["users"], "SELECT COUNT(*) FROM character_achievements WHERE character_id='10:card'") == 1
    assert _count(dbs["memory"], "SELECT COUNT(*) FROM chat_history WHERE session_id='10:web:def'") == 1
    assert _count(dbs["runtime"], "SELECT COUNT(*) FROM outbound_commands WHERE owner_id=10") == 1
    assert _count(dbs["agent"], "SELECT COUNT(*) FROM event_ledger WHERE session_key='channel:10:0'") == 1

    backups = list(archive.rglob("*.db"))
    assert {p.name for p in backups} == {"users.db", "sqlite.db", "agent_plane.db", "runtime_plane.db"}
    config_backups = list(archive.rglob("scheduler_config.json"))
    assert len(config_backups) == 1, "scheduler_config.json 必须随备份留存"
    saved_cfg = json.loads(config_backups[0].read_text(encoding="utf-8"))
    assert list(saved_cfg["throttle"]["llm_proactive_next_ok"]) == ["9:web:abc", "10:web:def"]
    active_cfg = json.loads(dbs["config"].read_text(encoding="utf-8"))
    assert list(active_cfg["throttle"]["llm_proactive_next_ok"]) == ["10:web:def"]
    assert (archive / "DELETION_LOG_ENTRY.md").exists()
    log_text = (archive / "DELETION_LOG_ENTRY.md").read_text(encoding="utf-8")
    assert "wbtest" in log_text or "e2e" in log_text.lower()
    assert "scheduler_config.json" in log_text

    # 二次执行幂等
    rc2 = mod.main(_args(dbs, archive, apply=True))
    assert rc2 == 0


def test_explicit_user_id_can_purge_non_namespace_account(tmp_path):
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    rc = mod.main(_args(dbs, tmp_path / "archive2", apply=True) + ["--user-id", "10"])
    # 10 不是测试命名空间，但显式 --user-id 必须允许（运维手工处置）
    assert rc == 0
    assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=10") == 0


def test_apply_purges_private_character_instances(tmp_path):
    """私有角色实例随账号出库：卡文件/知识文件/精确 cid 成就行，公共卡保留。"""
    dbs = _build_dbs(tmp_path)
    chars = tmp_path / "characters"
    know = tmp_path / "knowledge"
    chars.mkdir()
    know.mkdir()
    # user 9 私有卡：卡 id 故意不带 uid 前缀（对照 lifecycle 按 cid 精确删除）。
    (chars / "9priv.json").write_text(
        json.dumps({"id": "9priv", "user_id": "9", "name": "n9"}), encoding="utf-8"
    )
    (know / "9priv.json").write_text("{}", encoding="utf-8")
    # user 10 私有卡与无主公共卡必须保留。
    (chars / "10priv.json").write_text(
        json.dumps({"id": "10priv", "user_id": "10"}), encoding="utf-8"
    )
    (chars / "pub.json").write_text(
        json.dumps({"id": "pub", "user_id": ""}), encoding="utf-8"
    )
    conn = sqlite3.connect(str(dbs["users"]))
    conn.execute("INSERT INTO character_achievements VALUES (10, '9priv')")
    conn.commit()
    conn.close()

    mod = _load_script()
    rc = mod.main(
        _args(dbs, tmp_path / "archive", apply=True)
        + ["--characters-dir", str(chars), "--knowledge-dir", str(know)]
    )
    assert rc == 0
    assert not (chars / "9priv.json").exists()
    assert not (know / "9priv.json").exists()
    assert _count(
        dbs["users"],
        "SELECT COUNT(*) FROM character_achievements WHERE character_id='9priv'",
    ) == 0
    # 零误伤：他人私有卡、无主公共卡、无关成就行保留。
    assert (chars / "10priv.json").exists()
    assert (chars / "pub.json").exists()
    assert _count(
        dbs["users"],
        "SELECT COUNT(*) FROM character_achievements WHERE character_id='7:card'",
    ) == 1


def test_apply_rejected_when_channel_presence_active(tmp_path):
    """冷停闸门：目标账号 presence 活跃（服务在跑）时 --apply 必须拒绝且零删除。"""
    from proactive import runtime_plane

    dbs = _build_dbs(tmp_path)
    runtime_plane.set_db_path(dbs["runtime"])
    runtime_plane.heartbeat(9, 0, "live-host")  # 活跃连接器宿主（服务未冷停）
    mod = _load_script()
    try:
        rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
        assert rc != 0
        # 未冷停 → 不许动任何数据、不许备份。
        assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=9") == 1
        assert _count(
            dbs["memory"],
            "SELECT COUNT(*) FROM chat_history WHERE session_id='9:web:abc'",
        ) == 1
        assert not (tmp_path / "archive").exists()
    finally:
        runtime_plane.presence_release(9, 0)
        runtime_plane.set_db_path(None)


def test_apply_fails_closed_when_count_read_fails(tmp_path, capsys, monkeypatch):
    """fail-closed：验证阶段任何计数读失败（-1）都必须判不通过，绝不假通过。"""
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    # _throttle_count 只在清单/验证阶段消费，不影响删除事务本身。
    monkeypatch.setattr(mod, "_throttle_count", lambda *a, **k: -1)
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    captured = capsys.readouterr()
    assert rc != 0
    assert "零残留验证通过" not in captured.out


def test_apply_fails_closed_when_owner_count_raises(tmp_path, capsys, monkeypatch):
    """fail-closed：runtime_plane.count_owner 异常时按残留处理，退出码非零。"""
    from proactive import runtime_plane

    dbs = _build_dbs(tmp_path)
    mod = _load_script()

    def _boom(*_a, **_k):
        raise RuntimeError("count_owner unavailable")

    monkeypatch.setattr(runtime_plane, "count_owner", _boom)
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    captured = capsys.readouterr()
    assert rc != 0
    assert "零残留验证通过" not in captured.out

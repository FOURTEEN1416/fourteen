"""e2e/测试账号清理脚本红测（2026-11-21 主动线治理批；2026-11-22 对抗验收修复批）。

契约：
- 默认 ``--dry-run``：只列 4 库受影响表与键，零删除、零备份；
- ``--apply``：先备份到 ``data/archive/``（每库可读快照 + scheduler_config.json
  + affinity_state.json + chroma 向量库目录），再事务删除；
- 覆盖 runtime_plane 全部表、user_character_settings、emotion_stage_state、
  vital_signs_state、agent_core 三表与全部节流账本段；
- event_ledger 含 ``channel:<uid>:<slot>`` 通道状态事件（channel_state）；
- persona 画像两表按真实 TEXT scope 键（``{character_id}:{session_id}``）判属
  清除，无主/他人 scope 保留；删除面失效时零残留验证必须报非零（H2）；
- chroma 向量库与 affinity_state.json 纳入删除链与零残留验证（M3）；
- sqlite 会话键族覆盖 v1.28 前一段式裸 peer 键与 reminders/pending_intents
  数字 user_id 直列兜底（M4）；话题状态机四表含遗留 ``topic_plan``（L2）；
- 作业账：半途失败后不带 ``--user-id`` 重跑必须做全数据面残留扫描并响亮
  失败，不得假成功（M5）；
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

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "purge_e2e_accounts.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("purge_e2e_accounts", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _sandbox_data_planes(tmp_path, monkeypatch):
    """整文件级沙箱：微信通道目录与 ASE 状态一律指向 tmp_path，杜绝误触真实 data/。"""
    from proactive import ase_hub
    from wechat_direct import channel_paths

    monkeypatch.setattr(channel_paths, "PROJECT_ROOT", tmp_path)
    ase_dir = tmp_path / "ase_states"
    ase_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(ase_hub, "_STATE_DIR", ase_dir)
    monkeypatch.setattr(ase_hub, "_INDEX_PATH", ase_dir / "index.json")


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
    # 裸 peer 绑定（M4：v1.28 前一段式遗留键的归属依据）
    conn.execute("UPDATE wechat_bindings SET wxid='wxid9@im.wechat' WHERE user_id=9")
    conn.execute("UPDATE wechat_bindings SET wxid='wxid10@im.wechat' WHERE user_id=10")
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
        CREATE TABLE topic_plan (session_key TEXT, character_id TEXT, topic TEXT);
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
        -- persona 画像：真实 schema 是 TEXT scope 主键（persona_bank.py 建表 DDL 同构）
        CREATE TABLE user_persona (
            user_id TEXT PRIMARY KEY, ocean_json TEXT NOT NULL, pad_json TEXT NOT NULL,
            style_json TEXT NOT NULL, snapshot_count INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT NOT NULL, last_updated TEXT NOT NULL);
        CREATE TABLE user_persona_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, timestamp TEXT NOT NULL,
            ocean_json TEXT NOT NULL, pad_json TEXT NOT NULL, style_json TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 0.5, source TEXT NOT NULL DEFAULT '',
            trigger_message TEXT DEFAULT '');
        """
    )
    for key in ("9:web:abc", "10:web:def"):
        conn.execute("INSERT INTO chat_history (role,content,session_id,created_at) VALUES ('user','x',?,'2026-11-21')", (key,))
        conn.execute("INSERT INTO topic_state VALUES (?, 'default', 't', '话题', 'active', 1, '2026-11-21')", (key,))
        conn.execute("INSERT INTO thread_plan VALUES (?, 'default', '话题')", (key,))
        conn.execute("INSERT INTO topic_meta VALUES (?, 'default')", (key,))
        conn.execute("INSERT INTO topic_plan VALUES (?, 'default', '话题')", (key,))
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
    # M4：v1.28 前一段式裸 peer 遗留键（无 owner 前缀，只能按绑定 wxid 归属）
    conn.execute("INSERT INTO chat_history (role,content,session_id,created_at) VALUES ('user','legacy','wxid9@im.wechat','2026-11-20')")
    conn.execute("INSERT INTO chat_history (role,content,session_id,created_at) VALUES ('user','legacy','wxid10@im.wechat','2026-11-20')")
    conn.execute("INSERT INTO daily_summaries VALUES ('wxid9@im.wechat|2026-11-20')")
    conn.execute("INSERT INTO daily_summaries VALUES ('wxid10@im.wechat|2026-11-20')")
    # M4：reminders/pending_intents 数字 user_id 直列兜底行（session_key 无 owner 前缀）
    conn.execute("INSERT INTO reminders (session_key,user_id) VALUES ('legacy-session', 9)")
    conn.execute("INSERT INTO reminders (session_key,user_id) VALUES ('legacy-session-10', 10)")
    conn.execute("INSERT INTO pending_intents (session_key,user_id) VALUES ('legacy-session', 9)")
    conn.execute("INSERT INTO pending_intents (session_key,user_id) VALUES ('legacy-session-10', 10)")
    # H2：persona scope 行——uid 9 的三个 scope 必须清除；uid 10/11 与无主裸 scope 保留。
    persona_scopes = (
        "default:9:web:abc",
        "cardx:9:web:abc",
        "default:9:wxid9@im.wechat",
        "default:10:web:def",
        "default:11:web:zzz",
        "default",
    )
    for scope in persona_scopes:
        conn.execute(
            "INSERT INTO user_persona (user_id, ocean_json, pad_json, style_json, "
            "snapshot_count, first_seen, last_updated) VALUES (?, '{}', '{}', '{}', 1, "
            "'2026-11-21', '2026-11-21')",
            (scope,),
        )
        conn.execute(
            "INSERT INTO user_persona_snapshots (user_id, timestamp, ocean_json, pad_json, "
            "style_json) VALUES (?, '2026-11-21', '{}', '{}', '{}')",
            (scope,),
        )
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

    # M3：chroma 向量库（显式 embedding 离线写入，不触发模型下载/网络）
    chroma = tmp_path / "chroma_db"
    import chromadb

    client = chromadb.PersistentClient(path=str(chroma))
    coll = client.get_or_create_collection("user_facts")
    coll.upsert(
        ids=["f9", "f10"], documents=["fact9", "fact10"],
        embeddings=[[1.0] * 384, [2.0] * 384],
        metadatas=[{"user_key": "9:web:abc"}, {"user_key": "10:web:def"}],
    )

    # M3：affinity 点存（会话键 / 裸 uid / 裸 peer 三种遗留键形 + 他人键）
    affinity = tmp_path / "affinity_state.json"
    affinity.write_text(
        json.dumps({
            "9:web:abc::default": {"affection_points": 10.0},
            "9::default": {"affection_points": 5.0},
            "wxid9@im.wechat::default": {"affection_points": 7.0},
            "10:web:def::default": {"affection_points": 3.0},
        }),
        encoding="utf-8",
    )

    return {
        "users": users, "memory": memory, "agent": agent,
        "runtime": runtime, "config": cfg, "chroma": chroma,
        "affinity": affinity, "journal": tmp_path / "purge_e2e_jobs.json",
    }


def _args(dbs: dict[str, Path], archive: Path, *, apply: bool) -> list[str]:
    args = [
        "--users-db", str(dbs["users"]),
        "--sqlite-db", str(dbs["memory"]),
        "--agent-db", str(dbs["agent"]),
        "--runtime-db", str(dbs["runtime"]),
        "--scheduler-config", str(dbs["config"]),
        "--archive-dir", str(archive),
        "--chroma-dir", str(dbs["chroma"]),
        "--affinity-file", str(dbs["affinity"]),
        "--journal-path", str(dbs["journal"]),
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


def test_apply_purges_all_four_dbs_with_backup_and_verification(tmp_path, capsys):
    from wechat_direct import channel_paths

    dbs = _build_dbs(tmp_path)

    # 微信通道：uid 9 有凭证目录，uid 10 必须保留。
    for uid in (9, 10):
        slot = channel_paths.session_dir(uid, 0)
        slot.mkdir(parents=True, exist_ok=True)
        (slot / "credentials.json").write_text("{}", encoding="utf-8")

    # ASE：uid 9/10 各一条索引 + 状态文件（沙箱目录由 autouse 夹具指向 tmp_path）。
    ase_dir = tmp_path / "ase_states"
    index: dict[str, str] = {}
    # `9:orphan` 只存在于 ASE 索引（DB 已无引用），也必须按 owner 清除。
    for key in ("9:web:abc", "10:web:def", "9:orphan"):
        state = ase_dir / f"{key.replace(':', '_')}.json"
        state.write_text("{}", encoding="utf-8")
        index[key] = str(state)
    (ase_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")

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

    # 完整删除链：角色成就前缀行清除、审计行留存、微信目录清除。
    assert _count(dbs["users"], "SELECT COUNT(*) FROM character_achievements WHERE character_id='9:card'") == 0
    # 无关 uid（7 不在目标集合）的成就行必须零误伤保留。
    assert _count(dbs["users"], "SELECT COUNT(*) FROM character_achievements WHERE character_id='7:card'") == 1
    assert _count(dbs["users"], "SELECT COUNT(*) FROM operator_audit WHERE admin_id=9") == 1
    assert not (channel_paths.sessions_root() / "9").exists()
    assert channel_paths.session_dir(10, 0).exists()
    # ASE 索引：uid 9 的键（含 DB 无引用的孤儿键 9:orphan）清除，uid 10 保留。
    remain_index = json.loads((tmp_path / "ase_states" / "index.json").read_text(encoding="utf-8"))
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

    # 二次执行幂等（目标集合为空 + 作业账已完结 → 退出码 0）
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


# ── H2：persona 画像 TEXT scope 判属 ────────────────────────────────────


def test_persona_scope_owner_semantics():
    """scope 判属与 persona_bank.scope_owner_uid 同口径（剥 character 前缀后 owner_of）。"""
    mod = _load_script()
    assert mod._persona_scope_owner("default:9:web:abc", 9) is True
    assert mod._persona_scope_owner("cardx:9:web:abc", 9) is True
    assert mod._persona_scope_owner("default:9:wxid9@im.wechat", 9) is True
    assert mod._persona_scope_owner("default:10:web:def", 9) is False
    assert mod._persona_scope_owner("default:9:web:abc", 10) is False
    assert mod._persona_scope_owner("default", 9) is False  # 无 character 前缀的裸键
    assert mod._persona_scope_owner("", 9) is False


def test_persona_scope_rows_purged_and_others_kept(tmp_path):
    """H2：真实 TEXT scope 键按 owner 判属清除；uid 9 三个 scope 出库，他人/无主保留。"""
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    assert rc == 0
    conn = sqlite3.connect(str(dbs["memory"]))
    try:
        left_persona = {
            r[0] for r in conn.execute("SELECT user_id FROM user_persona")
        }
        left_snaps = {
            r[0] for r in conn.execute("SELECT user_id FROM user_persona_snapshots")
        }
    finally:
        conn.close()
    assert "default:9:web:abc" not in left_persona
    assert "cardx:9:web:abc" not in left_persona
    assert "default:9:wxid9@im.wechat" not in left_persona
    assert left_persona == {"default:10:web:def", "default:11:web:zzz", "default"}
    assert left_snaps == left_persona


def test_persona_residual_visible_to_verification(tmp_path, capsys, monkeypatch):
    """H2 红测：删除面漏掉 persona scope 行时，零残留验证必须报非零退出（不得假通过）。"""
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    monkeypatch.setattr(
        mod, "_delete_persona_scopes",
        lambda conn, uid: {"user_persona": 0, "user_persona_snapshots": 0},
    )
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    captured = capsys.readouterr()
    assert rc != 0
    assert "零残留验证通过" not in captured.out
    assert "残留" in captured.err
    # scope 行必须原样留存（删除被模拟跳过）
    assert _count(dbs["memory"], "SELECT COUNT(*) FROM user_persona") == 6


# ── M3：chroma 向量库 + affinity 点存 ───────────────────────────────────


def test_chroma_and_affinity_planes_purged_and_verified(tmp_path):
    """M3：chroma 向量库与 affinity_state.json 纳入删除链与零残留验证。"""
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    assert rc == 0

    import chromadb

    client = chromadb.PersistentClient(path=str(dbs["chroma"]))
    coll = client.get_or_create_collection("user_facts")
    metas = coll.get(include=["metadatas"])["metadatas"]
    assert {m.get("user_key") for m in metas} == {"10:web:def"}

    affinity = json.loads(dbs["affinity"].read_text(encoding="utf-8"))
    assert list(affinity) == ["10:web:def::default"]

    # 备份清单必须覆盖两个新数据面（备份落在时间戳子目录，rglob 校验）
    archive = tmp_path / "archive"
    assert list(archive.rglob("affinity_state.json")), "affinity_state.json 必须随备份留存"
    assert list(archive.rglob("chroma_db")), "chroma 向量库目录必须随备份留存"


def test_chroma_residual_visible_to_verification(tmp_path, capsys, monkeypatch):
    """M3 红测：chroma 清除被跳过时，零残留验证必须报非零退出。"""
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    monkeypatch.setattr(mod, "_purge_vectors", lambda *a, **k: {})
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    captured = capsys.readouterr()
    assert rc != 0
    assert "零残留验证通过" not in captured.out


def test_affinity_residual_visible_to_verification(tmp_path, capsys, monkeypatch):
    """M3 红测：affinity 清除被跳过时，零残留验证必须报非零退出。"""
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    monkeypatch.setattr(mod, "_purge_affinity", lambda *a, **k: 0)
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    captured = capsys.readouterr()
    assert rc != 0
    assert "零残留验证通过" not in captured.out


# ── M4：裸 peer 遗留键 + 数字 uid 直列兜底；L2：topic_plan ──────────────


def test_bare_peer_and_uid_fallback_and_topic_plan_purged(tmp_path):
    """M4：一段式裸 peer 键与 reminders/pending_intents 数字 uid 兜底；L2：topic_plan。"""
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    assert rc == 0
    conn = sqlite3.connect(str(dbs["memory"]))
    try:
        # 裸 peer 一段式遗留键：uid 9 的清除，uid 10 的保留
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id='wxid9@im.wechat'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id='wxid10@im.wechat'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM daily_summaries WHERE date='wxid9@im.wechat|2026-11-20'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM daily_summaries WHERE date='wxid10@im.wechat|2026-11-20'"
        ).fetchone()[0] == 1
        # 数字 user_id 直列兜底（session_key 无 owner 前缀的旧行）
        assert conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE user_id=9"
        ).fetchone()[0] == 0
        # uid 10 有两行（会话键行 + legacy 直列行），零误伤全保留
        assert conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE user_id=10"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM pending_intents WHERE user_id=9"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM pending_intents WHERE user_id=10"
        ).fetchone()[0] == 2
        # L2：话题状态机第 4 张遗留表 topic_plan 随会话键清除
        assert conn.execute(
            "SELECT COUNT(*) FROM topic_plan WHERE session_key='9:web:abc'"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM topic_plan WHERE session_key='10:web:def'"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_uid_fallback_residual_visible_to_verification(tmp_path, capsys, monkeypatch):
    """M4 红测：数字 uid 兜底删除被跳过时，零残留验证必须报非零退出。

    只盲化**删除路径**的 uid 兜底：``_intent_where`` 是删除与验证共用的
    谓词（``_target_counts`` 与 ``_delete_sqlite_db`` 都经模块全局查找消费），
    若全程盲化，验证侧同样看不见 ``user_id=9`` 的遗留行 → residual=0 →
    main 返回 0 → 本用例自败。因此包装 ``_delete_sqlite_db``，仅在删除执行
    期间替换谓词、返回后恢复，验证面保持完整（与 H2/M3 红测「只盲删除面」
    同构）。
    """
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    real_delete_sqlite = mod._delete_sqlite_db
    real_intent_where = mod._intent_where

    def _blind_intent_where(uid, owned):
        # 模拟「删除只按会话键、漏数字 uid 兜底」的旧口径
        return mod._key_family_where("session_key", uid, owned)

    def _delete_with_blind_intent(conn, target, keys, peers=(), character_ids=()):
        mod._intent_where = _blind_intent_where
        try:
            return real_delete_sqlite(conn, target, keys, peers, character_ids)
        finally:
            mod._intent_where = real_intent_where

    monkeypatch.setattr(mod, "_delete_sqlite_db", _delete_with_blind_intent)
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    captured = capsys.readouterr()
    assert rc != 0
    assert "零残留验证通过" not in captured.out
    assert "残留" in captured.err
    # 删除被模拟跳过的 uid 兜底行必须原样留存（'9:web:abc' 行按会话键已删）
    assert _count(dbs["memory"], "SELECT COUNT(*) FROM reminders WHERE user_id=9") == 1
    assert _count(
        dbs["memory"], "SELECT COUNT(*) FROM pending_intents WHERE user_id=9"
    ) == 1


# ── M5：作业账 + 半途失败重跑不得假成功 ─────────────────────────────────


def test_rerun_without_user_id_after_midway_failure_fails_loud(tmp_path, capsys, monkeypatch):
    """M5：users 行先删、数据面未清时半途失败，不带 --user-id 重跑必须响亮失败。"""
    dbs = _build_dbs(tmp_path)
    mod = _load_script()
    real_delete_sqlite = mod._delete_sqlite_db
    crashed = {"hit": False}

    def _crash(conn, target, keys, peers=(), character_ids=()):
        # 只在首次调用时模拟半途崩溃；之后的不带/带 --user-id 重跑必须走
        # 真实删除——否则本用例尾部的「显式 --user-id 补齐删除 → 零残留」
        # 断言与桩自相矛盾（桩恒崩则残留永远清不掉）。
        if not crashed["hit"]:
            crashed["hit"] = True
            raise RuntimeError("simulated midway crash")
        return real_delete_sqlite(conn, target, keys, peers, character_ids)

    monkeypatch.setattr(mod, "_delete_sqlite_db", _crash)
    with pytest.raises(RuntimeError, match="simulated midway crash"):
        mod.main(_args(dbs, tmp_path / "archive", apply=True))
    capsys.readouterr()

    # users 行已删（users.db 先删），sqlite 数据面残留
    assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=9") == 0
    assert _count(
        dbs["memory"], "SELECT COUNT(*) FROM chat_history WHERE session_id='9:web:abc'"
    ) == 1

    # 作业账应记录了未完结作业
    journal = json.loads(dbs["journal"].read_text(encoding="utf-8"))
    assert any(
        j["user_id"] == 9 and j["status"] == "running" for j in journal["jobs"]
    )

    # 不带 --user-id 重跑：目标集合为空 → 全数据面残留扫描 → 响亮失败
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    captured = capsys.readouterr()
    assert rc != 0
    assert "残留" in captured.err
    assert "--user-id" in captured.err
    assert "零残留验证通过" not in captured.out

    # 显式 --user-id 重跑：补齐删除 → 零残留 → 作业账完结
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True) + ["--user-id", "9"])
    captured = capsys.readouterr()
    assert rc == 0, captured.out + captured.err
    assert _count(
        dbs["memory"], "SELECT COUNT(*) FROM chat_history WHERE session_id='9:web:abc'"
    ) == 0
    journal = json.loads(dbs["journal"].read_text(encoding="utf-8"))
    done = [j for j in journal["jobs"] if j["user_id"] == 9]
    assert done and done[0]["status"] == "done"

    # 完结后再不带 --user-id 重跑：无未完结作业 → 幂等退出 0
    rc = mod.main(_args(dbs, tmp_path / "archive", apply=True))
    assert rc == 0


def test_apply_finish_scans_pending_jobs_outside_target_set(tmp_path, capsys, monkeypatch):
    """M5 纵深：apply 收尾必须补扫作业账中本次目标集之外的未完结作业。

    半途失败删掉 uid 9 的 users 行后，若测试命名空间仍有其他账号（uid 11），
    不带 ``--user-id`` 重跑会正常清理 uid 11 并退出——uid 9 的残留若只在
    「目标集合为空」时才扫描，就要等到下一次空跑才暴露。收尾补扫必须当场
    响亮失败（非零退出 + 提示显式 ``--user-id``）。
    """
    dbs = _build_dbs(tmp_path)
    conn = sqlite3.connect(str(dbs["users"]))
    conn.execute(
        "INSERT INTO users VALUES (11, 'wbtest-eleven', 'eleven@test.local', '2026-11-21')"
    )
    conn.commit()
    conn.close()

    mod = _load_script()
    real_delete_sqlite = mod._delete_sqlite_db
    crashed = {"hit": False}

    def _crash_on_uid9(conn, target, keys, peers=(), character_ids=()):
        # 仅 uid 9 **首次**删除时模拟半途崩溃；之后的显式 --user-id 补删必须
        # 走真实删除（否则尾部「补删清零」断言自相矛盾）。uid 11 始终真实删除
        # （重跑时它仍在目标集合内）。
        if int(target["id"]) == 9 and not crashed["hit"]:
            crashed["hit"] = True
            raise RuntimeError("simulated midway crash")
        return real_delete_sqlite(conn, target, keys, peers, character_ids)

    monkeypatch.setattr(mod, "_delete_sqlite_db", _crash_on_uid9)
    # 角色卡/知识目录指到临时空目录，隔离真实 config/characters 与 data/knowledge。
    isolated = (
        _args(dbs, tmp_path / "archive", apply=True)
        + ["--characters-dir", str(tmp_path / "characters"),
           "--knowledge-dir", str(tmp_path / "knowledge")]
    )

    with pytest.raises(RuntimeError, match="simulated midway crash"):
        mod.main(isolated)
    capsys.readouterr()

    # uid 9：users 行已删、sqlite 数据面残留；uid 11：尚未被处理
    assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=9") == 0
    assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=11") == 1
    assert _count(
        dbs["memory"], "SELECT COUNT(*) FROM chat_history WHERE session_id='9:web:abc'"
    ) == 1

    # 不带 --user-id 重跑：uid 11 被正常清理，但 uid 9 的残留必须让整体非零退出
    rc = mod.main(isolated)
    captured = capsys.readouterr()
    assert _count(dbs["users"], "SELECT COUNT(*) FROM users WHERE id=11") == 0
    assert rc != 0
    assert "残留" in captured.err
    assert "--user-id" in captured.err
    # uid 9 残留原样保留，等待显式 --user-id 处置；作业账保持未完结
    assert _count(
        dbs["memory"], "SELECT COUNT(*) FROM chat_history WHERE session_id='9:web:abc'"
    ) == 1
    journal = json.loads(dbs["journal"].read_text(encoding="utf-8"))
    assert any(
        j["user_id"] == 9 and j["status"] == "running" for j in journal["jobs"]
    )

    # 显式 --user-id 9 补删 → 可见残留清零 → 收尾补扫无未完结项 → 退出 0
    rc = mod.main(isolated + ["--user-id", "9"])
    captured = capsys.readouterr()
    assert rc == 0, captured.out + captured.err
    assert _count(
        dbs["memory"], "SELECT COUNT(*) FROM chat_history WHERE session_id='9:web:abc'"
    ) == 0

"""
结构化记忆系统 — 基于 SQLite

管理结构化数据：
- user_facts: 用户事实（偏好、习惯、事件）
- chat_history: 对话历史（结构化版本，带角色/会话归属）
- reminders: 提醒事项
- pending_intents: 澄清任务状态机
- reflections / trace_log / tool_call_log: 洞察与诊断

2026-09-22 清理：affinity_log（零写入）、emotion_trajectory（零写入）、
working_memory/sessions（唯一写入者 DB 版 WorkingMemory 已拆除）四张死表
出库并在初始化时幂等 DROP，见 docs/DELETION_LOG.md。
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from utils import session_key as session_key_mod
from utils.local_time import local_day_utc_bounds, now_local

# 默认库路径（2026-09-24 提为模块常量）：供 tests/conftest 的
# isolate_runtime_state_files monkeypatch——旧实现把默认值写在 __init__
# 参数签名里，测试隔离无挂钩点，测试实例化即直连宿主 data/sqlite.db
# （与 scheduler_config / agent_plane.db 同类事故的第三处镜像缺口）。
_DB_DEFAULT = "./data/sqlite.db"

logger = logging.getLogger("structured_memory")

# 全局注册表，用于跟踪所有 StructuredMemory 实例，确保程序退出时关闭连接
_structured_memory_instances: list[StructuredMemory] = []
_instances_lock = threading.Lock()


def _register_structured_memory(instance: StructuredMemory) -> None:
    """注册 StructuredMemory 实例到全局注册表"""
    with _instances_lock:
        if instance not in _structured_memory_instances:
            _structured_memory_instances.append(instance)


def _unregister_structured_memory(instance: StructuredMemory) -> None:
    """从全局注册表移除 StructuredMemory 实例"""
    with _instances_lock:
        if instance in _structured_memory_instances:
            _structured_memory_instances.remove(instance)


def _close_all_structured_memory() -> None:
    """关闭所有注册的 StructuredMemory 实例（atexit 处理器）"""
    with _instances_lock:
        instances = _structured_memory_instances.copy()
    for instance in instances:
        try:
            instance.close()
            logger.debug("StructuredMemory 连接已关闭: %s", instance.db_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("关闭 StructuredMemory 连接时出错: %s", e)


# 注册 atexit 处理器，确保程序退出时关闭所有数据库连接
atexit.register(_close_all_structured_memory)


class StructuredMemory:
    """
    SQLite 结构化记忆

    提供各表的 CRUD 操作，线程安全（连接级锁）。
    """

    def __init__(self, db_path: str | None = None):
        # 默认走模块常量 _DB_DEFAULT（conftest 可 patch）；显式传参优先（向后兼容）
        db_path = db_path or _DB_DEFAULT
        self.db_path = os.path.abspath(db_path)

        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._connection = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._write_lock = threading.Lock()
        self._degraded = False
        self._closed = False

        # 注册实例到全局注册表，确保程序退出时关闭连接
        _register_structured_memory(self)

        self._init_db()
        logger.info("StructuredMemory ready: %s", self.db_path)

    def close(self):
        """关闭数据库连接

        线程安全的数据库连接关闭方法，确保连接被正确释放。
        可通过 atexit 处理器自动调用，也可手动调用。
        """
        if self._closed:
            return

        with self._write_lock:
            if self._connection:
                try:
                    self._connection.close()
                    logger.debug("SQLite 连接已关闭: %s", self.db_path)
                except Exception as e:  # noqa: BLE001
                    logger.warning("关闭 SQLite 连接时出错: %s", e)
                finally:
                    self._connection = None
                    self._closed = True

        # 从全局注册表移除
        _unregister_structured_memory(self)

    def _execute_write(self, fn, *args, **kwargs):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with self._write_lock:
                    return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                    continue
                raise
        return None

    def __del__(self):
        """析构函数 — 确保连接被关闭

        注意：__del__ 不保证一定被调用，因此主要依赖 atexit 处理器。
        这里作为双重保险，在对象被垃圾回收时尝试关闭连接。
        """
        self.close()

    def _init_db(self) -> None:
        """初始化数据库和表结构"""
        with self._conn() as conn:
            assert conn is not None
            # 2026-09-24 P0 自愈：残缺 FTS 虚表先 DROP（见方法 docstring），
            # 随后的 CREATE VIRTUAL TABLE IF NOT EXISTS 才会真正重建。
            self._heal_facts_fts(conn)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fact TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'general',
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    emotion_tag TEXT DEFAULT '',
                    session_id TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    trigger_time TIMESTAMP,
                    active BOOLEAN DEFAULT 1,
                    triggered BOOLEAN DEFAULT 0,
                    session_key TEXT DEFAULT '',
                    user_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    delivered_at TIMESTAMP,
                    fail_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS pending_intents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    user_id INTEGER,
                    intent TEXT NOT NULL DEFAULT 'set_reminder',
                    slots_json TEXT NOT NULL DEFAULT '{}',
                    ask_count INTEGER NOT NULL DEFAULT 0,
                    last_question TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_pending_intents_session
                    ON pending_intents(session_key, status);
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(trigger_time, active, triggered);

                CREATE TABLE IF NOT EXISTS persona_evolution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dimension TEXT NOT NULL,
                    before_val REAL NOT NULL,
                    after_val REAL NOT NULL,
                    delta REAL NOT NULL,
                    trigger_reason TEXT DEFAULT '',
                    llm_reasoning TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tool_call_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tool_name TEXT NOT NULL,
                    arguments TEXT DEFAULT '{}',
                    result TEXT DEFAULT '',
                    duration_ms REAL DEFAULT 0,
                    success BOOLEAN DEFAULT 1,
                    trace_id TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS reflections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    session_id TEXT DEFAULT '',
                    source_fact_ids TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS trace_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    node TEXT NOT NULL,
                    duration_ms REAL DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_facts_category ON user_facts(category);
                CREATE INDEX IF NOT EXISTS idx_chat_timestamp ON chat_history(created_at);
                CREATE INDEX IF NOT EXISTS idx_trace_id ON trace_log(trace_id);

                CREATE INDEX IF NOT EXISTS idx_facts_category_confidence_updated
                    ON user_facts(category, confidence, updated_at);
                CREATE INDEX IF NOT EXISTS idx_chat_session_created
                    ON chat_history(session_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_reflections_created
                    ON reflections(created_at DESC);

                CREATE VIRTUAL TABLE IF NOT EXISTS user_facts_fts USING fts5(
                    fact,
                    content='user_facts',
                    content_rowid='id'
                );

                CREATE TRIGGER IF NOT EXISTS facts_fts_insert
                AFTER INSERT ON user_facts BEGIN
                    INSERT INTO user_facts_fts(rowid, fact)
                    VALUES (new.id, new.fact);
                END;

                CREATE TRIGGER IF NOT EXISTS facts_fts_delete
                AFTER DELETE ON user_facts BEGIN
                    INSERT INTO user_facts_fts(user_facts_fts, rowid, fact)
                    VALUES ('delete', old.id, old.fact);
                END;
            """)
            self._migrate_reminders_columns(conn)
            self._migrate_user_facts_columns(conn)
            self._migrate_chat_history_columns(conn)
            # 2026-09-22：死表批量清除（详见 docs/DELETION_LOG.md）——
            # pending_events（CrossSessionReasoner 死链）、affinity_log /
            # emotion_trajectory（零写入零读取）、working_memory / sessions
            # （唯一写入者 DB 版 WorkingMemory 已拆除）。行数据从未被任何
            # 运行时路径消费；幂等 DROP 兼顾存量库清理与新库跳过。
            for dead in (
                "pending_events",
                "affinity_log",
                "emotion_trajectory",
                "working_memory",
                "sessions",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {dead}")  # noqa: S608
            conn.commit()

    def _heal_facts_fts(self, conn) -> None:
        """user_facts_fts 虚表残缺自检 + 自愈（2026-09-24 P0 根治）。

        ``CREATE VIRTUAL TABLE IF NOT EXISTS`` 只看 sqlite_master 是否已有同名
        虚表，对「定义在、shadow 表残缺」的损坏态**不作为** → 损坏 100% 持久：
        任何 ``user_facts`` INSERT 都被同步触发器 ``facts_fts_insert`` 连带抛
        ``vtable constructor failed``，主表写入整体回滚（生产 09-21 12:45 起
        46 条告警、``user_facts`` 恒 0 行实锤）。FTS5 初始化必向 ``*_config``
        写版本行，故 ``_config`` 缺失或为空即残缺。

        🔴 残缺虚表**连 ``DROP TABLE`` 都不可用**（SQLite 对涉及虚表的任何
        语句都先构造实例），且同连接 writable_schema 摘除后 ``IF NOT EXISTS``
        仍被 schema cache 骗过。唯一可靠路径 = **先把 shadow 表补齐到可构造**
        （``_config`` 补 version 行；缺失的 shadow 表按 FTS5 标准结构重建），
        让虚表恢复可构造 → 正常 ``DROP``（连带清 shadow）→ 主建表脚本的
        ``IF NOT EXISTS`` 随即真正重建。两条损坏形态均经本地实测闭合。
        """
        try:
            # 🔴 跨 worker 互斥（2026-09-24 生产实证）：4 worker 同启时并发
            # 补 shadow/DROP/重建会交错产出混合残局（首版自愈部署后生产库
            # 即呈现「shadow 齐 + version 在仍构造失败」）。BEGIN IMMEDIATE
            # 拿写锁；后到 worker 锁内二次检测见健康即跳过。
            conn.commit()  # 结束 sqlite3 隐式事务，防 BEGIN 嵌套
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_facts_fts'"
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return  # 虚表本就不存在，交给正常建表
                broken = False
                try:
                    n = conn.execute(
                        "SELECT COUNT(*) FROM user_facts_fts_config"
                    ).fetchone()[0]
                    broken = not n
                except sqlite3.OperationalError:
                    broken = True  # _config 表缺失，残缺确凿
                if not broken:
                    conn.execute("ROLLBACK")
                    return  # 双重检测：别的 worker 已修好
                logger.warning(
                    "user_facts_fts 虚表残缺（_config 缺失/空），执行自愈重建: %s",
                    self.db_path,
                )
                # 1) 补齐缺失的 shadow 表（结构 = FTS5 标准 shadow，缺哪张补哪张）
                for shadow_ddl in (
                    "CREATE TABLE IF NOT EXISTS 'user_facts_fts_data'"
                    "(id INTEGER PRIMARY KEY, block BLOB)",
                    "CREATE TABLE IF NOT EXISTS 'user_facts_fts_idx'"
                    "(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID",
                    "CREATE TABLE IF NOT EXISTS 'user_facts_fts_docsize'"
                    "(id INTEGER PRIMARY KEY, sz BLOB)",
                    "CREATE TABLE IF NOT EXISTS 'user_facts_fts_config'"
                    "(k PRIMARY KEY, v) WITHOUT ROWID",
                ):
                    conn.execute(shadow_ddl)  # noqa: S608
                # 2) version 行 = 构造通行证（FTS5 初始化本会写入的值）
                conn.execute(
                    "INSERT OR REPLACE INTO user_facts_fts_config VALUES('version','4')"
                )
                # 3) 虚表现已可构造 → 正常 DROP（连带清全部 shadow）
                conn.execute("DROP TABLE user_facts_fts")
                # 4) 立即裸重建（自包含，不依赖主建表脚本的执行时序；
                #    正常 DROP 路径无 schema cache 问题）
                conn.execute(
                    "CREATE VIRTUAL TABLE user_facts_fts USING fts5("
                    "fact, content='user_facts', content_rowid='id')"
                )
                conn.execute("COMMIT")
                logger.warning("user_facts_fts 自愈重建完成")
            except Exception:
                # 回滚半成品，不留混合残局（ROLLBACK 失败=已无活动事务，可忽略）
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute("ROLLBACK")
                raise
        except Exception as e:  # noqa: BLE001
            # 自愈失败不阻断初始化（保持库可用性优先）；error 级确保可见
            logger.error("user_facts_fts 自愈检查失败: %s", e)

    def _migrate_chat_history_columns(self, conn) -> None:
        """chat_history 幂等迁移：发言者归属（2026-09-21 重扫）。

        旧表只有 ``role``(=user/assistant) + ``session_id``：
        - **assistant 行没有身份** → 同会话切换角色后，新角色把上一角色的回复
          当成"自己说过的话"（模型分不清哪句是谁说的）；
        - 重要性评分算完即弃（重建上下文时硬编码 0.5）；
        - 排序/去重都靠秒级 ``created_at``。

        新增列全部带默认值，存量行为 legacy 归属（读取时按"通用行"处理，
        升级不丢历史）。
        """
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(chat_history)").fetchall()
        }
        migrations = {
            "character_id": "ALTER TABLE chat_history ADD COLUMN character_id TEXT NOT NULL DEFAULT ''",
            "user_key": "ALTER TABLE chat_history ADD COLUMN user_key TEXT NOT NULL DEFAULT ''",
            "turn_id": "ALTER TABLE chat_history ADD COLUMN turn_id TEXT NOT NULL DEFAULT ''",
            "importance": "ALTER TABLE chat_history ADD COLUMN importance REAL NOT NULL DEFAULT 0.0",
            "channel": "ALTER TABLE chat_history ADD COLUMN channel TEXT NOT NULL DEFAULT ''",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(ddl)
        # 上下文重建的读取索引：会话 + 写入序（id 即 rowid，无需额外列）
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_session_character "
            "ON chat_history(session_id, character_id, id)"
        )

    def _migrate_user_facts_columns(self, conn) -> None:
        """user_facts 幂等迁移：多用户隔离 + 回忆强化 + 遗忘状态。

        - user_key：事实归属（从 session_id 派生，见 memory_pipeline）；
          存量行默认 ''（legacy），完整隔离策略下**不注入任何会话**。
        - access_count：检索/注入时自增（B4 回忆强化）。
        - status：active|forgotten；遗忘进回收站后本表删除行，status 供软路径。
        """
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(user_facts)").fetchall()
        }
        migrations = {
            "user_key": "ALTER TABLE user_facts ADD COLUMN user_key TEXT NOT NULL DEFAULT ''",
            "access_count": "ALTER TABLE user_facts ADD COLUMN access_count INTEGER NOT NULL DEFAULT 0",
            "status": "ALTER TABLE user_facts ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            # 包 Q · B-b：话题标签 + 近重复强化时间
            "topics": "ALTER TABLE user_facts ADD COLUMN topics TEXT NOT NULL DEFAULT ''",
            "last_seen_at": "ALTER TABLE user_facts ADD COLUMN last_seen_at TEXT",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_facts_user_key "
            "ON user_facts(user_key, status, confidence)"
        )
        # 回收站表（shisi 侧已存在同名结构；StructuredMemory 独立库可能没有）
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memory_recycle_bin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                memory_content TEXT NOT NULL,
                deleted_at TEXT NOT NULL DEFAULT (datetime('now')),
                restore_before TEXT NOT NULL,
                restored INTEGER NOT NULL DEFAULT 0
            )"""
        )

    @staticmethod
    def user_key_from_session(session_id: str) -> str:
        """session_id → 事实/记忆归属 user_key（隔离硬约束）。

        2026-09-21 生产串台修复：**返回完整会话键**，禁止剥掉 owner。
        隔离域 = `(channel_owner, peer_wxid)`，即 `N:wxid` 本身。
        旧实现 `N:wxid → wxid` 会让同一 peer 下不同注册账号共用
        user_facts / 跨会话尾巴 / 工具记忆检索（生产实证 user1 与 user4）。
        """
        if not session_id:
            return ""
        return str(session_id).strip()

    @staticmethod
    def bare_peer_from_session(session_id: str) -> str:
        """仅用于迁移/诊断：从 `N:wxid@im.wechat` 还原对端标识，不得用于运行时读路径。

        2026-09-21 重扫：格式解析统一委托 `utils.session_key`（唯一真源），
        不再本地手写 `split(":", 1)`。注意 `@im.wechat` 是 wxid 自身后缀，
        **不剥离**（生产 `chat_history.session_id` 取样实证）。
        """
        from utils.session_key import peer_of

        return peer_of(str(session_id or "")) or str(session_id or "")

    def migrate_legacy_isolation_keys(self) -> dict[str, int]:
        """一次性迁移：把「剥 owner 的裸 peer」事实/历史迁到唯一 owner 的完整会话键。

        规则：
        - 裸 user_key / 裸 session_id 在 chat_history 中若只被**一个** owner
          形态 `N:peer` 使用 → 迁到该完整键；
        - 多个 owner 共用同一 peer → **保持裸键不注入**（孤儿），避免串台；
        - 空键事实保持空键（默认不注入）。
        """
        stats = {
            "facts_migrated": 0,
            "facts_orphaned": 0,
            "chats_migrated": 0,
            "chats_orphaned": 0,
        }
        try:
            with self._conn(write=True) as conn:
                bare_keys: set[str] = set()
                for row in conn.execute(
                    "SELECT DISTINCT user_key FROM user_facts "
                    "WHERE user_key != '' AND user_key NOT LIKE '%:%'"
                ):
                    bare_keys.add(str(row["user_key"] or ""))
                bare_sessions: set[str] = set()
                for row in conn.execute(
                    "SELECT DISTINCT session_id FROM chat_history "
                    "WHERE session_id != '' AND session_id NOT LIKE '%:%'"
                ):
                    bare_sessions.add(str(row["session_id"] or ""))

                def _unique_owner(peer: str) -> str | None:
                    owners: set[str] = set()
                    for row in conn.execute(
                        "SELECT DISTINCT session_id FROM chat_history "
                        "WHERE session_id = ? OR session_id LIKE ?",
                        (peer, f"%:{peer}"),
                    ):
                        sid = str(row["session_id"] or "")
                        if sid == peer:
                            continue
                        # 2026-09-22 收口：拆 owner 走唯一 owner（禁手写 split）
                        head, _rest = session_key_mod.split_owner(sid)
                        if head:
                            owners.add(head)
                    if len(owners) == 1:
                        only = next(iter(owners))
                        return f"{only}:{peer}"
                    return None

                for bare in bare_keys:
                    if not bare:
                        continue
                    target = _unique_owner(bare)
                    if target:
                        conn.execute(
                            "UPDATE user_facts SET user_key = ? WHERE user_key = ?",
                            (target, bare),
                        )
                        stats["facts_migrated"] += 1
                    else:
                        stats["facts_orphaned"] += 1

                for bare in bare_sessions:
                    if not bare:
                        continue
                    target = _unique_owner(bare)
                    if target:
                        conn.execute(
                            "UPDATE chat_history SET session_id = ? WHERE session_id = ?",
                            (target, bare),
                        )
                        stats["chats_migrated"] += 1
                    else:
                        stats["chats_orphaned"] += 1
                conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("migrate_legacy_isolation_keys failed: %s", e)
        if any(stats.values()):
            logger.info("隔离键迁移完成: %s", stats)
        return stats

    def _migrate_reminders_columns(self, conn) -> None:
        """老库幂等迁移：reminders 补列（会话归属/投递状态）。

        存量行 session_key 保持空串——轮询只投递 session_key 非空的提醒，
        历史无主提醒自然静默（等价于旧行为：存了但永远不触发）。
        """
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()
        }
        migrations = {
            "session_key": "ALTER TABLE reminders ADD COLUMN session_key TEXT DEFAULT ''",
            "user_id": "ALTER TABLE reminders ADD COLUMN user_id INTEGER",
            "status": "ALTER TABLE reminders ADD COLUMN status TEXT DEFAULT 'pending'",
            "delivered_at": "ALTER TABLE reminders ADD COLUMN delivered_at TIMESTAMP",
            "fail_count": "ALTER TABLE reminders ADD COLUMN fail_count INTEGER DEFAULT 0",
        }
        for column, ddl in migrations.items():
            if column not in existing:
                conn.execute(ddl)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reminders_due "
            "ON reminders(trigger_time, active, triggered)"
        )

    @contextmanager
    def _conn(self, write: bool = False):
        """获取数据库连接

        Args:
            write: 是否为写操作，写操作会获取写锁
        """
        if write:
            with self._write_lock:
                yield self._connection
        else:
            yield self._connection

    @contextmanager
    def get_connection(self, write: bool = False):
        """公开的连接获取接口（用于CrossSessionReasoner等外部组件）

        Args:
            write: 是否为写操作，写操作会获取写锁
        """
        if write:
            with self._write_lock:
                yield self._connection
        else:
            yield self._connection

    # ── 用户事实 ──────────────────────────────────────────

    @staticmethod
    def _fact_bigrams(text: str) -> set[str]:
        t = "".join(str(text or "").lower().split())
        # 常见同义归一：阿拉伯数字与中文数字、标点
        trans = str.maketrans({"０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
                              "５": "5", "６": "6", "７": "7", "８": "8", "９": "9"})
        t = t.translate(trans)
        for a, b in (("十二", "12"), ("十一", "11"), ("十", "10"), ("一点", "1点")):
            t = t.replace(a, b)
        if len(t) < 2:
            return {t} if t else set()
        return {t[i : i + 2] for i in range(len(t) - 1)}

    @classmethod
    def facts_near_duplicate(cls, a: str, b: str, threshold: float = 0.72) -> bool:
        """bigram 相似度 + 子串包含（对齐 my-raze spirit）；视为近重复则 True。"""
        def _norm(s: str) -> str:
            t = "".join(str(s or "").lower().split())
            for x, y in (("十二", "12"), ("十一", "11"), ("十", "10")):
                t = t.replace(x, y)
            return t
        ta, tb = _norm(a), _norm(b)
        if not ta or not tb:
            return False
        if ta == tb:
            return True
        if ta in tb or tb in ta:
            return True
        ba, bb = cls._fact_bigrams(a), cls._fact_bigrams(b)
        if not ba or not bb:
            return False
        inter = len(ba & bb)
        union = len(ba | bb)
        if union == 0:
            return False
        return (inter / union) >= threshold or inter / max(len(ba), len(bb)) >= threshold

    def add_fact(self, fact: str, category: str = "general",
                 confidence: float = 0.5, source: str = "",
                 user_key: str = "", topics: str | list[str] | None = None) -> int:
        """添加用户事实（按 user_key 隔离）。

        包 Q · B-b：同 user_key 下 near-dup → UPDATE（confidence/access/last_seen/topics），
        **不双插**。
        """
        topics_text = (
            ",".join(str(t).strip() for t in topics if str(t).strip())
            if isinstance(topics, (list, tuple))
            else str(topics or "")
        )
        fact = str(fact or "").strip()
        if not fact:
            return -1

        with self._conn(write=True) as conn:
            # near-dup 扫描（同 user_key + active）
            rows = conn.execute(
                "SELECT id, fact, confidence, access_count, category, topics FROM user_facts "
                "WHERE user_key = ? AND status = 'active'",
                (user_key or "",),
            ).fetchall()
            for row in rows:
                existing = dict(row)
                if self.facts_near_duplicate(fact, existing.get("fact") or ""):
                    new_conf = max(float(existing.get("confidence") or 0.0), float(confidence))
                    merged_topics = existing.get("topics") or ""
                    if topics_text:
                        parts = [p for p in (merged_topics.split(",") + topics_text.split(",")) if p]
                        merged_topics = ",".join(dict.fromkeys(parts))
                    # 语义升级：relationship/commitment 覆盖 general/preference
                    new_cat = existing.get("category") or category
                    if category in ("relationship", "commitment") and new_cat not in (
                        "relationship",
                        "commitment",
                    ):
                        new_cat = category
                    conn.execute(
                        "UPDATE user_facts SET confidence = ?, access_count = access_count + 1, "
                        "category = ?, topics = ?, last_seen_at = CURRENT_TIMESTAMP, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (new_conf, new_cat, merged_topics, existing["id"]),
                    )
                    conn.commit()
                    return int(existing["id"])

            cursor = conn.execute(
                "INSERT INTO user_facts "
                "(fact, category, confidence, source, user_key, access_count, status, topics, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, 0, 'active', ?, CURRENT_TIMESTAMP)",
                (fact, category, confidence, source, user_key or "", topics_text),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_facts(self, category: str | None = None,
                  min_confidence: float = 0.0,
                  limit: int = 50,
                  user_key: str | None = None,
                  include_legacy: bool = False) -> list[dict[str, Any]]:
        """获取用户事实。

        Args:
            user_key: 指定归属；`None` 表示**不按用户过滤**（仅管理/内部维护路径）。
            include_legacy: 是否附带 user_key='' 的历史孤儿事实（完整隔离默认 False）。
        """
        with self._conn() as conn:
            clauses = ["status = 'active'", "confidence >= ?"]
            params: list[Any] = [min_confidence]
            if user_key is not None:
                if include_legacy:
                    clauses.append("(user_key = ? OR user_key = '')")
                    params.append(user_key or "")
                else:
                    clauses.append("user_key = ?")
                    params.append(user_key or "")
            if category:
                clauses.append("category = ?")
                params.append(category)
            sql = (
                "SELECT * FROM user_facts WHERE " + " AND ".join(clauses)
                + " ORDER BY updated_at DESC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def search_facts(self, keyword: str, user_key: str | None = None,
                     include_legacy: bool = False) -> list[dict[str, Any]]:
        """关键词搜索事实 — 优先FTS5，降级LIKE；user_key 过滤**下推 SQL**。

        2026-09-22 修复：旧实现先全库 ``LIMIT 20`` 再在 Python 侧按 user_key
        过滤——多用户下若命中窗口被他人事实占满，本人明明有匹配事实也被挤成
        空（静默漏检）。现在过滤条件下推，LIMIT 在过滤后生效。
        """
        with self._conn() as conn:
            # 归属过滤段（与 get_facts 同口径）
            key_sql = ""
            key_params: list[Any] = []
            if user_key is not None:
                if include_legacy:
                    key_sql = " AND user_key IN (?, ?)"
                    key_params = [user_key or "", ""]
                else:
                    key_sql = " AND user_key = ?"
                    key_params = [user_key or ""]

            def _filter(rows: list) -> list[dict[str, Any]]:
                # SQL 已带归属+active 过滤；此处仅兜底替身（假库不走 SQL 过滤）
                out = [dict(r) for r in rows]
                if user_key is None:
                    return [r for r in out if r.get("status", "active") == "active"]
                allowed = {user_key or ""}
                if include_legacy:
                    allowed.add("")
                return [
                    r for r in out
                    if r.get("status", "active") == "active" and r.get("user_key", "") in allowed
                ]

            try:
                rows = conn.execute(
                    """SELECT f.* FROM user_facts f
                       JOIN user_facts_fts fts ON f.id = fts.rowid
                       WHERE user_facts_fts MATCH ? AND f.status = 'active'"""
                    + key_sql.replace("user_key", "f.user_key")
                    + """
                       ORDER BY rank
                       LIMIT 20""",
                    (keyword, *key_params),
                ).fetchall()
                filtered = _filter(rows)
                if filtered:
                    return filtered
            except Exception as e:  # noqa: BLE001
                logger.debug("FTS5 search failed, falling back to LIKE: %s", e)
            rows = conn.execute(
                "SELECT * FROM user_facts WHERE fact LIKE ? AND status = 'active'"
                + key_sql
                + " ORDER BY confidence DESC LIMIT 20",
                (f"%{keyword}%", *key_params),
            ).fetchall()
            return _filter(rows)

    def update_fact_confidence(self, fact_id: int, confidence: float) -> None:
        """更新事实置信度"""
        with self._conn(write=True) as conn:
            conn.execute(
                "UPDATE user_facts SET confidence = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (confidence, fact_id),
            )
            conn.commit()

    def increment_fact_access(self, fact_ids: list[int]) -> int:
        """检索/注入时自增 access_count（B4 回忆强化）。返回实际更新行数。"""
        ids = [int(i) for i in fact_ids or [] if i is not None]
        if not ids:
            return 0
        with self._conn(write=True) as conn:
            placeholders = ",".join("?" * len(ids))
            cur = conn.execute(
                f"UPDATE user_facts SET access_count = access_count + 1 WHERE id IN ({placeholders})",  # noqa: S608
                ids,
            )
            conn.commit()
            return cur.rowcount or 0

    def delete_fact(self, fact_id: int, recycle: bool = True,
                    user_key: str = "", retain_days: int = 30) -> bool:
        """删除事实。

        B5 裁决「进回收站表」：默认先写入 `memory_recycle_bin` 再删主表行，
        数据可恢复、只增不减。`recycle=False` 时物理删除（维护路径）。
        """
        with self._conn(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM user_facts WHERE id = ?", (fact_id,)
            ).fetchone()
            if row is None:
                return False
            data = dict(row)
            # P0-4 写侧越权封堵：调用方声明了归属（user_key 非空）而该行不属于
            # 该归属 → 拒删（模型幻觉/恶意构造他人 fact_id 的防线）。
            if user_key and str(data.get("user_key") or "") != str(user_key):
                logger.warning(
                    "delete_fact 拒绝跨用户删除: id=%s 归属=%r 调用键=%r",
                    fact_id,
                    data.get("user_key"),
                    user_key,
                )
                return False
            if recycle:
                import json
                from datetime import datetime as _dt
                from datetime import timedelta as _td
                content = json.dumps(data, ensure_ascii=False, default=str)
                uk = user_key or data.get("user_key", "") or "legacy"
                deleted_at = _dt.now()
                restore_before = (deleted_at + _td(days=retain_days)).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute(
                    "INSERT INTO memory_recycle_bin "
                    "(character_id, memory_id, memory_content, deleted_at, restore_before, restored) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (
                        f"user_fact:{uk}",
                        str(fact_id),
                        content,
                        deleted_at.strftime("%Y-%m-%d %H:%M:%S"),
                        restore_before,
                    ),
                )
            conn.execute("DELETE FROM user_facts WHERE id = ?", (fact_id,))
            conn.commit()
            return True

    def restore_fact_from_recycle(self, recycle_id: int) -> int | None:
        """从回收站恢复事实到 user_facts；返回新 fact_id（失败 None）。"""
        import json
        with self._conn(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM memory_recycle_bin WHERE id = ? AND restored = 0",
                (recycle_id,),
            ).fetchone()
            if row is None:
                return None
            data = json.loads(dict(row)["memory_content"])
            cur = conn.execute(
                "INSERT INTO user_facts (fact, category, confidence, source, user_key, access_count, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active')",
                (
                    data.get("fact", ""),
                    data.get("category", "general"),
                    data.get("confidence", 0.5),
                    data.get("source", ""),
                    data.get("user_key", ""),
                    int(data.get("access_count", 0) or 0),
                ),
            )
            conn.execute(
                "UPDATE memory_recycle_bin SET restored = 1 WHERE id = ?", (recycle_id,)
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[no-any-return]

    def add_facts_batch(self, facts: list[dict[str, Any]], user_key: str = "") -> list[int]:
        """批量添加事实"""
        if not facts:
            return []
        ids: list[int] = []
        with self._conn(write=True) as conn:
            for f in facts:
                cur = conn.execute(
                    "INSERT INTO user_facts (fact, category, confidence, source, user_key, access_count, status) "
                    "VALUES (?, ?, ?, ?, ?, 0, 'active')",
                    (
                        f.get("fact", ""),
                        f.get("category", "general"),
                        f.get("confidence", 0.5),
                        f.get("source", ""),
                        f.get("user_key", user_key or ""),
                    ),
                )
                ids.append(int(cur.lastrowid or 0))
            conn.commit()
            return ids

    # ── 记忆反思 ──────────────────────────────────────────

    def add_reflection(self, content: str, session_id: str = "",
                       source_fact_ids: list[int] | None = None) -> int:
        """添加反思洞察"""
        import json
        source_ids = json.dumps(source_fact_ids or [])
        with self._conn(write=True) as conn:
            cursor = conn.execute(
                "INSERT INTO reflections (content, session_id, source_fact_ids) VALUES (?, ?, ?)",
                (content, session_id, source_ids),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_reflections(self, limit: int = 10,
                        session_id: str | None = None) -> list[dict[str, Any]]:
        """获取最近反思洞察（session_id 非 None 时按会话隔离）。"""
        with self._conn() as conn:
            if session_id is not None:
                rows = conn.execute(
                    "SELECT * FROM reflections WHERE session_id = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (str(session_id), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM reflections ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def search_reflections(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        """关键词搜索反思洞察"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reflections WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{keyword}%", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── 聊天历史 ──────────────────────────────────────────

    def add_chat(self, role: str, content: str,
                 emotion_tag: str = "", session_id: str = "",
                 character_id: str = "", user_key: str = "",
                 turn_id: str = "", importance: float = 0.0,
                 channel: str = "") -> int:
        """添加聊天记录（带**归属**落库）。

        2026-09-21 重扫：新增 `character_id` / `user_key` / `turn_id` /
        `importance` / `channel` 五列。旧实现只有 role + session_id ——
        同一会话切换角色后，新角色会把上一角色的回复当成"自己说过的话"
        （assistant 行没有任何身份信息）；且重要性评分算完即弃
        （重建上下文时硬编码 0.5，属假指标）。
        """
        with self._conn(write=True) as conn:
            cursor = conn.execute(                "INSERT INTO chat_history (role, content, emotion_tag, session_id, character_id, user_key, turn_id, importance, channel) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (role, content, emotion_tag, session_id, character_id, user_key,
                 turn_id, float(importance or 0.0), channel),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_recent_chats(self, n: int = 20) -> list[dict[str, Any]]:
        """获取最近 N 条聊天"""
        with self._conn() as conn:
            rows = conn.execute(                "SELECT * FROM chat_history ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
            return [dict(r) for r in rows][::-1]  # 反转成时间正序

    def get_chats_by_session(self, session_id: str) -> list[dict[str, Any]]:
        """获取某次会话的聊天"""
        with self._conn() as conn:
            rows = conn.execute(                "SELECT * FROM chat_history WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_chats_by_session_limit(
        self, session_id: str, limit: int, character_id: str = ""
    ) -> list[dict[str, Any]]:
        """获取某会话最近 limit 条聊天（**时间正序**）。

        ⚠️ 2026-09-21 重扫修复排序口径：`created_at` 是 ``CURRENT_TIMESTAMP``
        （秒级精度）—— 用户消息与角色回复常落在同一秒，仅按时间排序时相对次序
        不保证（"谁先说的"取决于扫描方向）。改为按 **`id`**（AUTOINCREMENT，
        等于写入序）判序，时间仅作展示。

        🔴 2026-09-22 二次根治（角色隔离真复发通道）：
        旧实现在 ``character_id`` 非空时取 ``character_id = ? OR character_id = ''``
        —— 兼容**迁移前无归属行**的善意条款，但存量数据**全部**无归属
        （生产实测 154/154 空串）时该条件恒真 = 角色过滤整体失效：
        切到角色 B 仍读到角色 A 的台词。且出站 assistant 行（主动消息/提醒/
        追问）从未写归属，新数据同样落在 ``''`` 上，隔离永无生效之日。

        现改为**两段式**：
          ① 命中归属行（``character_id = ?``）；
          ② 无归属行**按该会话当前绑定角色懒回填**后再判 —— 回填使存量行
             一次性归位，此后不再依赖兼容条款。
        回填仅在 ``character_id`` 非空且该会话确实绑定了该角色时发生；
        无法判定归属的行（跨角色迁移、测试残留）保守**排除**而非全放行
        —— 「宁缺毋串」优于"宁滥勿缺"。
        """
        sql = "SELECT * FROM chat_history WHERE session_id = ?"
        params: list[Any] = [session_id]
        if character_id:
            sql += " AND (character_id = ? OR character_id IS NULL OR character_id = '')"
            params.append(character_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            out = [dict(r) for r in rows][::-1]
        if character_id:
            out = self._backfill_legacy_attribution(out, character_id)
        return out

    def _backfill_legacy_attribution(
        self, rows: list[dict[str, Any]], character_id: str
    ) -> list[dict[str, Any]]:
        """把无归属的存量行按当前绑定角色回填（幂等），并**过滤掉不属于该角色**的行。

        判定规则（保守）：无归属行在"该会话当前绑定角色 == 查询角色"时视为
        该角色的历史并回填；否则视为不可判定，**排除**。
        由于调用方传的 ``character_id`` 正是该会话当前绑定角色，等价于：
        空归属行只对"当前绑定角色"可见，对其它角色不可见 —— 这正是隔离的
        预期语义（旧实现在所有角色下都可见）。

        ⚠️ **不构成跨用户误归属**：本方法只处理**单次查询命中的行**，而查询
        已按 ``session_id``（或 ``session_id IN (...)``）限定；一个会话键
        恒属于一个用户。因此"按当前绑定角色回填"不会把 A 的历史归给 B。

        🔴 2026-09-22 二次根治补：同时回填 ``user_key``。生产实测在归属空行
        中另有 324/382 行 ``user_key`` 也为空 —— 该列是跨会话检索
        （``get_cross_session_tail`` / ``user_key_from_session``）的归属键，
        留空会让这部分历史在"跨会话尾巴"注入中**整体缺席**。
        取值由 ``user_key_from_session(session_id)`` 推导（与写入路径同口径），
        推导不出时留空而非填 session_id（宁缺毋串）。
        """
        legacy_ids: list[int] = []
        user_key_fixes: list[tuple[str, int]] = []
        kept: list[dict[str, Any]] = []
        for row in rows:
            cid = str(row.get("character_id") or "")
            if cid:
                if cid == character_id:
                    kept.append(row)
                continue
            rid = int(row["id"])
            legacy_ids.append(rid)
            row["character_id"] = character_id
            if not str(row.get("user_key") or ""):
                derived = self.user_key_from_session(str(row.get("session_id") or ""))
                if derived:
                    row["user_key"] = derived
                    user_key_fixes.append((derived, rid))
            kept.append(row)
        if legacy_ids:
            try:
                with self._conn(write=True) as conn:
                    conn.executemany(
                        "UPDATE chat_history SET character_id = ? WHERE id = ?",
                        [(character_id, rid) for rid in legacy_ids],
                    )
                    if user_key_fixes:
                        conn.executemany(
                            "UPDATE chat_history SET user_key = ? WHERE id = ?",
                            user_key_fixes,
                        )
                    conn.commit()
                logger.info(
                    "存量对话归属回填: session=%s character=%s rows=%d user_key=%d",
                    rows[0].get("session_id") if rows else "",
                    character_id, len(legacy_ids), len(user_key_fixes),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("存量对话归属回填失败（本次结果仍按回填后返回）: %s", e)
        return kept

    def get_session_rows(
        self,
        session_ids: list[str] | tuple[str, ...],
        limit: int = 8,
        character_id: str = "",
    ) -> list[dict[str, Any]]:
        """多会话键取最近 limit 行（时间正序、按 id 判序）。

        🔴 2026-09-22：与 `get_chats_by_session_limit` 同口径 —— 无归属存量行
        按当前绑定角色懒回填后判定，非本角色的空归属行**排除**（旧 `OR ''`
        条款在存量全空时使角色过滤整体失效）。
        """
        forms = [str(s) for s in (session_ids or []) if str(s or "").strip()]
        if not forms:
            return []
        placeholders = ",".join("?" for _ in forms)
        sql = f"SELECT * FROM chat_history WHERE session_id IN ({placeholders})"
        params: list[Any] = list(forms)
        if character_id:
            sql += " AND (character_id = ? OR character_id IS NULL OR character_id = '')"
            params.append(character_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        out = [dict(r) for r in rows][::-1]
        if character_id:
            out = self._backfill_legacy_attribution(out, character_id)
        return out

    def get_cross_session_tail(
        self,
        session_id: str,
        limit: int = 8,
        user_key: str | None = None,
    ) -> list[str]:
        """跨会话尾巴：按**完整会话隔离键**取最近持久化消息。

        2026-09-21 串台修复：禁止把裸 peer / `N:bare` 并入 owner 会话——
        旧双形态合并会让 user1/user4 同时注入同一批遗留历史。
        """
        if limit <= 0:
            return []
        uk = user_key if user_key is not None else self.user_key_from_session(session_id)
        forms: set[str] = set()
        for key in (uk, session_id):
            k = str(key or "").strip()
            if k:
                forms.add(k)
        if not forms:
            return []
        placeholders = ",".join("?" for _ in forms)
        # 2026-09-22 块E：排序**一律按 `id`（写入序）** —— 与
        # `_load_session_history`（2026-09-21 已改）同口径。旧实现
        # `ORDER BY created_at DESC, id DESC` 以秒级时间戳为主键：同秒写入的
        # 用户/助手消息取哪几条由 `id` 兜底，但**跨秒边界**时"最近 limit 条"
        # 可能漏掉刚写入的消息（created_at 是写库时刻而非事件时刻，且 SQLite
        # `CURRENT_TIMESTAMP` 只有秒精度）。项目铁律：排序与去重禁止再用
        # 秒级 created_at 判序。
        sql = (
            "SELECT role, content FROM chat_history "
            f"WHERE session_id IN ({placeholders}) "
            "ORDER BY id DESC LIMIT ?"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, (*forms, limit)).fetchall()
        lines: list[str] = []
        for r in reversed([dict(x) for x in rows]):
            role = "用户" if r.get("role") == "user" else "助手"
            content = str(r.get("content") or "").strip()
            if not content:
                continue
            if "处理超时" in content or content.startswith("（处理消息"):
                continue
            if len(content) > 200:
                content = content[:200] + "…"
            lines.append(f"- {role}：{content}")
        return lines[-limit:]

    def get_chats_today(self, session_id: str | None = None) -> list[dict[str, Any]]:
        """获取「今天」（**本地日**）的聊天；session_id 非 None 时按会话隔离。"""
        start, end = local_day_utc_bounds()
        with self._conn() as conn:
            if session_id is not None:
                rows = conn.execute(
                    "SELECT * FROM chat_history "
                    "WHERE created_at >= ? AND created_at < ? AND session_id = ? "
                    "ORDER BY created_at ASC",
                    (start, end, str(session_id)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM chat_history WHERE created_at >= ? AND created_at < ? "
                    "ORDER BY created_at ASC",
                    (start, end),
                ).fetchall()
            return [dict(r) for r in rows]

    def count_chats_today(self) -> int:
        """今天（**本地日**）聊了多少条 —— 口径同 :meth:`get_chats_today`。"""
        start, end = local_day_utc_bounds()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM chat_history "
                "WHERE created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()
            return row["cnt"] if row else 0

    # ── 提醒 ──────────────────────────────────────────────

    @staticmethod
    def _now_local() -> str:
        """本地时间字符串（与 ``utils.local_time.now_local`` **同源**）。

        提醒的时间比较统一走应用层：SQLite ``datetime('now')`` 是 UTC，
        与 LLM 写入的北京时间字符串差 8 小时（历史缺陷）。

        ⚠️ 2026-09-20：原先直接 ``datetime.now()``（**依赖主机时区**，在非
        UTC+8 主机上会静默错 8 小时且无回退）—— 现统一委托公共真源，
        自动获得 UTC+8 回退，与 ASE/记忆管线共用同一时钟。
        """
        return now_local().strftime("%Y-%m-%d %H:%M:%S")

    def add_reminder(
        self,
        content: str,
        trigger_time: str | None = None,
        session_key: str = "",
        user_id: int | None = None,
    ) -> int:
        """添加提醒（session_key 为空 = 无投递目标，轮询不会投递它）"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(
                "INSERT INTO reminders (content, trigger_time, session_key, user_id) "
                "VALUES (?, ?, ?, ?)",
                (content, trigger_time, session_key, user_id),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_pending_reminders(self, session_key: str = "") -> list[dict[str, Any]]:
        """查询未触发的提醒（query 工具用；session_key 空 = 不过滤）"""
        with self._conn() as conn:
            sql = (
                "SELECT * FROM reminders WHERE active = 1 AND triggered = 0 "
                "AND trigger_time IS NOT NULL"
            )
            params: list[Any] = []
            if session_key:
                sql += " AND session_key = ?"
                params.append(session_key)
            sql += " ORDER BY trigger_time ASC"
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_due_reminders(self, now_local: str | None = None) -> list[dict[str, Any]]:
        """轮询专用：已到期且具备投递目标的提醒（北京时间应用层比较）"""
        now_local = now_local or self._now_local()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE active = 1 AND triggered = 0 "
                "AND status = 'pending' AND session_key != '' "
                "AND trigger_time IS NOT NULL AND trigger_time <= ? "
                "ORDER BY trigger_time ASC",
                (now_local,),
            ).fetchall()
            return [dict(r) for r in rows]

    def mark_reminder_result(self, reminder_id: int, delivered: bool) -> None:
        """记录投递结果：成功=已触发；失败累计，3 次后判死（可查不可发）"""
        now = self._now_local()
        with self._conn(write=True) as conn:
            if delivered:
                conn.execute(
                    "UPDATE reminders SET triggered = 1, status = 'delivered', "
                    "delivered_at = ? WHERE id = ?",
                    (now, reminder_id),
                )
            else:
                conn.execute(
                    "UPDATE reminders SET fail_count = fail_count + 1, "
                    "status = CASE WHEN fail_count + 1 >= 3 "
                    "THEN 'failed' ELSE status END, "
                    "active = CASE WHEN fail_count + 1 >= 3 "
                    "THEN 0 ELSE active END WHERE id = ?",
                    (reminder_id,),
                )
            conn.commit()

    def mark_reminder_triggered(self, reminder_id: int) -> None:
        """标记提醒已触发（兼容旧签名）"""
        self.mark_reminder_result(reminder_id, delivered=True)

    # ── 澄清任务状态机（pending_intents）──────────────────

    _PENDING_INTENT_TTL_MIN = 15

    def upsert_pending_intent(
        self,
        session_key: str,
        intent: str,
        slots: dict[str, Any],
        ask_count: int,
        last_question: str = "",
        user_id: int | None = None,
        ttl_minutes: int = _PENDING_INTENT_TTL_MIN,
    ) -> int:
        """记录/更新一条待澄清任务（同会话只保留最新一条）

        过期时刻必须与读侧 ``get_active_pending_intent`` / ``_now_local`` 同源：
        旧写法用裸 ``datetime.now()``（依赖主机时区），在 UTC CI/容器上比
        北京墙钟慢 8 小时，pending 一落库就被判过期。
        """
        now_str = self._now_local()
        now_dt = datetime.strptime(now_str, "%Y-%m-%d %H:%M:%S")
        expires = (now_dt + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        with self._conn(write=True) as conn:
            row = conn.execute(
                "SELECT id FROM pending_intents "
                "WHERE session_key = ? AND status = 'active'",
                (session_key,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE pending_intents SET intent = ?, slots_json = ?, "
                    "ask_count = ?, last_question = ?, updated_at = ?, expires_at = ? "
                    "WHERE id = ?",
                    (
                        intent,
                        json.dumps(slots, ensure_ascii=False),
                        ask_count,
                        last_question,
                        now_str,
                        expires,
                        row["id"],
                    ),
                )
                conn.commit()  # P1-14：_conn 上下文不自动提交，UPDATE 分支旧漏 commit
                return row["id"]  # type: ignore[no-any-return]
            cursor = conn.execute(
                "INSERT INTO pending_intents (session_key, user_id, intent, slots_json, "
                "ask_count, last_question, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_key,
                    user_id,
                    intent,
                    json.dumps(slots, ensure_ascii=False),
                    ask_count,
                    last_question,
                    expires,
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[no-any-return]

    def get_active_pending_intent(self, session_key: str) -> dict[str, Any] | None:
        """取会话当前待澄清任务；过期的顺带惰性置为 expired"""
        with self._conn(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM pending_intents "
                "WHERE session_key = ? AND status = 'active'",
                (session_key,),
            ).fetchone()
            if row and row["expires_at"] and row["expires_at"] <= self._now_local():
                conn.execute(
                    "UPDATE pending_intents SET status = 'expired' WHERE id = ?",
                    (row["id"],),
                )
                conn.commit()
                return None
            return dict(row) if row else None

    def resolve_pending_intent(self, session_key: str, status: str = "fulfilled") -> None:
        """关闭会话的待澄清任务（fulfilled / cancelled）"""
        with self._conn(write=True) as conn:
            conn.execute(
                "UPDATE pending_intents SET status = ?, updated_at = ? "
                "WHERE session_key = ? AND status = 'active'",
                (status, self._now_local(), session_key),
            )
            conn.commit()

    def expire_stale_intents(self) -> int:
        """批量过期超时任务（调度器周期调用；返回过期条数）"""
        with self._conn(write=True) as conn:
            cursor = conn.execute(
                "UPDATE pending_intents SET status = 'expired', updated_at = ? "
                "WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (self._now_local(), self._now_local()),
            )
            conn.commit()
            return cursor.rowcount  # type: ignore[no-any-return]
    def health_check(self) -> dict:
        """健康检查"""
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1")
                return {"connected": True, "path": self.db_path}
        except Exception:
            logger.exception("StructuredMemory健康检查异常")
            return {"connected": False, "error": "db_check_failed"}

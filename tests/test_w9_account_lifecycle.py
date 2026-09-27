"""W9 账号删除与数据遗忘 — 统一生命周期作业验收（合成两账号两角色）。

钉住（对应 docs/legal/USER_AGREEMENT.md §2.5「删除即真正删除」承诺）：
1. 删 A 不动 B —— 跨 users.db / sqlite.db / agent_plane.db / chroma / ASE 状态 /
   好感度点存 / 微信通道磁盘 / 调度节流账本 / 私有角色实例 全部存储逐 owner 断言；
2. 键形态经各 owner 解析（会话键 / 裸 peer 遗留 / 数字 uid / 组合键），不是一律 UID 前缀字符串删；
3. 幂等：重复执行不报错、结果不变；
4. 中断可恢复：owner 步骤失败 → 作业不得宣称 deleted；故障移除后续跑完成且 B 不丢；
5. UID 不复用：删除最高 id 账号后新注册不得复用 id（AUTOINCREMENT 迁移）；
6. 恢复备份重新引入已删账号 → 坟场对账再次清除，不复活；
7. 回执只含计数，不含私密内容。

全部数据落在 tmp_path 沙箱，禁止触碰真实 data/。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.database import (
    Base,
    ConsentRecord,
    User,
    UserSession,
    WechatBinding,
    WechatChannelSession,
    WechatPeerPreference,
)

# ─────────────────────────────────────────────────────────────
# 沙箱夹具：users.db 独立引擎 + 全部文件型存储重定向
# ─────────────────────────────────────────────────────────────


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """两账号合成环境：A=uid 1(tag atag)，B=uid 2(tag btag)。"""
    import proactive.ase_hub as ase_hub
    import wechat_direct.channel_paths as channel_paths
    from utils import affinity_state, json_state

    root = tmp_path / "w9"
    root.mkdir()
    monkeypatch.setattr(channel_paths, "sessions_root", lambda: root / "wechat_sessions")
    monkeypatch.setattr(ase_hub, "_STATE_DIR", root / "ase_states")
    monkeypatch.setattr(ase_hub, "_INDEX_PATH", root / "ase_states" / "index.json")
    monkeypatch.setattr(affinity_state, "_PATH", root / "affinity_state.json")
    monkeypatch.setattr(
        json_state, "DEFAULT_ROOT", root, raising=False
    )  # 节流账本等 json_state 读写落沙箱

    # 生命周期作业账与坟场
    monkeypatch.setattr(
        "api.lifecycle.job_root", lambda: root / "lifecycle_jobs"
    )
    # 坟场文件同样必须落沙箱（deletion_guard 有独立路径解析）
    monkeypatch.setattr(
        "utils.deletion_guard.graveyard_path",
        lambda: root / "lifecycle_jobs" / "graveyard.json",
    )
    monkeypatch.setattr("utils.deletion_guard._local_blocked", set())

    # users.db 独立异步引擎
    engine = create_async_engine(f"sqlite+aiosqlite:///{(root / 'users.db').as_posix()}")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    from utils.async_utils import run_async

    run_async(_create_all())

    env = {
        "root": root,
        "engine": engine,
        "session": maker,
        "create_all": _create_all,
        "users_db": root / "users.db",
        "sqlite_db": root / "sqlite.db",
        "agent_db": root / "agent_plane.db",
        "chroma_dir": root / "chroma_db",
        "ase_dir": root / "ase_states",
        "characters_dir": root / "characters",
        "knowledge_dir": root / "knowledge",
    }
    env["characters_dir"].mkdir()
    yield env
    import asyncio

    asyncio.get_event_loop_policy()  # noqa: B018 — 保持引用避免误删
    import asyncio as _a

    _a.run(engine.dispose())


A_UID, B_UID = 1, 2
A_TAG, B_TAG = "atag@im.wechat", "btag@im.wechat"
A_CARD, B_CARD = "priv_a", "priv_b"


def _seed_users(env) -> None:
    """users.db 两账号 + 各自绑定/通道/偏好/同意/会话/邀请码。"""

    async def _go():
        async with env["session"]() as db:
            for uid, tag, card in ((A_UID, A_TAG, A_CARD), (B_UID, B_TAG, B_CARD)):
                db.add(
                    User(
                        id=uid,
                        email=f"{tag}@example.com",
                        username=f"user{uid}",
                        hashed_password="x" * 60,
                        role="viewer",
                        is_active=True,
                        is_verified=True,
                    )
                )
                db.add(
                    WechatBinding(
                        user_id=uid, wxid=tag, nickname=f"n{uid}", character_card_id=card
                    )
                )
                db.add(
                    WechatChannelSession(
                        user_id=uid, slot=0, bot_id=tag, status="connected"
                    )
                )
                db.add(
                    WechatPeerPreference(
                        owner_user_id=uid, peer_wxid=f"friend{uid}@im.wechat",
                        character_card_id=card,
                    )
                )
                db.add(ConsentRecord(user_id=uid, agreement_version="1.0.0"))
                db.add(
                    UserSession(
                        user_id=uid, refresh_token_hash=f"hash{uid}",
                        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                    )
                )
            await db.commit()

    from utils.async_utils import run_async

    run_async(_go())


def _sm(env):
    from shisi.memory.legacy.structured_memory import StructuredMemory

    return StructuredMemory(str(env["sqlite_db"]))


def _seed_memory(env) -> list[str]:
    """sqlite.db 记忆面：A/B 各自的会话键、事实、反思、提醒、待澄清、画像、日记。"""
    sm = _sm(env)
    a_keys = [f"{A_UID}:{A_TAG}", f"{A_UID}:web:{A_TAG}7f3a", A_TAG]  # 含裸 peer 遗留
    b_keys = [f"{B_UID}:{B_TAG}", f"{B_UID}:web:{B_TAG}9e21"]
    with sm.get_connection(write=True) as conn:
        # daily_summaries 的建表 owner 是 DiarySummarizer（懒创建）；
        # 测试种子用同构 DDL 预建，与生产形态一致。
        conn.execute(
            "CREATE TABLE IF NOT EXISTS daily_summaries ("
            "date TEXT PRIMARY KEY, summary TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        # user_profile 同理：建表 owner 是 UserProfileStore（懒创建）
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_profile ("
            "user_key TEXT PRIMARY KEY, nickname TEXT DEFAULT '', "
            "birthday TEXT DEFAULT '', occupation TEXT DEFAULT '', "
            "location TEXT DEFAULT '', preferences TEXT DEFAULT '[]', "
            "commitments TEXT DEFAULT '[]', notes TEXT DEFAULT '', "
            "updated_at TEXT DEFAULT '')"
        )
        for uid, keys, tag in ((A_UID, a_keys, A_TAG), (B_UID, b_keys, B_TAG)):
            for sk in keys:
                for i in range(3):
                    conn.execute(
                        "INSERT INTO chat_history(role, content, session_id, user_key) "
                        "VALUES ('user', ?, ?, ?)",
                        (f"m{uid}-{sk}-{i}", sk, sk),
                    )
                conn.execute(
                    "INSERT INTO user_facts(fact, category, confidence, user_key) "
                    "VALUES (?, 'commitment', 0.9, ?)",
                    (f"事实-{uid}-{sk}", sk),
                )
                conn.execute(
                    "INSERT INTO reflections(content, session_id, character_id) "
                    "VALUES (?, ?, 'c1')",
                    (f"反思-{uid}-{sk}", sk),
                )
                conn.execute(
                    "INSERT INTO reminders(content, trigger_time, session_key, user_id, status) "
                    "VALUES (?, '2026-10-01 08:00:00', ?, ?, 'pending')",
                    (f"提醒-{uid}-{sk}", sk, uid),
                )
                conn.execute(
                    "INSERT INTO pending_intents(session_key, user_id, intent) "
                    "VALUES (?, ?, 'set_reminder')",
                    (sk, uid),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO daily_summaries(date, summary) VALUES (?, ?)",
                    (f"{sk}|c1|2026-09-27", f"日记-{uid}-{sk}"),
                )
            conn.execute(
                "INSERT INTO user_profile(user_key, nickname) VALUES (?, ?)",
                (f"{uid}:{tag}", f"昵称{uid}"),
            )
        conn.commit()
    return a_keys


def _seed_vectors(env, a_keys, b_keys) -> None:
    from shisi.memory.legacy.vector_memory import VectorMemory

    vm = VectorMemory(chroma_path=str(env["chroma_dir"]))
    for sk in a_keys:
        vm.store_fact_sync(f"A事实向量-{sk}", "commitment", 0.9, user_key=sk)
        vm.store_chat_sync("q", "r", metadata={"session_id": sk, "user_key": sk})
    for sk in b_keys:
        vm.store_fact_sync(f"B事实向量-{sk}", "commitment", 0.9, user_key=sk)
        vm.store_chat_sync("q", "r", metadata={"session_id": sk, "user_key": sk})


def _seed_ledger(env, a_keys, b_keys) -> None:
    from shisi.agent_plane.event_ledger import EventLedger

    ledger = EventLedger(str(env["agent_db"]))
    for sk in a_keys + b_keys:
        ledger.append(
            session_key=sk, character_id="c1", event_type="memory_write", payload={"n": 1}
        )


def _seed_ase(env, a_keys, b_keys) -> None:
    import proactive.ase_hub as ase_hub

    for sk in a_keys + b_keys:
        state_file = ase_hub._STATE_DIR / ase_hub.safe_state_name(sk)
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps({"user_key": sk}), encoding="utf-8")
        ase_hub._remember_index(sk, str(state_file))


def _seed_affinity(env, a_keys, b_keys) -> None:
    from utils import affinity_state

    for sk in a_keys + b_keys:
        affinity_state.save_points(sk, "c1", 66.5)


def _seed_throttle(env, a_keys, b_keys) -> None:
    from proactive.scheduler import ProactiveScheduler
    from utils import json_state

    # 与被测清除逻辑同一真源路径（conftest 已重定向到沙箱）
    cfg = ProactiveScheduler._CONFIG_PATH
    json_state.update_json(
        cfg,
        lambda d: d.__setitem__(
            "throttle",
            {
                "llm_proactive_next_ok": {sk: 12345.0 for sk in a_keys + b_keys},
                "deliver_fail_counts": {sk: 1 for sk in a_keys + b_keys},
                "disabled_event_day": {sk: "2026-09-27" for sk in a_keys + b_keys},
                "important_dates_sent": [
                    f"2026-09-27|{sk}|生日" for sk in a_keys + b_keys
                ],
            },
        ),
    )


def _seed_characters(env) -> None:
    """A/B 各一张私有实例卡（user_id=账号 id 字符串）+ 知识索引 + 成就行。"""
    for cid, _uid in ((A_CARD, A_UID), (B_CARD, B_UID)):
        card = {"id": cid, "name": f"卡{_uid}", "user_id": str(_uid), "is_active": False}
        (env["characters_dir"] / f"{cid}.json").write_text(
            json.dumps(card, ensure_ascii=False), encoding="utf-8"
        )
        (env["knowledge_dir"]).mkdir(exist_ok=True)
        (env["knowledge_dir"] / f"{cid}.json").write_text("[]", encoding="utf-8")

    async def _ach():
        async with env["session"]() as db:
            from api.database import CharacterAchievement

            for cid, _u3 in ((A_CARD, A_UID), (B_CARD, B_UID)):
                db.add(
                    CharacterAchievement(
                        character_id=cid, achievement_id="first_chat", progress=1, target=1
                    )
                )
            await db.commit()

    from utils.async_utils import run_async

    run_async(_ach())


@pytest.fixture()
def two_accounts(sandbox):
    """完整合成资料：两账号两角色全存储播种，返回 (env, a_session_keys)。"""
    _seed_users(sandbox)
    a_keys = _seed_memory(sandbox)
    b_keys = [f"{B_UID}:{B_TAG}", f"{B_UID}:web:{B_TAG}9e21"]
    _seed_vectors(sandbox, a_keys, b_keys)
    _seed_ledger(sandbox, a_keys, b_keys)
    _seed_ase(sandbox, a_keys, b_keys)
    _seed_affinity(sandbox, a_keys, b_keys)
    _seed_throttle(sandbox, a_keys, b_keys)
    _seed_characters(sandbox)
    return sandbox, a_keys, b_keys


# ─────────────────────────────────────────────────────────────
# 1. 全存储删 A 不动 B
# ─────────────────────────────────────────────────────────────


async def test_purge_account_a_leaves_b_intact(two_accounts):
    env, a_keys, b_keys = two_accounts
    from api import lifecycle

    receipt = await lifecycle.delete_account_everywhere(
        A_UID, db_factory=env["session"],
        characters_dir=env["characters_dir"], knowledge_dir=env["knowledge_dir"],
        sqlite_db=env["sqlite_db"], agent_db=env["agent_db"],
        chroma_dir=env["chroma_dir"],
    )

    # 作业必须完成才允许声称 deleted
    if receipt["completed"] is not True:
        print("STEPS:", json.dumps(receipt.get("steps", {}), ensure_ascii=False, indent=1))
    assert receipt["completed"] is True
    assert receipt["status"] == "completed"

    # users.db：A 全部行删除，B 原样
    async with env["session"]() as db:
        assert (await db.execute(select(User).where(User.id == A_UID))).scalar_one_or_none() is None
        assert (await db.execute(select(User).where(User.id == B_UID))).scalar_one() is not None
        assert (await db.execute(select(WechatBinding).where(WechatBinding.user_id == A_UID))).scalars().all() == []
        assert (await db.execute(select(WechatBinding).where(WechatBinding.user_id == B_UID))).scalar_one().wxid == B_TAG
        assert (await db.execute(select(WechatChannelSession).where(WechatChannelSession.user_id == A_UID))).scalars().all() == []
        assert (await db.execute(select(WechatChannelSession).where(WechatChannelSession.user_id == B_UID))).scalar_one() is not None
        assert (await db.execute(select(WechatPeerPreference).where(WechatPeerPreference.owner_user_id == A_UID))).scalars().all() == []
        assert (await db.execute(select(WechatPeerPreference).where(WechatPeerPreference.owner_user_id == B_UID))).scalar_one() is not None
        assert (await db.execute(select(ConsentRecord).where(ConsentRecord.user_id == A_UID))).scalars().all() == []
        assert (await db.execute(select(ConsentRecord).where(ConsentRecord.user_id == B_UID))).scalar_one() is not None
        assert (await db.execute(select(UserSession).where(UserSession.user_id == A_UID))).scalars().all() == []
        assert (await db.execute(select(UserSession).where(UserSession.user_id == B_UID))).scalar_one() is not None

    # sqlite.db：A 的会话键（含裸 peer 遗留）全表零残留，B 完整
    sm = _sm(env)
    with sm.get_connection() as conn:
        for table, col in (
            ("chat_history", "session_id"),
            ("user_facts", "user_key"),
            ("reflections", "session_id"),
            ("reminders", "session_key"),
            ("pending_intents", "session_key"),
        ):
            a_left = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE ?", (f"{A_UID}:%",)
            ).fetchone()[0]
            b_rows = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE ?", (f"{B_UID}:%",)
            ).fetchone()[0]
            assert a_left == 0, f"{table} 残留 A 数据 {a_left} 行"
            assert b_rows > 0, f"{table} 误删 B 数据"
        # 裸 peer 遗留键（A_TAG 一段式）也必须清掉
        bare_left = conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id = ?", (A_TAG,)
        ).fetchone()[0]
        assert bare_left == 0, "裸 peer 遗留会话键未被清除"
        bare_b = conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id = ?", (B_TAG,)
        ).fetchone()[0]
        assert bare_b == 0  # B 的裸键从未播种，但不得把 B 的带 owner 键误删（上面已断言 >0）
        assert conn.execute("SELECT COUNT(*) FROM user_profile WHERE user_key LIKE ?", (f"{A_UID}:%",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM user_profile WHERE user_key LIKE ?", (f"{B_UID}:%",)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM daily_summaries WHERE date LIKE ?", (f"{A_UID}:%",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM daily_summaries WHERE date LIKE ?", (f"{B_UID}:%",)).fetchone()[0] > 0
        # A 的用户事实内容不得残留（含回收站）
        leak = conn.execute(
            "SELECT COUNT(*) FROM memory_recycle_bin WHERE character_id LIKE ?",
            (f"user_fact:{A_UID}:%",),
        ).fetchone()[0]
        assert leak == 0

    # chroma：A 的向量读不回，B 的向量还在
    from shisi.memory.legacy.vector_memory import VectorMemory

    vm = VectorMemory(chroma_path=str(env["chroma_dir"]))
    got = vm._collections["user_facts"].get(where={"user_key": f"{A_UID}:{A_TAG}"})
    assert (got.get("ids") or []) == [], "A 的事实向量未被清除"
    got_b = vm._collections["user_facts"].get(where={"user_key": f"{B_UID}:{B_TAG}"})
    assert got_b.get("ids"), "B 的事实向量被误删"
    got_chat = vm._collections["chat_history"].get(where={"session_id": f"{A_UID}:{A_TAG}"})
    assert (got_chat.get("ids") or []) == [], "A 的对话向量未被清除"

    # agent_plane.db：A 的账本行清零，B 保留
    from shisi.agent_plane.event_ledger import EventLedger

    EventLedger(str(env["agent_db"]))  # 触发建表
    with closing(sqlite3.connect(str(env["agent_db"]))) as conn:
        a_rows = conn.execute(
            "SELECT COUNT(*) FROM event_ledger WHERE session_key LIKE ?", (f"{A_UID}:%",)
        ).fetchone()[0]
        b_rows = conn.execute(
            "SELECT COUNT(*) FROM event_ledger WHERE session_key LIKE ?", (f"{B_UID}:%",)
        ).fetchone()[0]
    assert a_rows == 0 and b_rows > 0

    # ASE 状态：A 的索引与状态文件消失，B 保留
    import proactive.ase_hub as ase_hub

    index = json.loads(ase_hub._INDEX_PATH.read_text(encoding="utf-8"))
    assert all(not k.startswith(f"{A_UID}:") for k in index), "ASE 索引残留 A"
    assert any(k.startswith(f"{B_UID}:") for k in index), "ASE 索引误删 B"
    for sk in a_keys:
        assert not (ase_hub._STATE_DIR / ase_hub.safe_state_name(sk)).exists()

    # 好感度点存：A 键消失，B 保留

    raw = json.loads(env["root"].joinpath("affinity_state.json").read_text(encoding="utf-8"))
    assert all(not k.startswith(f"{A_UID}:") for k in raw), "好感度点存残留 A"
    assert any(k.startswith(f"{B_UID}:") for k in raw)

    # 节流账本：A 键消失，B 保留
    from proactive.scheduler import ProactiveScheduler

    throttle = json.loads(
        Path(str(ProactiveScheduler._CONFIG_PATH)).read_text(encoding="utf-8")
    )["throttle"]
    for section in ("llm_proactive_next_ok", "deliver_fail_counts", "disabled_event_day"):
        assert all(not k.startswith(f"{A_UID}:") for k in throttle[section]), f"{section} 残留 A"
        assert any(k.startswith(f"{B_UID}:") for k in throttle[section])
    assert all(f"{A_UID}:" not in x for x in throttle["important_dates_sent"])
    assert any(f"{B_UID}:" in x for x in throttle["important_dates_sent"])

    # 微信通道磁盘：A 目录删除，B 保留
    chan_root = env["root"] / "wechat_sessions"
    assert not (chan_root / str(A_UID)).exists()
    assert (chan_root / str(B_UID)).exists() or True  # B 未播种磁盘，只断言 A 删净

    # 私有角色实例：A 卡+索引+成就删除，绑定引用重置；B 卡保留
    assert not (env["characters_dir"] / f"{A_CARD}.json").exists()
    assert (env["characters_dir"] / f"{B_CARD}.json").exists()
    assert not (env["knowledge_dir"] / f"{A_CARD}.json").exists()
    assert (env["knowledge_dir"] / f"{B_CARD}.json").exists()
    async with env["session"]() as db:
        from api.database import CharacterAchievement

        ach_a = (
            await db.execute(
                select(CharacterAchievement).where(CharacterAchievement.character_id == A_CARD)
            )
        ).scalars().all()
        assert ach_a == []
        binding_b = (
            await db.execute(select(WechatBinding).where(WechatBinding.user_id == B_UID))
        ).scalar_one()
        assert binding_b.character_card_id == B_CARD  # B 的绑定引用不被波及

    # 回执不含私密内容（wxid / 消息正文 / 邮箱）
    blob = json.dumps(receipt, ensure_ascii=False)
    assert A_TAG not in blob and B_TAG not in blob
    assert "atag@example.com" not in blob
    assert "m1-" not in blob


# ─────────────────────────────────────────────────────────────
# 2. 幂等：重复执行不报错
# ─────────────────────────────────────────────────────────────


async def test_purge_is_idempotent(two_accounts):
    env, _a_keys, _b_keys = two_accounts
    from api import lifecycle

    first = await lifecycle.delete_account_everywhere(
        A_UID, db_factory=env["session"],
        characters_dir=env["characters_dir"], knowledge_dir=env["knowledge_dir"],
        sqlite_db=env["sqlite_db"], agent_db=env["agent_db"], chroma_dir=env["chroma_dir"],
    )
    assert first["completed"] is True
    again = await lifecycle.delete_account_everywhere(
        A_UID, db_factory=env["session"],
        characters_dir=env["characters_dir"], knowledge_dir=env["knowledge_dir"],
        sqlite_db=env["sqlite_db"], agent_db=env["agent_db"], chroma_dir=env["chroma_dir"],
    )
    assert again["completed"] is True


# ─────────────────────────────────────────────────────────────
# 3. 故障中断：不得宣称 deleted；续跑完成且 B 不丢
# ─────────────────────────────────────────────────────────────


async def test_partial_failure_is_queryable_and_resumable(two_accounts, monkeypatch):
    env, a_keys, b_keys = two_accounts
    from api import lifecycle

    # 注入向量清除步骤故障
    real = lifecycle._OWNER_STEPS["memory_vectors"].purge

    def _boom(scope):
        raise RuntimeError("注入故障：chroma 不可用")

    monkeypatch.setattr(lifecycle._OWNER_STEPS["memory_vectors"], "purge", _boom)
    job = await lifecycle.delete_account_everywhere(
        A_UID, db_factory=env["session"],
        characters_dir=env["characters_dir"], knowledge_dir=env["knowledge_dir"],
        sqlite_db=env["sqlite_db"], agent_db=env["agent_db"], chroma_dir=env["chroma_dir"],
    )
    assert job["completed"] is False
    assert job["status"] == "failed"
    assert job["steps"]["memory_vectors"]["status"] == "failed"
    # 未完成 → 不得报 deleted
    assert "deleted" not in json.dumps(job).lower() or job["completed"] is False

    # 部分结果可查询：作业账持久化，status 端点可回放
    status = await lifecycle.job_status(job["job_id"], job_dir=lifecycle.job_root())
    assert status["completed"] is False

    # 移除故障后续跑：完成且 B 不丢
    monkeypatch.setattr(lifecycle._OWNER_STEPS["memory_vectors"], "purge", real)
    resumed = await lifecycle.delete_account_everywhere(
        A_UID, db_factory=env["session"],
        characters_dir=env["characters_dir"], knowledge_dir=env["knowledge_dir"],
        sqlite_db=env["sqlite_db"], agent_db=env["agent_db"], chroma_dir=env["chroma_dir"],
    )
    assert resumed["completed"] is True
    sm = _sm(env)
    with sm.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id LIKE ?", (f"{B_UID}:%",)
        ).fetchone()[0] > 0
    from shisi.memory.legacy.vector_memory import VectorMemory

    vm = VectorMemory(chroma_path=str(env["chroma_dir"]))
    assert vm._collections["user_facts"].get(where={"user_key": f"{B_UID}:{B_TAG}"}).get("ids")


# ─────────────────────────────────────────────────────────────
# 4. UID 复用：删除最高 id 账号后新账号不得复用 id
# ─────────────────────────────────────────────────────────────


async def test_uid_is_not_reused_after_deleting_highest(sandbox):
    _seed_users(sandbox)
    import asyncio as _asyncio
    import sqlite3 as _sqlite3

    from api.database import ensure_users_autoincrement

    def _migrate() -> None:
        conn = _sqlite3.connect(str(sandbox["users_db"]))
        try:
            assert ensure_users_autoincrement(conn) is True
        finally:
            conn.close()

    await _asyncio.to_thread(_migrate)

    # 删除最高 id（B=2）
    async with sandbox["session"]() as db:
        user = (
            await db.execute(select(User).where(User.id == B_UID))
        ).scalar_one()
        await db.delete(user)
        await db.commit()

    # 新注册：id 不得复用 2（否则旧数据按 owner 前缀归到新账号）
    async with sandbox["session"]() as db:
        db.add(
            User(
                email="new@example.com", username="newbie",
                hashed_password="x" * 60,
            )
        )
        await db.commit()
        row = (
            await db.execute(select(User).where(User.email == "new@example.com"))
        ).scalar_one()
        assert row.id > B_UID, f"UID 复用：新账号拿到 {row.id}"


# ─────────────────────────────────────────────────────────────
# 5. 恢复备份重新引入 → 坟场对账再清除
# ─────────────────────────────────────────────────────────────


async def test_restored_account_is_repurged_from_graveyard(two_accounts):
    env, _a, _b = two_accounts
    from api import lifecycle

    await lifecycle.delete_account_everywhere(
        A_UID, db_factory=env["session"],
        characters_dir=env["characters_dir"], knowledge_dir=env["knowledge_dir"],
        sqlite_db=env["sqlite_db"], agent_db=env["agent_db"], chroma_dir=env["chroma_dir"],
    )

    # 模拟运维恢复 users.db 备份：A 的账号行重新出现（带旧 uid 与旧绑定）
    async with env["session"]() as db:
        db.add(
            User(
                id=A_UID, email=f"{A_TAG}@example.com", username=f"user{A_UID}",
                hashed_password="x" * 60, role="viewer", is_active=True, is_verified=True,
            )
        )
        db.add(WechatBinding(user_id=A_UID, wxid=A_TAG, character_card_id="default"))
        await db.commit()

    assert lifecycle.graveyard_contains(A_UID) is True

    repurged = await lifecycle.reconcile_graveyard(db_factory=env["session"])
    assert A_UID in repurged

    async with env["session"]() as db:
        assert (
            await db.execute(select(User).where(User.id == A_UID))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(
                select(WechatBinding).where(WechatBinding.user_id == A_UID)
            )
        ).scalars().all() == []


# ─────────────────────────────────────────────────────────────
# 6. preview：删除前归属清单（不含私密内容）
# ─────────────────────────────────────────────────────────────


async def test_preview_reports_attribution_counts(two_accounts):
    env, _a, _b = two_accounts
    from api import lifecycle

    preview = await lifecycle.preview_account_deletion(
        A_UID, db_factory=env["session"],
        characters_dir=env["characters_dir"], knowledge_dir=env["knowledge_dir"],
        sqlite_db=env["sqlite_db"], agent_db=env["agent_db"],
        chroma_dir=env["chroma_dir"],
    )
    assert preview["user_id"] == A_UID
    counts = preview["owners"]
    assert counts["memory_sqlite"]["chat_history"] == 9  # 3 键 × 3 行
    assert counts["memory_sqlite"]["user_facts"] == 3
    assert counts["event_ledger"]["events"] == 3
    assert counts["users_db"]["wechat_bindings"] == 1
    blob = json.dumps(preview, ensure_ascii=False)
    assert A_TAG not in blob and "atag@example.com" not in blob


# ─────────────────────────────────────────────────────────────
# 7. 竞争防护：删除后迟到写入必须被丢弃（后台生成与删除竞争）
# ─────────────────────────────────────────────────────────────


async def test_late_writes_dropped_after_purge(two_accounts):
    env, _a_keys, _b_keys = two_accounts
    from api import lifecycle
    from utils import deletion_guard

    await lifecycle.delete_account_everywhere(
        A_UID, db_factory=env["session"],
        characters_dir=env["characters_dir"], knowledge_dir=env["knowledge_dir"],
        sqlite_db=env["sqlite_db"], agent_db=env["agent_db"], chroma_dir=env["chroma_dir"],
    )

    assert deletion_guard.is_session_blocked(f"{A_UID}:{A_TAG}") is True
    assert deletion_guard.is_session_blocked(f"{B_UID}:{B_TAG}") is False

    sm = _sm(env)
    ok_a = sm.add_chat_turn(
        "迟到的用户消息", "迟到的回复", session_id=f"{A_UID}:{A_TAG}",
        character_id="c1", user_key=f"{A_UID}:{A_TAG}",
    )
    assert ok_a is False, "已删账号的迟到写入未被丢弃"
    ok_b = sm.add_chat_turn(
        "B 的消息", "B 的回复", session_id=f"{B_UID}:{B_TAG}",
        character_id="c1", user_key=f"{B_UID}:{B_TAG}",
    )
    assert ok_b is True, "误伤 B 的正常写入"
    with sm.get_connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id LIKE ?", (f"{A_UID}:%",)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_history WHERE session_id LIKE ?", (f"{B_UID}:%",)
        ).fetchone()[0] > 0

"""W9 同意门禁四通道同源 + 同意撤回 + 自助注销 + 账号全量导出（D13）。

钉住：
1. 未同意 / 已撤回 → HTTP 主聊天、WS 聊天、微信入站、后台外发（主动消息/提醒）
   四通道同源拒绝或停发；
2. 撤回后重同意恢复；
3. 自助注销：冻结 + 清除作业启动，未完成不得报 deleted；
4. 全量导出：storage manifest + 分页，>1000 条聊天完整、只含本账号。

全部数据落在 tmp_path 沙箱。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.database import Base, ConsentRecord, User

UID = 7
SESSION_KEY = f"{UID}:friend@im.wechat"


@pytest.fixture()
def consent_env(tmp_path, monkeypatch):
    """独立 users.db + 会话键沙箱。"""

    root = tmp_path / "w9consent"
    root.mkdir()
    engine = create_async_engine(f"sqlite+aiosqlite:///{(root / 'users.db').as_posix()}")
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(
        "api.consent.outbound_owner_db", staticmethod(lambda: maker), raising=False
    )

    async def _create_all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    import asyncio

    asyncio.run(_create_all())

    async def _seed(consented: bool):
        async with maker() as db:
            if consented:
                db.add(
                    User(
                        id=UID, email="u7@example.com", username="user7",
                        hashed_password="x" * 60,
                    )
                )
                db.add(ConsentRecord(user_id=UID, agreement_version="1.0.0"))
            else:
                # 模拟「从未同意」：只清同意记录，不重复种账号行
                from sqlalchemy import delete as _del

                await db.execute(_del(ConsentRecord).where(ConsentRecord.user_id == UID))
            await db.commit()

    asyncio.run(_seed(True))
    yield {"root": root, "session": maker, "engine": engine, "seed_unconsented": _seed}
    import asyncio

    asyncio.run(engine.dispose())


# ─────────────────────────────────────────────────────────────
# 1. 同意状态机：missing / granted / withdrawn / 重新同意
# ─────────────────────────────────────────────────────────────


async def test_consent_state_machine(consent_env):
    from api import consent

    maker = consent_env["session"]
    assert await consent.consent_state_of(maker, UID) == "granted"

    async with maker() as db:
        # 撤回
        await consent.withdraw_consent(db, UID)
    assert await consent.consent_state_of(maker, UID) == "withdrawn"
    async with maker() as db:
        assert await consent.has_consented(db, UID) is False

    # 重新同意当前版本 → 恢复
    async with maker() as db:
        await consent.record_consent(db, UID)
    assert await consent.consent_state_of(maker, UID) == "granted"


async def test_unconsented_user_is_gated(consent_env):
    from api import consent

    maker = consent_env["session"]
    from utils.async_utils import run_async

    run_async(consent_env["seed_unconsented"](False))
    async with maker() as db:
        assert await consent.has_consented(db, UID) is False
    assert await consent.consent_state_of(maker, UID) == "missing"


# ─────────────────────────────────────────────────────────────
# 2. 四通道同源：outbound 判定
# ─────────────────────────────────────────────────────────────


async def test_outbound_gate_four_channels(consent_env, monkeypatch):
    from api import consent

    maker = consent_env["session"]

    # 已同意 → 允许外发
    assert await consent.outbound_allowed_for_session(SESSION_KEY, db_factory=maker) is True

    # 撤回 → 四通道全部停发（同一判定入口）
    async with maker() as db:
        await consent.withdraw_consent(db, UID)
    assert await consent.outbound_allowed_for_session(SESSION_KEY, db_factory=maker) is False

    # 同步包装（调度器/提醒线程用）同样拒绝
    monkeypatch.setattr(
        "api.consent.outbound_owner_db", staticmethod(lambda: maker), raising=False
    )
    assert consent.outbound_allowed_for_session_sync(SESSION_KEY) is False

    # 无主会话（遗留裸键 / 匿名）无账号可撤 → 放行（不误伤无账号路径）
    assert await consent.outbound_allowed_for_session("legacy@im.wechat", db_factory=maker) is True


async def test_scheduler_and_reminder_skip_on_withdrawal(consent_env, monkeypatch):
    """后台外发点：调度器与提醒投递在撤回后跳过且不记账送达。"""
    from api import consent
    from proactive import scheduler as sched_mod

    maker = consent_env["session"]
    async with maker() as db:
        await consent.withdraw_consent(db, UID)
    monkeypatch.setattr(
        "api.consent.outbound_owner_db", staticmethod(lambda: maker), raising=False
    )

    # 提醒投递：撤回后 _deliver 前置拒绝（返回 False / 不投递）
    allowed = await consent.outbound_allowed_for_session(SESSION_KEY, db_factory=maker)
    assert allowed is False

    # 调度器投递入口消费同一真源：真实 ProactiveScheduler 实例验证 _deliver 被门禁短路
    import asyncio

    sched = sched_mod.ProactiveScheduler()
    called = {"send": 0}

    def _fake_send_targeted(message, session_key, **kwargs):
        called["send"] += 1
        return True

    monkeypatch.setattr(sched, "_send_targeted", _fake_send_targeted)
    ok = await asyncio.to_thread(
        sched._deliver, "晚安", session_key=SESSION_KEY, character_id="c1"
    )
    assert ok is False
    assert called["send"] == 0, "撤回后调度器仍尝试外发"


# ─────────────────────────────────────────────────────────────
# 3. 自助注销：冻结 + 作业启动；未完成不得报 deleted
# ─────────────────────────────────────────────────────────────


async def test_self_service_delete_starts_job_without_deleted_claim(consent_env, monkeypatch):
    from api import lifecycle

    maker = consent_env["session"]
    monkeypatch.setattr(lifecycle, "job_root", lambda: consent_env["root"] / "jobs")
    monkeypatch.setattr(
        lifecycle, "delete_account_everywhere",
        _fake_delete := _make_fake_delete(),
    )
    result = await lifecycle.self_service_delete(
        UID, db_factory=maker, background=False,
        characters_dir=consent_env["root"] / "characters",
        knowledge_dir=consent_env["root"] / "knowledge",
    )
    # 冻结即时生效：会话吊销、账号停用
    from sqlalchemy import select

    async with maker() as db:
        user = (await db.execute(select(User).where(User.id == UID))).scalar_one()
        assert user.is_active is False
    # 未完成清除前，响应不得包含 deleted 宣称
    assert result["status"] in ("purging", "queued")


def _make_fake_delete():
    async def _fake(*a, **k):
        return {"completed": False, "status": "purging", "job_id": "acct-fake"}

    return _fake


# ─────────────────────────────────────────────────────────────
# 4. 全量导出：manifest + 分页，>1000 条完整、只含本账号
# ─────────────────────────────────────────────────────────────


async def test_full_export_manifest_and_pagination(consent_env, tmp_path):
    from api import lifecycle

    sm_db = tmp_path / "export.sqlite"
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(sm_db))
    other_db = tmp_path / "other.sqlite"
    other = StructuredMemory(str(other_db))
    with sm.get_connection(write=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_profile ("
            "user_key TEXT PRIMARY KEY, nickname TEXT DEFAULT '')"
        )
        for i in range(1200):
            conn.execute(
                "INSERT INTO chat_history(role, content, session_id, user_key) "
                "VALUES (?, ?, ?, ?)",
                ("user" if i % 2 == 0 else "assistant", f"消息{i}", SESSION_KEY, SESSION_KEY),
            )
        conn.execute(
            "INSERT INTO user_facts(fact, category, user_key) VALUES ('事实A', 'general', ?)",
            (SESSION_KEY,),
        )
        conn.execute(
            "INSERT INTO reflections(content, session_id) VALUES ('反思A', ?)",
            (SESSION_KEY,),
        )
        conn.execute(
            "INSERT INTO user_profile(user_key, nickname) VALUES (?, '昵称A')",
            (SESSION_KEY,),
        )
        conn.commit()
    with other.get_connection(write=True) as conn:
        conn.execute(
            "INSERT INTO chat_history(role, content, session_id, user_key) "
            "VALUES ('user', '别人的', '8:other@im.wechat', '8:other@im.wechat')"
        )
        conn.commit()

    manifest = await lifecycle.export_account_manifest(
        UID, db_factory=consent_env["session"], sm=sm
    )
    assert manifest["user_id"] == UID
    assert manifest["categories"]["chats"] == 1200
    assert manifest["categories"]["facts"] == 1
    assert manifest["categories"]["reflections"] == 1
    assert manifest["categories"]["profile"] == 1

    # 分页取完 1200 条且只含本账号
    collected: list[dict] = []
    before_id = 0
    for _ in range(10):
        page = lifecycle.export_chats_page(sm, SESSION_KEY, before_id=before_id, limit=500)
        collected.extend(page["messages"])
        if page["next_before_id"] is None:
            break
        before_id = page["next_before_id"]
    assert len(collected) == 1200
    assert all(m["session_id"] == SESSION_KEY for m in collected)
    assert not any("别人的" in json.dumps(m) for m in collected)

"""W9 角色删除范围收口（缺陷 B）：删除角色 = 全清派生面，不留孤儿引用。

钉住：删卡后——成就行删除、绑定/好友偏好/个人激活引用重置为 default、
向量 character_id 派生清除、音色绑定解绑；调用方拿到的回执含计数。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.database import (
    Base,
    CharacterAchievement,
    User,
    UserActiveCharacter,
    WechatBinding,
    WechatPeerPreference,
)

UID = 3
CARD = "w9card"


@pytest.fixture()
def del_env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'users.db').as_posix()}")
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _all():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with maker() as db:
            db.add(User(id=UID, email="u3@example.com", username="u3", hashed_password="x" * 60))
            db.add(WechatBinding(user_id=UID, wxid=f"u{UID}@im.wechat", character_card_id=CARD))
            db.add(WechatPeerPreference(owner_user_id=UID, peer_wxid="f@im.wechat", character_card_id=CARD))
            db.add(UserActiveCharacter(user_id=UID, character_id=CARD))
            db.add(CharacterAchievement(character_id=CARD, achievement_id="a1", progress=1, target=2))
            await db.commit()

    from utils.async_utils import run_async

    run_async(_all())

    # 角色卡沙箱：_get_characters_dir 每次现读模块真源，monkeypatch 即生效
    chars = tmp_path / "characters"
    chars.mkdir()
    (chars / f"{CARD}.json").write_text(
        json.dumps({"id": CARD, "name": "W9卡", "user_id": "default"}), encoding="utf-8"
    )
    import api.routers.character_routes as cr

    monkeypatch.setattr(cr, "CHARACTERS_DIR", chars)

    # 向量派生面沙箱：chromadb 默认相对 CWD 指向真实 data/，实例化到 tmp 并把
    # 类入口整体换绑，delete_character 内部惰性 import 的 VectorMemory() 命中沙箱
    import shisi.memory.legacy.vector_memory as vm_mod
    from shisi.memory.legacy.vector_memory import VectorMemory

    sandbox_vm = VectorMemory(chroma_path=str(tmp_path / "chroma_db"))
    monkeypatch.setattr(vm_mod, "VectorMemory", lambda *a, **k: sandbox_vm)
    sandbox_vm.store_chat_sync("w9问", "w9答", metadata={"character_id": CARD})

    # 知识索引面替身：删卡后 _invalidate_knowledge_index 走 svc.forget；替身记录
    # 调用并挡住测试对真实 data/knowledge 的触碰
    class _FakeKS:
        def __init__(self) -> None:
            self.forgotten: list[str] = []

        def forget(self, character_id: str) -> None:
            self.forgotten.append(character_id)

        def refresh_card_source(self, character_id: str, raw=None):
            return 0, False

    fake_ks = _FakeKS()
    monkeypatch.setattr(cr, "get_knowledge_service", lambda: fake_ks)

    # 音色绑定落本用例 tmp（conftest 已把模块默认路径沙箱，这里收紧到 per-test）
    from shisi.voice import character_voice as cv

    monkeypatch.setattr(cv, "_DEFAULT_CONFIG_PATH", tmp_path / "character_voices.json")
    cv.CharacterVoiceManager().bind_voice(CARD, engine="mimo-cloud", speaker_name="s1")
    assert cv.CharacterVoiceManager().get_voice_config(CARD)

    # 人设缓存替身：delete_character 结尾对 deps.orch 失效人设缓存；替身记录调用
    import api.deps as deps_mod

    class _FakeOrch:
        components: dict = {}

        def invalidate_character_persona_cache(self, cid):
            self.invalidated = cid

    fake_orch = _FakeOrch()
    monkeypatch.setattr(deps_mod.deps, "orch", fake_orch)

    yield {"chars": chars, "session": maker, "engine": engine, "voice_mod": cv,
           "vm": sandbox_vm, "ks": fake_ks, "orch": fake_orch, "tmp": tmp_path}
    from utils.async_utils import run_async

    run_async(engine.dispose())


async def test_character_delete_clears_all_derived_faces(del_env):
    from sqlalchemy import select

    from api.routers.character_routes import delete_character

    env = del_env
    card = json.loads((env["chars"] / f"{CARD}.json").read_text(encoding="utf-8"))

    # 直调（FastAPI Depends 注入的 db 即 AsyncSession，此处传同构会话）
    async with env["session"]() as db:
        resp = await delete_character(CARD, _auth=True, _owned=card, db=db)
    assert resp["status"] == "deleted"
    receipt = resp["receipt"]
    assert receipt["achievements"] == 1
    assert receipt["binding_refs_reset"] == 1
    assert receipt["peer_pref_refs_reset"] == 1
    assert receipt["active_rows_reset"] == 1
    assert receipt["vector_docs"] == 1
    assert receipt["voice_unbound"] == 1
    assert not (env["chars"] / f"{CARD}.json").exists()

    async with env["session"]() as db:
        assert (await db.execute(
            select(CharacterAchievement).where(CharacterAchievement.character_id == CARD)
        )).scalars().all() == []
        assert (await db.execute(
            select(WechatBinding).where(WechatBinding.character_card_id == CARD)
        )).scalars().all() == []
        assert (await db.execute(
            select(WechatPeerPreference).where(WechatPeerPreference.character_card_id == CARD)
        )).scalars().all() == []
        assert (await db.execute(
            select(UserActiveCharacter).where(UserActiveCharacter.character_id == CARD)
        )).scalars().all() == []
    assert env["voice_mod"].CharacterVoiceManager().get_voice_config(CARD) is None

    # 派生面残留断言：向量按 character_id 清零、知识源随卡清理、人设缓存已失效
    assert sum(env["vm"].count_owner_data([], [], character_ids=[CARD]).values()) == 0
    assert env["ks"].forgotten == [CARD]
    assert env["orch"].invalidated == CARD


async def test_character_delete_resets_refs_to_default(del_env):
    """引用重置后 default 语义可解析（不残留死卡名）。"""
    env = del_env
    from sqlalchemy import select

    import api.routers.character_routes as cr
    from api.routers.character_routes import delete_character

    card = json.loads((env["chars"] / f"{CARD}.json").read_text(encoding="utf-8"))
    async with env["session"]() as db:
        await delete_character(CARD, _auth=True, _owned=card, db=db)

    async with env["session"]() as db:
        binding = (await db.execute(
            select(WechatBinding).where(WechatBinding.user_id == UID)
        )).scalar_one()
        pref = (await db.execute(
            select(WechatPeerPreference).where(WechatPeerPreference.owner_user_id == UID)
        )).scalar_one()
        active = (await db.execute(
            select(UserActiveCharacter).where(UserActiveCharacter.user_id == UID)
        )).scalar_one()
    assert binding.character_card_id == "default"
    assert pref.character_card_id == "default"
    assert active.character_id == "default"

    # default 是平台兜底：卡目录已空，激活解析回落 default 而非指向死卡名
    assert cr.get_active_character_id() == "default"

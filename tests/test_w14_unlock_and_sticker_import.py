"""W14：D10 好感阈值解锁真接线 + D12-K 表情包 ZIP 导入真入库。

任务书判据（BOARD 2026-09-28 W13–W17 出具条）：
- 解锁真实化：check_unlocks 后首次解锁 INSERT 进 affinity_unlocks（user×character×档位）；
- 表情包按 unlock_threshold 过滤（亲和真源 = shisi 刻度 user×character）；
- 语音 75：专属音色未解锁回落默认；
- 话题 25/50：解锁档位进 prompt 注入槽；
- 90 特殊互动：只落表 + HTTP 展示（不改主动决策）；
- ZIP 导入：逐张写 stickers 表 + check_sticker_safety 门禁 + 回执 accepted/failed。
"""

from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path

import pytest

from shisi.affinity.unlock_manager import UnlockManager
from shisi.migrations import run_migrations

# ---------------------------------------------------------------------------
# 夹具：隔离库（走正典 run_migrations 建表）
# ---------------------------------------------------------------------------


def _migrated_db(tmp_path: Path) -> Path:
    db = tmp_path / "w14.sqlite"
    run_migrations(db)
    return db


@pytest.fixture()
def unlock_db(tmp_path: Path) -> Path:
    return _migrated_db(tmp_path)


def _rows(db: Path, sql: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A1：解锁落表 affinity_unlocks
# ---------------------------------------------------------------------------


class TestUnlockPersistence:
    def test_check_unlocks_inserts_rows_with_full_key(self, unlock_db: Path) -> None:
        mgr = UnlockManager(db_path=unlock_db)
        newly = mgr.check_unlocks("u1::c1", 24, 26)
        assert newly, "跨过 25 档应有解锁事件"
        rows = _rows(
            unlock_db,
            "SELECT character_id, threshold, unlock_type, unlock_name FROM affinity_unlocks",
        )
        assert ("u1::c1", 25, "topic", "个人话题") in rows

    def test_first_unlock_only(self, unlock_db: Path) -> None:
        mgr = UnlockManager(db_path=unlock_db)
        assert mgr.check_unlocks("u1::c1", 24, 26)
        # 衰减跌破后再次跨过：事件再次产生，但表内仍只有首次一行
        assert mgr.check_unlocks("u1::c1", 26, 20) == []
        assert mgr.check_unlocks("u1::c1", 20, 30)
        n = _rows(unlock_db, "SELECT COUNT(*) FROM affinity_unlocks")[0][0]
        assert n == 1

    def test_no_db_no_persist_no_raise(self, tmp_path: Path) -> None:
        mgr = UnlockManager()  # 无库（管理/展示场景）
        assert mgr.check_unlocks("c1", 0, 55)  # 事件照常返回
        assert not (tmp_path / "w14.sqlite").exists()

    def test_missing_table_degrades_to_warning(self, tmp_path: Path) -> None:
        db = tmp_path / "empty.sqlite"
        sqlite3.connect(str(db)).close()  # 空库，无 affinity_unlocks 表
        mgr = UnlockManager(db_path=db)
        assert mgr.check_unlocks("c1", 0, 55)  # 不抛，只告警

    def test_recorded_unlocks_reads_table(self, unlock_db: Path) -> None:
        mgr = UnlockManager(db_path=unlock_db)
        mgr.check_unlocks("u1::c1", 0, 55)
        mgr.check_unlocks("u2::c1", 0, 30)
        rec = mgr.recorded_unlocks("u1::c1")
        assert len(rec) == 3
        assert all(r["character_id"] == "u1::c1" for r in rec)
        assert {r["unlock_name"] for r in rec} == {"个人话题", "亲密话题", "专属表情包"}
        assert mgr.recorded_unlocks("u2::c1") != []
        assert mgr.recorded_unlocks("nobody::c1") == []

    def test_enhancer_update_persists_unlocks(self, unlock_db: Path) -> None:
        from shisi.affinity.enhancer import AffinityEnhancer

        enhancer = AffinityEnhancer(db_path=unlock_db)
        _, unlocks = enhancer.update("c1", 30.0, reason="test", user_id="u1")
        assert unlocks, "enhancer.update 应返回解锁事件"
        rows = _rows(
            unlock_db,
            "SELECT character_id FROM affinity_unlocks",
        )
        # 隔离键口径：user×character 完整键（沿 affinity_records 先例）
        assert rows == [("u1::c1",)]

    def test_enhancer_user_isolation_in_unlock_table(self, unlock_db: Path) -> None:
        from shisi.affinity.enhancer import AffinityEnhancer

        enhancer = AffinityEnhancer(db_path=unlock_db)
        enhancer.update("c1", 30.0, reason="test", user_id="u1")
        enhancer.update("c1", 30.0, reason="test", user_id="u2")
        n = _rows(unlock_db, "SELECT COUNT(*) FROM affinity_unlocks")[0][0]
        assert n == 2, "两个用户各自首次解锁各记一行"

    def test_read_user_affinity_resolves_via_shisi_reg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from shisi.affinity import unlock_manager as um

        class _FakeEnhancer:
            def get_value(self, character_id: str, user_id: str = "") -> float:
                return 42.0

        class _FakeReg:
            affinity_enhancer = _FakeEnhancer()

        class _FakeDeps:
            shisi_reg = _FakeReg()

        monkeypatch.setattr("api.deps.deps", _FakeDeps())
        assert um.read_user_affinity("c1", "u1") == 42.0

        class _EmptyDeps:
            shisi_reg = None

        monkeypatch.setattr("api.deps.deps", _EmptyDeps())
        assert um.read_user_affinity("c1", "u1") is None
        assert um.read_user_affinity("c1", "") is None


# ---------------------------------------------------------------------------
# A2：表情包按 unlock_threshold 过滤（round-trip）
# ---------------------------------------------------------------------------


class TestStickerThresholdFilter:
    @pytest.fixture()
    def sticker_env(self, tmp_path: Path):
        from shisi.sticker.sticker_manager import StickerManager

        db = _migrated_db(tmp_path / "sticker.sqlite")
        data_dir = tmp_path / "sticker_data"
        mgr = StickerManager(db_path=db, data_dir=data_dir)
        mgr.add_sticker("locked_one", "开心", ["开心"], "data/stickers/locked_one.png")
        mgr.add_sticker("open_one", "开心", ["开心"], "data/stickers/open_one.png")
        mgr.bind_to_character("c1", ["locked_one"], unlock_threshold=50)
        return mgr

    def test_roundtrip_low_affinity_invisible_high_visible(self, sticker_env) -> None:
        mgr = sticker_env
        mgr._affinity_provider = lambda cid, uid: 30.0
        low_ids = [s["sticker_id"] for s in mgr.recommend(["开心"], character_id="c1", user_id="low")]
        assert "locked_one" not in low_ids, "好感 30 < 50，专属表情包不可见"

        mgr._affinity_provider = lambda cid, uid: 60.0
        high_ids = [s["sticker_id"] for s in mgr.recommend(["开心"], character_id="c1", user_id="high")]
        assert "locked_one" in high_ids, "好感 60 ≥ 50，专属表情包可见"

    def test_locked_sticker_not_leaked_via_general_fill(self, sticker_env) -> None:
        mgr = sticker_env
        # 兜底池必须排除全部角色绑定表情（含未解锁），否则低好感经 general 又可见
        mgr._affinity_provider = lambda cid, uid: 30.0
        low_ids = [s["sticker_id"] for s in mgr.recommend(["开心"], character_id="c1", user_id="low", limit=5)]
        assert "locked_one" not in low_ids

    def test_no_user_context_keeps_admin_visibility(self, sticker_env) -> None:
        mgr = sticker_env
        ids = [s["sticker_id"] for s in mgr.recommend(["开心"], character_id="c1", limit=5)]
        assert "locked_one" in ids, "无用户上下文（管理/绑定面）不启用门禁"

    def test_get_character_sticker_ids_affinity_aware(self, sticker_env) -> None:
        mgr = sticker_env
        assert mgr._get_character_sticker_ids("c1", affinity=30.0) == set()
        assert mgr._get_character_sticker_ids("c1", affinity=60.0) == {"locked_one"}
        assert mgr._get_character_sticker_ids("c1") == {"locked_one"}
        assert mgr._get_character_sticker_ids("c1", affinity=None) == {"locked_one"}

    def test_threshold_zero_always_visible(self, tmp_path: Path) -> None:
        from shisi.sticker.sticker_manager import StickerManager

        db_path = _migrated_db(tmp_path)
        data_dir = tmp_path / "d2"
        mgr = StickerManager(db_path=db_path, data_dir=data_dir)
        mgr.add_sticker("free_one", "开心", ["开心"], "x/free_one.png")
        mgr.bind_to_character("c1", ["free_one"], unlock_threshold=0)
        mgr._affinity_provider = lambda cid, uid: 0.0
        ids = [s["sticker_id"] for s in mgr.recommend(["开心"], character_id="c1", user_id="newbie")]
        assert "free_one" in ids, "阈值 0 的绑定表情不受门禁"


# ---------------------------------------------------------------------------
# A3：语音 75 专属音色过滤回落
# ---------------------------------------------------------------------------


class TestVoiceUnlockGate:
    @pytest.fixture()
    def voice_mgr(self, tmp_path: Path):
        from shisi.voice.character_voice import CharacterVoiceManager

        mgr = CharacterVoiceManager(config_path=str(tmp_path / "cv.json"))
        mgr.bind_voice(
            "c1",
            "mimo-tts",
            speaker_name="female-tianmei",
            voice_id="clone-exclusive-xyz",
            mimo_model="mimo-v2.5-tts-voiceclone",
        )
        return mgr

    def _gate(self, mgr, affinity: float) -> None:
        mgr._affinity_provider = lambda cid, uid: affinity

    def test_below_voice_threshold_falls_back_to_default(self, voice_mgr) -> None:
        from shisi.affinity.unlock_manager import voice_unlock_threshold

        th = voice_unlock_threshold()
        assert th is not None, "config affinity.unlocks 应含 voice 档"
        self._gate(voice_mgr, th - 0.5)
        assert voice_mgr.resolve_voice_spec("c1", user_id="u1") is None, (
            "未达专属语音阈值应回落引擎默认（None → 调用方用默认音色）"
        )

    def test_at_or_above_threshold_keeps_exclusive_voice(self, voice_mgr) -> None:
        from shisi.affinity.unlock_manager import voice_unlock_threshold

        th = voice_unlock_threshold()
        self._gate(voice_mgr, float(th))
        spec = voice_mgr.resolve_voice_spec("c1", user_id="u1")
        assert spec is not None and spec.voice_id == "clone-exclusive-xyz"

    def test_no_user_context_no_gate(self, voice_mgr) -> None:
        self._gate(voice_mgr, 0.0)
        spec = voice_mgr.resolve_voice_spec("c1")
        assert spec is not None and spec.voice_id == "clone-exclusive-xyz"

    def test_missing_affinity_source_no_gate(self, voice_mgr) -> None:
        # provider 返回 None（无法评估）→ 不得误杀专属音色
        voice_mgr._affinity_provider = lambda cid, uid: None
        assert voice_mgr.resolve_voice_spec("c1", user_id="u1") is not None

    def test_unbound_character_still_none(self, tmp_path: Path) -> None:
        from shisi.voice.character_voice import CharacterVoiceManager

        mgr = CharacterVoiceManager(config_path=str(tmp_path / "cv2.json"))
        mgr._affinity_provider = lambda cid, uid: 99.0
        assert mgr.resolve_voice_spec("c-none", user_id="u1") is None


# ---------------------------------------------------------------------------
# A4：话题 25/50 解锁档位进 prompt 注入槽
# ---------------------------------------------------------------------------


class TestTopicUnlockPromptSlot:
    def test_unlocked_topic_names(self) -> None:
        from shisi.affinity.unlock_manager import unlocked_topic_names

        assert unlocked_topic_names(10.0) == []
        assert unlocked_topic_names(30.0) == ["个人话题"]
        assert unlocked_topic_names(60.0) == ["个人话题", "亲密话题"]

    def _svc(self):
        from shisi.application.persona_service import PersonaService

        return PersonaService(config_loader=None, llm_gateway=None)

    def test_prompt_contains_slot_after_25(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "shisi.affinity.unlock_manager.read_user_affinity", lambda cid, uid: 30.0
        )
        prompt = self._svc().build_system_prompt(
            character_id="c1", memory_context={"user_key": "u1"}
        )
        assert "话题解锁" in prompt
        assert "个人话题" in prompt

    def test_prompt_no_slot_below_first_tier(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "shisi.affinity.unlock_manager.read_user_affinity", lambda cid, uid: 10.0
        )
        prompt = self._svc().build_system_prompt(
            character_id="c1", memory_context={"user_key": "u1"}
        )
        assert "话题解锁" not in prompt

    def test_prompt_no_slot_without_user_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "shisi.affinity.unlock_manager.read_user_affinity",
            lambda cid, uid: (_ for _ in ()).throw(AssertionError("不应读取亲和")),
        )
        prompt = self._svc().build_system_prompt(character_id="c1", memory_context={})
        assert "话题解锁" not in prompt


# ---------------------------------------------------------------------------
# A5：落表解锁 HTTP 展示（90 档不改主动决策，只读面）
# ---------------------------------------------------------------------------

def _principal_client(tmp_path: Path, *routers, role: str):
    """挂给定 router 的 app：users 表真库（admin+viewer）+ get_current_user_id
    覆盖（P0 收口后 affinity/sticker 端点均要求登录主体；
    模式沿 tests/test_sec_p0_shisi_surface.py::make_principal_client）。
    返回 TestClient；引擎在本函数内建拆（Windows 文件锁即时释放）。"""
    import asyncio

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from api.auth_jwt import get_current_user_id
    from api.database import Base, User, get_db

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'users.db'}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as s:
            s.add(User(id=1, email="a@test", username="a",
                       hashed_password="x", role="admin",
                       is_active=True, is_verified=True))
            s.add(User(id=2, email="v@test", username="v",
                       hashed_password="x", role="viewer",
                       is_active=True, is_verified=True))
            await s.commit()

    try:
        asyncio.run(_init())

        async def _get_db():
            async with session_maker() as s:
                yield s

        app = FastAPI()
        for r in routers:
            app.include_router(r)
        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[get_current_user_id] = lambda: (1 if role == "admin" else 2)
        return TestClient(app)
    finally:
        asyncio.run(engine.dispose())


class TestUnlockHttpDisplay:
    def test_unlocks_route_shows_recorded_rows(self, unlock_db: Path, tmp_path: Path) -> None:
        """P0 收口后新契约：键归属由 JWT 主体导出——viewer 登录态下，
        query user_id 被主体覆盖，recorded 只能来自主体键 2::c1。"""
        from shisi.affinity.enhancer import AffinityEnhancer
        from shisi.api import affinity_routes

        enhancer = AffinityEnhancer(db_path=unlock_db)
        enhancer.update("c1", 95.0, reason="test", user_id="2")  # 主体键连跨 25/50/75/90
        # 干扰键：若无主体覆盖，query user_id=victim 会读到该键（旧漏洞口径）
        enhancer.update("c1", 10.0, reason="seed-victim", user_id="victim")
        affinity_routes.set_enhancer(enhancer)

        client = _principal_client(tmp_path, affinity_routes.router, role="viewer")
        resp = client.get("/api/shisi/affinity/c1/unlocks", params={"user_id": "victim"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        names = {r["unlock_name"] for r in data["recorded"]}
        assert "特殊互动" in names, "90 档落表后 HTTP 可查（主体键 2::c1）"
        assert "个人话题" in names
        assert data["affinity"] == pytest.approx(
            enhancer.get_value("c1", user_id="2")
        ), "读数必须来自主体键（query user_id=victim 被覆盖）"

    def test_unlocks_route_without_user_keeps_old_shape(
        self, unlock_db: Path, tmp_path: Path
    ) -> None:
        """边界钉：不带 user_id 的响应 shape 不变（unlocks/recorded 字段族）；
        P0 收口后「不带」读主体键——空库下 recorded 仍为空列表。"""
        from shisi.affinity.enhancer import AffinityEnhancer
        from shisi.api import affinity_routes

        affinity_routes.set_enhancer(AffinityEnhancer(db_path=unlock_db))
        client = _principal_client(tmp_path, affinity_routes.router, role="viewer")
        resp = client.get("/api/shisi/affinity/c1/unlocks")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "unlocks" in data and "recorded" in data and data["recorded"] == []


# ---------------------------------------------------------------------------
# B：ZIP 导入真入库 + 安全门禁 + 回执语义
# ---------------------------------------------------------------------------


def _make_zip(path: Path, entries: list[tuple[str, bytes]]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return path


class TestStickerImportPersistence:
    @pytest.fixture()
    def import_env(self, tmp_path: Path):
        from shisi.sticker.importer import StickerImporter

        db_path = _migrated_db(tmp_path)
        data_dir = tmp_path / "stk_data"
        importer = StickerImporter(db_path=db_path, data_dir=data_dir)
        return importer, db_path, tmp_path

    def test_import_writes_sticker_rows(self, import_env) -> None:
        importer, db, tmp_path = import_env
        z = _make_zip(tmp_path / "z.zip", [("happy.png", b"\x89PNG-fake-1"), ("cry.png", b"\x89PNG-fake-2")])
        accepted, failed = importer.import_zip(z, "cat1")
        assert (accepted, failed) == (2, 0)
        rows = _rows(db, "SELECT sticker_id, category, file_path, emotion_tags FROM stickers")
        assert len(rows) == 2, "导入必须逐张写 stickers 表（此前只落文件不可见）"
        assert all(r[1] == "cat1" for r in rows)
        assert all(r[3] == "[]" for r in rows), "emotion_tags 留空 = 不进推荐"

    def test_imported_sticker_visible_in_same_category(self, import_env) -> None:
        importer, db, tmp_path = import_env
        z = _make_zip(tmp_path / "z.zip", [("happy.png", b"\x89PNG-fake-1")])
        importer.import_zip(z, "cat1")
        from shisi.sticker.sticker_manager import StickerManager

        mgr = StickerManager(db_path=db, data_dir=tmp_path / "d")
        listed = [s["sticker_id"] for s in mgr.list_by_category("cat1")]
        assert len(listed) == 1, "导入 → 同分类可见（round-trip）"
        rec = mgr.recommend(["开心"], limit=5)
        assert rec == [], "emotion_tags 留空的导入卡不进情感推荐"

    def test_stable_sticker_id_idempotent_reimport(self, import_env) -> None:
        importer, db, tmp_path = import_env
        z = _make_zip(tmp_path / "z.zip", [("happy.png", b"\x89PNG-fake-1")])
        importer.import_zip(z, "cat1")
        first = _rows(db, "SELECT sticker_id FROM stickers")
        accepted2, _ = importer.import_zip(z, "cat1")
        assert accepted2 == 1
        second = _rows(db, "SELECT sticker_id FROM stickers")
        assert first == second, "同内容同文件名重复导入 id 稳定（不重复堆积）"

    def test_safety_gate_blocks_bad_filename(self, import_env) -> None:
        importer, db, tmp_path = import_env
        z = _make_zip(
            tmp_path / "z.zip",
            [("色情_图.png", b"\x89PNG-bad"), ("ok.png", b"\x89PNG-ok")],
        )
        accepted, failed = importer.import_zip(z, "cat1")
        assert (accepted, failed) == (1, 1), "不合规文件名计入 failed"
        names = _rows(db, "SELECT file_path FROM stickers")
        assert len(names) == 1 and "ok" in names[0][0]
        assert not list(tmp_path.rglob("*色情*")), "被拦截文件不得落盘"

    def test_unsupported_format_still_failed(self, import_env) -> None:
        importer, db, tmp_path = import_env
        z = _make_zip(tmp_path / "z.zip", [("note.txt", b"hello")])
        accepted, failed = importer.import_zip(z, "cat1")
        assert (accepted, failed) == (0, 1)
        assert _rows(db, "SELECT COUNT(*) FROM stickers")[0][0] == 0

    def test_manager_import_zip_passes_db(self, tmp_path: Path) -> None:
        from shisi.sticker.sticker_manager import StickerManager

        db = _migrated_db(tmp_path)
        mgr = StickerManager(db_path=db, data_dir=tmp_path / "d")
        z = _make_zip(tmp_path / "z.zip", [("a.png", b"\x89PNG-a")])
        accepted, failed = mgr.import_zip(z, "catX")
        assert (accepted, failed) == (1, 0)
        assert _rows(db, "SELECT COUNT(*) FROM stickers")[0][0] == 1


class TestStickerImportRouteReceipt:
    def test_route_returns_accepted_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """P0 收口后新契约：sticker import 走 admin 门禁——admin 通道 200
        且回执语义不变（accepted=真实入库数）。"""
        from shisi.api import sticker_routes
        from shisi.sticker.sticker_manager import StickerManager

        db = _migrated_db(tmp_path)
        mgr = StickerManager(db_path=db, data_dir=tmp_path / "d")
        monkeypatch.setattr(sticker_routes, "_manager", mgr)

        client = _principal_client(tmp_path, sticker_routes.router, role="admin")

        z = _make_zip(tmp_path / "z.zip", [("a.png", b"\x89PNG-a"), ("色情.png", b"\x89PNG-b")])
        with open(z, "rb") as f:
            resp = client.post(
                "/api/shisi/stickers/import",
                files={"file": ("z.zip", f, "application/zip")},
                data={"category": "cat1"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data == {"accepted": 1, "failed": 1}, "回执语义：accepted=真实入库数"


# ---------------------------------------------------------------------------
# A3b：chat 主链门禁接线守卫（W14 遗留收口，2026-09-30）
# ---------------------------------------------------------------------------

class TestChatChainVoiceGateWiring:
    """chat 链 ``resolve_voice_spec`` 必须传 user_id，否则专属语音门禁
    （TestVoiceUnlockGate 锁定的行为）在对话主链整体失效。"""

    def test_chat_chain_resolves_voice_spec_with_user(self) -> None:
        import ast
        from pathlib import Path

        src_path = Path(__file__).resolve().parents[1] / "orchestrator" / "optimized_orchestrator.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))

        calls = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "resolve_voice_spec"
        ]
        assert calls, "orchestrator 应调用 resolve_voice_spec（语音合成分支）"
        for call in calls:
            kw_names = [kw.arg for kw in call.keywords if kw.arg]
            assert "user_id" in kw_names, (
                "chat 链 resolve_voice_spec 调用必须传 user_id=W14 专属语音门禁，"
                "漏传则未解锁用户在对话中照用锁定音色（fail-open 仅限无用户上下文）"
            )

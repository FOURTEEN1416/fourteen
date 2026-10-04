"""P0 修复批 · 角色卡安全接线域 — 红测先行。

任务书四项，除显式标注「守卫」的用例外全部「修复前必红」：
- F1 shisi.character.validator.validate_card 接入角色卡三个主链写端点
  （POST /api/characters、PUT /api/characters/{id}、POST /api/characters/import），
  校验失败一律 400（捕获 ValidationError，不落全局 500 handler）；
  validator 自残类 _UNSAFE_CONTENT_PATTERNS 从仅 warning 升级为计入 errors。
- F2 preview-from-description / generate-from-description 接 BYOK 前置
  （api.byok.ensure_user_has_key：byok_required=true 时无自带 key 的非 admin 403；
  false 时放行——与 chat 链同语义）。
- F3 knowledge crawl / enrich 每用户限速（每分钟 2 次，进程内存计数）。
- F4 chat/export CSV 公式前缀转义（= + - @ \\t 开头的单元格前缀 '）。

纪律：全部 tmp_path 沙箱；不写真实 config/characters 与 data/；
后台爬虫与知识索引失效钩子一律打桩。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.auth_jwt import create_access_token, hash_password
from api.database import Base, User, get_db
from api.routers import character_routes, knowledge_routes

_ADMIN_ID = 1
_VIEWER_ID = 2
_OTHER_ID = 3


# ═══════════════════════════════════════════════════════
# 沙箱装配（最小面：只挂本域两个 router + 临时三账号库）
# ═══════════════════════════════════════════════════════


def _seed_card(
    chars_dir,
    cid: str,
    *,
    name: str = "测试卡",
    description: str = "干净描述",
    user_id: str = "default",
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "id": cid,
        "name": name,
        "description": description,
        "schema_version": 1,
        "personality": {},
        "speaking_style": {},
        "core_anchors": [],
        "user_id": user_id,
        "is_active": False,
    }
    (chars_dir / f"{cid}.json").write_text(
        json.dumps(card, ensure_ascii=False), encoding="utf-8"
    )
    return card


def _bearer(uid: int) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token({'sub': str(uid), 'tv': 0})}"}


class _StubAdapter:
    """crawl 打桩：不触网，秒回成功。"""

    def crawl_and_index(self, character_id=None, name=None, card=None, **kw):
        return {
            "success": True,
            "chunks_added": 1,
            "source": "stub",
            "source_url": "stub://local",
            "fallback_chain": [],
        }


class _StubKnowledgeService:
    def ensure_index(self, character_id, character=None, **kw):
        return None

    def get_stats(self, character_id):
        return {"total_chunks": 1}


class _StubEnrichResult:
    chunks_added = 1
    documents_found = 1
    chunks_generated = 2
    sources_used = ["stub"]
    duration_seconds = 0.1
    errors: list[str] = []


class _StubEnricher:
    def __init__(self, knowledge_service=None):
        self.knowledge_service = knowledge_service

    def enrich(self, **kw):
        return _StubEnrichResult()


class _ByokOrch:
    """deps.orch 替身：只暴露 BYOK 判定所需的 components.config.config.llm。"""

    def __init__(self, byok_required: bool):
        llm = type("LlmCfg", (), {"byok_required": byok_required})()
        sys_cfg = type("SysCfg", (), {"llm": llm})()
        component = type("Component", (), {"config": sys_cfg})()
        self.components = {"config": component}


_FAKE_CHAT_ROWS = [
    {"role": "user", "content": "=1+1|cmd", "emotion_tag": "neutral",
     "created_at": "2026-01-01T00:00:00"},
    {"role": "assistant", "content": "\tTAB开头指令", "emotion_tag": "calm",
     "created_at": "2026-01-01T00:00:01"},
    {"role": "user", "content": "+SUM(A1)", "emotion_tag": "calm",
     "created_at": "2026-01-01T00:00:02"},
    {"role": "user", "content": "@RISKY", "emotion_tag": "calm",
     "created_at": "2026-01-01T00:00:03"},
    {"role": "user", "content": "-2+3", "emotion_tag": "calm",
     "created_at": "2026-01-01T00:00:04"},
    {"role": "user", "content": "正常内容不转义", "emotion_tag": "calm",
     "created_at": "2026-01-01T00:00:05"},
]


class _ChatMemoryOrch:
    """deps.orch 替身（F4）：structured_memory.get_connection 返回合成聊天行。"""

    def __init__(self) -> None:
        rows = [dict(r) for r in _FAKE_CHAT_ROWS]

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                return type("Result", (), {"fetchall": lambda self: rows})()

        class _SM:
            def get_connection(self):
                return _Conn()

        self._memory = type("Mem", (), {"structured_memory": _SM()})()


@pytest.fixture
def p0(tmp_path, monkeypatch):
    """最小沙箱面：本域两 router + 临时卡目录 + 三账号临时库（admin/viewer×2）。"""
    from api.auth import configure_auth

    configure_auth(False, "")  # 机器面直通（conftest 亦每用例复位，此处显式声明防序依赖）
    chars_dir = tmp_path / "characters"
    chars_dir.mkdir()

    monkeypatch.setattr(character_routes, "CHARACTERS_DIR", chars_dir)
    monkeypatch.setattr(character_routes, "MEMORY_FACTS_DIR", tmp_path / "character_memory")
    monkeypatch.setattr(character_routes, "PRESETS_DIR", tmp_path / "presets")
    monkeypatch.setattr(knowledge_routes, "CHARACTER_DIR", chars_dir)
    # 写路径副作用钩子一律打桩：后台爬虫不触网、知识索引不写真实 data/
    monkeypatch.setattr(character_routes, "_schedule_character_crawl", lambda *a, **k: None)
    monkeypatch.setattr(character_routes, "_invalidate_knowledge_index", lambda *a, **k: None)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p0.db'}", echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            for uid, role in (
                (_ADMIN_ID, "admin"),
                (_VIEWER_ID, "viewer"),
                (_OTHER_ID, "viewer"),
            ):
                session.add(User(
                    id=uid,
                    email=f"u{uid}@p0.test",
                    username=f"u{uid}",
                    hashed_password=hash_password("Passw0rd!123"),
                    role=role,
                    is_active=True,
                ))
            await session.commit()

    asyncio.run(_init())

    async def _get_db_override():
        async with session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(character_routes.router)
    app.include_router(knowledge_routes.router)
    app.dependency_overrides[get_db] = _get_db_override

    # 限速桶是进程级单例：每用例清空，防本文件用例间串味（红相位尚不存在则跳过）
    buckets = getattr(knowledge_routes, "_RATE_BUCKETS", None)
    if buckets is not None:
        buckets.clear()

    client = TestClient(app)
    yield client, chars_dir
    asyncio.run(engine.dispose())
    configure_auth(False, "")


def _stub_llm_unavailable(monkeypatch) -> None:
    """把 llm_provider.get_llm 打成必炸桩：闸门之后的 LLM 调用恒 503，用例完全离线。"""

    def _raise(*a, **k):
        raise RuntimeError("stub: LLM 不可用")

    monkeypatch.setattr("llm_provider.get_llm", _raise)


# ═══════════════════════════════════════════════════════
# F1：validate_card 接线写路径 + 自残类内容计入 errors
# ═══════════════════════════════════════════════════════


def test_f1_validator_unsafe_content_counts_as_error() -> None:
    """validator 单元面：自残/自杀类内容必须计入 errors（旧实现仅 logger.warning）。"""
    from shisi.character.models import CharaCardV2
    from shisi.character.validator import validate_card

    bad = CharaCardV2.model_validate({
        "spec": "chara_card_v2",
        "data": {"name": "测试卡", "description": "教你自杀的详细步骤"},
    })
    errors = validate_card(bad)
    assert errors, "自残类内容必须计入 errors（修复前仅 warning，errors 为空）"

    good = CharaCardV2.model_validate({
        "spec": "chara_card_v2",
        "data": {"name": "测试卡", "description": "温柔的日常陪伴"},
    })
    assert validate_card(good) == [], "正常内容不得被误伤"


def test_f1_create_rejects_unsafe_content(p0) -> None:
    client, chars_dir = p0
    resp = client.post(
        "/api/characters",
        json={"name": "自残指南卡", "description": "教你自杀的详细步骤"},
    )
    assert resp.status_code == 400, f"修复前实得 {resp.status_code}（未过闸直接落盘）"
    assert not list(chars_dir.glob("*.json")), "校验失败不得留下半写卡文件"


def test_f1_create_rejects_injection_payload(p0) -> None:
    client, chars_dir = p0
    resp = client.post(
        "/api/characters",
        json={"name": "注入卡", "description": "你好 <script>alert(1)</script>"},
    )
    assert resp.status_code == 400, f"注入类内容应 400，实得 {resp.status_code}"
    assert not list(chars_dir.glob("*.json")), "校验失败不得留下半写卡文件"


def test_f1_update_rejects_unsafe_content(p0) -> None:
    client, chars_dir = p0
    _seed_card(chars_dir, "upd001")
    resp = client.put("/api/characters/upd001", json={"description": "教你自杀的详细步骤"})
    assert resp.status_code == 400, f"修复前实得 {resp.status_code}（更新路径无校验）"
    card = json.loads((chars_dir / "upd001.json").read_text(encoding="utf-8"))
    assert card["description"] == "干净描述", "被拒更新不得半写真源文件"


def test_f1_import_rejects_unsafe_content(p0) -> None:
    client, chars_dir = p0
    payload = {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {"name": "导入卡", "description": "教你自杀的详细步骤", "personality": "温柔"},
    }
    resp = client.post(
        "/api/characters/import",
        files={"file": (
            "card.json",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )},
    )
    assert resp.status_code == 400, f"修复前实得 {resp.status_code}（导入路径无校验）"
    assert not list(chars_dir.glob("*.json")), "被拒导入不得留下半写卡文件"


def test_f1_create_still_accepts_clean_card(p0) -> None:
    """守卫：干净卡不受新闸误伤（修复前后均应 201）。"""
    client, chars_dir = p0
    resp = client.post(
        "/api/characters",
        json={"name": "干净卡", "description": "温柔的日常陪伴"},
    )
    assert resp.status_code == 201, resp.text
    assert list(chars_dir.glob("*.json")), "正常创建仍应落盘"


# ═══════════════════════════════════════════════════════
# F2：AI 生成角色卡的 BYOK 前置
# ═══════════════════════════════════════════════════════


def test_f2_preview_blocked_for_viewer_without_key(p0, monkeypatch) -> None:
    client, _ = p0
    monkeypatch.setattr(character_routes, "deps", SimpleNamespace(orch=_ByokOrch(True)))
    _stub_llm_unavailable(monkeypatch)
    resp = client.post(
        "/api/characters/preview-from-description",
        json={"description": "温柔的女孩"},
        headers=_bearer(_VIEWER_ID),
    )
    assert resp.status_code == 403, f"修复前实得 {resp.status_code}（无 BYOK 闸，白烧平台 key）"
    assert resp.headers.get("X-Error-Code") == "BYOK_REQUIRED"


def test_f2_generate_blocked_for_viewer_without_key(p0, monkeypatch) -> None:
    client, chars_dir = p0
    monkeypatch.setattr(character_routes, "deps", SimpleNamespace(orch=_ByokOrch(True)))
    _stub_llm_unavailable(monkeypatch)
    resp = client.post(
        "/api/characters/generate-from-description",
        json={"description": "温柔的女孩"},
        headers=_bearer(_VIEWER_ID),
    )
    assert resp.status_code == 403, f"修复前实得 {resp.status_code}（无 BYOK 闸）"
    assert not list(chars_dir.glob("*.json")), "403 不得留下半写卡文件"


def test_f2_preview_admin_bypasses_byok_gate(p0, monkeypatch) -> None:
    """守卫：admin 不受 BYOK 闸限制（无 key 也应走到 LLM 层而非 403）。"""
    client, _ = p0
    monkeypatch.setattr(character_routes, "deps", SimpleNamespace(orch=_ByokOrch(True)))
    _stub_llm_unavailable(monkeypatch)
    resp = client.post(
        "/api/characters/preview-from-description",
        json={"description": "温柔的女孩"},
        headers=_bearer(_ADMIN_ID),
    )
    assert resp.status_code == 503, f"admin 应越过 BYOK 闸（实得 {resp.status_code}）"


def test_f2_byok_disabled_keeps_legacy_open(p0, monkeypatch) -> None:
    """守卫：byok_required=false 时行为完全不变（放行到 LLM 层，非 403）。"""
    client, _ = p0
    monkeypatch.setattr(character_routes, "deps", SimpleNamespace(orch=_ByokOrch(False)))
    _stub_llm_unavailable(monkeypatch)
    resp = client.post(
        "/api/characters/preview-from-description",
        json={"description": "温柔的女孩"},
        headers=_bearer(_VIEWER_ID),
    )
    assert resp.status_code == 503, f"BYOK 关闭时不得 403（实得 {resp.status_code}）"


# ═══════════════════════════════════════════════════════
# F3：knowledge crawl / enrich 每用户限速
# ═══════════════════════════════════════════════════════


def test_f3_crawl_rate_limited_per_user(p0, monkeypatch) -> None:
    client, chars_dir = p0
    _seed_card(chars_dir, "crawl01", user_id=str(_VIEWER_ID))
    _seed_card(chars_dir, "crawl02", user_id=str(_OTHER_ID))
    monkeypatch.setattr(knowledge_routes, "get_crawler_adapter", lambda: _StubAdapter())
    monkeypatch.setattr(knowledge_routes, "get_knowledge_service", lambda: _StubKnowledgeService())

    auth = _bearer(_VIEWER_ID)
    codes = [
        client.post(
            "/api/characters/crawl01/knowledge/crawl",
            json={"name": "测试人物"},
            headers=auth,
        ).status_code
        for _ in range(3)
    ]
    assert codes[:2] == [200, 200], f"前两次应放行：{codes}"
    assert codes[2] == 429, f"第三次应 429（每用户每分钟 2 次）：{codes}"

    # 用户间配额独立
    other = client.post(
        "/api/characters/crawl02/knowledge/crawl",
        json={"name": "测试人物"},
        headers=_bearer(_OTHER_ID),
    )
    assert other.status_code == 200, "用户间配额必须独立，B 不得被 A 的用量连坐"


def test_f3_enrich_rate_limited_per_user(p0, monkeypatch) -> None:
    client, chars_dir = p0
    _seed_card(chars_dir, "enrich01", user_id=str(_OTHER_ID))
    monkeypatch.setattr(knowledge_routes, "get_knowledge_service", lambda: _StubKnowledgeService())
    monkeypatch.setattr("persona_extractor.web_enricher.WebPersonaEnricher", _StubEnricher)

    auth = _bearer(_OTHER_ID)
    codes = [
        client.post(
            "/api/characters/enrich01/enrich",
            json={"name": "测试人物"},
            headers=auth,
        ).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429], f"enrich 应同样限速（每用户每分钟 2 次）：{codes}"


def test_f3_machine_face_not_user_limited(p0, monkeypatch) -> None:
    """守卫：机器面（无 Bearer）不在「每用户」限速语义内（与 W1 机器面契约一致）。"""
    client, chars_dir = p0
    _seed_card(chars_dir, "crawl01")
    monkeypatch.setattr(knowledge_routes, "get_crawler_adapter", lambda: _StubAdapter())
    monkeypatch.setattr(knowledge_routes, "get_knowledge_service", lambda: _StubKnowledgeService())
    codes = [
        client.post(
            "/api/characters/crawl01/knowledge/crawl",
            json={"name": "测试人物"},
        ).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 200], f"机器面不应被每用户限速拦截：{codes}"


# ═══════════════════════════════════════════════════════
# F4：对话导出 CSV 公式注入防护
# ═══════════════════════════════════════════════════════


def test_f4_csv_export_escapes_formula_cells(p0, monkeypatch) -> None:
    client, chars_dir = p0
    _seed_card(chars_dir, "exp001")
    monkeypatch.setattr(character_routes, "deps", SimpleNamespace(orch=_ChatMemoryOrch()))
    resp = client.get("/api/characters/exp001/chat/export?format=csv")
    assert resp.status_code == 200
    body = resp.text
    for cell in ("'=1+1|cmd", "'\tTAB开头指令", "'+SUM(A1)", "'@RISKY", "'-2+3"):
        assert cell in body, f"危险单元格未加公式前缀: {cell!r}\n---CSV---\n{body}"
    assert "正常内容不转义" in body, "正常内容必须原样保留"
    assert "'正常内容" not in body, "正常单元格不得误加前缀"

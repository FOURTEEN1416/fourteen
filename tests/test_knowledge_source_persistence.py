"""W5 · 知识源保存、索引重建与网络增强 — 源材料持久化与幂等回归。

缺陷背景（W5 任务书）：
A. 索引被当可重建缓存，却是上传文档与抓取内容的唯一存储；路由绕过
   ensure_index 闭环、角色卡失效点整库 unlink → 重启后首次 stats、
   或改名/激活角色时，上传与抓取的外部知识即丢。
B. /enrich 端点构造 WebPersonaEnricher() 未传 knowledge_service → 永不落库。
C. Vault 定期采集对相同 source_id 反复 add_chunks → 非幂等块数膨胀。

钉住的行为：
1. 上传文档是不可丢源材料：全新 service（冷 worker）ensure 后必须可检索；
2. 卡片刷新（改名/激活/编辑）只替换 card 来源，上传/抓取/增强来源保留；
3. Vault 按 (character_id, source_id) 幂等：同卡重采不增块，卡变替换旧块；
4. 多 service 交替写入不回退（锁 + 版本派生，不靠 mtime 无锁覆盖）；
5. 网络增强走统一知识服务：成功 chunks_added>0 且旧知识保留；
   保存失败不得报 enriched（chunks_added 保持 0 且错误可见）；
6. prompt 知识槽命中新块。
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.character.models import CharaCardV2
from shisi.core.models.character_aggregate import CharacterAggregate
from shisi.knowledge.character_knowledge_service import (
    CharacterKnowledgeService,
    build_character_aggregate,
)
from shisi.knowledge.retriever import KnowledgeChunk

UNIQUE_WORD = "量子玫瑰养殖手册"


def _make_raw(cid: str = "w5test01", name: str = "薇尔莉特", **extra: Any) -> dict[str, Any]:
    raw = {
        "id": cid,
        "name": name,
        "description": "她是自动手记人偶服务的技师，信守与客户的约定。",
        "personality_text": "她克制而温柔，做事一丝不苟，从不成半途而废之人。",
        "scenario": "清晨的邮局里，她整理着信件。",
        "creator_notes": "【原作知识要点】她曾是一名军人，如今以写信为业。",
        "core_anchors": ["约定锚点：替人传信绝不失约"],
        "mes_example": "用户：你好\n薇尔莉特：您有什么想要传达的话语吗。",
        "updated_at": "2026-09-01T00:00:00+00:00",
    }
    raw.update(extra)
    return raw


def _make_aggregate(raw: dict[str, Any]) -> CharacterAggregate:
    return build_character_aggregate(raw)


def _make_card(raw: dict[str, Any]) -> CharaCardV2:
    return CharaCardV2Parser.parse(raw)


def _make_service(tmp_path: Path) -> CharacterKnowledgeService:
    return CharacterKnowledgeService(
        use_bm25=True, index_dir=tmp_path / "knowledge",
    )


def _doc_chunks(text: str, doc_id: str = "doc0001") -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(content=text, source=f"{doc_id}.txt", source_id=f"{doc_id}_{i}")
        for i, text in enumerate([text[:50], text[50:100] or text])
    ]


# ── 1. 上传文档是源材料：冷 service 可检索 ──────────────────


class TestUploadSurvivesColdService:
    def test_upload_then_cold_service_stats_and_search(self, tmp_path: Path):
        """上传含独特词文档后，全新 service 首次 ensure/stats/search 仍存在。"""
        raw = _make_raw()
        cid = raw["id"]
        svc1 = _make_service(tmp_path)
        svc1.ensure_index(cid, character=_make_aggregate(raw))
        count, changed = svc1.upsert_source(
            cid, key="doc:doc0001", kind="upload", version="doc0001",
            chunks=_doc_chunks(f"这本《{UNIQUE_WORD}》记载了独特配方。"),
        )
        assert count >= 1 and changed is True

        # 冷 worker：全新 service 实例（模拟另一进程/重启）
        svc2 = _make_service(tmp_path)
        assert svc2.ensure_index(cid, character=_make_aggregate(raw)) is True
        stats = svc2.get_stats(cid)
        assert stats["indexed"] is True
        result = svc2.search(cid, UNIQUE_WORD, top_k=5)
        content = "".join(c.content for c in result.chunks)
        assert UNIQUE_WORD in content

        # 无 card 参数的纯读路径也必须自愈（store 已含 card 来源）
        svc3 = _make_service(tmp_path)
        assert svc3.ensure_index(cid) is True
        assert UNIQUE_WORD in "".join(c.content for c in svc3.search(cid, UNIQUE_WORD, top_k=5).chunks)

    def test_cold_service_without_store_and_card_fails_gracefully(self, tmp_path: Path):
        """store 与卡都不存在时 ensure 返回 False，不抛异常。"""
        svc = _make_service(tmp_path)
        assert svc.ensure_index("no_such_char_w5") is False


# ── 2. 卡片刷新只替换 card 来源 ────────────────────────────


class TestCardRefreshKeepsExternal:
    def test_rename_refresh_keeps_upload_and_updates_card(self, tmp_path: Path):
        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        svc.upsert_source(cid, key="doc:doc0001", kind="upload", version="doc0001",
                          chunks=_doc_chunks(f"《{UNIQUE_WORD}》第二卷。"))

        new_raw = _make_raw(name="薇尔莉特·伊芙加登")
        svc.refresh_card_source(cid, raw=new_raw)

        # 上传来源保留
        assert UNIQUE_WORD in "".join(
            c.content for c in svc.search(cid, UNIQUE_WORD, top_k=5).chunks)
        # 卡来源已更新（新名可检索，且 card 来源 version 变化）
        assert "伊芙加登" in "".join(
            c.content for c in svc.search(cid, "伊芙加登", top_k=8).chunks)
        store = svc._source_store.read(cid)
        assert store["sources"]["card"]["version"] != store["sources"]["doc:doc0001"]["version"]

    def test_refresh_is_idempotent_for_same_card(self, tmp_path: Path):
        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        before = svc.get_stats(cid)["total_chunks"]
        count1, changed1 = svc.refresh_card_source(cid, raw=dict(raw))
        count2, changed2 = svc.refresh_card_source(cid, raw=dict(raw))
        assert changed1 is False and changed2 is False
        assert svc.get_stats(cid)["total_chunks"] == before
        assert count1 == count2

    def test_index_file_stamps_sources_version(self, tmp_path: Path):
        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        svc.upsert_source(cid, key="doc:doc0001", kind="upload", version="doc0001",
                          chunks=_doc_chunks("额外内容块。"))
        data = json.loads((tmp_path / "knowledge" / f"{cid}.json").read_text(encoding="utf-8"))
        store_version = svc._source_store.version(cid)
        assert data.get("sources_version") == store_version
        assert store_version >= 2  # card 来源 + doc 来源两次变更

        # 冷加载后版本记账一致
        svc2 = _make_service(tmp_path)
        assert svc2.load_index(cid) is True
        assert svc2._index_versions[cid] == store_version


# ── 3. Vault 幂等采集 ──────────────────────────────────────


class TestVaultIdempotentCollect:
    def test_repeated_collect_no_growth_and_card_change_replaces(self, tmp_path: Path):
        from shisi.vault import VaultCollector

        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        svc.upsert_source(cid, key="doc:doc0001", kind="upload", version="doc0001",
                          chunks=_doc_chunks(f"《{UNIQUE_WORD}》外部文档。"))
        collector = VaultCollector(knowledge_service=svc)
        card = _make_card(raw)

        n1 = collector.collect_card(cid, card)
        total1 = svc.get_stats(cid)["total_chunks"]
        collector.collect_card(cid, card)
        total2 = svc.get_stats(cid)["total_chunks"]
        assert n1 > 0
        assert total2 == total1, "同卡重复采集不得增块"

        # 源更新（卡内容变化）→ 替换旧块而非追加；sid 稳定但内容变化必须生效
        # （实测 PersonaAdapter 的锚点派生自卡 description，改它才改变 vault 块）
        changed_raw = _make_raw(
            description="她是自动手记人偶服务的技师，信守与客户的约定，"
                        "且有绝不迟到的职业信条。")
        n3 = collector.collect_card(cid, _make_card(changed_raw))
        assert n3 >= n1
        assert "绝不迟到" in "".join(
            c.content for c in svc.search(cid, "绝不迟到", top_k=5).chunks), (
            "源更新后新内容必须可检索（替换式写入，不得因 source_id 稳定而跳过）")
        total3 = svc.get_stats(cid)["total_chunks"]
        assert total3 <= total1 + 1, "源更新应替换旧块，块数不得按采集次数累积"
        # 外部上传文档仍在
        assert UNIQUE_WORD in "".join(
            c.content for c in svc.search(cid, UNIQUE_WORD, top_k=5).chunks)


# ── 4. 多 service 交替写入不回退 ────────────────────────────


class TestMultiServiceNoRollback:
    def test_alternating_appends_both_survive(self, tmp_path: Path):
        raw = _make_raw()
        cid = raw["id"]
        svc_a = _make_service(tmp_path)
        svc_a.ensure_index(cid, character=_make_aggregate(raw))

        svc_b = _make_service(tmp_path)
        count, _ = svc_b.upsert_source(cid, key="doc:doc0001", kind="upload",
                                       version="doc0001", chunks=_doc_chunks("甲写的资料。"))
        assert count >= 1
        count, _ = svc_a.upsert_source(cid, key="doc:doc0002", kind="upload",
                                       version="doc0002", chunks=_doc_chunks("乙写的资料。"))
        assert count >= 1

        # 交替后两侧都可见（不回退）
        for svc in (svc_a, svc_b):
            assert svc.ensure_index(cid) is True
            hits = "".join(c.content for c in svc.search(cid, "资料", top_k=10).chunks)
            assert "甲写的资料" in hits and "乙写的资料" in hits

        # 全新 service 读到完整派生
        svc_c = _make_service(tmp_path)
        svc_c.ensure_index(cid)
        hits = "".join(c.content for c in svc_c.search(cid, "资料", top_k=10).chunks)
        assert "甲写的资料" in hits and "乙写的资料" in hits

    def test_concurrent_upserts_all_survive(self, tmp_path: Path):
        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        errors: list[Exception] = []

        def _write(i: int) -> None:
            try:
                svc.upsert_source(cid, key=f"doc:k{i}", kind="upload", version=f"v{i}",
                                  chunks=_doc_chunks(f"并发资料第{i}号。", doc_id=f"k{i}"))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert svc.ensure_index(cid) is True
        hits = "".join(c.content for c in svc.search(cid, "并发资料", top_k=20).chunks)
        for i in range(8):
            assert f"并发资料第{i}号" in hits

    def test_separate_process_write_visible_to_worker(self, tmp_path: Path):
        """另一 worker（真实子进程）写入源存储后，本 worker 在一致性窗口内可读。

        （Windows 开发机 flock 退化为进程内锁，跨进程互斥由 POSIX 生产环境
        承担；本用例验证的是跨进程可见性与版本化派生的正确性。）
        """
        import subprocess
        import sys

        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))

        child_code = (
            "import sys; sys.path.insert(0, '.')\n"
            "from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService\n"
            f"svc = CharacterKnowledgeService(use_bm25=True, index_dir=r'{tmp_path / 'knowledge'}')\n"
            "from shisi.knowledge.retriever import KnowledgeChunk\n"
            "svc.upsert_source(\n"
            f"    r'{cid}', key='doc:fromproc', kind='upload', version='p1',\n"
            "    chunks=[KnowledgeChunk(content='子进程独有词符琥珀螺。',\n"
            "                           source='proc.txt', source_id='fromproc_0')])\n"
            "print('ok')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", child_code], capture_output=True, text=True,
            cwd=".", timeout=120,
        )
        assert proc.returncode == 0, f"子进程写入失败: {proc.stderr}"

        # 本 worker ensure 后按版本判定过期 → 从源存储派生重建 → 子进程写入可见
        assert svc.ensure_index(cid, character=_make_aggregate(raw)) is True
        hits = "".join(c.content for c in svc.search(cid, "琥珀螺", top_k=5).chunks)
        assert "琥珀螺" in hits


# ── 5. 按 doc_id / 来源粒度删除 ────────────────────────────


class TestRemoveBySourceGranularity:
    def test_remove_doc_keeps_card_and_other_docs(self, tmp_path: Path):
        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        svc.upsert_source(cid, key="doc:aaa", kind="upload", version="aaa",
                          chunks=_doc_chunks("要删除的文档内容含有冬瓜糖。", doc_id="aaa"))
        svc.upsert_source(cid, key="doc:bbb", kind="upload", version="bbb",
                          chunks=_doc_chunks("要保留的文档内容含有桂花糕。", doc_id="bbb"))

        removed = svc.remove_source(cid, "doc:aaa")
        assert removed >= 1
        hits = "".join(c.content for c in svc.search(cid, "文档内容", top_k=10).chunks)
        assert "冬瓜糖" not in hits
        assert "桂花糕" in hits
        # 卡来源不受删除影响
        assert "薇尔莉特" in "".join(c.content for c in svc.search(cid, "薇尔莉特", top_k=8).chunks)

        # 冷 service 一致
        svc2 = _make_service(tmp_path)
        svc2.ensure_index(cid)
        hits2 = "".join(c.content for c in svc2.search(cid, "文档内容", top_k=10).chunks)
        assert "冬瓜糖" not in hits2 and "桂花糕" in hits2


# ── 6. 网络增强：统一知识服务 + 诚实计数 ────────────────────


def _mock_docs() -> list[Any]:
    from persona_extractor.web_enricher import RawDocument
    return [RawDocument(
        url="https://example.com/violet",
        title="薇尔莉特资料",
        content="薇尔莉特·伊芙加登是 C·H 邮政公司的自动手记人偶服务技师，"
                "她曾经是一名少校，如今以代笔写信为业，信守每一份约定。",
        source="mock_source",
    )]


class TestWebEnricherPersistence:
    def test_enrich_persists_chunks_and_keeps_old_knowledge(self, tmp_path: Path, monkeypatch):
        from persona_extractor.web_enricher import WebPersonaEnricher

        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        enricher = WebPersonaEnricher(knowledge_service=svc)
        monkeypatch.setattr(enricher, "_collect_docs", lambda *a, **k: _mock_docs())

        result = enricher.enrich(character_id=cid, character_name="薇尔莉特")
        assert result.chunks_added > 0, "传入知识服务后必须真实落库"
        assert result.errors == []

        # 新块可检索，旧（卡）知识保留
        hits = "".join(c.content for c in svc.search(cid, "手记人偶", top_k=8).chunks)
        assert "自动手记人偶" in hits
        assert "替人传信" in "".join(c.content for c in svc.search(cid, "传信 约定", top_k=8).chunks)

        # 冷 service 可见
        svc2 = _make_service(tmp_path)
        svc2.ensure_index(cid)
        assert "自动手记人偶" in "".join(
            c.content for c in svc2.search(cid, "手记人偶", top_k=8).chunks)

    def test_enrich_save_failure_is_honest(self, tmp_path: Path, monkeypatch):
        from persona_extractor.web_enricher import WebPersonaEnricher

        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))

        class _BrokenStore:
            def upsert_source(self, *a: Any, **k: Any) -> tuple[int, bool]:
                raise OSError("disk full")

            def version(self, cid: str) -> int:
                return 0

            def exists(self, cid: str) -> bool:
                return False

        svc._source_store = _BrokenStore()
        enricher = WebPersonaEnricher(knowledge_service=svc)
        monkeypatch.setattr(enricher, "_collect_docs", lambda *a, **k: _mock_docs())

        result = enricher.enrich(character_id=cid, character_name="薇尔莉特")
        assert result.chunks_added == 0, "保存失败不得计入 added"
        assert any("写入失败" in e for e in result.errors)
        # 路由状态派生：chunks_added==0 → 不得报 enriched
        status = "enriched" if result.chunks_added > 0 else "no_new_chunks"
        assert status != "enriched"

    def test_enrich_route_passes_knowledge_service_and_pre_ensures(self, monkeypatch):
        """路由必须注入统一知识服务并先 ensure 已有索引。"""
        from api.routers import knowledge_routes
        from persona_extractor import web_enricher as we_mod

        raw = _make_raw()
        cid = raw["id"]
        captured: dict[str, Any] = {}

        class _CaptureEnricher:
            def __init__(self, knowledge_service=None, **kw: Any) -> None:
                captured["knowledge_service"] = knowledge_service

            def enrich(self, **kw: Any) -> Any:
                captured["enrich_kwargs"] = kw
                from persona_extractor.web_enricher import EnrichResult
                return EnrichResult(character_id=cid, character_name=kw.get("character_name", ""))

        monkeypatch.setattr(we_mod, "WebPersonaEnricher", _CaptureEnricher)

        ensured: list[Any] = []

        class _RouteService:
            def ensure_index(self, character_id: str, **kw: Any) -> bool:
                ensured.append((character_id, kw))
                return True

            def get_stats(self, character_id: str) -> dict[str, Any]:
                return {"indexed": True, "total_chunks": 0, "retriever_type": "bm25"}

        monkeypatch.setattr(knowledge_routes, "get_knowledge_service", lambda: _RouteService())
        monkeypatch.setattr(knowledge_routes, "_load_character_data", lambda cid_: raw)

        req = knowledge_routes.EnrichRequest(name="薇尔莉特")
        resp = asyncio.run(knowledge_routes.enrich_character_persona(cid, req, _auth=True))
        assert resp["status"] == "no_new_chunks"  # 无真实网络材料，诚实返回未新增
        assert captured["knowledge_service"] is not None
        assert captured["knowledge_service"] is not we_mod.WebPersonaEnricher
        assert ensured and ensured[0][0] == cid


# ── 7. prompt 知识槽命中新块 ────────────────────────────────


class TestPromptSlotHitsNewChunks:
    def test_prompt_context_contains_uploaded_doc(self, tmp_path: Path, monkeypatch):
        from shisi.core.services import prompt_builder

        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        monkeypatch.setattr(prompt_builder, "get_knowledge_service", lambda: svc)

        svc.ensure_index(cid, character=_make_aggregate(raw))
        svc.upsert_source(cid, key="doc:doc0001", kind="upload", version="doc0001",
                          chunks=_doc_chunks(f"《{UNIQUE_WORD}》记载了稀有花的栽培要点。"))

        ctx = prompt_builder._get_knowledge_context(
            _make_aggregate(raw), UNIQUE_WORD, enabled=True)
        assert UNIQUE_WORD in ctx


# ── 8. 路由层 ensure 闭环（冷 service 首开 stats 不丢外部源）──


class TestRouteEnsureClosure:
    def test_cold_service_stats_flow_keeps_uploaded_doc(self, tmp_path: Path, monkeypatch):
        """冷 worker 首开知识统计（stats 端点路径）不得覆盖销毁上传文档。"""
        from api.routers import knowledge_routes as kr

        raw = _make_raw()
        cid = raw["id"]
        svc1 = _make_service(tmp_path)
        svc1.ensure_index(cid, character=_make_aggregate(raw))
        svc1.upsert_source(cid, key="doc:doc0001", kind="upload", version="doc0001",
                           chunks=_doc_chunks(f"《{UNIQUE_WORD}》传阅记录。"))

        monkeypatch.setattr(kr, "get_knowledge_service", lambda: svc1)
        monkeypatch.setattr(kr, "_load_character_data", lambda cid_: dict(raw))
        resp = asyncio.run(kr.get_knowledge_stats(cid, _auth=True))
        assert resp["indexed"] is True

        # 冷 service（模拟重启后）：ensure 走 store 派生，文档仍可检索
        svc2 = _make_service(tmp_path)
        monkeypatch.setattr(kr, "get_knowledge_service", lambda: svc2)
        resp2 = asyncio.run(kr.get_knowledge_stats(cid, _auth=True))
        assert resp2["indexed"] is True
        source_names = {s["name"] for s in resp2["sources"]}
        assert any("doc0001" in n for n in source_names), f"上传来源丢失: {resp2['sources']}"

    def test_route_delete_document_removes_source(self, tmp_path: Path, monkeypatch):
        from api.routers import knowledge_routes as kr

        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        svc.upsert_source(cid, key="doc:deadbeef", kind="upload", version="deadbeef",
                          chunks=_doc_chunks("待删除文档，含独一无二词符冬瓜糖。", doc_id="deadbeef"))
        monkeypatch.setattr(kr, "get_knowledge_service", lambda: svc)

        resp = asyncio.run(kr.delete_knowledge_document(cid, "deadbeef", _auth=True))
        assert resp["status"] == "deleted"
        assert resp["removed_chunks"] >= 1
        hits = "".join(c.content for c in svc.search(cid, "冬瓜糖", top_k=5).chunks)
        assert "冬瓜糖" not in hits

        # 删除不存在的文档 → 404
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            asyncio.run(kr.delete_knowledge_document(cid, "no_such_doc", _auth=True))


# ── 9. 卡片失效点：角色路由刷新而非整库 unlink ──────────────


class TestCharacterRoutesInvalidation:
    def test_invalidate_refreshes_card_source_keeps_upload(self, tmp_path: Path, monkeypatch):
        import api.routers.character_routes as cr

        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        svc.upsert_source(cid, key="doc:doc0001", kind="upload", version="doc0001",
                          chunks=_doc_chunks(f"《{UNIQUE_WORD}》外部资料。"))
        monkeypatch.setattr(cr, "get_knowledge_service", lambda: svc)
        monkeypatch.setattr(cr, "_load_character", lambda cid_: dict(raw, name="改名后的角色"))
        # 索引目录与 character_routes 的失效路径一致（tmp 化）
        monkeypatch.setattr(cr, "project_path",
                            lambda *parts: tmp_path if "knowledge" in parts else Path(*parts))

        cr._invalidate_knowledge_index(cid)

        # 上传来源保留 + 新名可检索（card 来源已替换）
        assert UNIQUE_WORD in "".join(
            c.content for c in svc.search(cid, UNIQUE_WORD, top_k=5).chunks)
        assert "改名后的角色" in "".join(
            c.content for c in svc.search(cid, "改名后的角色", top_k=8).chunks)

    def test_invalidate_with_missing_card_forges_clean_state(self, tmp_path: Path, monkeypatch):
        """卡文件已不在（角色被删）→ 内存与磁盘索引、源存储一并清理。"""
        import api.routers.character_routes as cr

        raw = _make_raw()
        cid = raw["id"]
        svc = _make_service(tmp_path)
        svc.ensure_index(cid, character=_make_aggregate(raw))
        monkeypatch.setattr(cr, "get_knowledge_service", lambda: svc)
        monkeypatch.setattr(cr, "_load_character", lambda cid_: None)
        monkeypatch.setattr(cr, "project_path",
                            lambda *parts: tmp_path if "knowledge" in parts else Path(*parts))

        cr._invalidate_knowledge_index(cid)
        assert svc.has_index(cid) is False
        assert not (tmp_path / "knowledge" / f"{cid}.json").exists()
        assert not (tmp_path / "knowledge" / "sources" / f"{cid}.json").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

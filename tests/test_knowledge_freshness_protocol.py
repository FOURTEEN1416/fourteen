"""知识索引新鲜度协议根治 — 自举迁移 + 无条件 bump 根治（红测先行）。

缺陷背景（角色域 2026-10-12 批 item 1，聚合根因一项修三类）：
① 无 source_store 的存量卡索引与 sv=0 互判「永久新鲜」，版本协议失效
  （本地实测 40/42 卡无 store、39/42 现役索引无 sources_version 戳）；
② 冷进程 prompt_builder 首条消息必经 index_character 无指纹检查、无条件
  bump，违反 source_store.transaction「version 严格随内容变化递增」不变量
  （探针实测：冷 service 连调两次 store version 1→2）；
③ 带戳索引 + store 缺失（如 store 被误删）时 ensure_index 静默 return False，
  读路径无法自愈。

钉住的行为：
- 红0 边界回归：无索引+无 store+不带卡 → 返回 False 且不创建任何文件
  （ensure_index neither-exists 语义不得被自举迁移破坏）；
- 红1 无戳旧索引+无 store → ensure_index 自举：从派生索引恢复源材料
  （store 创建）并给磁盘索引落上 sources_version>=1；
- 红1b 带戳索引+store 缺失 → 同样自举恢复；外部源材料按块 source 前缀
  尽力恢复源键粒度（vault.*→canonical "vault"、crawler_*→canonical
  "external"、真实键不可恢复者按源值独立分桶），不合并异源块；
- 红2 冷 service 对同一聚合根连调 index_character → store version 不变；
- 红2b 指纹碰撞防御：chunk_fingerprint 恒定时内容不同仍必须刷新
  （锁死 index_character「version==fp 且 chunks==items」双比对实现；
  该用例针对「只比 fp 弱化实现」变红，对现状无条件 bump 实现天然通过）；
- 红2c ensure_index 锁内带卡路径同一双比对口径（碰撞时必须刷新）；
- 绿保障：卡内容变化经 ensure_index 带卡路径刷新生效（:261-271 回归保护）；
  自举迁移收敛：恢复后同内容 index_character 不得再 bump。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import shisi.knowledge.character_knowledge_service as cks_module
from shisi.knowledge.character_knowledge_service import (
    CharacterKnowledgeService,
    build_character_aggregate,
)
from shisi.knowledge.retriever import BM25Retriever, KnowledgeChunk

CID = "fresh01"
UNIQUE = "琥珀螺典藏"


def _raw(**extra: Any) -> dict[str, Any]:
    raw = {
        "id": CID,
        "name": "薇尔莉特",
        "description": "她是自动手记人偶服务的技师，信守与客户的约定。",
        "personality_text": "她克制而温柔，做事一丝不苟。",
        "scenario": "清晨的邮局里，她整理着信件。",
        "creator_notes": "",
        "core_anchors": ["约定锚点：替人传信绝不失约"],
        "mes_example": "",
    }
    raw.update(extra)
    return raw


def _service(tmp_path: Path) -> CharacterKnowledgeService:
    return CharacterKnowledgeService(use_bm25=True, index_dir=tmp_path / "knowledge")


def _write_index_file(index_dir: Path, cid: str, chunks: list[KnowledgeChunk],
                      sources_version: int | None) -> Path:
    """按 BM25 落盘格式写索引文件；sources_version=None 模拟 W5 之前旧格式（无戳）。"""
    retriever = BM25Retriever()
    retriever.index(chunks)
    p = index_dir / f"{cid}.json"
    retriever.save(p, sources_version=sources_version or 0)
    if sources_version is None:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.pop("sources_version", None)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# ── 红0 · neither-exists 边界回归 ───────────────────────────


class TestNeitherExistsBoundary:
    def test_no_index_no_store_no_card_returns_false_and_creates_nothing(
            self, tmp_path: Path):
        svc = _service(tmp_path)
        assert svc.ensure_index("ghost_fresh") is False
        knowledge_dir = tmp_path / "knowledge"
        assert not knowledge_dir.exists() or not any(knowledge_dir.rglob("*")), (
            "neither-exists 分支不得创建任何文件（自举迁移不得越界触发）")


# ── 红1 · 无戳旧索引 + 无 store → 自举 ──────────────────────


class TestBootstrapFromUnstampedIndex:
    def test_unstamped_index_without_store_bootstraps_store_and_stamps(
            self, tmp_path: Path):
        index_dir = tmp_path / "knowledge"
        index_dir.mkdir(parents=True)
        _write_index_file(index_dir, CID, [
            KnowledgeChunk(content="约定锚点：替人传信绝不失约",
                           source="personality.core_anchors", source_id="a0"),
        ], sources_version=None)

        svc = _service(tmp_path)
        assert svc.ensure_index(CID) is True

        store_path = index_dir / "sources" / f"{CID}.json"
        assert store_path.exists(), "旧索引无戳且无 store 时必须自举创建源存储（现状沿用旧索引→红）"
        store = svc._source_store.read(CID)
        assert int(store["version"]) >= 1

        disk = json.loads((index_dir / f"{CID}.json").read_text(encoding="utf-8"))
        assert int(disk.get("sources_version", 0)) >= 1, "磁盘索引必须落上 sources_version（sv=0 互判永久新鲜→协议失效）"
        assert int(disk["sources_version"]) == int(store["version"])

        # 自举不丢内容：锚点仍可检索
        hits = "".join(c.content for c in svc.search(CID, "传信 绝不失约", top_k=5).chunks)
        assert "替人传信" in hits


# ── 红1b · 带戳索引 + store 缺失 → 自举（现状静默 return False）──


class TestBootstrapFromStampedIndex:
    def test_stamped_index_with_missing_store_bootstraps_and_keeps_external(
            self, tmp_path: Path):
        index_dir = tmp_path / "knowledge"
        index_dir.mkdir(parents=True)
        _write_index_file(index_dir, CID, [
            KnowledgeChunk(content="约定锚点：替人传信绝不失约",
                           source="personality.core_anchors", source_id="a0"),
            KnowledgeChunk(content=f"《{UNIQUE}》外部上传文档内容。",
                           source="doc0001.txt", source_id="doc0001_0"),
            KnowledgeChunk(content="她的背景：南方小城长大。",
                           source="vault.background", source_id="vault_bg0"),
            KnowledgeChunk(content="网络摘要：她喜好收集旧邮票。",
                           source="crawler_summary", source_id="cw0"),
        ], sources_version=5)

        svc = _service(tmp_path)
        assert svc.ensure_index(CID) is True, "带戳索引+store 缺失现状静默 return False→红"

        store = svc._source_store.read(CID)
        assert int(store["version"]) >= 1
        sources = store["sources"]
        assert "card" in sources, "卡标签块必须归入 card 源"
        # 键粒度按块 source 前缀尽力恢复：vault→canonical "vault"；
        # crawler_*→canonical "external"（add_knowledge_chunks 固定键）；
        # 其余（上传文档等真实键不可恢复）按源值分桶，不合并异源块。
        assert "vault" in sources, "vault.* 块必须归入 canonical vault 源键"
        assert "external" in sources, "crawler_* 块必须归入 canonical external 源键"
        assert any(k.startswith("boot:") for k in sources), (
            "真实键不可恢复的块必须按源值独立分桶（不得合并进单一 external 丢粒度）")

        disk = json.loads((index_dir / f"{CID}.json").read_text(encoding="utf-8"))
        assert int(disk["sources_version"]) == int(store["version"])

        # 外部源材料仍可检索（store 丢失可自愈）
        assert UNIQUE in "".join(c.content for c in svc.search(CID, UNIQUE, top_k=5).chunks)
        assert "旧邮票" in "".join(c.content for c in svc.search(CID, "旧邮票", top_k=5).chunks)


# ── 红2 · 冷 service 连调 index_character 不得无条件 bump ───


class TestIndexCharacterNoSpuriousBump:
    def test_cold_service_repeated_index_character_keeps_version(
            self, tmp_path: Path):
        agg = build_character_aggregate(_raw())
        svc1 = _service(tmp_path)
        svc1.index_character(CID, agg)
        v1 = svc1._source_store.version(CID)

        svc2 = _service(tmp_path)  # 冷 service：模拟重启后首条消息路径
        svc2.index_character(CID, agg)
        v2 = svc2._source_store.version(CID)
        assert v2 == v1, f"内容未变不得 bump（现状 {v1}→{v2}，违反 version 随内容变化递增）"


# ── 红2b · 指纹碰撞防御（锁死双比对实现）────────────────────


class TestFingerprintCollisionDefense:
    def test_same_fingerprint_different_content_still_refreshes(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(cks_module, "chunk_fingerprint",
                            lambda chunks: "collidedfp00000")
        svc = _service(tmp_path)
        svc.index_character(CID, build_character_aggregate(_raw()))
        svc.index_character(CID, build_character_aggregate(
            _raw(description="她有绝不迟到的职业信条，广受好评。")))

        store = svc._source_store.read(CID)
        assert int(store["version"]) >= 2, "指纹相同但内容不同必须刷新（只比 fp 的弱化实现即红）"
        items = store["sources"]["card"]["chunks"]
        assert any("绝不迟到" in it["content"] for it in items), "新内容必须真实写入 card 源"


# ── 红2c · ensure_index 带卡路径同锁双比对 ──────────────────


class TestEnsureCardPathCollisionDefense:
    def test_same_fingerprint_different_content_still_refreshes_in_ensure(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """ensure_index 锁内带卡路径（card 比对分支）与 index_character/refresh
        同一口径：version==fp 但 chunks 不同必须刷新（单指纹比对即红）。"""
        monkeypatch.setattr(cks_module, "chunk_fingerprint",
                            lambda chunks: "collidedfp00000")
        svc = _service(tmp_path)
        svc.ensure_index(CID, character=build_character_aggregate(_raw()))
        # card 比对分支只在磁盘索引不可用时触达（新鲜即短路，card 参数本不参与）
        (tmp_path / "knowledge" / f"{CID}.json").unlink()
        v1 = svc._source_store.version(CID)

        cold = _service(tmp_path)
        cold.ensure_index(CID, character=build_character_aggregate(
            _raw(description="她有绝不迟到的职业信条，广受好评。")))

        store = svc._source_store.read(CID)
        assert int(store["version"]) > int(v1), "指纹相同但内容不同必须刷新（单指纹比对即红）"
        assert any("绝不迟到" in it["content"]
                   for it in store["sources"]["card"]["chunks"]), "新内容必须真实写入 card 源"


# ── 绿保障 · 既有刷新语义回归 ────────────────────────────────


class TestEnsureIndexCardRefreshRegression:
    def test_ensure_with_changed_card_refreshes_when_disk_index_missing(
            self, tmp_path: Path):
        """ensure_index 带卡且卡内容改动 → 刷新生效（:261-271 回归保护）。

        注：磁盘索引新鲜时 ensure_index 短路返回（card 参数本就不参与，
        卡变更走 refresh_card_source 失效入口）；card 比对路径只在索引
        不可用时触达，故删盘索引 + 冷 service 构造该路径。
        """
        svc = _service(tmp_path)
        svc.ensure_index(CID, character=build_character_aggregate(_raw()))
        (tmp_path / "knowledge" / f"{CID}.json").unlink()
        v1 = svc._source_store.version(CID)

        cold = _service(tmp_path)
        cold.ensure_index(CID, character=build_character_aggregate(
            _raw(description="她有绝不迟到的职业信条，广受好评。")))
        v2 = svc._source_store.version(CID)
        assert v2 > v1, "卡内容变化必须递增 version 并刷新"
        cold2 = _service(tmp_path)
        cold2.ensure_index(CID)
        assert "绝不迟到" in "".join(
            c.content for c in cold2.search(CID, "绝不迟到", top_k=5).chunks)

    def test_bootstrap_converges_no_bump_on_same_content(self, tmp_path: Path):
        """自举迁移收敛：恢复后同内容 index_character 不得再 bump（防版本漂移）。

        模拟 W5 前存量：先按现抽取路径落盘索引，再删 store 复刻「有索引无 store」。
        """
        svc0 = _service(tmp_path)
        svc0.index_character(CID, build_character_aggregate(_raw()))
        (tmp_path / "knowledge" / "sources" / f"{CID}.json").unlink()

        cold = _service(tmp_path)
        cold.ensure_index(CID)  # 自举恢复 store
        v_after = svc0._source_store.version(CID)

        cold.index_character(CID, build_character_aggregate(_raw()))
        assert svc0._source_store.version(CID) == v_after, (
            "自举恢复的 card 源与现抽取一致时，同内容 index_character 不得再 bump")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

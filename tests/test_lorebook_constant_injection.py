"""导入卡 lorebook constant 条目恒注入（红测先行）。

缺陷背景（角色域 2026-10-12 批 item 3）：character_card_v2 解析已完整读取
WorldInfoEntry.constant（角色恒真设定，SillyTavern worldinfo 语义），但唯一
消费者 _extract_from_card 只取 enabled+content 压平成 world_info BM25 块 ——
constant 条目在检索未命中时从 prompt 缺席。

仅取 constant「恒注入」语义（对标 SillyTavern worldinfo，AGPL 未抄代码，
实现为原创）。现役 42 卡全自建、无 character_book → 生产零行为变化（红4）。

钉住的行为：
- 红1 constant+enabled 条目给独立来源标签 world_info_constant（仍参与索引）；
  top_k 窗口被强命中填充条目占满时，get_knowledge_context 仍含 constant
  文本（不依赖 BM25 命中）；
- 红2 同场景 constant 文本只出现一次（前置段与检索命中按 content 去重，
  防「既索引又前置」双份注入）；
- 红3（突变锁，top_k 覆盖全库时）constant 必须居首位（恒注入前置）且只出现
  一次 —— 锁死「前置 + 去重」双机制：现状红于位次；去重缺失突变红于计数；
- 绿1 exclude_sources={'world_info_constant'} 时输出不含 constant（既有排除
  机制兼容；直击查询下现状因无独立标签同样红）；
- 绿2 无 character_book 的自建卡输出与纯检索结果逐字节一致（生产零变化回归）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from shisi.character.character_card_v2 import CharaCardV2Parser
from shisi.knowledge.character_knowledge_service import CharacterKnowledgeService

CID = "lore01"
CID_PLAIN = "lore02"
CONSTANT_TEXT = "她自幼修行星轨魔法，能看见常人看不见的星辉。"
QUERY = "青龙祭坛在哪里"  # 与 constant 零词面交集；与填充条目强命中


def _card_raw(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": "星织",
        "description": "",
        "personality": "",
        "scenario": "",
        "creator_notes": "",
        "mes_example": "",
        "character_book": {
            "name": "lore",
            "entries": [
                {"id": 1, "keys": ["星辉"], "content": CONSTANT_TEXT,
                 "constant": True, "enabled": True},
                {"id": 2, "keys": ["青龙"], "content": "青龙祭坛坐落于北山第一层，香客络绎不绝。",
                 "constant": False, "enabled": True},
                {"id": 3, "keys": ["祭坛"], "content": "祭坛的传说始于三百年前，灵验非凡。",
                 "constant": False, "enabled": True},
                {"id": 4, "keys": ["北山"], "content": "北山幽深，祭坛在云雾缭绕的山巅。",
                 "constant": False, "enabled": True},
            ],
        },
    }
    data.update(overrides)
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


def _service(tmp_path: Path) -> CharacterKnowledgeService:
    return CharacterKnowledgeService(use_bm25=True, index_dir=tmp_path / "knowledge")


@pytest.fixture()
def svc(tmp_path: Path) -> CharacterKnowledgeService:
    service = _service(tmp_path)
    service.index_from_card(CID, CharaCardV2Parser.parse(_card_raw()))
    return service


# ── 红1 · constant 恒注入（不依赖 BM25 命中）────────────────


class TestConstantAlwaysInjected:
    def test_constant_present_when_top_k_window_filled_by_filler_hits(
            self, svc: CharacterKnowledgeService):
        ctx = svc.get_knowledge_context(CID, QUERY, top_k=3)
        assert CONSTANT_TEXT in ctx, (
            "constant 条目在检索未命中时也必须恒注入（现状缺席→红）")


# ── 红2 · 防双份注入 ────────────────────────────────────────


class TestNoDoubleInjection:
    def test_constant_applies_exactly_once(self, svc: CharacterKnowledgeService):
        ctx = svc.get_knowledge_context(CID, QUERY, top_k=3)
        assert ctx.count(CONSTANT_TEXT) == 1, (
            "constant 前置段与检索命中必须按 content 去重（现状 0 次→红；去重缺失→双份）")


# ── 红3 · 突变锁：前置位次 + 去重（top_k 覆盖全库）──────────


class TestConstantFirstAndOnce:
    def test_constant_is_first_section_and_unique(self, svc: CharacterKnowledgeService):
        ctx = svc.get_knowledge_context(CID, QUERY, top_k=5)  # 5 块全进检索窗口
        assert CONSTANT_TEXT in ctx.split("\n\n")[0], (
            "constant 必须拼在检索结果之前（现状按 BM25 位次垫底→红）")
        assert ctx.count(CONSTANT_TEXT) == 1, "前置+检索同载时去重必须生效（缺失即双份→红）"


# ── 绿1 · exclude_sources 兼容（world_info_constant 可整体排除）──


class TestExcludeSourcesCompat:
    def test_excluded_constant_absent_for_both_query_shapes(
            self, svc: CharacterKnowledgeService):
        ctx = svc.get_knowledge_context(CID, QUERY, top_k=3,
                                        exclude_sources={"world_info_constant"})
        assert CONSTANT_TEXT not in ctx
        # 直击查询：constant 可被检索命中，排除机制同样生效
        ctx2 = svc.get_knowledge_context(CID, "星辉", top_k=3,
                                         exclude_sources={"world_info_constant"})
        assert CONSTANT_TEXT not in ctx2, (
            "world_info_constant 必须纳入既有 exclude_sources 排除口径")


# ── 绿2 · 自建卡（无 character_book）生产零变化回归 ─────────


class TestSelfBuiltCardUnchanged:
    def test_output_equals_plain_retrieval_without_book(self, tmp_path: Path):
        svc = _service(tmp_path)
        raw = _card_raw()
        raw["data"].pop("character_book")
        raw["data"]["description"] = "她信守约定，以传信为业。"
        svc.index_from_card(CID_PLAIN, CharaCardV2Parser.parse(raw))

        ctx = svc.get_knowledge_context(CID_PLAIN, "约定 传信", top_k=3)
        expected = svc.search(CID_PLAIN, "约定 传信", top_k=3).to_prompt_context(k=3)
        assert ctx == expected, "无 character_book 的卡输出必须与纯检索逐字节一致"
        assert "world_info" not in ctx


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

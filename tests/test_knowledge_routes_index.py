"""knowledge_routes 建索引路径回归测试（2026-09-20；W5 适配 ensure 闭环）。

背景：stats/search/documents 端点缺索引时曾走 index_from_card(CharaCardV2)，
该路径不携带 core_anchors（V2 schema 丢弃顶层扩展字段），首次访问即以降级索引
覆盖重建脚本的全量索引（实测米彩 18 块被覆盖成 7 块、锚点全丢）。
钉住行为：端点侧必须走 CharacterAggregate 全量抽取 + 服务层 ensure 闭环
（W5：冷 worker 磁盘优先、版本化派生，不再「仅内存判定后整库覆盖」）。
"""
from __future__ import annotations

from typing import Any

import pytest

from api.routers import knowledge_routes


class _FakeService:
    def __init__(self) -> None:
        self.has_index_flag = False
        self.indexed_with: dict[str, Any] = {}
        self.saved: list[str] = []

    def has_index(self, character_id: str) -> bool:
        return self.has_index_flag

    def ensure_index(self, character_id: str, card=None, character=None) -> bool:
        self.indexed_with[character_id] = character
        return True

    def save_index(self, character_id: str) -> None:
        self.saved.append(character_id)

    # index_from_card 出现即代表退回降级路径——直接失败
    def index_from_card(self, character_id: str, card) -> None:  # pragma: no cover
        raise AssertionError("端点不得走 index_from_card 降级路径")


def test_ensure_full_index_uses_aggregate_full_extraction(monkeypatch):
    """_ensure_full_index 必须携带 core_anchors 与 source_data（mes_example）"""
    fake = _FakeService()
    monkeypatch.setattr(knowledge_routes, "get_knowledge_service", lambda: fake)

    raw = {
        "id": "test0001",
        "name": "测试角色",
        "description": "测试描述",
        "personality_text": "性格文本",
        "scenario": "场景",
        "creator_notes": "扮演规则",
        "core_anchors": ["锚点一", "锚点二"],
        "mes_example": "用户：你好\n测试角色：嗯。",
    }
    knowledge_routes._ensure_full_index("test0001", raw)

    assert "test0001" in fake.indexed_with
    ch = fake.indexed_with["test0001"]
    assert list(ch.persona.core_anchors) == ["锚点一", "锚点二"]
    assert ch.source_data.get("mes_example")


def test_ensure_full_index_always_routes_through_service_ensure(monkeypatch):
    """W5 回归：端点不得自行做「内存 has_index 判定 + 整库覆盖」——
    冷 worker 的新鲜度判定与磁盘优先加载必须交给服务层 ensure 闭环
    （旧实现 has_index 为假时直接 index_character+save，销毁磁盘外部知识）。"""
    fake = _FakeService()
    fake.has_index_flag = True
    monkeypatch.setattr(knowledge_routes, "get_knowledge_service", lambda: fake)

    knowledge_routes._ensure_full_index("test0002", {"id": "test0002", "name": "X"})
    # 即使内存声称有索引，也必须经服务层 ensure（其内部自行判新鲜度）
    assert "test0002" in fake.indexed_with


def test_service_level_extraction_covers_all_sources(tmp_path):
    """端点同款聚合根构建 → 抽取出的知识块须含锚点与示例对话两类源"""
    from shisi.knowledge.character_knowledge_service import (
        CharacterKnowledgeService,
        build_character_aggregate,
    )

    raw = {
        "id": "test0003",
        "name": "测试角色",
        "description": "她是测试角色，来自回归测试。",
        "core_anchors": ["约定锚点：要去稻城亚丁求婚"],
        "personality_text": "她温柔且坚韧，做事有始有终，从不成半途而废之人。",
        "scenario": "清晨的小镇。",
        "creator_notes": "【原作知识要点】她与荔枝有约，稻城亚丁的星空是两人共同的目的地与约定。",
        "mes_example": "用户：去哪\n测试角色：稻城。",
    }
    svc = CharacterKnowledgeService(use_bm25=True, index_dir=tmp_path / "knowledge")
    svc.index_character("test0003", build_character_aggregate(raw))
    stats = svc.get_stats("test0003")
    # 7 类源齐备：name/anchors/description/personality/scenario/notes/mes_example
    assert stats["total_chunks"] >= 7

    # 锚点块可被原查询命中（含"稻城"关键词）
    result = svc.search("test0003", "稻城求婚", top_k=8)
    sources = {c.source for c in result.chunks}
    content_all = "".join(c.content for c in result.chunks)
    assert "稻城" in content_all
    assert "personality.core_anchors" in sources


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

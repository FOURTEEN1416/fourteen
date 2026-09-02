"""请求级角色/画像上下文隔离回归测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _KnowledgeResult:
    chunks: list[object] = []
    total_chunks = 0

    def __init__(self, character_id: str):
        self.character_id = character_id

    def get_top(self, _top_k: int):
        return []


class _KnowledgeService:
    def __init__(self):
        self.seen: list[tuple[str, str]] = []

    def search(self, character_id: str, query: str, top_k: int = 5):
        self.seen.append((character_id, query))
        return _KnowledgeResult(character_id)


@pytest.mark.asyncio
async def test_knowledge_adapter_uses_explicit_character_per_request():
    from shisi.application.knowledge_service import ShisiKnowledgeAdapter

    service = _KnowledgeService()
    adapter = ShisiKnowledgeAdapter(knowledge_service=service)
    adapter.set_character_id("stale-global-character")

    await asyncio.gather(
        adapter.retrieve_async("request-a", character_id="character-a"),
        adapter.retrieve_async("request-b", character_id="character-b"),
    )

    assert set(service.seen) == {
        ("character-a", "request-a"),
        ("character-b", "request-b"),
    }


@pytest.mark.asyncio
async def test_orchestrator_passes_request_scoped_persona_and_rag_ids():
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    persona_ids: list[str] = []
    rag_ids: list[tuple[str, str]] = []

    class PersonaExtractor:
        async def process_message(self, message, context="", user_id=None):
            await asyncio.sleep(0)
            persona_ids.append(user_id)
            return f"persona={user_id}"

    class Rag:
        def retrieve(self, query, top_k=5, character_id=None):
            rag_ids.append((query, character_id))
            return {"character_id": character_id}

    class Persona:
        def build_system_prompt(self, **kwargs):
            return f"rag={kwargs['rag_context']}"

    emotion = SimpleNamespace(analyze=lambda _message, _recent: SimpleNamespace())
    memory = SimpleNamespace(
        get_recent_context=lambda _n: "",
        retrieve_context=lambda **_kwargs: {},
        get_chat_context=lambda **_kwargs: ([], ""),
    )

    orch = OptimizedOrchestrator()
    orch.components = {
        "persona_extractor": PersonaExtractor(),
        "emotion": emotion,
        "memory": memory,
        "rag": Rag(),
        "persona": Persona(),
        "world_info": None,
        "llm": None,
    }

    a, b = await asyncio.gather(
        orch._prepare_context("request-a", "session-a", "character-a"),
        orch._prepare_context("request-b", "session-b", "character-b"),
    )

    assert set(persona_ids) == {
        "character-a:session-a",
        "character-b:session-b",
    }
    assert set(rag_ids) == {
        ("request-a", "character-a"),
        ("request-b", "character-b"),
    }
    assert "character-a" in a["system_prompt"]
    assert "character-b" in b["system_prompt"]


def test_chat_default_character_resolves_active_role(monkeypatch):
    from api.routers import character_routes, chat_routes

    monkeypatch.setattr(
        character_routes,
        "_list_all_characters",
        lambda normalize=False: [
            {"id": "inactive", "is_active": False},
            {"id": "active-role", "is_active": True},
        ],
    )

    assert chat_routes._resolve_character_id("default") == "active-role"
    assert chat_routes._resolve_character_id("explicit-role") == "explicit-role"

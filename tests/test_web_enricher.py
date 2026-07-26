"""Tests for web_enricher — RawDocument, EnrichResult, WebPersonaEnricher core."""

from __future__ import annotations

from persona_extractor.web_enricher import (
    EnrichResult,
    RawDocument,
    WebPersonaEnricher,
)


class TestRawDocument:
    def test_default_construction(self):
        doc = RawDocument()
        assert doc.url == ""
        assert doc.title == ""
        assert doc.content == ""
        assert doc.source == ""
        assert doc.score == 0.0

    def test_full_construction(self):
        doc = RawDocument(
            url="https://example.com",
            title="Test Title",
            content="Hello World",
            source="test",
            score=0.95,
        )
        assert doc.url == "https://example.com"
        assert doc.title == "Test Title"
        assert doc.content == "Hello World"
        assert doc.source == "test"
        assert doc.score == 0.95

    def test_truncated_content(self):
        content = "x" * 2000
        doc = RawDocument(content=content)
        assert len(doc.content) == 2000


class TestEnrichResult:
    def test_default(self):
        r = EnrichResult()
        assert r.documents_found == 0
        assert r.documents_processed == 0
        assert r.chunks_added == 0
        assert r.errors == []
        assert r.sources_used == []

    def test_with_values(self):
        r = EnrichResult(
            character_id="test_char",
            character_name="Test",
            documents_found=3,
            chunks_added=12,
            sources_used=["web", "bilibili"],
        )
        assert r.character_id == "test_char"
        assert r.documents_found == 3
        assert r.chunks_added == 12
        assert len(r.sources_used) == 2
        assert "web" in r.sources_used

    def test_with_errors(self):
        r = EnrichResult(errors=["Network error", "Timeout"])
        assert len(r.errors) == 2
        assert r.errors[0] == "Network error"


class TestWebPersonaEnricher:
    def test_constructor_defaults(self):
        enricher = WebPersonaEnricher()
        assert enricher is not None
        assert "direct_scrape" in enricher._available_sources

    def test_constructor_with_knowledge_service(self):
        class FakeKS:
            def add_knowledge_chunks(self, chunks):
                return len(chunks)
            def ensure_index(self, character_id):
                pass
        ks = FakeKS()
        enricher = WebPersonaEnricher(knowledge_service=ks)
        assert enricher.knowledge_service is ks

    def test_constructor_with_llm(self):
        class FakeLLM:
            def chat(self, query, system_prompt, history, temperature, max_tokens):
                return "fake reply"
        llm = FakeLLM()
        enricher = WebPersonaEnricher(llm_gateway=llm)
        assert enricher.llm is llm

    def test_has_expected_methods(self):
        enricher = WebPersonaEnricher()
        assert hasattr(enricher, "add_url")
        assert hasattr(enricher, "add_content")
        assert hasattr(enricher, "search_all_sources")
        assert hasattr(enricher, "search_agent_reach")

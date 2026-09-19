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


class TestCrawl4AIAvailabilityHonesty:
    """crawl4ai「谎报可用」修复（2026-09-19）。

    旧实现 `Crawl4AISource.available` 写死 `return True`，注释断言「Crawl4AI 已预装，
    永远可用（无需 API Key）」；而 `pyproject.toml` **从未声明该依赖** —— 按 pyproject
    安装的生产服务器上 `import crawl4ai` 必然失败。后果链条：
    `_detect_sources()` 把 crawl4ai 列入可用源 → `search_all_sources()` 无条件调用
    → `ModuleNotFoundError` 直接抛到 `/enrich` 端点。
    """

    def test_available_reflects_real_importability(self):
        import importlib.util

        from persona_extractor.web_enricher import Crawl4AISource

        expected = importlib.util.find_spec("crawl4ai") is not None
        assert Crawl4AISource().available is expected

    def test_search_returns_empty_instead_of_raising(self, monkeypatch):
        from persona_extractor.web_enricher import Crawl4AISource

        src = Crawl4AISource()
        monkeypatch.setattr(type(src), "available", property(lambda self: False))
        assert src.search("任意查询", 3) == []

    def test_scrape_returns_empty_doc_instead_of_raising(self, monkeypatch):
        from persona_extractor.web_enricher import Crawl4AISource

        src = Crawl4AISource()
        monkeypatch.setattr(type(src), "available", property(lambda self: False))
        doc = src.scrape("https://example.com")
        assert doc.content == ""
        assert doc.source == "crawl4ai"

    def test_available_sources_agree_with_detection(self):
        """可用源列表不得与真探测结果背离（否则又是「谎报」）。"""
        enricher = WebPersonaEnricher()
        assert ("crawl4ai" in enricher._available_sources) is enricher.crawl4ai.available

    def test_direct_scrape_always_listed(self):
        """direct_scrape 只依赖 requests/bs4（已在 pyproject 声明），恒可用。"""
        assert "direct_scrape" in WebPersonaEnricher()._available_sources

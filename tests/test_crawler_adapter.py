"""单元测试: shisi 知识爬虫适配器"""
import sys

sys.path.insert(0, ".")

from shisi.knowledge.crawler_adapter import _profile_to_chunks, _split_text
from shisi.knowledge.retriever import KnowledgeChunk


def test_split_text_short_returns_single():
    assert _split_text("短文本") == ["短文本"]


def test_split_text_long_returns_multiple():
    text = "a" * 500
    chunks = _split_text(text, max_len=200, overlap=20)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_profile_to_chunks_extracts_name_and_basic_info():
    profile = {
        "name": "测试角色",
        "basic_info": {"职业": "作家", "生日": "1月1日"},
        "summary": "这是一个简短介绍。",
        "source": "test_source",
        "source_url": "https://example.com",
    }
    chunks = _profile_to_chunks(profile)
    texts = [c.content for c in chunks]
    assert any("测试角色" in t for t in texts)
    assert any("职业" in t for t in texts)
    assert any("简短介绍" in t for t in texts)
    assert any("https://example.com" in t for t in texts)
    assert all(isinstance(c, KnowledgeChunk) for c in chunks)


def test_profile_to_chunks_uses_content_when_no_summary():
    profile = {
        "content": "正文内容很长，" + "x" * 1000,
    }
    chunks = _profile_to_chunks(profile)
    texts = [c.content for c in chunks]
    assert any("正文内容" in t for t in texts)


def test_profile_to_chunks_empty_returns_empty():
    assert _profile_to_chunks({}) == []


def test_crawler_adapter_lazy_tool_handles_missing_deps():
    """当爬虫工具依赖缺失时，应返回明确错误而非抛异常。"""
    import builtins
    from unittest.mock import patch

    from shisi.knowledge.crawler_adapter import CharacterCrawlerAdapter

    def _raising_import(name, *args, **kwargs):
        if "character_crawler_tool" in name:
            raise ImportError(" simulated missing dependency")
        return builtins.__import__(name, *args, **kwargs)

    adapter = CharacterCrawlerAdapter()
    with patch.object(builtins, "__import__", side_effect=_raising_import):
        result = adapter.crawl_and_index("char_1", "测试人物")
    assert result["success"] is False
    assert "爬虫工具不可用" in result["error"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All crawler_adapter tests passed!")

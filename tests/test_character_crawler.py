"""单元测试: 人物爬虫工具"""
import sys
sys.path.insert(0, ".")

from tool_system.builtin.character_crawler_tool import CharacterCrawlerTool


def test_validate_url_blocks_localhost():
    tool = CharacterCrawlerTool()
    assert not tool._validate_url("http://localhost/admin")
    assert not tool._validate_url("http://127.0.0.1/admin")


def test_validate_url_blocks_private_ip():
    tool = CharacterCrawlerTool()
    assert not tool._validate_url("http://10.0.0.1/internal")
    assert not tool._validate_url("http://192.168.1.1/router")
    assert not tool._validate_url("http://172.16.0.1/metadata")


def test_validate_url_allows_public():
    tool = CharacterCrawlerTool()
    assert tool._validate_url("https://zh.wikipedia.org/wiki/Test")
    assert tool._validate_url("https://example.com/page")


def test_validate_url_blocks_no_scheme():
    tool = CharacterCrawlerTool()
    assert not tool._validate_url("ftp://example.com/file")


def test_fetch_wiki_no_name():
    tool = CharacterCrawlerTool()
    result = tool.execute(action="fetch_wiki")
    assert not result.success


def test_fetch_url_no_url():
    tool = CharacterCrawlerTool()
    result = tool.execute(action="fetch_url")
    assert not result.success


def test_unknown_action():
    tool = CharacterCrawlerTool()
    result = tool.execute(action="invalid")
    assert not result.success


def test_generate_character_prompt():
    from tool_system.builtin.character_crawler_tool import CharacterKnowledgeImporter
    importer = CharacterKnowledgeImporter()
    prompt = importer.generate_character_prompt({
        "name": "测试角色",
        "basic_info": {"职业": "测试"},
        "summary": "这是一个测试角色",
    })
    assert "测试角色" in prompt
    assert "第一人称" in prompt


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All character_crawler tests passed!")

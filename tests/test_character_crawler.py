"""单元测试: 人物爬虫工具"""
import sys
import time

sys.path.insert(0, ".")

import tools.builtin.character_crawler_tool as _cct
from tools.base_tool import ToolResult
from tools.builtin.character_crawler_tool import CharacterCrawlerTool


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
    from tools.builtin.character_crawler_tool import CharacterKnowledgeImporter
    importer = CharacterKnowledgeImporter()
    prompt = importer.generate_character_prompt({
        "name": "测试角色",
        "basic_info": {"职业": "测试"},
        "summary": "这是一个测试角色",
    })
    assert "测试角色" in prompt
    assert "第一人称" in prompt


# ─────────────────────────────────────────────────────────────
# 2026-09-19 修复：内容有效性判定 + 结构归一
#
# 生产实测根因：百度百科对爬虫返回反爬壳页，HTML 体积 95 KB（能通过原有的
# `len(resp.text) < 2000` 校验），但正文抽取后只剩「百度百科」4 个字符。
# 旧代码据此判 success=True → _fetch_person_fallback 的降级链在第 1 步短路 →
# 本可用的 search_fetch（实测 1212 字符）永不被调用。表现为「语义成功、数据为空」。
# ─────────────────────────────────────────────────────────────


class _FakeSession:
    """替身 session：绕过 cloudscraper 的网络访问。"""

    def __init__(self, resp):
        self._resp = resp
        self.headers = {}

    def get(self, *args, **kwargs):
        return self._resp


class _FakeResp:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


# 大体积但无正文的壳页：400 个空 div（≈8.8 KB）+ 4 字符正文
_SHELL_HTML = (
    "<html><head><title>百度百科</title></head><body>"
    + '<div class="x"></div>' * 400
    + "百度百科</body></html>"
)


def test_is_usable_profile_rejects_anti_bot_shell():
    """实测形态：正文只有 4 字符的壳页必须判为不可用。"""
    tool = CharacterCrawlerTool()
    usable, reason = tool._is_usable_profile(
        {"content": "百度百科", "summary": "", "basic_info": {}}
    )
    assert not usable
    assert "低于阈值" in reason


def test_is_usable_profile_rejects_anti_bot_marker():
    tool = CharacterCrawlerTool()
    usable, reason = tool._is_usable_profile(
        {"content": "百度安全验证" + "x" * 500, "summary": "", "basic_info": {}}
    )
    assert not usable
    assert "反爬特征" in reason


def test_is_usable_profile_accepts_real_content():
    tool = CharacterCrawlerTool()
    assert tool._is_usable_profile({"content": "李" * 300})[0]
    assert tool._is_usable_profile({"summary": "摘" * 100})[0]
    assert tool._is_usable_profile({"basic_info": {"职业": "诗人"}})[0]


def test_normalize_profile_fills_content_from_summary():
    """维基来源只有 summary —— 调用方统一读 content，必须补齐。"""
    tool = CharacterCrawlerTool()
    p = tool._normalize_profile({"summary": "维基摘要" * 20})
    assert p["content"] == p["summary"]
    assert p["content_length"] == len(p["content"])
    assert p["basic_info"] == {}


def test_normalize_profile_fills_summary_from_content():
    tool = CharacterCrawlerTool()
    p = tool._normalize_profile({"content": "只有正文" * 20})
    assert p["summary"] == p["content"]


# ─────────── 维基不可达记忆（实测白等 32s → 记忆后瞬时返回）───────────


def test_wiki_memo_is_time_bounded():
    """单域名超时必须显著小于原 8s，否则整条链耗时仍不可接受。"""
    assert _cct._WIKI_TIMEOUT <= 5


def test_wiki_domain_blocked_lifecycle():
    _cct._WIKI_MEMO.clear()
    tool = CharacterCrawlerTool()
    domain = "https://zh.wikipedia.org"
    assert tool._wiki_domain_blocked(domain) is False
    tool._mark_wiki_domain_blocked(domain)
    assert tool._wiki_domain_blocked(domain) is True
    # 过期应重新尝试
    _cct._WIKI_MEMO[domain] = time.monotonic() - 1
    assert tool._wiki_domain_blocked(domain) is False
    _cct._WIKI_MEMO.clear()


def test_wiki_returns_instantly_when_both_domains_memoized():
    """两个域名都被记忆后，_fetch_wikipedia 必须瞬时返回、不发起网络请求。"""
    _cct._WIKI_MEMO.clear()
    tool = CharacterCrawlerTool()
    for d in ("https://zh.wikipedia.org", "https://en.wikipedia.org"):
        tool._mark_wiki_domain_blocked(d)
    t0 = time.monotonic()
    r = tool._fetch_wikipedia("李白")
    elapsed = time.monotonic() - t0
    try:
        assert not r.success
        assert "已记忆" in (r.error or "")
        assert elapsed < 0.5, f"应为瞬时返回，实测 {elapsed:.2f}s"
    finally:
        _cct._WIKI_MEMO.clear()


def test_fetch_baike_rejects_shell_page():
    """体积达标但正文为空的壳页不得返回 success。"""
    tool = CharacterCrawlerTool()
    tool._session = _FakeSession(_FakeResp(_SHELL_HTML))
    original = _cct.HAS_CLOUDSCRAPER
    _cct.HAS_CLOUDSCRAPER = True
    try:
        r = tool._fetch_baike("李白")
    finally:
        _cct.HAS_CLOUDSCRAPER = original
    assert not r.success
    assert "反爬" in (r.error or "")


def test_fetch_person_falls_through_after_baike_shell():
    """百度壳页被拒后，降级链必须继续走到 search_fetch。"""
    tool = CharacterCrawlerTool()
    flag, orig_baike = _cct.HAS_CLOUDSCRAPER, tool._fetch_baike
    orig_wiki, orig_search = tool._fetch_wikipedia, tool._search_and_fetch
    _cct.HAS_CLOUDSCRAPER = True
    tool._fetch_baike = lambda n: ToolResult(False, error="反爬壳页")
    tool._fetch_wikipedia = lambda n: ToolResult(False, error="不可达")
    tool._search_and_fetch = lambda n: ToolResult(
        True, data={"content": "真实人物片段" * 100, "source": "baidu_search"}
    )
    try:
        r = tool.execute("fetch_person", name="李白")
    finally:
        _cct.HAS_CLOUDSCRAPER = flag
        tool._fetch_baike, tool._fetch_wikipedia = orig_baike, orig_wiki
        tool._search_and_fetch = orig_search
    assert r.success
    assert r.data["source"] == "baidu_search"
    assert len(r.data["fallback_chain"]) == 3
    assert r.data["fallback_chain"][-1] == "search_fetch"
    # 归一后 content 可直接被调用方消费
    assert r.data["content_length"] > 200


def test_fetch_person_all_sources_down():
    tool = CharacterCrawlerTool()
    flag = _cct.HAS_CLOUDSCRAPER
    orig_wiki, orig_search = tool._fetch_wikipedia, tool._search_and_fetch
    _cct.HAS_CLOUDSCRAPER = False
    tool._fetch_wikipedia = lambda n: ToolResult(False, error="不可达")
    tool._search_and_fetch = lambda n: ToolResult(False, error="empty")
    try:
        r = tool.execute("fetch_person", name="不存在的人")
    finally:
        _cct.HAS_CLOUDSCRAPER = flag
        tool._fetch_wikipedia, tool._search_and_fetch = orig_wiki, orig_search
    assert not r.success
    assert "降级链" in (r.error or "")


def test_fetch_person_requires_name():
    r = CharacterCrawlerTool().execute("fetch_person")
    assert not r.success


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("All character_crawler tests passed!")

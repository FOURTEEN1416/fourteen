"""单元测试: 搜索工具（Bing 主后端 / 标题归一 / 健康态诚实性）

覆盖 2026-09-19 修复。根因来自生产实测：
- Bing 的 `li.b_algo` 内**第一个** <a> 是站点面包屑，标题形如
  `'ynu.edu.cnhttps://www.ynu.edu.cn› xxgk › sbyd.htm'`；
  真正标题在 `h2 a`。旧代码用 `li.find("a")` → 标题全是面包屑。
- `health_check` 曾无条件返回 `available: True`，调用方分不清「真能搜到」
  与「只剩降级路径」—— 与「投递链路六层谎报」同类。
"""
import sys
from unittest.mock import patch

sys.path.insert(0, ".")

from tools.builtin.search_tool import SearchTool, _clean_title


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code != 200:
            raise RuntimeError(f"HTTP {self.status_code}")


# 真实结构复刻：面包屑 <a> 在 h2 之前
_BING_HTML = """
<html><body><ol id="b_results">
  <li class="b_algo">
    <div class="b_attribution">
      <a href="https://www.ynu.edu.cn/xxgk/sbyd.htm">ynu.edu.cnhttps://www.ynu.edu.cn&#8250; xxgk &#8250; sbyd.htm</a>
    </div>
    <h2><a href="https://www.ynu.edu.cn/xxgk/sbyd.htm">识别云大-云南大学YunnanUniversity</a></h2>
    <p>这八字校训，“自尊”谓心态，“致知”求学。</p>
  </li>
  <li class="b_algo">
    <div class="b_attribution"><a href="https://baike.baidu.com/item/X">baidu.comhttps://baike.baidu.com</a></div>
    <h2><a href="https://baike.baidu.com/item/X">云南大学_百度百科</a></h2>
    <p>云南大学，简称云大，位于云南省昆明市。</p>
  </li>
  <li class="b_algo"><div>无标题无摘要的噪声项</div></li>
</ol></body></html>
"""


# ─────────── _clean_title ───────────

def test_clean_title_derives_from_breadcrumb():
    """面包屑式标题（含 http + 面包屑符号）应退化为「域名 — 路径末段」。"""
    out = _clean_title(
        "ynu.edu.cnhttps://www.ynu.edu.cn\u203a xxgk \u203a sbyd.htm",
        "https://www.ynu.edu.cn/xxgk/sbyd.htm",
    )
    assert "ynu.edu.cn" in out
    assert "http" not in out


def test_clean_title_keeps_normal_title():
    assert _clean_title("识别云大-云南大学", "https://x.com/a") == "识别云大-云南大学"


def test_clean_title_empty():
    assert _clean_title("", "https://x.com/a") == ""
    assert _clean_title(None, "https://x.com/a") == ""


# ─────────── _bing_search ───────────

def test_bing_search_takes_title_from_h2_not_breadcrumb():
    with patch("requests.get", return_value=_FakeResp(_BING_HTML)):
        r = SearchTool()._bing_search("云南大学 校训", max_results=5)
    assert r.success
    titles = [i["title"] for i in r.data]
    assert titles[0] == "识别云大-云南大学YunnanUniversity"
    assert titles[1] == "云南大学_百度百科"
    # 面包屑绝不能出现在标题里
    for t in titles:
        assert "https://" not in t


def test_bing_search_respects_max_results():
    with patch("requests.get", return_value=_FakeResp(_BING_HTML)):
        r = SearchTool()._bing_search("q", max_results=1)
    assert r.success
    assert len(r.data) == 1


def test_bing_search_skips_empty_items():
    """既无标题又无摘要的 li 不应进入结果。"""
    with patch("requests.get", return_value=_FakeResp(_BING_HTML)):
        r = SearchTool()._bing_search("q", max_results=10)
    assert all(i["title"] or i["body"] for i in r.data)
    assert len(r.data) == 2


def test_bing_search_no_result_is_failure():
    with patch("requests.get", return_value=_FakeResp("<html><body>nothing</body></html>")):
        r = SearchTool()._bing_search("q")
    assert not r.success
    assert r.error


def test_bing_search_request_error_is_failure():
    with patch("requests.get", side_effect=OSError("boom")):
        r = SearchTool()._bing_search("q")
    assert not r.success


# ─────────── execute 编排 ───────────

def test_execute_requires_query():
    r = SearchTool().execute(query="")
    assert not r.success
    assert "query" in (r.error or "")


def test_execute_prefers_bing(monkeypatch):
    """Bing 成功时不应再走 DuckDuckGo。"""
    tool = SearchTool()
    called = {"ddg": False}

    def _ddg(*a, **k):
        called["ddg"] = True
        return None

    monkeypatch.setattr(tool, "_bing_search", lambda q, n=5: r_ok(q))
    monkeypatch.setattr(tool, "_ddg_search", _ddg)
    r = tool.execute(query="q")
    assert r.success
    assert called["ddg"] is False


def test_execute_falls_back_to_ddg(monkeypatch):
    from tools.base_tool import ToolResult

    tool = SearchTool()
    monkeypatch.setattr(
        tool, "_bing_search", lambda q, n=5: ToolResult(False, error="bing down")
    )
    monkeypatch.setattr(
        tool, "_ddg_search", lambda q, n=5: ToolResult(True, data=[{"title": "t", "body": "b", "href": "h"}])
    )
    monkeypatch.setattr("tools.builtin.search_tool.HAS_DDG", True)
    r = tool.execute(query="q")
    assert r.success
    assert r.data[0]["title"] == "t"


def test_execute_reports_both_failures(monkeypatch):
    from tools.base_tool import ToolResult

    tool = SearchTool()
    monkeypatch.setattr(tool, "_bing_search", lambda q, n=5: ToolResult(False, error="E1"))
    monkeypatch.setattr(tool, "_ddg_search", lambda q, n=5: ToolResult(False, error="E2"))
    monkeypatch.setattr("tools.builtin.search_tool.HAS_DDG", True)
    r = tool.execute(query="q")
    assert not r.success
    assert "E1" in r.error and "E2" in r.error


def r_ok(q):
    from tools.base_tool import ToolResult

    return ToolResult(True, data=[{"title": "bing", "body": "b", "href": "h"}])


# ─────────── health_check 诚实性 ───────────

def test_health_check_self_consistent():
    h = SearchTool().health_check()
    assert {"available", "error", "primary", "backends"} <= set(h)
    assert isinstance(h["backends"], dict)
    # primary 必须与 backends 自洽
    assert h["backends"]["bing"] == (h["primary"] == "bing")
    # available 必须与 error 自洽（成功 ⇔ 无错误信息）
    assert h["available"] == (h["error"] == "")

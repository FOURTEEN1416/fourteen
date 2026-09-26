"""W6 · 配置生效与可信工具 — 缺陷 C/D/E/F/G 红测（可信工具域）。

C：character_card 曾向模型暴露 ``output_file`` —— 可写任意进程可写路径。
D：SSRF 校验曾只做字面前缀匹配（无 DNS 解析 / IPv4-mapped / 保留网段）。
E：天气无 key 时返回随机假数据并写缓存；缓存不按城市分键。
F：插件开关写 plugins.json 但无任何运行时消费者。
G：工具禁用只改本进程 registry，刷新后列表失去入口、重启即恢复。
"""

from __future__ import annotations

import asyncio
import socket
import types
from pathlib import Path

import pytest

from tools import tool_state
from tools.base_tool import BaseTool, ToolDispatcher, ToolRegistry, ToolResult
from tools.builtin.character_crawler_tool import CharacterCrawlerTool
from tools.builtin.weather_tool import WeatherTool
from tools.url_guard import assert_public_http_url

# ── 共享夹具：合成 DNS（拒绝全网访问，矩阵全部本地合成） ─────────

_DNS_MAP = {
    "loop.local": "127.0.0.1",
    "ten.local": "10.1.2.3",
    "one72.local": "172.16.0.9",
    "one92.local": "192.168.5.5",
    "mapped.local": "::ffff:127.0.0.1",  # IPv4-mapped 回环
    "v6loop.local": "::1",
    "link.local": "169.254.169.254",     # 云元数据端点
    "2130706433": "127.0.0.1",           # 十进制 IP 字面量（OS 会解析为 127.0.0.1）
    "0x7f000001": "127.0.0.1",           # 十六进制 IP 字面量
    "pub.local": "93.184.216.34",
    "redirector.local": "93.184.216.34",
}


def _fake_dns(host, port, *args, **kwargs):
    host = str(host).lower()
    if host not in _DNS_MAP:
        raise socket.gaierror(f"synthetic DNS has no record: {host}")
    ip = _DNS_MAP[host]
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, 1, 6, "", (ip, port or 80))]


def _install_fake_dns(monkeypatch):
    import urllib.parse

    monkeypatch.setattr(socket, "getaddrinfo", _fake_dns)
    # gaierror 属 socket 模块属性，url_guard 内部 import socket 后取用同一对象
    monkeypatch.setattr(urllib.parse, "urljoin", urllib.parse.urljoin)


class _FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class _FakeSession:
    """记录请求序列的假 session；按 (host, 序号) 返回脚本化响应。"""

    def __init__(self, script):
        # script: list[_FakeResponse]，按请求顺序弹出；耗尽后复用最后一个
        self.script = list(script)
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        assert kwargs.get("allow_redirects") is False, "守卫必须手动逐跳校验重定向"
        idx = min(len(self.calls) - 1, len(self.script) - 1)
        return self.script[idx]


# ── 缺陷 C：output_file 任意路径写 ────────────────────────────


def test_output_file_param_removed_from_schema(tmp_path):
    import json

    tool = CharacterCrawlerTool(storage_root=tmp_path / "store")
    assert "output_file" not in json.dumps(tool.parameters_schema)
    assert not hasattr(tool, "_save"), "任意路径写方法必须随参数一并删除"


def test_output_file_kwarg_rejected_and_writes_nothing(tmp_path):
    tool = CharacterCrawlerTool(storage_root=tmp_path / "store")
    evil = tmp_path / "evil" / "payload.json"
    result = tool.execute(action="fetch_wiki", name="x", output_file=str(evil))
    assert not result.success
    assert "output_file" in (result.error or "")
    assert not evil.exists()


@pytest.mark.parametrize(
    "evil_name",
    ["../../evil", "a/../../evil", "C:\\Windows\\evil", "\\\\srv\\share\\evil", "..", "."],
)
def test_crawl_results_stay_inside_controlled_root(tmp_path, monkeypatch, evil_name):
    root = tmp_path / "store"
    tool = CharacterCrawlerTool(storage_root=root)
    monkeypatch.setattr(
        tool,
        "_fetch_wikipedia",
        lambda name: ToolResult(True, data={"name": name, "summary": "人物资料" * 20}),
    )

    result = tool.execute(action="fetch_wiki", name=evil_name)
    assert result.success, evil_name
    saved = Path(result.data["saved_to"]).resolve()
    assert saved.parent == root.resolve(), f"受控存储被逃逸: {evil_name} -> {saved}"
    assert saved.exists()
    # 整个临时树中除受控根外不得出现任何新文件
    others = [
        p for p in tmp_path.rglob("*")
        if root not in p.parents and p.resolve() != root.resolve()
    ]
    assert others == [], f"根外出现文件: {others}"


# ── 缺陷 D：SSRF 矩阵 ────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://loop.local/x",
        "http://ten.local/x",
        "http://one72.local/x",
        "http://one92.local/x",
        "http://mapped.local/x",
        "http://v6loop.local/x",
        "http://link.local/x",
        "http://2130706433/x",
        "http://0x7f000001/x",
        "ftp://pub.local/x",
    ],
)
def test_url_guard_blocks_internal_matrix(monkeypatch, url):
    _install_fake_dns(monkeypatch)
    assert assert_public_http_url(url), f"必须拒绝: {url}"


def test_url_guard_allows_public(monkeypatch):
    _install_fake_dns(monkeypatch)
    assert assert_public_http_url("https://pub.local/x") is None
    assert assert_public_http_url("http://redirector.local/a") is None


@pytest.mark.parametrize(
    "url",
    [
        "http://loop.local/x",
        "http://ten.local/x",
        "http://mapped.local/x",
        "http://2130706433/x",
    ],
)
def test_crawler_fetch_rejects_internal(monkeypatch, tmp_path, url):
    _install_fake_dns(monkeypatch)
    tool = CharacterCrawlerTool(storage_root=tmp_path / "store")
    session = _FakeSession([_FakeResponse(200, "<html>never</html>")])
    tool._session = session
    result = tool._fetch_generic(url)
    assert not result.success
    assert session.calls == [], "内网目标在连接前就必须被拒，不得发出请求"


def test_crawler_redirect_to_internal_rejected(monkeypatch, tmp_path):
    _install_fake_dns(monkeypatch)
    tool = CharacterCrawlerTool(storage_root=tmp_path / "store")
    session = _FakeSession([
        _FakeResponse(302, headers={"location": "http://ten.local/secret"}),
        _FakeResponse(200, "<html>internal</html>"),
    ])
    tool._session = session
    result = tool._fetch_generic("http://redirector.local/entry")
    assert not result.success
    assert len(session.calls) == 1, "第二跳（内网）不得发出请求"


def test_crawler_follows_validated_public_redirect(monkeypatch, tmp_path):
    _install_fake_dns(monkeypatch)
    tool = CharacterCrawlerTool(storage_root=tmp_path / "store")
    session = _FakeSession([
        _FakeResponse(302, headers={"location": "https://pub.local/ok"}),
        _FakeResponse(200, "<html><title>t</title><body>" + "正文内容" * 50 + "</body></html>"),
    ])
    tool._session = session
    result = tool._fetch_generic("http://redirector.local/entry")
    assert result.success
    assert len(session.calls) == 2
    assert session.calls[1] == "https://pub.local/ok"


def test_web_summary_guard_contract_unchanged(monkeypatch):
    """既有契约：WebSummaryTool._check_url_ssrf 的拒绝语义与错误串保持。"""
    from tools.builtin.extra_tools import WebSummaryTool

    _install_fake_dns(monkeypatch)
    check = WebSummaryTool._check_url_ssrf
    assert check("http://loop.local/x") == "禁止访问内网/保留地址"
    assert check("http://link.local/x") == "禁止访问内网/保留地址"
    assert check("https://pub.local/x") is None
    assert check("file:///etc/passwd") == "仅允许 http/https URL"

    result = WebSummaryTool().execute(url="http://loop.local/x")
    assert not result.success
    assert result.error.startswith("web_summary_blocked")


# ── 缺陷 E：天气失败语义与城市缓存 ─────────────────────────────


def test_weather_plugin_no_key_returns_none_not_fake(monkeypatch):
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)
    from plugins.weather import WeatherPlugin

    plugin = WeatherPlugin(api_key="", city="Shanghai")
    assert plugin.get_weather() is None, "无 key 不得返回随机假数据"


def test_weather_simulation_only_when_explicit_and_marked(monkeypatch):
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)
    from plugins.weather import WeatherPlugin

    plugin = WeatherPlugin(api_key="", city="Shanghai", allow_simulation=True)
    data = plugin.get_weather()
    assert data is not None and data.get("source") == "simulation"
    assert plugin.get_weather("Beijing")["city"] == "Beijing"


def _install_fake_owm(monkeypatch, payloads: dict, counter: list):
    """伪造 OpenWeatherMap HTTP 层：按请求 params 中的城市名返回载荷。"""
    from plugins import weather as weather_mod

    class _Resp:
        def __init__(self, data):
            self._d = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._d

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            counter.append(url)
            city = str((kwargs.get("params") or {}).get("q") or "")
            if city in payloads:
                return _Resp(payloads[city])
            raise AssertionError(f"unexpected city url: {url} params={kwargs.get('params')}")

    monkeypatch.setattr(weather_mod, "httpx", types.SimpleNamespace(Client=_Client), raising=False)


def test_weather_cache_keyed_by_city(monkeypatch):
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)
    from plugins.weather import WeatherPlugin

    payloads = {
        "Shanghai": {"weather": [{"main": "Clouds", "description": "多云", "icon": "03d"}],
                     "main": {"temp": 31.0, "humidity": 60}, "wind": {"speed": 3.0}},
        "Beijing": {"weather": [{"main": "Clear", "description": "晴", "icon": "01d"}],
                    "main": {"temp": 11.0, "humidity": 30}, "wind": {"speed": 1.0}},
    }
    calls: list = []
    _install_fake_owm(monkeypatch, payloads, calls)

    plugin = WeatherPlugin(api_key="k", city="Shanghai")
    sh = plugin.get_weather("Shanghai")
    bj = plugin.get_weather("Beijing")
    sh2 = plugin.get_weather("Shanghai")

    assert sh["temp"] == 31.0 and sh["city"] == "Shanghai"
    assert bj["temp"] == 11.0 and bj["city"] == "Beijing", "两城数据不得串缓存"
    assert sh2["temp"] == 31.0
    assert len(calls) == 2, "每城市只应拉取一次（命中各自缓存）"


def test_weather_api_failure_is_failure_not_fake(monkeypatch):
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)
    from plugins import weather as weather_mod
    from plugins.weather import WeatherPlugin

    class _BoomClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, **kwargs):
            raise RuntimeError("network down")

    monkeypatch.setattr(weather_mod, "httpx", types.SimpleNamespace(Client=_BoomClient), raising=False)
    plugin = WeatherPlugin(api_key="k", city="Shanghai")
    assert plugin.get_weather() is None, "API 失败不得回落假数据"
    assert plugin._cache == {}, "失败结果不得写入缓存"


def test_weather_tool_no_data_is_explicit_failure(monkeypatch):
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)
    tool = WeatherTool(api_key="")
    import requests as requests_mod

    def _boom(*args, **kwargs):
        raise requests_mod.ConnectionError("wttr unreachable")

    monkeypatch.setattr(requests_mod, "get", _boom)
    result = tool.execute(city="Shanghai")
    assert not result.success, "插件无数据且降级失败时必须返回失败"
    assert not result.data


def test_weather_tool_health_reflects_config(monkeypatch):
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)
    tool = WeatherTool(api_key="")
    health = tool.health_check()
    assert health["available"] is True
    assert health["api_configured"] is False
    assert health["simulation_enabled"] is False

    configured = WeatherTool(api_key="k")
    assert configured.health_check()["api_configured"] is True


def test_weather_tool_plugin_disabled_skips_plugin(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENWEATHERMAP_API_KEY", raising=False)
    monkeypatch.setattr(tool_state, "_STATE_PATH", tmp_path / "runtime_switches.json")
    monkeypatch.setattr(tool_state, "_cache", None, raising=False)
    tool_state.set_plugin_enabled("weather", False)
    try:
        tool = WeatherTool(api_key="k")
        calls: list = []
        monkeypatch.setattr(
            tool._plugin, "get_weather", lambda city="": calls.append(city) or {"temp": 1}
        )
        import requests as requests_mod

        monkeypatch.setattr(
            requests_mod, "get",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")),
        )
        result = tool.execute(city="Shanghai")
        assert calls == [], "插件停用后不得再调用插件能力"
        assert not result.success  # 降级 wttr 也失败 → 明确失败

        tool_state.set_plugin_enabled("weather", True)
        tool.execute(city="Shanghai")
        assert calls == ["Shanghai"], "插件恢复后应重新被消费"
    finally:
        tool_state.set_plugin_enabled("weather", True)


# ── 缺陷 F/G：统一库存与开关持久化 ─────────────────────────────


class _DummyTool(BaseTool):
    name = "dummy_w6"
    description = "W6 测试工具"
    permission_level = "friend"

    def execute(self, **kwargs):
        return ToolResult(True, data={"ok": True})


def _isolate_state(monkeypatch, tmp_path):
    monkeypatch.setattr(tool_state, "_STATE_PATH", tmp_path / "runtime_switches.json")
    monkeypatch.setattr(tool_state, "_cache", None, raising=False)


def test_tool_state_persists_across_readers(tmp_path, monkeypatch):
    _isolate_state(monkeypatch, tmp_path)
    assert not tool_state.is_tool_disabled("x")
    tool_state.set_tool_disabled("x", True)
    assert tool_state.is_tool_disabled("x")
    assert "x" in tool_state.disabled_tools()

    # 模拟另一进程/重启后的读者：缓存清空后从盘上读
    monkeypatch.setattr(tool_state, "_cache", None, raising=False)
    assert tool_state.is_tool_disabled("x")

    tool_state.set_tool_disabled("x", False)
    assert not tool_state.is_tool_disabled("x")


def test_dispatch_gated_by_persisted_state(tmp_path, monkeypatch):
    _isolate_state(monkeypatch, tmp_path)
    registry = ToolRegistry()
    registry.register(_DummyTool())
    dispatcher = ToolDispatcher(registry)
    assert dispatcher.dispatch("dummy_w6", {}, affinity_level=99).success

    tool_state.set_tool_disabled("dummy_w6", True)
    blocked = dispatcher.dispatch("dummy_w6", {}, affinity_level=99)
    assert not blocked.success and "disabled" in (blocked.error or "")

    # 模拟重启：全新 registry + dispatcher，持久状态仍拦截
    fresh = ToolDispatcher(ToolRegistry())
    fresh.registry.register(_DummyTool())
    blocked = fresh.dispatch("dummy_w6", {}, affinity_level=99)
    assert not blocked.success and "disabled" in (blocked.error or "")

    tool_state.set_tool_disabled("dummy_w6", False)
    assert fresh.dispatch("dummy_w6", {}, affinity_level=99).success


def test_tools_list_inventory_survives_refresh(tmp_path, monkeypatch):
    from api.deps import deps
    from api.main_routes import ToolToggleRequest
    from api.routers import tools_routes

    _isolate_state(monkeypatch, tmp_path)
    registry = ToolRegistry()
    registry.register(_DummyTool())
    fake_orch = type("O", (), {"_tools": type("T", (), {"registry": registry})()})()
    monkeypatch.setattr(deps, "orch", fake_orch)
    monkeypatch.setattr(deps, "config", None, raising=False)

    listed = asyncio.run(tools_routes.tools_list(True))
    assert listed["tools"] == ["dummy_w6"]
    entry = {e["name"]: e for e in listed["inventory"]}["dummy_w6"]
    assert entry["status"] == "enabled"

    asyncio.run(tools_routes.toggle_tool(
        "dummy_w6", ToolToggleRequest(enabled=False), True, (1, None)))

    # G 根治断言：禁用后刷新，库存仍可见且可恢复
    refreshed = asyncio.run(tools_routes.tools_list(True))
    assert refreshed["tools"] == []
    entry = {e["name"]: e for e in refreshed["inventory"]}["dummy_w6"]
    assert entry["status"] == "disabled"
    assert entry["reason"]

    asyncio.run(tools_routes.toggle_tool(
        "dummy_w6", ToolToggleRequest(enabled=True), True, (1, None)))
    recovered = asyncio.run(tools_routes.tools_list(True))
    entry = {e["name"]: e for e in recovered["inventory"]}["dummy_w6"]
    assert entry["status"] == "enabled"


def test_plugin_toggle_routes_through_state_store(tmp_path, monkeypatch):
    from api.deps import deps
    from api.routers import tools_routes

    _isolate_state(monkeypatch, tmp_path)
    monkeypatch.setattr(deps, "orch", None, raising=False)

    listing = asyncio.run(tools_routes.list_plugins(True))
    assert "weather" in listing["plugins"]
    assert listing["plugins"]["weather"]["enabled"] is True

    asyncio.run(tools_routes.toggle_plugin("weather", False, True, (1, None)))
    assert tool_state.is_plugin_enabled("weather") is False
    listing = asyncio.run(tools_routes.list_plugins(True))
    assert listing["plugins"]["weather"]["status"] == "disabled"

    asyncio.run(tools_routes.toggle_plugin("weather", True, True, (1, None)))
    assert tool_state.is_plugin_enabled("weather") is True

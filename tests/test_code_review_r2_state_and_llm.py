"""代码审查第二轮（2026-09-21）修复回归。

钉住三类「反复出现」的缺陷形态，防回退：

1. **状态文件持久化各自实现** —— 5 处 `data/*.json` 曾有 5 套读改写，
   原子性与失败语义各异；现统一 `utils.json_state`（原子写 + 跨进程锁）。
   钉住：无 `.tmp` 残留、损坏文件不炸、并发读改写不丢键。
2. **同步→异步桥多份实现** —— `web_enricher` 曾自建
   `asyncio.new_event_loop().run_until_complete()`（泄漏循环）；
   `run_async` 在有循环时曾新建一次性循环（跨循环原语失灵）。
   钉住：静态无自建循环 + 有循环时落在**常驻共享循环**上。
3. **修复只落在网关一层** —— `LLMGatewayV2.chat_stream` 曾把错误文案 yield
   成"内容"，使 `MultiProviderGateway` 的降级链失效（与 P1-1 要修的是同一
   问题，只是漏了 provider 侧）。另：工具重试曾劈成两处且含不可达分支。
"""

from __future__ import annotations

import ast
import asyncio
import json
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════
#  1. utils.json_state —— 状态文件唯一 owner
# ══════════════════════════════════════════════════════════

def test_atomic_write_leaves_no_tmp_and_is_valid_json(tmp_path):
    from utils import json_state

    path = tmp_path / "state.json"
    json_state.atomic_write_json(path, {"a": 1})
    json_state.atomic_write_json(path, {"a": 2, "b": "中文"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2, "b": "中文"}
    assert [p.name for p in tmp_path.iterdir() if ".tmp" in p.name] == []


def test_read_json_tolerates_missing_and_corrupt(tmp_path):
    from utils import json_state

    path = tmp_path / "state.json"
    assert json_state.read_json(path, default={}) == {}
    path.write_text("{ not json", encoding="utf-8")
    assert json_state.read_json(path, default={}) == {}, "半写/损坏文件必须降级返回默认值"
    path.write_text('["list"]', encoding="utf-8")
    assert json_state.read_json(path, default={}) == {}, "类型不符时回落默认值"


def test_update_json_concurrent_read_modify_write_loses_nothing(tmp_path):
    """并发读改写不得丢键（旧实现把「读」放在锁外 → 陈旧快照互相覆盖）。"""
    from utils import json_state

    path = tmp_path / "state.json"
    barrier = threading.Barrier(8)

    def _worker(i: int) -> None:
        barrier.wait()
        json_state.update_json(path, lambda data, i=i: data.__setitem__(f"k{i}", i))

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(data) == [f"k{i}" for i in range(8)], f"并发读改写丢键: {sorted(data)}"


def test_affinity_state_is_atomic_and_isolated(tmp_path, monkeypatch):
    from utils import affinity_state as astate

    monkeypatch.setattr(astate, "_PATH", tmp_path / "affinity_state.json")
    astate.save_points("u1", "c1", 250)
    astate.save_points("u2", "c1", 80)
    assert astate.load_points("u1", "c1") == 250.0
    assert astate.load_points("u2", "c1") == 80.0
    assert astate.load_points("u3", "c1") == 0.0
    assert [p.name for p in tmp_path.iterdir() if ".tmp" in p.name] == [], "必须原子写"
    astate.clear("u1", "c1")
    assert astate.load_points("u1", "c1") == 0.0
    assert astate.load_points("u2", "c1") == 80.0, "清除他人键不得影响本键"


def test_ase_hub_index_update_merges_without_losing_entries(tmp_path, monkeypatch):
    from proactive import ase_hub as hub_mod

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")
    barrier = threading.Barrier(6)

    def _worker(i: int) -> None:
        barrier.wait()
        hub_mod._remember_index(f"u{i}", str(tmp_path / f"u{i}.json"))

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(hub_mod._read_index_raw()) == [f"u{i}" for i in range(6)]
    hub_mod._forget_index("u0")
    assert "u0" not in hub_mod._read_index_raw()


# ══════════════════════════════════════════════════════════
#  2. 同步→异步桥：单一真源
# ══════════════════════════════════════════════════════════

def _bodies_code(path: Path, func_names: tuple[str, ...]) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if n.name not in func_names:
            continue
        body = list(n.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]  # 剥 docstring（修复说明里会引用旧写法作反面教材）
        out.append("\n".join(ast.unparse(stmt) for stmt in body))
    return out


def test_web_enricher_no_self_made_event_loop():
    """不得再自建事件循环（旧写法泄漏 loop 且是第 4 份桥接实现）。

    同文件有多个 search/scrape 实现（DirectScraper / Crawl4AISource …），
    逐个函数体检查，避免只命中第一个同名函数。
    """
    path = ROOT / "persona_extractor" / "web_enricher.py"
    bodies = _bodies_code(path, ("search", "scrape"))
    assert bodies, "未找到 search/scrape 函数体（测试自身失效）"
    offenders = [b for b in bodies if "new_event_loop" in b]
    assert not offenders, "不得自建事件循环，应委托 utils.async_utils"
    assert any("run_async" in b for b in bodies), "爬虫源的同步入口应委托 utils.async_utils.run_async"


def test_run_async_inside_running_loop_lands_on_shared_loop():
    """有运行中循环时，协程必须跑在**常驻共享循环**上（而非一次性循环）。

    一次性循环会让协程内缓存的 asyncio 原语（Lock/Queue/httpx 连接池）
    绑定到一个用完即弃的循环 → 跨调用唤醒失灵、连接池反复重建。
    """
    from utils.async_utils import get_shared_loop, run_async

    seen: dict = {}

    def _worker() -> None:
        async def _inner():
            async def _probe():
                seen["loop"] = asyncio.get_running_loop()

            run_async(_probe())

        asyncio.run(_inner())

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=15)
    assert not t.is_alive(), "run_async 在有循环时挂死"
    assert seen.get("loop") is get_shared_loop()


# ══════════════════════════════════════════════════════════
#  3. LLM 网关：流式降级契约 + 常驻循环 + 跨循环 client
# ══════════════════════════════════════════════════════════

class _BoomStreamClient:
    def stream(self, *args, **kwargs):
        raise RuntimeError("transport down")


class _RecordingClient:
    def __init__(self) -> None:
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


def test_chat_stream_raises_before_first_token_so_chain_can_fall_back():
    """首 token 前失败必须上抛 —— 网关据此切换 provider（P1-1 契约）。"""
    from llm_provider.llm_gateway import LLMGatewayV2

    gw = LLMGatewayV2(api_key="test-key")

    async def _collect():
        loop = asyncio.get_running_loop()
        gw._clients[id(loop)] = (loop, _BoomStreamClient())  # type: ignore[assignment]
        return [t async for t in gw.chat_stream(query="hi")]

    with pytest.raises(RuntimeError, match="transport down"):
        asyncio.run(_collect())


def test_chat_stream_yields_tokens_then_surfaces_mid_stream_break():
    """已下发内容后中断仍上抛（重放会半句+整句重复），由调用方兜底。"""
    from llm_provider.llm_gateway import LLMGatewayV2

    class _HalfThenFail:
        def stream(self, *args, **kwargs):
            class _Ctx:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *exc):
                    return False

                def raise_for_status(self):
                    return None

                async def aiter_lines(self):
                    yield 'data: {"choices":[{"delta":{"content":"半句"}}]}'
                    raise RuntimeError("mid-stream break")

            return _Ctx()

    gw = LLMGatewayV2(api_key="test-key")

    async def _collect():
        loop = asyncio.get_running_loop()
        gw._clients[id(loop)] = (loop, _HalfThenFail())  # type: ignore[assignment]
        out = []
        with pytest.raises(RuntimeError, match="mid-stream break"):
            async for tok in gw.chat_stream(query="hi"):
                out.append(tok)
        return out

    assert asyncio.run(_collect()) == ["半句"]


def test_chat_sync_uses_shared_loop(monkeypatch):
    """chat_sync 不再每次 `asyncio.run` 新建循环（httpx 连接池/事件循环反复重建）。"""
    from llm_provider.llm_gateway import LLMGatewayV2
    from utils.async_utils import get_shared_loop

    gw = LLMGatewayV2(api_key="test-key")
    seen: dict = {}

    async def _fake_chat(**kwargs):
        seen["loop"] = asyncio.get_running_loop()
        return "ok"

    monkeypatch.setattr(gw, "chat", _fake_chat)
    assert gw.chat_sync(query="hi") == "ok"
    assert seen.get("loop") is get_shared_loop()


def test_single_resident_loop_owner():
    """同进程只允许一个常驻循环真源（曾并存两份 → 连接池/asyncio 原语跨循环不复用）。"""
    from llm_provider.multi_provider_gateway import _get_sync_loop
    from utils.async_utils import get_shared_loop

    assert _get_sync_loop() is get_shared_loop()


def test_stale_client_closed_on_its_own_loop():
    """跨循环回收必须发生在 client **所属**的循环上。"""
    from llm_provider.llm_gateway import LLMGatewayV2

    gw = LLMGatewayV2(api_key="test-key")
    other_loop = asyncio.new_event_loop()
    fake = _RecordingClient()
    gw._clients[id(other_loop)] = (other_loop, fake)  # type: ignore[assignment]

    async def _current():
        return asyncio.get_running_loop()

    this_loop = asyncio.run(_current())
    gw._reap_stale_clients(this_loop)
    assert fake.closed == 1, "旧循环上的 client 必须被关闭（旧实现把 aclose 投到新循环，等于没关）"
    assert not gw._clients
    other_loop.close()


# ══════════════════════════════════════════════════════════
#  4. 工具调度：重试语义单一化
# ══════════════════════════════════════════════════════════

class _RaisingOnceTool:
    name = "search"
    description = "flaky"
    permission_level = "public"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def execute(self, **kwargs):
        from tools.base_tool import ToolResult

        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise RuntimeError("transient")
        return ToolResult(True, "recovered")

    def health_check(self) -> dict:
        return {"available": True}


class _DecidedFailureTool:
    name = "weather"
    description = "decided failure"
    permission_level = "public"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **kwargs):
        from tools.base_tool import ToolResult

        self.calls += 1
        return ToolResult(False, error="城市不存在")

    def health_check(self) -> dict:
        return {"available": True}


def _dispatcher(tool, **kw):
    from tools.base_tool import ToolDispatcher, ToolRegistry

    reg = ToolRegistry()
    reg.register(tool)
    kw.setdefault("timeout", 5.0)
    kw.setdefault("rate_limit_per_minute", 100)
    return ToolDispatcher(reg, **kw)


def test_retry_on_transient_exception_and_metric_recorded_once(monkeypatch):
    calls: list[bool] = []
    monkeypatch.setattr(
        "observability.metrics.record_tool_call",
        lambda name, dur, ok: calls.append(ok),
        raising=False,
    )
    tool = _RaisingOnceTool()
    disp = _dispatcher(tool, retry_count=1, retry_tools={"search"})
    r = disp.dispatch("search", {"q": "x"})
    assert r.success
    assert len(tool.calls) == 2, "瞬态异常必须重试一次"
    assert calls == [True], "指标必须只记最终结果（旧实现为失败首跳记一次、重试成功不再记）"
    disp.close()


def test_no_retry_when_tool_returns_decided_failure():
    """工具内已有 fallback 链，返回 success=False 是已定论失败，不再重试。"""
    tool = _DecidedFailureTool()
    disp = _dispatcher(tool, retry_count=1, retry_tools={"weather"})
    r = disp.dispatch("weather", {"city": "不存在"})
    assert not r.success
    assert tool.calls == 1, "已定论失败不得重试（重复烧时间）"
    disp.close()


def test_timeout_retried_for_retry_tools_and_error_message_kept():
    from tools.base_tool import BaseTool, ToolDispatcher, ToolRegistry

    class _SleepTool(BaseTool):
        name = "search"
        description = "slow"
        permission_level = "public"

        def execute(self, **kwargs):
            import time as _t

            _t.sleep(0.4)
            from tools.base_tool import ToolResult

            return ToolResult(True, "late")

        def health_check(self) -> dict:
            return {"available": True}

    reg = ToolRegistry()
    reg.register(_SleepTool())
    disp = ToolDispatcher(reg, timeout=0.05, rate_limit_per_minute=100,
                          retry_count=1, retry_tools={"search"})
    r = disp.dispatch("search", {})
    assert not r.success
    assert r.error.startswith("tool_timeout"), f"错误文案须保持 tool_timeout 前缀: {r.error}"
    disp.close()


def test_timeout_not_retried_for_non_retry_tools():
    from tools.base_tool import BaseTool, ToolDispatcher, ToolRegistry

    class _SlowEcho(BaseTool):
        name = "echo"
        description = "slow"
        permission_level = "public"

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, **kwargs):
            import time as _t

            self.calls += 1
            _t.sleep(0.4)
            from tools.base_tool import ToolResult

            return ToolResult(True, "late")

        def health_check(self) -> dict:
            return {"available": True}

    tool = _SlowEcho()
    reg = ToolRegistry()
    reg.register(tool)
    disp = ToolDispatcher(reg, timeout=0.05, rate_limit_per_minute=100)
    r = disp.dispatch("echo", {})
    assert not r.success
    assert tool.calls == 1, "非 retry_tools 不得重试"
    disp.close()

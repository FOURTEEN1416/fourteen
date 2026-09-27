"""W4 · 手动 curator 账号模型与异步（缺陷 J / 包任务 10）。

钉住：
1. `run_curator_for_session` 优先 `llm_resolver(session_key)`，不再吃裸平台代理；
2. resolver 失败/无模型时只做确定性规则整理，summary 留空（不静默借用平台凭证）；
3. 路由层 curator 走 `asyncio.to_thread`（不在事件循环跑同步整理）。
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

from shisi.agent_plane.curator import run_curator_for_session
from shisi.memory.legacy.structured_memory import StructuredMemory

SESSION = "7:peer-a@im.wechat"


class _RecLLM:
    def __init__(self):
        self.calls = []

    def chat_sync(self, prompt, **kw):
        self.calls.append(prompt)
        return "用户稳定偏好：咖啡"


def test_curator_prefers_llm_resolver_over_platform_llm():
    tmp = Path(tempfile.mkdtemp())
    sm = StructuredMemory(str(tmp / "s.db"))
    sm.add_fact("用户喜欢手冲咖啡", user_key=SESSION, category="preference")
    platform = _RecLLM()
    resolved = _RecLLM()
    out = run_curator_for_session(
        SESSION, sm=sm, llm=platform, apply=True,
        llm_resolver=lambda key: resolved if key == SESSION else None,
    )
    assert out["ok"] is True
    assert resolved.calls, "必须用账号模型策略解析出的 llm"
    assert not platform.calls, "不得静默借用平台代理"


def test_curator_without_model_still_rules_only():
    tmp = Path(tempfile.mkdtemp())
    sm = StructuredMemory(str(tmp / "s.db"))
    sm.add_fact("叫我", user_key=SESSION)  # 垃圾（_GARBAGE 精确匹配）
    sm.add_fact("用户喜欢手冲咖啡", user_key=SESSION)
    out = run_curator_for_session(
        SESSION, sm=sm, llm=None, apply=True,
        llm_resolver=lambda key: None,
    )
    assert out["ok"] is True
    assert out["summary"] == "", "无账号模型时不得借用凭证生成摘要"
    assert out["dropped"] >= 1


def test_curator_route_is_async_off_loop():
    import api.routers.agent_plane_routes as routes

    src = inspect.getsource(routes.agent_plane_curate)
    assert "asyncio.to_thread" in src, "手动 curator 必须脱离事件循环"
    assert 'comps.get("llm")' not in src, "不得再取平台 llm 直接调用"
    assert "llm_resolver" in src

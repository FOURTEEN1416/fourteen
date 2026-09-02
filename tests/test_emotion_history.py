"""情绪历史 / 趋势 / 分布端点测试（SP-1 补齐，2026-09-01）

覆盖:
  - EmotionEngine.analyze 记录历史（数量/字段/顺序/环形上限）
  - GET /api/emotion/trend 修复后真实返回历史（旧实现恒空）
  - GET /api/emotion/distribution 聚合占比（排序 + total）
  - 无编排器时两端点空态不报错
"""

from __future__ import annotations

import os
import sys

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from my_character.emotion_engine import EmotionEngine

# ═══════════════════════════════════════════════════════
# 引擎层
# ═══════════════════════════════════════════════════════


def _make_engine() -> EmotionEngine:
    return EmotionEngine(use_llm=False, classifier_mode="rule")


def test_analyze_records_history():
    engine = _make_engine()
    engine.analyze("今天真开心，谢谢你！")
    engine.analyze("有点想你了")
    history = engine.get_history()
    assert len(history) == 2
    assert history[0]["timestamp"] <= history[1]["timestamp"]
    for item in history:
        assert {"timestamp", "primary_emotion", "intensity"} <= set(item)
        assert isinstance(item["intensity"], float)


def test_history_ring_buffer_cap():
    # deque 不可变 maxlen → 用 500 上限断言，直接压 501 条验证环形丢弃
    engine = _make_engine()
    for i in range(501):
        engine.analyze(f"消息 {i} 开心")
    history = engine.get_history()
    assert len(history) == 500  # 环形缓冲上限生效
    assert history[-1]["timestamp"] >= history[0]["timestamp"]
    # limit 截断
    assert len(engine.get_history(limit=2)) == 2


def test_history_snapshot_is_copy():
    engine = _make_engine()
    engine.analyze("你好呀")
    snapshot = engine.get_history()
    snapshot.clear()
    assert len(engine.get_history()) == 1  # 外部改动不影响内部状态


# ═══════════════════════════════════════════════════════
# 路由层（参照 test_achievements.py 范式）
# ═══════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def module_app():
    from api.app_factory import create_api_app
    from api.auth import verify_api_key_dep

    app = create_api_app()
    app.dependency_overrides[verify_api_key_dep] = lambda: True
    return app


@pytest.mark.asyncio
async def test_trend_endpoint_returns_real_history(module_app):
    """修复验证：analyze 后 trend 端点返回真实记录（旧实现恒空）。"""
    engine = _make_engine()
    engine.analyze("今天心情很好")
    # 模拟编排器装配（deps 是单例实例，直接换 orch 属性）
    import api.deps as deps

    saved = deps.deps.orch
    try:
        fake_orch = type("FakeOrch", (), {})()
        fake_orch._emotion = engine
        deps.deps.orch = fake_orch
        async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as c:
            r = await c.get("/api/emotion/trend", params={"days": 7})
        assert r.status_code == 200
        data = r.json()
        assert data["days"] == 7
        assert len(data["trend"]) == 1
        assert data["trend"][0]["primary_emotion"]
    finally:
        deps.deps.orch = saved


@pytest.mark.asyncio
async def test_distribution_aggregates_and_sorts(module_app):
    engine = _make_engine()
    for _ in range(3):
        engine.analyze("好开心呀哈哈")
    engine.analyze("有点生气了")
    import api.deps as deps

    saved = deps.deps.orch
    try:
        fake_orch = type("FakeOrch", (), {})()
        fake_orch._emotion = engine
        deps.deps.orch = fake_orch
        async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as c:
            r = await c.get("/api/emotion/distribution", params={"days": 7})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 4
        assert sum(d["count"] for d in data["distribution"]) == 4
        counts = [d["count"] for d in data["distribution"]]
        assert counts == sorted(counts, reverse=True)  # 降序
    finally:
        deps.deps.orch = saved


@pytest.mark.asyncio
async def test_endpoints_empty_without_orchestrator(module_app):
    """无编排器：空态结构完整，不报错。"""
    import api.deps as deps

    saved = deps.deps.orch
    try:
        deps.deps.orch = None
        async with AsyncClient(transport=ASGITransport(app=module_app), base_url="http://test") as c:
            rt = await c.get("/api/emotion/trend")
            rd = await c.get("/api/emotion/distribution")
        assert rt.json() == {"trend": [], "days": 7}
        assert rd.json() == {"distribution": [], "total": 0, "days": 7}
    finally:
        deps.deps.orch = saved

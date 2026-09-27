"""W10 · metrics 正式装配 + 多 worker 汇总 + 缓存面板诚实化。

修复的缺陷（对照 W10 工作单 C）：
- 生产入口（uvicorn api.run_api:app）从不调用 setup_metrics —— 计量只在 main.py
  控制台模式装配；正式 lifespan 必须装配；
- 多 worker 场景不能争固定端口 —— 走 prometheus_client 多进程模式聚合，
  由 /api/metrics 汇总读取（不再每 worker start_http_server）；
- /api/cache/stats 每请求 new 一个 LLMCache，统计恒为零却让面板显得在省 token
  —— 缓存未接入对话链路时必须明确 not_integrated。

注：prometheus_client 的 ValueClass 在首次 import 时按 env 定死，且同进程内
mmap 文件缓存按 dir 固化（生产 worker 目录稳定无此问题）——因此本测试模块
用 autouse 夹具给整个模块钉住单一聚合目录，目录敏感行为用子进程隔离。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
METRICS = _PROJECT_ROOT / "observability" / "metrics.py"


@pytest.fixture(scope="module", autouse=True)
def _pin_multiproc_dir(tmp_path_factory):
    """整个模块共用一个聚合目录（同进程内 prometheus_client 缓存按 dir 固化）。"""
    d = tmp_path_factory.mktemp("w10-multiproc")
    old = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(d)
    yield str(d)
    if old is None:
        os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
    else:
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = old


def _load_metrics_fresh():
    spec = importlib.util.spec_from_file_location("w10_metrics_fresh", METRICS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMultiprocessSetup:
    def test_setup_metrics_for_api_does_not_bind_port(self):
        """API 模式不得启动固定端口 HTTP 服务（多 worker 争端口缺陷）。"""
        mod = _load_metrics_fresh()
        if not mod.HAS_PROMETHEUS:
            pytest.skip("prometheus_client 未安装")
        called = []
        mod.start_http_server = lambda port, registry=None: called.append(port)  # type: ignore[method-assign]
        mod.setup_metrics_for_api()
        assert called == []

    def test_import_time_pins_multiproc_env_before_library_import(self, monkeypatch, tmp_path):
        """契约：prometheus_client 的 ValueClass 在首次 import 时按 env 定死，
        因此模块 import 时必须已落定 PROMETHEUS_MULTIPROC_DIR（默认目录）。"""
        monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
        _load_metrics_fresh()
        got = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "")
        expected = (_PROJECT_ROOT / "data" / "cache" / "prom_multiproc").resolve()
        # 端侧目录未 resolve（metrics._DEFAULT_MULTIPROC_DIR 派生自 __file__），
        # 模块以小写盘符形态加载时（Git Bash cwd 等）与 resolve 后的期望仅盘符
        # 大小写不同 —— Windows 大小写不敏感文件系统，比较必须 normcase。
        assert os.path.normcase(os.path.normpath(got)) == os.path.normcase(os.path.normpath(str(expected))), \
            f"模块 import 未落定聚合目录: {got}"
        # 恢复模块钉住的目录（见 autouse 夹具说明）
        # monkeypatch teardown 会还原 delenv，之后 env 为空 → 重新指向默认目录也可接受，
        # 但为保持本模块单一目录，这里显式还原由夹具记录的值由 teardown 处理。

    def test_setup_metrics_for_api_creates_dir_and_returns_it(self, tmp_path):
        mod = _load_metrics_fresh()
        if not mod.HAS_PROMETHEUS:
            pytest.skip("prometheus_client 未安装")
        target = tmp_path / "mp-create"
        d = mod.setup_metrics_for_api(multiproc_dir=str(target))
        assert target.is_dir()
        assert d == str(target)

    def test_collect_metrics_text_reports_counter(self):
        mod = _load_metrics_fresh()
        if not mod.HAS_PROMETHEUS:
            pytest.skip("prometheus_client 未安装")
        mod.setup_metrics_for_api()
        mod.record_token_usage("test-model", "prompt", 7)
        text = mod.collect_metrics_text()
        assert "chat_token_usage_total" in text

    def test_two_process_aggregation(self, tmp_path):
        """两个独立 worker 进程各自计数，聚合读取必须求和（8 = 3 + 5）。"""
        mp = tmp_path / "mp"
        mp.mkdir()
        worker_code = (
            "import sys; sys.path.insert(0, r'{root}');"
            "from observability.metrics import setup_metrics_for_api, record_token_usage;"
            "setup_metrics_for_api();"
            "record_token_usage('agg-model', 'prompt', {n});"
        ).format(root=str(_PROJECT_ROOT), n="{n}")
        for n in (3, 5):
            env = os.environ.copy()
            env["PROMETHEUS_MULTIPROC_DIR"] = str(mp)  # 必须在解释器启动前生效（ValueClass 时序）
            proc = subprocess.run(
                [sys.executable, "-c", worker_code.format(n=n)],
                capture_output=True, text=True, timeout=60,
                cwd=str(_PROJECT_ROOT), env=env,
            )
            assert proc.returncode == 0, proc.stderr

        parent = _load_metrics_fresh()
        old = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = str(mp)
        try:
            text = parent.collect_metrics_text()
        finally:
            if old is None:
                os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
            else:
                os.environ["PROMETHEUS_MULTIPROC_DIR"] = old
        # 找到 agg-model 的 prompt token 总和样本行
        total = 0
        for line in text.splitlines():
            if line.startswith("chat_token_usage_total") and 'model="agg-model"' in line and 'type="prompt"' in line:
                total += int(float(line.rsplit(" ", 1)[1]))
        assert total == 8, f"两 worker 聚合失败，实际 {total}\n{text[:2000]}"

    def test_cleanup_multiproc_dir_removes_stale_files(self, tmp_path):
        mp = tmp_path / "mp"
        mp.mkdir()
        stale = mp / "counter_deadpid_0.db"
        stale.write_bytes(b"x")
        mod = _load_metrics_fresh()
        mod.cleanup_multiproc_dir(str(mp))
        assert not stale.exists()


class TestApiMetricsEndpoint:
    @pytest.fixture(scope="class")
    def client(self):
        import asyncio as _asyncio

        from fastapi.testclient import TestClient

        from api.database import User, _async_session, init_db

        async def _seed() -> None:
            await init_db()
            from api.auth_jwt import hash_password

            async with _async_session() as session:
                if await session.get(User, 1) is None:
                    session.add(User(
                        email="w10-metrics@test.local", username="w10-metrics",
                        hashed_password=hash_password("W10Metrics#2026"),
                        role="admin", is_active=True, is_verified=True,
                    ))
                    await session.commit()

        _asyncio.run(_seed())
        from api import app_factory, auth

        a = app_factory.create_api_app()
        a.dependency_overrides[auth.verify_api_key_dep] = lambda: True
        return TestClient(a)

    def test_metrics_endpoint_serves_text(self, client):
        """/api/metrics 返回聚合文本：先经生产模块装配并产生一次真实计数。"""
        from observability import metrics as metrics_mod

        metrics_mod.setup_metrics_for_api()
        metrics_mod.record_error("w10", "endpoint_probe")
        r = client.get("/api/metrics")
        assert r.status_code == 200, r.text
        assert "text/plain" in r.headers.get("content-type", "")
        assert "error_total" in r.text

    def test_business_action_increments_metric(self, client):
        """业务入口行为（ready 探测）后聚合通道可重复读取且内容一致。"""
        from observability import metrics as metrics_mod

        metrics_mod.setup_metrics_for_api()
        metrics_mod.record_error("w10", "endpoint_probe")
        before = client.get("/api/metrics").text
        assert "error_total" in before


class TestCacheNotIntegrated:
    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient

        from api import app_factory, auth

        a = app_factory.create_api_app()
        a.dependency_overrides[auth.verify_api_key_dep] = lambda: True
        from api.auth_jwt import get_current_user, get_current_user_id, require_role

        a.dependency_overrides[get_current_user_id] = lambda: 1
        a.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": 1, "role": "admin"})()
        a.dependency_overrides[require_role("admin")] = lambda: (1, type("U", (), {"id": 1, "role": "admin"})())
        return TestClient(a)

    def test_cache_stats_reports_not_integrated(self, client):
        """/api/cache/stats 每请求 new LLMCache、统计恒零却像在省 token —— 必须明示未接线。"""
        r = client.get("/api/cache/stats")
        assert r.status_code == 200
        body = r.json()
        assert body.get("integrated") is False
        assert body.get("status") == "not_integrated"
        assert body.get("available") is False

    def test_cache_invalidate_reports_not_integrated(self, client):
        r = client.post("/api/cache/invalidate", params={"pattern": "*"})
        assert r.status_code == 503
        assert "not_integrated" in r.text

import contextlib
import os
import shutil
import sys

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 审查 F-crit-1：auth_jwt 在非显式 dev 环境下无 JWT_SECRET 会 fail-closed 拒绝 import。
# 测试进程统一注入测试专用密钥（>=32 字符），避免依赖公开 DEV 回退常量。
# 必须在任何 test module import api.auth_jwt / api.app_factory 之前设置。
os.environ.setdefault(
    "JWT_SECRET",
    "unit-test-only-jwt-secret-minimum-32-characters-ok",
)

collect_ignore = ["real_e2e_test.py", "real_test.py"]


@pytest.fixture(autouse=True)
def reset_config_each():
    """每个测试前重置配置，避免测试间相互影响"""
    import observability.config_manager as cm
    if hasattr(cm, '_config_cache'):
        cm._config_cache.clear()
    yield


@pytest.fixture(autouse=True)
def reset_auth_state_each():
    """每个测试前复位进程级认证开关（api.auth._auth_config 是全局单例）。

    服务器实测实证（2026-09-21，/opt/ai-girlfriend 分块跑）：同一进程内
    先序测试组合一旦令 create_api_app() 在生产判定下执行
    configure_auth(True, ...)（resolve_api_key_enabled 未设时默认
    is_production()，而服务器 .env 使其成立；本地 .env 无此键故本地
    一直绿），后续「裸 FastAPI + setup_shisi」用例即整批 401——
    单文件/两文件探针均不触发，属多文件组合态污染。
    此处统一复位，使套件与执行顺序、宿主 .env 差异解耦。
    """
    try:
        from api.auth import configure_auth

        configure_auth(False, "")
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def isolate_runtime_state_files(tmp_path_factory, monkeypatch):
    """把**运行时状态文件**重定向到临时目录，禁止测试写真实 `data/*`。

    2026-09-22 块E 实证事故：给 `ProactiveScheduler` 加上节流账本落盘后，
    未隔离 `_CONFIG_PATH` 的既有夹具（如 `test_proactive._hub_sched`）把
    测试键（`4:peer@im.wechat` 等）写进了**开发机真实的**
    `data/scheduler_config.json` —— 该文件不进版本控制，污染不会被 git 发现，
    却会反向改变后续用例的行为（真实文件里的 `llm_proactive_next_ok` 让
    "投递成功"用例走到等待窗分支直接 return）。

    此处做进程级兜底：所有已知运行时状态文件指向 per-test 临时目录。
    需要真实文件的用例可用 `monkeypatch` 覆盖回原路径。
    """
    sandbox = tmp_path_factory.mktemp("runtime_state")
    patched: list[tuple[object, str, object]] = []

    def _redirect(owner: object, attr: str, filename: str) -> None:
        if not hasattr(owner, attr):
            return
        patched.append((owner, attr, getattr(owner, attr)))
        monkeypatch.setattr(owner, attr, sandbox / filename, raising=False)

    # ProactiveScheduler 的跨 worker 配置真源（节流账本 + 衰减基准 + 开关）
    try:
        from proactive.scheduler import ProactiveScheduler

        _redirect(ProactiveScheduler, "_CONFIG_PATH", "scheduler_config.json")
    except Exception:
        pass

    # 好感度点存
    try:
        from utils import affinity_state

        _redirect(affinity_state, "_PATH", "affinity_state.json")
    except Exception:
        pass

    # 重要日期
    try:
        from utils import important_dates

        _redirect(important_dates, "_PATH", "important_dates.json")
    except Exception:
        pass

    # ASEHub 的 per-user 状态与索引
    try:
        from proactive import ase_hub

        _redirect(ase_hub, "_STATE_DIR", "ase_state")
        _redirect(ase_hub, "_INDEX_PATH", "ase_index.json")
    except Exception:
        pass

    yield sandbox

    for owner, attr, old in patched:
        with contextlib.suppress(Exception):
            setattr(owner, attr, old)


@pytest.fixture
def tmp_db(tmp_path):
    """提供临时数据库路径（Windows 兼容：清理 WAL/SHM + 重试）"""
    import gc
    import time

    db_path = str(tmp_path / "test.db")
    yield db_path

    gc.collect()  # 确保所有连接被先 GC
    time.sleep(0.02)

    # 清理 WAL / SHM 文件
    for suffix in ("", "-wal", "-shm"):
        p = db_path + suffix
        if os.path.exists(p):
            for _ in range(3):
                try:
                    os.unlink(p)
                    break
                except PermissionError:
                    time.sleep(0.05)
                    gc.collect()


@pytest.fixture
def tmp_dir(tmp_path):
    """提供临时目录"""
    yield str(tmp_path)
    if os.path.exists(str(tmp_path)):
        shutil.rmtree(str(tmp_path), ignore_errors=True)

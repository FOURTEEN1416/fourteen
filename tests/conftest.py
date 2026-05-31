import os
import shutil
import sys

import pytest

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

collect_ignore = ["real_e2e_test.py", "real_test.py"]


@pytest.fixture(autouse=True)
def reset_config_each():
    """每个测试前重置配置，避免测试间相互影响"""
    import observability.config_manager as cm
    if hasattr(cm, '_config_cache'):
        cm._config_cache.clear()
    yield


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

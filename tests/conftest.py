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
    """提供临时数据库路径"""
    db_path = str(tmp_path / "test.db")
    yield db_path
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def tmp_dir(tmp_path):
    """提供临时目录"""
    yield str(tmp_path)
    if os.path.exists(str(tmp_path)):
        shutil.rmtree(str(tmp_path), ignore_errors=True)

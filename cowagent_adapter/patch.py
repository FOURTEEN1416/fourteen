"""
CowAgent 打补丁 — 在运行时将 GirlfriendBot 注册到 CowAgent

通过修改 cowagent_src 的内存模块：
1. 在 common.const 添加 GIRLFRIEND 常量
2. 替换 models.bot_factory.create_bot 注册我们的 GirlfriendBot
3. 提供 start_cowagent() 快捷入口

这样无需修改 CowAgent 的源码文件，完全运行时注入。
"""

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("cowagent.patch")

# CowAgent 源码路径
COWAGENT_SRC = Path(__file__).parent.parent / "cowagent_src"
BOT_TYPE = "girlfriend"


def patch_cowagent():
    """
    在运行时给 CowAgent 打补丁，注册我们的 GirlfriendBot。
    幂等 — 多次调用安全。
    """
    logger.debug("patch_cowagent() 开始执行...")
    if _is_patched():
        logger.debug("CowAgent 补丁已存在，跳过")
        return

    logger.debug("_add_src_to_path()...")
    _add_src_to_path()
    logger.debug("_register_bot_type()...")
    _register_bot_type()
    logger.info("✅ CowAgent 补丁已应用 (bot_type=%s)", BOT_TYPE)


def start_cowagent(config_path: str = None, no_wechat: bool = False):  # type: ignore
    """
    启动 CowAgent（带 GirlfriendBot 补丁）。

    Args:
        config_path: CowAgent 配置文件路径
        no_wechat: 如果为 True，只注册不启动微信（测试用）
    """
    patch_cowagent()

    if no_wechat:
        logger.info("CowAgent 微信模式已跳过 (--no-wechat)")
        return

    # 确保配置存在
    if config_path:
        if not os.path.exists(config_path):
            logger.warning("CowAgent 配置文件不存在: %s，将使用默认配置", config_path)
        else:
            os.environ["COWAGENT_CONFIG"] = config_path

    import cowagent_src.app as cowapp

    logger.info("正在启动 CowAgent 微信通道...")
    try:
        cowapp.run()
    except Exception as e:
        logger.error("CowAgent 启动失败: %s", e)
        raise


# ── 内部实现 ──


def _is_patched() -> bool:
    """检查是否已打过补丁"""
    try:
        from common import const  # type: ignore
        return hasattr(const, "GIRLFRIEND")
    except (ImportError, AttributeError):
        return False


def _add_src_to_path():
    """将 cowagent_src 加入 Python 路径"""
    src = str(COWAGENT_SRC.resolve())
    if src not in sys.path:
        sys.path.insert(0, src)


def _register_bot_type():
    """
    核心补丁逻辑：
    1. 在 const 模块注入 GIRLFRIEND = "girlfriend"
    2. 替换 bot_factory.create_bot 使其支持 girlfriend 类型

    注意：项目根目录有 common/（有 __init__.py）会阻挡 cowagent_src/common/ 的导入，
    所以用 importlib 直接从 cowagent_src/common/const.py 加载 const 模块，
    并注入到 sys.modules["common.const"] 和 common 模块的属性。
    这样后续所有 from common import const 都能找到正确的模块。
    """
    import importlib.util
    import sys

    # ── 直接从 cowagent_src/common/const.py 加载 const 模块 ──
    _const_file = COWAGENT_SRC / "common" / "const.py"
    if not _const_file.exists():
        raise FileNotFoundError(f"const.py 不存在: {_const_file}")

    _spec = importlib.util.spec_from_file_location("common.const", str(_const_file))
    const = importlib.util.module_from_spec(_spec)  # type: ignore

    # 注入到 sys.modules，并设为 common 模块的属性
    # 这样 from common import const 就能从 sys.modules 或属性中找到
    import common as _common_mod
    sys.modules["common.const"] = const
    _common_mod.const = const  # type: ignore

    # 执行模块（const.py 只有常量定义，无导入依赖）
    _spec.loader.exec_module(const)  # type: ignore

    # 1. 注入常量
    const.GIRLFRIEND = BOT_TYPE  # type: ignore

    # 2. 替换 bot_factory.create_bot
    import models.bot_factory as bf  # type: ignore

    original_create = bf.create_bot

    def patched_create_bot(bot_type):
        if bot_type == BOT_TYPE:
            from cowagent_adapter.shared_state import bot_registry
            instance = bot_registry.get()
            if instance is not None:
                return instance
            logger.warning("BotRegistry 中无注册实例，返回占位实例")
            from cowagent_adapter.girlfriend_bot import GirlfriendBot
            return GirlfriendBot(None, None, None, None, None)
        return original_create(bot_type)

    bf.create_bot = patched_create_bot

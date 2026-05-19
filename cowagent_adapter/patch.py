"""
CowAgent 打补丁 — 在运行时将 GirlfriendBot 注册到 CowAgent

通过修改 cowagent_src 的内存模块：
1. 在 common.const 添加 GIRLFRIEND 常量
2. 替换 models.bot_factory.create_bot 注册我们的 GirlfriendBot
3. 提供 start_cowagent() 快捷入口

这样无需修改 CowAgent 的源码文件，完全运行时注入。
"""

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("cowagent.patch")

# CowAgent 源码路径
COWAGENT_SRC = Path(__file__).parent.parent / "cowagent_src"
BOT_TYPE = "girlfriend"


def patch_cowagent():
    """
    在运行时给 CowAgent 打补丁，注册我们的 GirlfriendBot。
    幂等 — 多次调用安全。
    """
    if _is_patched():
        logger.debug("CowAgent 补丁已存在，跳过")
        return

    _add_src_to_path()
    _register_bot_type()
    logger.info("✅ CowAgent 补丁已应用 (bot_type=%s)", BOT_TYPE)


def start_cowagent(config_path: str = None, no_wechat: bool = False):
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
        from common import const
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
    """
    from common import const

    # 1. 注入常量
    const.GIRLFRIEND = BOT_TYPE

    # 2. 替换 bot_factory.create_bot
    import models.bot_factory as bf

    original_create = bf.create_bot

    def patched_create_bot(bot_type):
        if bot_type == BOT_TYPE:
            from cowagent_adapter._globals import _girlfriend_bot_instance
            if _girlfriend_bot_instance is not None:
                return _girlfriend_bot_instance
            logger.warning("GirlfriendBot 全局引用未设置，返回占位实例")
            # 创建占位实例（main.py 会在初始化后设置全局引用）
            from cowagent_adapter.girlfriend_bot import GirlfriendBot
            return GirlfriendBot(None, None, None, None, None)
        return original_create(bot_type)

    bf.create_bot = patched_create_bot

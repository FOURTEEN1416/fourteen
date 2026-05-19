"""
CowAgent 适配器模块

在运行时将我们的 AI 女友引擎注册为 CowAgent 的自定义 Bot，
使得 CowAgent 的微信通道收到消息后自动路由到 小暖 的完整处理管线。

用法:
    from cowagent_adapter import patch_cowagent, GirlfriendBot

    # 传入已初始化的引擎组件
    bot = GirlfriendBot(persona, emotion_engine, memory_pipeline, tone_mimic, llm)

    # 打补丁 → CowAgent 就能用 "girlfriend" bot_type
    patch_cowagent()

    # 然后启动 CowAgent (配置 bot_type = "girlfriend")
"""

import sys
from pathlib import Path

# 确保 cowagent_src 在 sys.path 中（GirlfriendBot 要 import bridge 等模块）
_cowagent_src = Path(__file__).parent.parent / "cowagent_src"
if str(_cowagent_src) not in sys.path:
    sys.path.insert(0, str(_cowagent_src))

from .girlfriend_bot import GirlfriendBot
from .patch import patch_cowagent

__all__ = ["patch_cowagent", "GirlfriendBot"]

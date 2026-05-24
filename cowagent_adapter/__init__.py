"""
CowAgent 适配器模块

在运行时将我们的 AI 女友引擎注册为 CowAgent 的自定义 Bot，
使得 CowAgent 的微信通道收到消息后自动路由到 十四 的完整处理管线。

用法 A — 完整启动微信通道（方案 C）:
    from cowagent_adapter import initialize_wechat_channel

    status = initialize_wechat_channel(
        orchestrator=my_orchestrator,
        cowagent_config="config/cowagent_config.json",
    )
    print(status["message"])  # "微信通道已启动 (PID=12345)"

用法 B — 仅注册编排器，不启动微信:
    from cowagent_adapter import register_standalone

    register_standalone(orchestrator=my_orchestrator)
"""

import sys
from pathlib import Path

# 确保 cowagent_src 在 sys.path 中（GirlfriendBot 要 import bridge 等模块）
_cowagent_src = Path(__file__).parent.parent / "cowagent_src"
if str(_cowagent_src) not in sys.path:
    sys.path.insert(0, str(_cowagent_src))

from .girlfriend_bot import GirlfriendBot  # noqa: E402
from .wechat_launcher import initialize_wechat_channel, register_standalone  # noqa: E402
from .patch import patch_cowagent, start_cowagent  # noqa: E402

__all__ = [
    "patch_cowagent",
    "start_cowagent",
    "GirlfriendBot",
    "initialize_wechat_channel",
    "register_standalone",
]

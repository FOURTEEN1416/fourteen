"""
cowagent_adapter 全局变量模块

patch.py 和 main.py 通过此模块传递 GirlfriendBot 实例引用，
避免循环导入。
"""


class BotRegistry:
    def __init__(self):
        self._bot = None

    def register(self, bot) -> None:
        self._bot = bot

    def get(self):
        return self._bot


bot_registry = BotRegistry()
_girlfriend_bot_instance = None

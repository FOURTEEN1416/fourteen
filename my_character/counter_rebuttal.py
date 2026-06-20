"""
计数反诘模块 — 跟踪用户连续说"没事"等敷衍词的次数

当用户连续多次用"没事"/"我没事"/"我很好"等敷衍词回避真实情绪时，
在第 5 次触发反诘，逼出真实状态。这是"不哄不骗"原则的执行机制。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("counter_rebuttal")


class CounterRebuttal:
    """计数反诘：第 5 次说"没事"时回一句"这已经是第 5 次了。你不用跟我装没事。"

    设计要点：
    - 仅当消息"主要是"敷衍词时才计数（避免误触发）
    - 任何非敷衍消息都会重置计数（用户一旦说出真实内容就清零）
    - 触发反诘后计数清零，下一轮重新开始
    - 线程安全：多 session 并发安全
    """

    # 触发词：用户用来回避真实情绪的敷衍表达
    TRIGGER_PATTERNS = ["没事", "我没事", "我很好", "还行", "还好"]

    # 触发阈值：连续达到此次数后反诘
    THRESHOLD = 5

    # 反诘回复模板：{count} 会被替换为实际次数
    RESPONSES = [
        "这已经是第 {count} 次了。你不用跟我装没事。",
        "你又说了没事。这是第 {count} 次了。",
    ]

    def __init__(self):
        # session_id -> 当前连续敷衍计数
        self._counts: dict[str, int] = {}
        # 多 session 并发保护
        self._lock = threading.Lock()

    def _is_perfunctory(self, message: str) -> bool:
        """判断消息是否属于敷衍回避。

        判定规则：消息长度较短（≤10 字）且包含触发词，
        避免把"我没事啊刚才在忙"这种含真实信息的消息误判。
        """
        if not message:
            return False
        msg = message.strip()
        # 长消息可能含真实内容，不视为纯敷衍
        if len(msg) > 10:
            return False
        return any(p in msg for p in self.TRIGGER_PATTERNS)

    def check_and_increment(self, message: str, session_id: str) -> str | None:
        """检查是否触发反诘，返回反诘文本或 None。

        Args:
            message: 用户最新消息
            session_id: 会话 ID（用于多用户隔离）

        Returns:
            触发反诘时返回反诘文本，否则返回 None
        """
        if not session_id:
            session_id = "__default__"

        with self._lock:
            if not self._is_perfunctory(message):
                # 非敷衍消息：重置计数（用户说出真实内容就清零）
                if self._counts.get(session_id, 0) > 0:
                    self._counts[session_id] = 0
                    logger.debug("CounterRebuttal reset: session=%s", session_id)
                return None

            # 敷衍消息：计数 +1
            current = self._counts.get(session_id, 0) + 1
            self._counts[session_id] = current
            logger.debug(
                "CounterRebuttal increment: session=%s count=%d", session_id, current
            )

            # 达到阈值：触发反诘并清零
            if current >= self.THRESHOLD:
                response = self.RESPONSES[current % len(self.RESPONSES)].format(
                    count=current
                )
                self._counts[session_id] = 0
                logger.info(
                    "CounterRebuttal triggered: session=%s count=%d", session_id, current
                )
                return response

            return None

    def reset(self, session_id: str) -> None:
        """重置指定 session 的计数。

        Args:
            session_id: 会话 ID
        """
        if not session_id:
            return
        with self._lock:
            if session_id in self._counts:
                del self._counts[session_id]

    def get_count(self, session_id: str) -> int:
        """获取当前连续敷衍计数（调试/可观测用）。"""
        if not session_id:
            return 0
        with self._lock:
            return self._counts.get(session_id, 0)

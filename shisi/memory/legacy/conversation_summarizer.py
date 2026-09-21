from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("conversation_summarizer")

# P1-8（2026-09-21 审查修复）：旧实现缓存键 = f"{session}:{older_count}"，
# older_count 每轮 +1 → 缓存**永不命中**，51–80 条区间的会话每轮都同步跑一次
# 摘要 LLM（还直接在事件循环上调用）。现在按"步长"复用：距上次摘要边界
# 不足 SUMMARY_UPDATE_STRIDE 条时直接用旧摘要，越界才重摘——滞后有界、
# 成本降为 ~1/stride，且每会话只保留一条最新摘要（缓存不再无界增长）。
SUMMARY_UPDATE_STRIDE = 30

SUMMARY_PROMPT = """将以下对话内容压缩成一段简洁的摘要（不超过200字），保留关键信息：
- 双方讨论了什么话题
- 用户表达了什么想法、需求、情绪
- 用户做出了什么决定或承诺
- AI给出了什么重要回复

对话内容：
{messages}

摘要："""


class ConversationSummarizer:
    def __init__(self, llm_gateway):
        self._llm = llm_gateway
        # 唯一适配点（utils.llm_bridge）：旧实现在 _summarize 里内联三分支
        # （chat_sync / chat + 一次性线程池 asyncio.run / 放弃），是项目第 5 份
        # 「同步跑协程」实现 —— 与记忆管道其余三处各自漂移。
        from utils.llm_bridge import to_sync_callable

        self._call = to_sync_callable(llm_gateway, max_tokens=256, temperature=0.3)
        self._cache: dict[str, str] = {}
        self._cache_boundary: dict[str, int] = {}

    def get_chat_context(
        self,
        working_messages: list[dict[str, Any]],
        session_id: str = "",
        keep_recent: int = 50,
        summary_trigger: int = 80,
    ) -> tuple[list[dict[str, str]], str]:
        total = len(working_messages)

        if total == 0:
            return [], ""

        if total <= keep_recent:
            return self._format_history(working_messages), ""

        recent = working_messages[-keep_recent:]
        older = working_messages[:-keep_recent]
        older_count = len(older)

        cached_summary = self._cache.get(session_id, "")
        cached_boundary = self._cache_boundary.get(session_id)

        # 缓存命中三条件：有旧摘要、旧边界不新于当前窗口、漂移不足一个步长。
        # （旧实现的 startswith 会复用**最早**边界摘要 → 摘要永久滞后。）
        if (
            cached_summary
            and cached_boundary is not None
            and cached_boundary <= older_count
            and older_count - cached_boundary < SUMMARY_UPDATE_STRIDE
        ):
            summary = cached_summary
        else:
            summary = self._summarize(older)
            if summary:
                self._cache[session_id] = summary
                self._cache_boundary[session_id] = older_count

        return self._format_history(recent), summary

    def _format_history(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        """保留归属字段（character_id/turn_id/importance/timestamp）。

        2026-09-21 重扫：旧实现只回 role+content，把存储层的归属**再次丢掉** ——
        这里返回的列表会一路传到 `_prepare_context`，中间任何一环丢字段，
        「哪句是谁说的」就断了。发给 LLM 前的 `utils.prompt_sanitize.
        sanitize_llm_history` 只白名单取 role/content，故带上归属是安全的。
        """
        result: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            if role not in {"user", "assistant"}:
                continue
            entry: dict[str, Any] = {"role": role, "content": msg.get("content", "")}
            for key in ("character_id", "turn_id", "user_key", "timestamp", "emotion"):
                if msg.get(key):
                    entry[key] = msg[key]
            if msg.get("importance") is not None:
                entry["importance"] = msg["importance"]
            result.append(entry)
        return result  # type: ignore[return-value]

    def _summarize(self, messages: list[dict[str, Any]]) -> str:
        if not self._llm:
            return ""

        lines = []
        for msg in messages:
            role = "用户" if msg.get("role") == "user" else "AI"
            content = msg.get("content", "")
            lines.append(f"{role}: {content}")

        full_text = "\n".join(lines)
        if len(full_text) > 4000:
            full_text = full_text[-4000:]

        prompt = SUMMARY_PROMPT.format(messages=full_text)

        try:
            if self._call is None:
                return ""
            result = self._call(prompt)
            if not result:
                return ""
            logger.info("Conversation summary generated (%d messages → %d chars)", len(messages), len(result))
            return result.strip()  # type: ignore[no-any-return]
        except Exception as e:  # noqa: BLE001
            logger.warning("Summary generation failed: %s", e)
            return ""

    def clear_cache(self, session_id: str = "") -> None:
        # P1-8 后缓存以 session_id 为键（不再是 session:older_count 前缀键）
        if session_id:
            self._cache.pop(session_id, None)
            self._cache_boundary.pop(session_id, None)
        else:
            self._cache.clear()
            self._cache_boundary.clear()

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("conversation_summarizer")

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
        self._cache: Dict[str, str] = {}
        self._cache_boundary: Dict[str, int] = {}

    def get_chat_context(
        self,
        working_messages: List[Dict[str, Any]],
        session_id: str = "",
        keep_recent: int = 50,
        summary_trigger: int = 80,
    ) -> Tuple[List[Dict[str, str]], str]:
        total = len(working_messages)

        if total == 0:
            return [], ""

        if total <= keep_recent:
            return self._format_history(working_messages), ""

        recent = working_messages[-keep_recent:]
        older = working_messages[:-keep_recent]
        older_count = len(older)

        cache_key = f"{session_id}:{older_count}"

        if total <= summary_trigger:
            summary = self._cache.get(cache_key, "")
            if not summary:
                summary = self._summarize(older)
                if summary:
                    self._cache[cache_key] = summary
                    self._cache_boundary[session_id] = older_count
        else:
            if cache_key in self._cache:
                summary = self._cache[cache_key]
            else:
                for cached_key, cached_summary in list(self._cache.items()):
                    if cached_key.startswith(f"{session_id}:"):
                        summary = cached_summary
                        break
                else:
                    summary = self._summarize(older)
                    if summary:
                        self._cache[cache_key] = summary
                        self._cache_boundary[session_id] = older_count

        return self._format_history(recent), summary

    def _format_history(self, messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        result = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                result.append({"role": "user", "content": content})
            elif role == "assistant":
                result.append({"role": "assistant", "content": content})
        return result

    def _summarize(self, messages: List[Dict[str, Any]]) -> str:
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
            if hasattr(self._llm, "chat_sync"):
                result = self._llm.chat_sync(query=prompt, system_prompt="", max_tokens=256, temperature=0.3)
            elif hasattr(self._llm, "chat"):
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(asyncio.run, self._llm.chat(query=prompt, max_tokens=256, temperature=0.3))
                        result = future.result()
                except RuntimeError:
                    result = asyncio.run(self._llm.chat(query=prompt, max_tokens=256, temperature=0.3))
            else:
                return ""
            logger.info("Conversation summary generated (%d messages → %d chars)", len(messages), len(result))
            return result.strip()
        except Exception as e:
            logger.warning("Summary generation failed: %s", e)
            return ""

    def clear_cache(self, session_id: str = "") -> None:
        if session_id:
            keys_to_remove = [k for k in self._cache if k.startswith(f"{session_id}:")]
            for k in keys_to_remove:
                del self._cache[k]
            self._cache_boundary.pop(session_id, None)
        else:
            self._cache.clear()
            self._cache_boundary.clear()

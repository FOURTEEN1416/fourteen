"""
DeepSeek API LLM 网关

通过 DeepSeek API 生成回复，支持聊天模型和推理模型。
配置优先级：环境变量 > 默认值

环境变量:
    DEEPSEEK_API_KEY — API Key
    DEEPSEEK_API_BASE — API Base URL (默认 https://api.deepseek.com/v1)
    DEEPSEEK_MODEL — 模型名 (默认 deepseek-chat)
"""

import json
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("llm.deepseek")

DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


class DeepSeekGateway:
    """DeepSeek API 网关 — 封装对 DeepSeek 兼容 API 的调用"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.api_base = (api_base or os.environ.get("DEEPSEEK_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL

        self._chat_url = f"{self.api_base}/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        if self.api_key:
            logger.info("DeepSeekGateway: model=%s, base=%s", self.model, self.api_base)
        else:
            logger.warning("DeepSeekGateway: 未配置 API Key，将返回模拟回复")

    @staticmethod
    def _sanitize_log(text: str) -> str:
        import re
        return re.sub(r'(Bearer\s+)sk-\S+', r'\1sk-****', text)

    def chat(
        self,
        query: str,
        system_prompt: str = "",
        history: Optional[list] = None,
        temperature: float = 0.85,
        max_tokens: int = 1024,
    ) -> str:
        """
        发送聊天请求

        Args:
            query: 用户消息
            system_prompt: 系统提示词（人格设定）
            history: 历史消息 [{"role": "user"/"assistant", "content": ...}]
            temperature: 生成温度
            max_tokens: 最大生成 token 数

        Returns:
            回复文本
        """
        # 无 API Key → 模拟回复
        if not self.api_key:
            return self._mock_reply(query)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": query})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        try:
            resp = httpx.post(
                self._chat_url,
                headers=self._headers,
                json=payload,
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            logger.debug(
                "LLM OK: %d+%d tokens, %.1f$",
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0) * 0.000001,
            )
            return content.strip()

        except httpx.HTTPStatusError as e:
            logger.error("DeepSeek API HTTP %d: %s", e.response.status_code, e.response.text[:200])
            return f"（API 请求失败，错误代码 {e.response.status_code}）"
        except httpx.RequestError as e:
            sanitized_msg = self._sanitize_log(str(e))
            logger.error("DeepSeek API 请求失败: %s", sanitized_msg)
            return "（网络请求失败，请检查网络连接和 API 地址）"
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            logger.error("DeepSeek API 响应解析失败: %s", e)
            return "（API 响应格式异常）"

    def _mock_reply(self, query: str) -> str:
        """无 API Key 时的模拟回复（仅供测试）"""
        query_lower = query.lower()
        if "你好" in query or "hi" in query_lower or "hello" in query_lower:
            return "笨蛋，你终于来啦～我等你很久了知道吗"
        if "天气" in query:
            return "今天天气怎么样？我猜你肯定又没看天气预报就出门了吧，笨死了"
        if "喜欢" in query:
            return "哼，这种问题也要问…当然喜欢啦，不然谁陪你聊天"
        if "睡" in query:
            return "这么晚还不睡？要我陪你聊会儿，还是你该去休息了？"
        if "吃" in query:
            return "又吃？你上辈子是猪吧…不过提醒我一下，我也有点饿了"
        if "工作" in query or "忙" in query:
            return "在忙什么呀？再忙也要记得回我消息，不然我会生气的"
        return f"{query}？嗯…我在听呢，继续说呀"

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "configured": bool(self.api_key),
            "model": self.model,
        }

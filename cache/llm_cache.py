"""
LLM响应缓存 — 基于语义相似度的智能缓存

特性:
- 基于请求内容的哈希缓存
- 语义相似度匹配（可选）
- 缓存统计和命中率监控
- 支持流式和非流式响应
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from .redis_client import RedisClient, get_redis_client

logger = logging.getLogger("cache.llm")

T = TypeVar("T")


class CacheStats:
    """缓存统计信息"""

    def __init__(self):
        self.hits = 0
        self.misses = 0
        self.errors = 0
        self.saved_tokens = 0
        self.saved_cost = 0.0
        self._start_time = time.time()

    @property
    def total_requests(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        total = self.total_requests
        return self.hits / total if total > 0 else 0.0

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    def to_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "errors": self.errors,
            "hit_rate": round(self.hit_rate, 4),
            "total_requests": self.total_requests,
            "saved_tokens": self.saved_tokens,
            "saved_cost": round(self.saved_cost, 4),
            "uptime_seconds": round(self.uptime_seconds, 2),
        }


class LLMCache:
    """LLM响应缓存管理器"""

    DEFAULT_TTL = 3600 * 24 * 7  # 7天
    KEY_PREFIX = "llm:cache:v1"
    STATS_KEY = "llm:cache:stats"

    def __init__(
        self,
        redis_client: RedisClient | None = None,
        ttl: int = DEFAULT_TTL,
        enabled: bool = True,
    ):
        self.redis = redis_client or get_redis_client()
        self.ttl = ttl
        self.enabled = enabled and self.redis.enabled
        self.stats = CacheStats()
        self._local_stats: dict[str, Any] = {}

        if not self.enabled:
            logger.info("LLM缓存已禁用")
        else:
            logger.info("LLM缓存已启用，TTL=%s秒", ttl)

    def _generate_key(
        self,
        messages: list[dict],
        model: str,
        temperature: float,
        max_tokens: int | None = None,
        **kwargs,
    ) -> str:
        """
        生成缓存键

        基于消息内容、模型参数生成确定性哈希
        """
        # 构建缓存键内容
        cache_content = {
            "messages": messages,
            "model": model,
            "temperature": round(temperature, 2),
            "max_tokens": max_tokens,
        }

        # 添加其他影响输出的参数
        for key in ["top_p", "presence_penalty", "frequency_penalty"]:
            if key in kwargs:
                cache_content[key] = kwargs[key]

        # 生成哈希
        content_str = json.dumps(cache_content, sort_keys=True, ensure_ascii=False)
        hash_value = hashlib.sha256(content_str.encode()).hexdigest()[:32]

        return f"{self.KEY_PREFIX}:{hash_value}"

    def get(
        self,
        messages: list[dict],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs,
    ) -> dict | None:
        """
        获取缓存的响应

        Returns:
            缓存的响应数据，或None（未命中）
        """
        if not self.enabled:
            return None

        key = self._generate_key(messages, model, temperature, max_tokens, **kwargs)

        try:
            cached = self.redis.get(key)
            if cached:
                data = json.loads(cached)
                self.stats.hits += 1

                # 估算节省的token和成本
                response_data = data.get("response", {})
                usage_data = response_data.get("usage", {})
                tokens = usage_data.get("total_tokens", 0)
                self.stats.saved_tokens += tokens
                self.stats.saved_cost += tokens * 0.000002  # 估算成本

                logger.debug("缓存命中: %s", key[:16])
                return data

            self.stats.misses += 1
            logger.debug("缓存未命中: %s", key[:16])
            return None

        except Exception as e:
            self.stats.errors += 1
            logger.warning("缓存读取失败: %s", e)
            return None

    def set(
        self,
        messages: list[dict],
        response: dict,
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        ttl: int | None = None,
        **kwargs,
    ) -> bool:
        """
        设置缓存响应

        Returns:
            是否成功缓存
        """
        if not self.enabled:
            return False

        # 只缓存成功的响应
        if response.get("error"):
            return False

        key = self._generate_key(messages, model, temperature, max_tokens, **kwargs)

        try:
            cache_data = {
                "response": response,
                "cached_at": time.time(),
                "model": model,
            }

            expire = ttl or self.ttl
            success = self.redis.set(key, json.dumps(cache_data), expire=expire)

            if success:
                logger.debug("缓存已设置: %s (TTL=%s)", key[:16], expire)

            return success

        except Exception as e:
            self.stats.errors += 1
            logger.warning("缓存写入失败: %s", e)
            return False

    def invalidate(
        self,
        pattern: str = "*",
    ) -> int:
        """
        使缓存失效

        Args:
            pattern: 键匹配模式，默认全部

        Returns:
            删除的键数量
        """
        if not self.enabled:
            return 0

        try:
            keys = self.redis.keys(f"{self.KEY_PREFIX}:{pattern}")
            for key in keys:
                self.redis.delete(key)

            logger.info("缓存已清理: %s 个键", len(keys))
            return len(keys)

        except Exception as e:
            logger.error("缓存清理失败: %s", e)
            return 0

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        stats = self.stats.to_dict()

        if self.enabled:
            try:
                keys = self.redis.keys(f"{self.KEY_PREFIX}:*")
                stats["cached_keys"] = len(keys)
            except Exception:
                stats["cached_keys"] = 0
        else:
            stats["cached_keys"] = 0

        return stats

    def health_check(self) -> dict:
        """健康检查"""
        redis_health = self.redis.health_check()

        return {
            "enabled": self.enabled,
            "redis": redis_health,
            "ttl": self.ttl,
            "stats": self.get_stats(),
        }


def cached_chat(
    cache: LLMCache | None = None,
    ttl: int | None = None,
    key_func: Callable[..., str] | None = None,
):
    """
    装饰器 — 为LLM聊天函数添加缓存

    用法:
        @cached_chat()
        async def chat_completion(messages, model, **kwargs):
            return await call_llm(messages, model, **kwargs)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        _cache = cache or LLMCache()

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            if not _cache.enabled:
                return await func(*args, **kwargs)

            # 提取缓存参数
            messages = kwargs.get("messages") or (args[0] if args else [])
            model = kwargs.get("model") or (args[1] if len(args) > 1 else "unknown")
            temperature = kwargs.get("temperature", 0.7)
            max_tokens = kwargs.get("max_tokens")

            # 尝试从缓存获取
            cached = _cache.get(messages, model, temperature, max_tokens, **kwargs)
            if cached:
                return cached.get("response")

            # 调用原始函数
            response = await func(*args, **kwargs)

            # 缓存响应
            _cache.set(messages, response, model, temperature, max_tokens, ttl=ttl, **kwargs)

            return response

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            if not _cache.enabled:
                return func(*args, **kwargs)

            # 提取缓存参数
            messages = kwargs.get("messages") or (args[0] if args else [])
            model = kwargs.get("model") or (args[1] if len(args) > 1 else "unknown")
            temperature = kwargs.get("temperature", 0.7)
            max_tokens = kwargs.get("max_tokens")

            # 尝试从缓存获取
            cached = _cache.get(messages, model, temperature, max_tokens, **kwargs)
            if cached:
                return cached.get("response")

            # 调用原始函数
            response = func(*args, **kwargs)

            # 缓存响应
            _cache.set(messages, response, model, temperature, max_tokens, ttl=ttl, **kwargs)

            return response

        # 根据函数类型返回适当的包装器
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator

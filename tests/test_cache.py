"""
LLM缓存层测试

测试内容:
- Redis客户端连接
- 缓存键生成
- 缓存读写
- 缓存统计
- 装饰器功能
"""

import json
import time
from unittest.mock import Mock, patch

import pytest

# 标记是否可测试缓存
try:
    from cache.llm_cache import CacheStats, LLMCache, cached_chat
    from cache.redis_client import RedisClient, get_redis_client
    CACHE_AVAILABLE = True
except ImportError:
    CACHE_AVAILABLE = False


@pytest.fixture
def mock_redis_client():
    """创建Mock Redis客户端"""
    client = Mock(spec=RedisClient)
    client.enabled = True
    client.get = Mock(return_value=None)
    client.set = Mock(return_value=True)
    client.delete = Mock(return_value=True)
    client.exists = Mock(return_value=False)
    client.keys = Mock(return_value=[])
    return client


@pytest.fixture
def llm_cache(mock_redis_client):
    """创建带Mock的LLMCache实例"""
    cache = LLMCache(redis_client=mock_redis_client, ttl=3600, enabled=True)
    return cache


class TestCacheStats:
    """测试缓存统计"""

    def test_stats_initialization(self):
        """测试统计初始化"""
        stats = CacheStats()
        assert stats.hits == 0
        assert stats.misses == 0
        assert stats.errors == 0
        assert stats.saved_tokens == 0
        assert stats.saved_cost == 0.0
        assert stats.total_requests == 0
        assert stats.hit_rate == 0.0

    def test_stats_hit_rate(self):
        """测试命中率计算"""
        stats = CacheStats()
        stats.hits = 80
        stats.misses = 20
        assert stats.total_requests == 100
        assert stats.hit_rate == 0.8

    def test_stats_to_dict(self):
        """测试统计字典转换"""
        stats = CacheStats()
        stats.hits = 10
        stats.misses = 5
        stats.saved_tokens = 1000
        stats.saved_cost = 0.002

        data = stats.to_dict()
        assert data["hits"] == 10
        assert data["misses"] == 5
        assert abs(data["hit_rate"] - 10 / 15) < 0.001  # 浮点数精度容差
        assert data["saved_tokens"] == 1000
        assert data["saved_cost"] == 0.002


class TestLLMCache:
    """测试LLM缓存"""

    def test_generate_key_consistency(self, llm_cache):
        """测试缓存键生成一致性"""
        messages = [{"role": "user", "content": "Hello"}]
        key1 = llm_cache._generate_key(messages, "gpt-4", 0.7, 1024)
        key2 = llm_cache._generate_key(messages, "gpt-4", 0.7, 1024)
        assert key1 == key2

    def test_generate_key_different_params(self, llm_cache):
        """测试不同参数生成不同键"""
        messages = [{"role": "user", "content": "Hello"}]
        key1 = llm_cache._generate_key(messages, "gpt-4", 0.7, 1024)
        key2 = llm_cache._generate_key(messages, "gpt-4", 0.8, 1024)
        assert key1 != key2

    def test_cache_get_hit(self, llm_cache, mock_redis_client):
        """测试缓存命中"""
        cached_data = {
            "response": {"content": "Cached response", "usage": {"total_tokens": 100}},
            "cached_at": time.time(),
        }
        mock_redis_client.get.return_value = json.dumps(cached_data)

        messages = [{"role": "user", "content": "Hello"}]
        result = llm_cache.get(messages, "gpt-4", 0.7, 1024)

        assert result is not None
        assert result["response"]["content"] == "Cached response"
        assert llm_cache.stats.hits == 1
        assert llm_cache.stats.saved_tokens == 100

    def test_cache_get_miss(self, llm_cache, mock_redis_client):
        """测试缓存未命中"""
        mock_redis_client.get.return_value = None

        messages = [{"role": "user", "content": "Hello"}]
        result = llm_cache.get(messages, "gpt-4", 0.7, 1024)

        assert result is None
        assert llm_cache.stats.misses == 1

    def test_cache_set_success(self, llm_cache, mock_redis_client):
        """测试缓存设置成功"""
        messages = [{"role": "user", "content": "Hello"}]
        response = {"content": "Test response", "usage": {"total_tokens": 50}}

        result = llm_cache.set(messages, response, "gpt-4", 0.7, 1024)

        assert result is True
        mock_redis_client.set.assert_called_once()

    def test_cache_set_error_response(self, llm_cache, mock_redis_client):
        """测试错误响应不缓存"""
        messages = [{"role": "user", "content": "Hello"}]
        response = {"content": "Error", "error": True}

        result = llm_cache.set(messages, response, "gpt-4", 0.7, 1024)

        assert result is False
        mock_redis_client.set.assert_not_called()

    def test_cache_invalidate(self, llm_cache, mock_redis_client):
        """测试缓存失效"""
        mock_redis_client.keys.return_value = ["llm:cache:v1:key1", "llm:cache:v1:key2"]

        deleted = llm_cache.invalidate("*")

        assert deleted == 2
        assert mock_redis_client.delete.call_count == 2

    def test_get_stats(self, llm_cache, mock_redis_client):
        """测试获取统计信息"""
        mock_redis_client.keys.return_value = ["key1", "key2"]
        llm_cache.stats.hits = 10
        llm_cache.stats.misses = 5

        stats = llm_cache.get_stats()

        assert stats["hits"] == 10
        assert stats["misses"] == 5
        assert stats["cached_keys"] == 2

    def test_health_check_enabled(self, llm_cache, mock_redis_client):
        """测试健康检查（启用状态）"""
        mock_redis_client.health_check.return_value = {
            "available": True,
            "latency_ms": 1.5,
        }

        health = llm_cache.health_check()

        assert health["enabled"] is True
        assert health["ttl"] == 3600

    def test_health_check_disabled(self):
        """测试健康检查（禁用状态）"""
        mock_client = Mock(spec=RedisClient)
        mock_client.enabled = False
        cache = LLMCache(redis_client=mock_client, enabled=False)

        health = cache.health_check()

        assert health["enabled"] is False


class TestCachedChatDecorator:
    """测试缓存装饰器"""

    @pytest.mark.asyncio
    async def test_cached_chat_async_hit(self, mock_redis_client):
        """测试异步缓存装饰器命中"""
        cached_data = {
            "response": {"content": "Cached!", "usage": {}},
            "cached_at": time.time(),
        }
        mock_redis_client.get.return_value = json.dumps(cached_data)

        cache = LLMCache(redis_client=mock_redis_client, enabled=True)

        @cached_chat(cache=cache)
        async def async_chat(messages, model, **kwargs):
            return {"content": "Fresh!"}

        result = await async_chat([{"role": "user", "content": "Hi"}], "gpt-4")

        assert result["content"] == "Cached!"

    @pytest.mark.asyncio
    async def test_cached_chat_async_miss(self, mock_redis_client):
        """测试异步缓存装饰器未命中"""
        mock_redis_client.get.return_value = None

        cache = LLMCache(redis_client=mock_redis_client, enabled=True)

        @cached_chat(cache=cache)
        async def async_chat(messages, model, **kwargs):
            return {"content": "Fresh!", "usage": {"total_tokens": 10}}

        result = await async_chat([{"role": "user", "content": "Hi"}], "gpt-4")

        assert result["content"] == "Fresh!"
        mock_redis_client.set.assert_called_once()

    def test_cached_chat_sync_hit(self, mock_redis_client):
        """测试同步缓存装饰器命中"""
        cached_data = {
            "response": {"content": "Cached!", "usage": {}},
            "cached_at": time.time(),
        }
        mock_redis_client.get.return_value = json.dumps(cached_data)

        cache = LLMCache(redis_client=mock_redis_client, enabled=True)

        @cached_chat(cache=cache)
        def sync_chat(messages, model, **kwargs):
            return {"content": "Fresh!"}

        result = sync_chat([{"role": "user", "content": "Hi"}], "gpt-4")

        assert result["content"] == "Cached!"

    def test_cached_chat_disabled(self, mock_redis_client):
        """测试缓存禁用时不使用缓存"""
        cache = LLMCache(redis_client=mock_redis_client, enabled=False)

        @cached_chat(cache=cache)
        def sync_chat(messages, model, **kwargs):
            return {"content": "Fresh!"}

        result = sync_chat([{"role": "user", "content": "Hi"}], "gpt-4")

        assert result["content"] == "Fresh!"
        mock_redis_client.get.assert_not_called()


class TestRedisClient:
    """测试Redis客户端"""

    @patch("cache.redis_client.REDIS_AVAILABLE", False)
    def test_redis_not_available(self):
        """测试Redis不可用时的回退"""
        client = RedisClient(enabled=True)
        assert client.enabled is False
        assert client.get("key") is None
        assert client.set("key", "value") is False

    def test_get_redis_client_from_env(self):
        """测试从环境变量创建客户端"""
        with patch.dict("os.environ", {
            "REDIS_HOST": "redis.example.com",
            "REDIS_PORT": "6380",
            "REDIS_DB": "1",
            "REDIS_ENABLED": "false",
        }):
            client = get_redis_client()
            assert client._host == "redis.example.com"
            assert client._port == 6380
            assert client._db == 1
            assert client.enabled is False


@pytest.mark.skipif(not CACHE_AVAILABLE, reason="Cache module not available")
class TestIntegration:
    """集成测试（需要Redis服务）"""

    def test_real_redis_operations(self):
        """测试真实Redis操作（需要本地Redis）"""
        client = get_redis_client(enabled=True)

        if not client.enabled:
            pytest.skip("Redis not available")

        # 测试基本操作
        key = "test:integration:key"
        value = json.dumps({"test": "data"})

        # 设置
        assert client.set(key, value, expire=60) is True

        # 获取
        result = client.get(key)
        assert result == value

        # 存在检查
        assert client.exists(key) is True

        # 删除
        assert client.delete(key) is True
        assert client.exists(key) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

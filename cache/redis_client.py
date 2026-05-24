"""
Redis客户端封装 — 支持连接池和健康检查
"""

from __future__ import annotations

import logging
import os
from typing import Any

try:
    import redis
    from redis.connection import ConnectionPool
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger("cache.redis")


class RedisClient:
    """Redis客户端封装"""

    _instance: RedisClient | None = None
    _pool: Any | None = None

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        max_connections: int = 50,
        socket_timeout: float = 5.0,
        socket_connect_timeout: float = 5.0,
        enabled: bool = True,
    ):
        self.enabled = enabled and REDIS_AVAILABLE
        self._host = host
        self._port = port
        self._db = db
        self._password = password
        self._max_connections = max_connections
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._client: Any | None = None

        if not REDIS_AVAILABLE:
            logger.warning("Redis未安装，缓存功能将禁用。请执行: pip install redis")
            self.enabled = False
            return

        if not self.enabled:
            logger.info("Redis缓存已禁用")
            return

        try:
            self._connect()
            logger.info("Redis连接成功: %s:%s/%s", host, port, db)
        except Exception as e:
            logger.error("Redis连接失败: %s", e)
            self.enabled = False

    def _connect(self) -> None:
        """建立Redis连接"""
        if not REDIS_AVAILABLE:
            return

        self._pool = redis.ConnectionPool(
            host=self._host,
            port=self._port,
            db=self._db,
            password=self._password,
            max_connections=self._max_connections,
            socket_timeout=self._socket_timeout,
            socket_connect_timeout=self._socket_connect_timeout,
            decode_responses=True,
        )
        self._client = redis.Redis(connection_pool=self._pool)

    def get(self, key: str) -> str | None:
        """获取缓存值"""
        if not self.enabled or not self._client:
            return None
        try:
            value = self._client.get(key)
            return value.decode('utf-8') if isinstance(value, bytes) else value
        except Exception as e:
            logger.debug("Redis get失败: %s", e)
            return None

    def set(
        self,
        key: str,
        value: str,
        expire: int | None = None,
    ) -> bool:
        """设置缓存值"""
        if not self.enabled or not self._client:
            return False
        try:
            self._client.set(key, value, ex=expire)
            return True
        except Exception as e:
            logger.debug("Redis set失败: %s", e)
            return False

    def delete(self, key: str) -> bool:
        """删除缓存键"""
        if not self.enabled or not self._client:
            return False
        try:
            self._client.delete(key)
            return True
        except Exception as e:
            logger.debug("Redis delete失败: %s", e)
            return False

    def exists(self, key: str) -> bool:
        """检查键是否存在"""
        if not self.enabled or not self._client:
            return False
        try:
            return bool(self._client.exists(key))
        except Exception as e:
            logger.debug("Redis exists失败: %s", e)
            return False

    def ttl(self, key: str) -> int:
        """获取键剩余过期时间"""
        if not self.enabled or not self._client:
            return -2
        try:
            return self._client.ttl(key)
        except Exception as e:
            logger.debug("Redis ttl失败: %s", e)
            return -2

    def keys(self, pattern: str = "*") -> list[str]:
        """查找匹配模式的键"""
        if not self.enabled or not self._client:
            return []
        try:
            keys = self._client.keys(pattern)
            return [k.decode('utf-8') if isinstance(k, bytes) else k for k in keys]
        except Exception as e:
            logger.debug("Redis keys失败: %s", e)
            return []

    def flushdb(self) -> bool:
        """清空当前数据库"""
        if not self.enabled or not self._client:
            return False
        try:
            self._client.flushdb()
            logger.info("Redis数据库已清空")
            return True
        except Exception as e:
            logger.error("Redis flushdb失败: %s", e)
            return False

    def info(self) -> dict:
        """获取Redis服务器信息"""
        if not self.enabled or not self._client:
            return {}
        try:
            info = self._client.info()
            return {k.decode('utf-8') if isinstance(k, bytes) else k:
                    v.decode('utf-8') if isinstance(v, bytes) else v
                    for k, v in info.items()}
        except Exception as e:
            logger.debug("Redis info失败: %s", e)
            return {}

    def health_check(self) -> dict:
        """健康检查"""
        if not self.enabled:
            return {"available": False, "reason": "disabled"}

        if not self._client:
            return {"available": False, "reason": "not_connected"}

        try:
            start = __import__('time').time()
            self._client.ping()
            latency = (__import__('time').time() - start) * 1000
            return {
                "available": True,
                "latency_ms": round(latency, 2),
                "host": self._host,
                "port": self._port,
                "db": self._db,
            }
        except Exception:
            logger.exception("Redis健康检查异常")
            return {"available": False, "reason": "redis_check_failed"}

    def close(self) -> None:
        """关闭连接"""
        if self._pool:
            try:
                self._pool.disconnect()
                logger.info("Redis连接已关闭")
            except Exception as e:
                logger.debug("Redis关闭失败: %s", e)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_redis_client(
    host: str | None = None,
    port: int | None = None,
    db: int | None = None,
    password: str | None = None,
    enabled: bool | None = None,
) -> RedisClient:
    """
    获取Redis客户端实例（支持环境变量配置）

    环境变量:
        REDIS_HOST — Redis主机 (默认: localhost)
        REDIS_PORT — Redis端口 (默认: 6379)
        REDIS_DB — Redis数据库 (默认: 0)
        REDIS_PASSWORD — Redis密码 (默认: None)
        REDIS_ENABLED — 是否启用Redis (默认: true)
    """
    host = host or os.environ.get("REDIS_HOST", "localhost")
    port = port or int(os.environ.get("REDIS_PORT", "6379"))
    db = db or int(os.environ.get("REDIS_DB", "0"))
    password = password or os.environ.get("REDIS_PASSWORD")
    enabled = enabled if enabled is not None else os.environ.get("REDIS_ENABLED", "true").lower() == "true"

    return RedisClient(
        host=host,
        port=port,
        db=db,
        password=password,
        enabled=enabled,
    )

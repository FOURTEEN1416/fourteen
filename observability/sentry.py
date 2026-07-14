"""
Sentry 错误监控集成

用于生产环境错误追踪。配置方式：
1. 设置环境变量 SENTRY_DSN
2. 在 production .env 中添加 SENTRY_DSN=your-dsn
3. 可选：SENTRY_ENVIRONMENT=production（默认 prod）/ SENTRY_TRACES_SAMPLE_RATE=0.1

如果未配置 SENTRY_DSN，模块自动禁用，不影响启动。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("observability.sentry")

# 从环境变量读取 Sentry DSN
SENTRY_DSN = os.environ.get("SENTRY_DSN", "").strip()
SENTRY_ENVIRONMENT = os.environ.get("SENTRY_ENVIRONMENT", "production")
SENTRY_TRACES_SAMPLE_RATE = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.1"))

# 敏感字段关键词（匹配 header / extra 中的 key，不区分大小写）
_SENSITIVE_KEYS = {"password", "token", "api_key", "secret", "authorization", "cookie"}


def _scrub_sensitive_data(event, hint):
    """过滤 Sentry 事件中的敏感数据"""
    try:
        if "request" in event and "headers" in event["request"]:
            headers = event["request"]["headers"]
            for key in list(headers.keys()):
                if any(s in key.lower() for s in _SENSITIVE_KEYS):
                    headers[key] = "[REDACTED]"
        # 过滤 extra 中的敏感字段
        if "extra" in event:
            for key in list(event["extra"].keys()):
                if any(s in key.lower() for s in _SENSITIVE_KEYS):
                    event["extra"][key] = "[REDACTED]"
    except Exception:
        pass
    return event


def init_sentry() -> bool:
    """初始化 Sentry SDK。返回是否成功启用。"""
    if not SENTRY_DSN:
        logger.info("Sentry 未配置（SENTRY_DSN 为空），跳过")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=SENTRY_ENVIRONMENT,
            traces_sample_rate=SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,  # 不发送用户 PII
            before_send=_scrub_sensitive_data,  # 过滤敏感数据
            integrations=[
                StarletteIntegration(),
                FastApiIntegration(),
                LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
            ],
        )
        logger.info(
            "Sentry 已启用 (env=%s, traces_sample_rate=%.2f)",
            SENTRY_ENVIRONMENT,
            SENTRY_TRACES_SAMPLE_RATE,
        )
        return True
    except Exception:
        logger.warning("Sentry 初始化失败（sentry-sdk 未安装？），跳过")
        return False

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any

from .multi_provider_gateway import DEFAULT_PROVIDER_CONFIG, MultiProviderGateway

logger = logging.getLogger("llm_provider")


def _as_dict(config: Any | None) -> dict[str, Any]:
    if config is None:
        return {}
    if isinstance(config, dict):
        return dict(config)
    if hasattr(config, "model_dump"):
        return config.model_dump()
    raise TypeError("LLM config must be a mapping or Pydantic model")


# 已下线的 provider 列表 — 解析时自动回退到 auto，避免启动崩溃
_RETIRED_PROVIDERS = {"opencode_zen"}


def _resolve_provider(provider: str | None = None) -> str:
    if provider:
        if provider in _RETIRED_PROVIDERS:
            logger.warning(
                "Provider '%s' has been retired; falling back to 'auto'. "
                "Please update your configuration.", provider,
            )
            return "auto"
        return provider
    env_provider = os.environ.get("LLM_PROVIDER", "").strip()
    if env_provider in _RETIRED_PROVIDERS:
        logger.warning(
            "Env LLM_PROVIDER='%s' has been retired; falling back to 'auto'. "
            "Please update your .env file.", env_provider,
        )
        return "auto"
    return env_provider or "auto"


def _provider_defaults(provider: str) -> dict[str, Any]:
    return dict(DEFAULT_PROVIDER_CONFIG.get(provider, {}))


def _build_backend(
    provider: str,
    config: dict[str, Any],
    models_config: list[dict] | None,
) -> Any:
    model = config.get("model") or config.get("primary_model")
    api_key = config.get("api_key") or ""
    api_base = config.get("api_base") or ""
    temperature = config.get("temperature", 0.85)
    max_tokens = config.get("max_tokens", 2048)

    if provider == "auto":
        chain = config.get("fallback_chain") or None
        provider_configs = config.get("providers") or None
        return MultiProviderGateway(
            fallback_chain=chain,
            providers_config=provider_configs,
        )

    # 已下线 provider 的最终防御：即使绕过 _resolve_provider，
    # 也回退到 auto 而非抛 ValueError，避免系统启动崩溃
    if provider in _RETIRED_PROVIDERS:
        logger.warning(
            "Provider '%s' has been retired; falling back to 'auto'.", provider,
        )
        return MultiProviderGateway(
            fallback_chain=config.get("fallback_chain") or None,
            providers_config=config.get("providers") or None,
        )

    if provider == "deepseek":
        from .llm_gateway import LLMGatewayV2

        defaults = _provider_defaults(provider)
        return LLMGatewayV2(
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY", ""),
            api_base=api_base or os.environ.get("DEEPSEEK_API_BASE") or defaults.get("api_base"),
            model=model or os.environ.get("DEEPSEEK_MODEL") or defaults.get("model"),
            models_config=models_config,
        )

    if provider in {"sensenova", "zhipu", "xunfei", "baidu", "custom"}:
        from .openai_compatible_provider import OpenAICompatibleProvider

        defaults = _provider_defaults(provider)
        env_prefix = provider.upper()
        resolved_key = api_key or os.environ.get(f"{env_prefix}_API_KEY", "")
        resolved_base = api_base or os.environ.get(f"{env_prefix}_API_BASE", "") or defaults.get("api_base", "")
        resolved_model = model or os.environ.get(f"{env_prefix}_MODEL", "") or defaults.get("model", "")
        auth_mode = "oauth" if provider == "baidu" else "bearer"
        extra_payload = defaults.get("extra_payload")
        return OpenAICompatibleProvider(
            provider_name=provider,
            api_key=resolved_key,
            api_base=resolved_base,
            model=resolved_model,
            auth_mode=auth_mode,
            api_secret=config.get("api_secret") or os.environ.get(f"{env_prefix}_API_SECRET", ""),
            models_config=models_config,
            max_tokens=max_tokens,
            temperature=temperature,
            stream_enabled=config.get("stream_enabled", True),
            extra_payload=extra_payload,
        )

    raise ValueError(f"Unsupported LLM provider: {provider}")


class ReloadableLLMGateway:
    """Stable proxy whose backend can be replaced without invalidating consumers."""

    def __init__(self) -> None:
        self._target: Any | None = None
        self._fingerprint = ""
        self._lock = threading.RLock()

    @property
    def target(self) -> Any:
        with self._lock:
            if self._target is None:
                raise RuntimeError("LLM gateway is not configured")
            return self._target

    @property
    def fingerprint(self) -> str:
        with self._lock:
            return self._fingerprint

    def swap(self, target: Any, fingerprint: str) -> Any | None:
        with self._lock:
            old = self._target
            self._target = target
            self._fingerprint = fingerprint
            return old

    def __getattr__(self, name: str) -> Any:
        return getattr(self.target, name)

    async def close(self) -> None:
        target = self.target
        close = getattr(target, "close", None)
        if close is None:
            return
        result = close()
        if asyncio.iscoroutine(result):
            await result


_gateway = ReloadableLLMGateway()
_gateway_config_lock = threading.Lock()


def _config_fingerprint(provider: str, config: dict[str, Any], models_config: list[dict] | None) -> str:
    return json.dumps(
        {"provider": provider, "config": config, "models": models_config},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _configure_gateway(
    provider: str | None,
    models_config: list[dict] | None,
    config: Any | None,
) -> tuple[ReloadableLLMGateway, Any | None]:
    config_dict = _as_dict(config)
    resolved = _resolve_provider(provider or config_dict.get("provider"))
    effective_models = models_config or config_dict.get("models_priority")
    fingerprint = _config_fingerprint(resolved, config_dict, effective_models)

    with _gateway_config_lock:
        if _gateway.fingerprint == fingerprint and _gateway._target is not None:
            return _gateway, None
        backend = _build_backend(resolved, config_dict, effective_models)
        old = _gateway.swap(backend, fingerprint)
        logger.info("Configured reloadable LLM gateway: provider=%s, backend=%s", resolved, type(backend).__name__)
        return _gateway, old


def get_llm(
    provider: str | None = None,
    models_config: list[dict] | None = None,
    config: Any | None = None,
) -> Any:
    """Return the process-wide stable LLM proxy.

    Passing config initializes/reconfigures its backend. Calls without arguments
    reuse the active backend so auxiliary routes stay aligned with the runtime.
    """
    if provider is None and config is None and _gateway._target is not None:
        return _gateway
    gateway, old = _configure_gateway(provider, models_config, config)
    if old is not None:
        close = getattr(old, "close", None)
        if close is not None:
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        asyncio.run(result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to close replaced LLM backend: %s", exc)
    return gateway


async def reconfigure_llm(config: Any) -> ReloadableLLMGateway:
    """Atomically replace the active backend and close the previous one."""
    config_dict = _as_dict(config)
    gateway, old = _configure_gateway(None, None, config_dict)
    if old is not None:
        close = getattr(old, "close", None)
        if close is not None:
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to close replaced LLM backend: %s", exc)
    return gateway


# ── 用户级 LLM gateway 缓存（多用户 API Key 隔离） ──

_user_gateways: dict[int, ReloadableLLMGateway] = {}
_user_gateways_lock = threading.Lock()


def get_user_llm(user_id: int, user_config: dict | None) -> Any:
    """返回用户级 LLM gateway。若 user_config 为 None，回退到全局 gateway。

    每个用户维护独立的 ReloadableLLMGateway，配置变更时按 fingerprint 重建。
    """
    if not user_config:
        return get_llm()

    with _user_gateways_lock:
        gw = _user_gateways.get(user_id)
        config_dict = _as_dict(user_config)
        resolved = _resolve_provider(config_dict.get("provider"))
        effective_models = config_dict.get("models_priority")
        fingerprint = _config_fingerprint(resolved, config_dict, effective_models)

        if gw is None:
            gw = ReloadableLLMGateway()
            _user_gateways[user_id] = gw

        if gw.fingerprint != fingerprint or gw._target is None:
            backend = _build_backend(resolved, config_dict, effective_models)
            old = gw.swap(backend, fingerprint)
            if old is not None:
                close = getattr(old, "close", None)
                if close is not None:
                    try:
                        result = close()
                        if asyncio.iscoroutine(result):
                            try:
                                asyncio.get_running_loop().create_task(result)
                            except RuntimeError:
                                pass
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to close user %s LLM backend: %s", user_id, exc)
            logger.info("Configured user %s LLM gateway: provider=%s", user_id, resolved)

        return gw


def invalidate_user_llm(user_id: int) -> None:
    """清除指定用户的 LLM gateway 缓存（用户配置变更后调用）。"""
    with _user_gateways_lock:
        gw = _user_gateways.pop(user_id, None)
    if gw:
        close = getattr(gw, "close", None)
        if close is not None:
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to close user %s LLM on invalidate: %s", user_id, exc)


def get_llm_names(provider: str | None = None) -> list[str]:
    resolved = _resolve_provider(provider)
    if resolved == "sensenova":
        return ["glm-5.2", "deepseek-v4-flash", "sensenova-6.7-flash-lite"]
    if resolved == "zhipu":
        return ["glm-4-flash"]
    if resolved == "xunfei":
        return ["spark-lite"]
    if resolved == "baidu":
        return ["ernie-speed-128k"]
    if resolved == "deepseek":
        return ["deepseek-chat", "deepseek-reasoner"]
    return ["auto (多供应商网关)"]

"""
共享认证模块 - 统一的 API Key 验证

所有路由模块都应从此模块导入 verify_api_key_dep，而非自行定义。
"""

from __future__ import annotations

import hmac
import logging
import threading
import time
from typing import Any

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.runtime_config import is_production

logger = logging.getLogger("api.auth")

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# 全局认证配置（可在运行时更新）
_auth_config: dict[str, Any] = {
    "enabled": False,
    "api_key": "",
}
_auth_lock = threading.Lock()


def configure_auth(enabled: bool, api_key: str) -> None:
    """在应用启动时设置认证参数"""
    with _auth_lock:
        _auth_config["enabled"] = enabled
        _auth_config["api_key"] = api_key


def update_auth_key(api_key: str) -> None:
    """运行时更新 API Key"""
    with _auth_lock:
        _auth_config["api_key"] = api_key


async def verify_api_key_dep(
    request: Request,
    db: AsyncSession = Depends(get_db),
    api_key: str | None = Security(_api_key_header),
) -> bool:
    """统一认证：优先放行**有效 JWT**（控制台用户路径，不把 API Key 打进前端）。

    2026-09-19 裁决：用户侧 API 仅 JWT；API Key 保留给机器/脚本/E2E。
    - 有效 Bearer access token → 通过
    - 否则若 API_KEY_ENABLED 且 X-API-Key 匹配 → 通过
    - API_KEY_ENABLED=false → 保持原放行（生产告警）

    P0 修复批 F1（2026-10-04）：JWT 分支不再只验签——验签通过后必须追加
    主体校验（存在 + is_active + 撤销版本，复用 auth_jwt.resolve_principal_
    from_request 的 W1 单一主体路径）。已停用/已删除/已改密账号的旧 token
    落入 API Key 分支或 401，不再借「能验签」放行（越权缺陷链根因）。
    语义不变量：有效且启用用户的 access JWT 继续放行（user-plane 依赖此行为）。
    """
    # 1) JWT 优先：控制台登录用户无需携带全局 API Key
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        try:
            # 延迟导入（本文件既有惯例，保持导入方向 api.auth ← api.auth_jwt 单向）
            from api.auth_jwt import resolve_principal_from_request

            principal = await resolve_principal_from_request(request, db)
            if principal is not None:
                return True
        except HTTPException:
            # 验签失败 / 主体不存在 / 已停用 / 撤销版本过期 → 落入 API Key 分支
            pass
        except Exception as e:  # noqa: BLE001
            logger.debug("JWT 主体校验失败，回落 API Key: %s", e)

    with _auth_lock:
        enabled = _auth_config["enabled"]
        key = _auth_config["api_key"]

    if not enabled:
        if is_production():
            logger.warning(
                "API 认证未启用，生产环境存在安全风险，"
                "请设置 API_KEY_ENABLED=true 并配置 API_KEY（见 .env.example）"
            )
        return True

    # P0 修复批 F2：移除 query `?api_key=` 传 key 通道——URL 带 key 会进
    # 访问日志/Referer/代理日志（泄露面）；消费面 grep（frontend/e2e/tests/
    # scripts）确认为零引用，header 通道不变。
    candidate = api_key or ""
    if key and hmac.compare_digest(candidate, key):
        return True

    raise HTTPException(
        status_code=401,
        detail="Invalid or missing API key",
        headers={"X-Error-Code": "AUTH_ERROR"},
    )


# ═══════════════════════════════════════════════════════
# 登录/注册防爆破（P0 修复批 F3，2026-10-04）
# ═══════════════════════════════════════════════════════
# 两个机制：
#   1) IP 失败滑窗：同一 IP 60s 内累计 5 次**认证失败** → 429；
#   2) 账号锁定：同一 email/username 连续失败 5 次 → 锁 15 分钟，
#      锁定窗口内 401 通用文案（不泄露「账号存在且被锁」）。
# 状态存进程内存（多 worker 各自生效可接受，与 app_factory 回退限流器同口径）。
# 计数口径为「失败」而非「全部请求」：成功认证不占 IP 额度并清空该账号失败账，
# 否则办公 NAT 等同 IP 高频成功登录会被误伤（爆破面——连续失败——不受影响）。

_BF_IP_WINDOW_SECONDS = 60.0     # IP 滑窗宽
_BF_IP_MAX_FAILURES = 5          # 5 次/分/IP
_BF_FAIL_MAX = 5                 # 连续失败 5 次
_BF_LOCK_SECONDS = 15 * 60.0     # 锁 15 分钟
_BF_MAX_KEYS = 50_000            # 防高基数 key 撑爆内存（同 app_factory 口径）

_bf_ip_hits: dict[str, list[float]] = {}
_bf_account_fails: dict[str, list[float]] = {}
_bf_account_locked_until: dict[str, float] = {}
_bf_lock = threading.Lock()
_bf_last_cleanup: list[float] = [time.time()]


def _bf_now() -> float:
    """时钟缝（测试用 monkeypatch 钉住做时间推进）。"""
    return time.time()


def _bf_account_key(account: str) -> str:
    """账号键归一（登录名/邮箱大小写不敏感，去首尾空白）。"""
    return account.strip().lower()


def _bf_client_ip(request: Request) -> str:
    """客户端 IP：优先 X-Forwarded-For 首跳（nginx 反代真源），再 X-Real-IP，
    最后直连地址。只取 request.client.host 时反代后所有用户同 IP，
    限速会退化成全站 5 次/分钟。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


def _bf_prune(now: float) -> None:
    """过期 key 惰性清理（调用方须已持有 _bf_lock）。"""
    if now - _bf_last_cleanup[0] <= 300 and len(_bf_ip_hits) <= _BF_MAX_KEYS:
        return
    _bf_last_cleanup[0] = now
    for ip, hits in list(_bf_ip_hits.items()):
        if not hits or now - hits[-1] >= _BF_IP_WINDOW_SECONDS:
            _bf_ip_hits.pop(ip, None)
    for key, fails in list(_bf_account_fails.items()):
        if not fails or now - fails[-1] >= _BF_LOCK_SECONDS:
            _bf_account_fails.pop(key, None)
    for key, until in list(_bf_account_locked_until.items()):
        if until <= now:
            _bf_account_locked_until.pop(key, None)


def auth_rate_limit_ip(request: Request) -> None:
    """IP 级失败滑窗**检查**（调用点：登录 / register-invite 入口）。超限 429。

    只查不记：记账在 auth_record_failure（失败才发生）——成功认证不占额度，
    否则同 IP 高频成功登录（办公 NAT / 正常用户流）会被误伤。
    """
    ip = _bf_client_ip(request)
    now = _bf_now()
    with _bf_lock:
        _bf_prune(now)
        hits = [t for t in _bf_ip_hits.get(ip, []) if now - t < _BF_IP_WINDOW_SECONDS]
        _bf_ip_hits[ip] = hits
        if len(hits) >= _BF_IP_MAX_FAILURES:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"X-Error-Code": "RATE_LIMIT"},
            )


def auth_assert_account_unlocked(
    *accounts: str, detail: str = "Invalid login credentials"
) -> None:
    """账号级锁定检查：任一键处于 15 分钟锁定窗内 → 401 通用文案。

    默认文案与登录失败完全一致（锁定状态本身也是秘密）；注册端点传入
    自己的通用文案。
    """
    now = _bf_now()
    with _bf_lock:
        for account in accounts:
            if _bf_account_locked_until.get(_bf_account_key(account), 0.0) > now:
                raise HTTPException(
                    status_code=401,
                    detail=detail,
                    headers={"X-Error-Code": "AUTH_LOCKED"},
                )


def auth_record_failure(request: Request, *accounts: str) -> None:
    """记录一次认证失败：IP 失败滑窗 +1，各账号键失败账 +1。

    同账号键窗口内累计满 _BF_FAIL_MAX → 锁 15 分钟。仅凭证类失败应调用
    （邀请码无效/密码错误/CAS 落败）；409 资源冲突等非凭证失败不计入。
    """
    ip = _bf_client_ip(request)
    now = _bf_now()
    with _bf_lock:
        hits = [t for t in _bf_ip_hits.get(ip, []) if now - t < _BF_IP_WINDOW_SECONDS]
        hits.append(now)
        _bf_ip_hits[ip] = hits
        for account in accounts:
            key = _bf_account_key(account)
            fails = [
                t for t in _bf_account_fails.get(key, [])
                if now - t < _BF_LOCK_SECONDS
            ]
            fails.append(now)
            if len(fails) >= _BF_FAIL_MAX:
                _bf_account_locked_until[key] = now + _BF_LOCK_SECONDS
                _bf_account_fails.pop(key, None)
            else:
                _bf_account_fails[key] = fails


def auth_record_success(*accounts: str) -> None:
    """认证成功：清空该账号失败账（连续失败计数归零）。"""
    with _bf_lock:
        for account in accounts:
            _bf_account_fails.pop(_bf_account_key(account), None)


__all__ = [
    "auth_assert_account_unlocked",
    "auth_rate_limit_ip",
    "auth_record_failure",
    "auth_record_success",
    "configure_auth",
    "update_auth_key",
    "verify_api_key_dep",
]

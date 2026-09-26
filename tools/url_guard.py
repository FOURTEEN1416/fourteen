"""SSRF 守卫 — 所有对外抓取入口的唯一校验 owner。

背景（W6 缺陷 D）：character_card 的 ``_validate_url`` 只做字面前缀匹配
（精确黑名单主机 + 10./172.16-31./192.168. 前缀），无 DNS 解析——
IPv4-mapped IPv6、十进制/十六进制 IP 字面量、保留网段、DNS 解析到内网的
合成域名全部放行。WebSummaryTool 已有一份基于 getaddrinfo + ipaddress 的
正确实现但仅自用。本模块把该机制抽成共享真源，所有抓取入口统一走这里。

两个入口：
- ``assert_public_http_url(url)``：解析并校验，返回拒绝原因（None=放行）。
- ``fetch_guarded(session, url)``：手动逐跳重定向 + 每跳先校验再发请求。

⚠️ 残余风险登记（非本窗自决扩 scope）：校验与连接之间存在 DNS rebinding
TOCTOU 窗口；彻底封死需要 transport 级固定解析 IP（自定义连接池/SNI 处理），
跨 requests/urllib3 版本脆弱。当前以「每跳发请求前一刻重校验 + 禁自动重定向」
收窄窗口，固定解析 IP 登记为遗留事项待专项裁决。
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

logger = logging.getLogger("url_guard")

_ALLOWED_SCHEMES = ("http", "https")
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECT_HOPS = 3

REASON_SCHEME = "仅允许 http/https URL"
REASON_NO_HOST = "URL 缺少主机名"
REASON_UNRESOLVED = "主机名无法解析"
REASON_BAD_ADDR = "地址解析异常"
REASON_INTERNAL = "禁止访问内网/保留地址"


class UrlBlockedError(RuntimeError):
    """URL 被 SSRF 守卫拒绝；message 即面向调用方的中文原因。"""


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:  # type: ignore[name-defined]
    # ::ffff:127.0.0.1 这类 IPv4-mapped 地址解包后再判，否则映射回环可绕过检查
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def assert_public_http_url(url: str) -> str | None:
    """校验 URL 指向公网；返回拒绝原因，None 表示放行。

    校验基于 DNS 解析结果而非字面主机名：十进制/十六进制 IP 字面量由
    getaddrinfo 按操作系统语义归一成点分 IP 后统一判定。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return REASON_UNRESOLVED
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return REASON_SCHEME
    host = parsed.hostname or ""
    if not host:
        return REASON_NO_HOST
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port)
    except socket.gaierror:
        return REASON_UNRESOLVED
    except OSError:
        return REASON_UNRESOLVED
    if not infos:
        return REASON_UNRESOLVED
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return REASON_BAD_ADDR
        if _ip_is_blocked(ip):
            return REASON_INTERNAL
    return None


def fetch_guarded(
    session: Any,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
    max_hops: int = _MAX_REDIRECT_HOPS,
) -> Any:
    """经守卫的 GET：禁自动重定向，每一跳发请求前一刻重校验目标。

    session 只需提供 ``get(url, headers=, timeout=, allow_redirects=)``，
    requests 模块本身（``requests.get``）即满足该协议。
    非法目标抛 :class:`UrlBlockedError`，绝不发出请求。
    """
    reason = assert_public_http_url(url)
    if reason:
        raise UrlBlockedError(reason)
    current = url
    for _ in range(max_hops + 1):
        resp = session.get(
            current,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
        )
        if resp.status_code in _REDIRECT_CODES:
            location = resp.headers.get("location", "")
            if not location:
                raise UrlBlockedError("重定向缺少 location")
            current = urljoin(current, location)
            reason = assert_public_http_url(current)
            if reason:
                raise UrlBlockedError(f"重定向被拒（{reason}）")
            continue
        return resp
    raise UrlBlockedError("重定向次数超限")

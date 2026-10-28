"""BYOK 加固红测（2026-10-13 用户裁决「要对抗和优化」：红队探针转用例）。

红队战果（3 实洞，本文件先红后绿钉死）：
- H1 SSRF：用户 custom/白名单供应商的 api_base 无任何校验——
  http://169.254.169.254/（云元数据，可窃取主机角色凭证）、
  http://127.0.0.1:8000/（内网端口探测）均构造成功（探针1/3c）。
- H2 幽灵链：provider=auto 的 fallback_chain 引用不存在供应商 → 构造期
  成功、运行期每轮请求试坏（探针3a）。
- H3 巨量配置：save_user_llm_config 对 req.config 无大小/结构钳制 →
  5120KB 任意 JSON 可落 SQLite 行（探针6）。

未被攻破（护栏用例，防未来回归）：读侧递归脱敏（探针4）、用户网关
inherit_platform=False 平台 env 物理隔离（探针2）、网关委托链不借平台
（探针7）。

修法：
- H1 → `llm_provider.__init__._build_backend` 对用户路径
  （inherit_platform=False）的 api_base 统一走 `tools.url_guard.
  assert_public_http_url`（复用统一 SSRF 真源；平台自建配置不受限）。
- H2 → get_user_llm 构造期校验 chain 逐项存在于 providers 且带 api_key
  （原有「逐项带 key」检查已在，补「键存在」）。
- H3 → save 端点：config 总大小 ≤64KB、llm_cfg 键白名单、深度钳制。
"""

from __future__ import annotations

import pytest

from llm_provider import _build_backend, get_user_llm
from llm_provider.multi_provider_gateway import MultiProviderGateway

UK = 998001  # 探针用用户 id（不落库，仅构造内存网关）

# 合成 DNS 用的公网地址（example.com 的历史 A 记录；只要 is_private/is_reserved
# 全为假即可，不参与任何网络请求）。
_PUBLIC_IP = "93.184.216.34"


def _cfg(**kw):
    base = {"provider": "custom", "api_key": "sk-u", "model": "m",
            "api_base": "https://api.example.com/v1"}
    base.update(kw)
    return base


def _fake_dns(monkeypatch, host_ip: str = _PUBLIC_IP):
    """合成 DNS：把任意主机名解析到给定地址，测试不依赖本机真实解析结果。

    放行面用例必须在**确定性**的公网解析结果下跑：本机（及大量开发/CI 环境）
    装了 fake-IP 模式的代理（Clash/Surge 等把域名解析到 198.18.0.0/15 或
    fc00::/7），而 ``tools/url_guard._ip_is_blocked`` 会按 ``ip.is_private``
    判为内网并拒绝——守卫本身工作正常，是测试误把「本机网络环境」当成了
    「产品行为」。守卫在调用点取 ``socket.getaddrinfo``，patch 该模块命名
    空间即全局生效（与 tests/test_character_crawler.py 同一手法）。
    """
    import socket as _socket

    def fake(host, port, *a, **kw):
        return [(2, 1, 6, "", (host_ip, port or 80))]

    monkeypatch.setattr(_socket, "getaddrinfo", fake)


# ── H1 SSRF：用户 api_base 必须指向公网 ──────────────────────────


@pytest.mark.parametrize("bad_base", [
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:8000/v1",
    "http://10.0.0.5/v1",
    "http://192.168.1.1/v1",
    "http://[::1]:9000/v1",
    "file:///etc/passwd",
])
def test_user_api_base_blocks_internal_and_bad_scheme(bad_base):
    with pytest.raises(ValueError, match="api_base"):
        get_user_llm(UK, _cfg(api_base=bad_base))


def test_user_api_base_allows_public(monkeypatch):
    """放行面：真实可解析的公网端点（agnes 生产端点 + 合成 DNS）。

    合成 DNS 见 ``_fake_dns``：本机 fake-IP 代理会把域名解析到 198.18.0.0/15，
    那不是被测行为，必须固定成公网地址才能测「守卫对公网放行」。
    """
    _fake_dns(monkeypatch)
    gw = get_user_llm(UK, _cfg(api_base="https://apihub.agnes-ai.com/v1"))
    assert gw is not None


def test_platform_path_not_ssrf_checked(monkeypatch):
    """平台自建配置（inherit_platform=True）不受用户 SSRF 校验约束——
    平台运维可能指向内网网关；守卫只钉用户提交面。"""
    backend = _build_backend(
        "custom",
        {"api_key": "k", "api_base": "http://127.0.0.1:9999/v1", "model": "m"},
        None,
        inherit_platform=True,
    )
    assert backend is not None


# ── H2 幽灵链：auto 链逐项必须真实存在 ───────────────────────────


def test_auto_chain_ghost_provider_rejected():
    with pytest.raises(ValueError, match="fallback_chain"):
        get_user_llm(UK + 1, {
            "provider": "auto",
            "providers": {"agnes": {"api_key": "k1"}},
            "fallback_chain": ["agnes", "ghost"],
        })


def test_auto_chain_valid_builds_gateway():
    gw = get_user_llm(UK + 2, {
        "provider": "auto",
        "providers": {"agnes": {"api_key": "k1"}, "zhipu": {"api_key": "k2"}},
        "fallback_chain": ["agnes", "zhipu"],
    })
    assert isinstance(gw.target, MultiProviderGateway)


# ── H3 巨量配置：写入端点钳制 ────────────────────────────────────


def test_llm_config_size_and_depth_limits():
    from api.routers.misc_routes import _validate_llm_config_payload

    # 超大小
    with pytest.raises(ValueError, match="过大"):
        _validate_llm_config_payload({"provider": "custom", "junk": "A" * (65 * 1024)})
    # 超深度
    deep = {"provider": "custom"}
    cur = deep
    for _ in range(12):
        cur["n"] = {}
        cur = cur["n"]
    with pytest.raises(ValueError, match="嵌套"):
        _validate_llm_config_payload(deep)
    # 合法配置放行
    assert _validate_llm_config_payload(_cfg()) is None


# ── 护栏：未被攻破的三面不得回归 ─────────────────────────────────


def test_sanitize_nested_provider_key():
    from api.routers.misc_routes import _sanitize_llm_config
    out = _sanitize_llm_config({
        "provider": "custom",
        "api_key": "sk-top",
        "providers": {"custom": {"api_key": "sk-nested"}},
    })
    assert "sk-top" not in str(out) and "sk-nested" not in str(out)


def test_user_gateway_has_no_platform_env():
    """平台 env 凭证不得渗入用户网关：unset 用户 key 后构造必须失败。"""
    import os
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.endswith("_API_KEY")}
    try:
        with pytest.raises(ValueError):
            get_user_llm(UK + 3, {"provider": "agnes", "model": "agnes-3.0-flash"})
    finally:
        os.environ.update(saved)


def test_max_tokens_clamped_for_user_backend(monkeypatch):
    """红队 3b：用户提交 10^9 级 max_tokens 必须钳到 32768 上界。

    同 ``test_user_api_base_allows_public``：SSRF 校验在钳制之前（
    ``llm_provider/__init__.py:71-79``），DNS 不放行就永远到不了钳制逻辑，
    故这里同样用合成 DNS 固定公网解析。
    """
    _fake_dns(monkeypatch)
    backend = _build_backend(
        "custom",
        {"api_key": "k", "api_base": "https://apihub.agnes-ai.com/v1",
         "model": "m", "max_tokens": 10**9},
        None,
        inherit_platform=False,
    )
    assert int(getattr(backend, "_max_tokens", getattr(backend, "max_tokens", 0))) <= 32768

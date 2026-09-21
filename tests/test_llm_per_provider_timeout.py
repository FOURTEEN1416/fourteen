"""按供应商超时闸门 + 链首口径回归（2026-09-21「人机味」批次）。

背景：agnes 降出链首后用户报「回复人机味重」。同 prompt 形状下 agnes 出角色腔、
glm-4-flash 出客服腔 → 需要 agnes 回链首；但 agnes 会挂死（read 超时 25s），
链首挂死 + 编排层整链预算 30s = 再次吐出超时兜底句。
因此链首必须带**按供应商收紧的超时**，使「挂死 → 降级」留在 30s 预算内。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from llm_provider.multi_provider_gateway import MultiProviderGateway
from llm_provider.openai_compatible_provider import OpenAICompatibleProvider

ROOT = Path(__file__).resolve().parent.parent
# 编排层整链预算（optimized_orchestrator 的 asyncio.wait_for）
CHAIN_BUDGET = 30.0


def _read_timeout(seconds: float | None) -> float:
    provider = OpenAICompatibleProvider(
        provider_name="t",
        api_key="k",
        api_base="https://example.invalid/v1",
        model="m",
        request_timeout=seconds,
    )
    return provider._request_timeout.read  # noqa: SLF001


def test_request_timeout_default_and_override() -> None:
    assert _read_timeout(None) == 25.0
    assert _read_timeout(20) == 20.0


def test_gateway_plumbs_request_timeout_from_provider_config() -> None:
    gw = MultiProviderGateway(
        fallback_chain=["agnes"],
        providers_config={
            "agnes": {
                "api_key": "sk-test",
                "api_base": "https://example.invalid/v1",
                "model": "agnes-3.0-flash",
                "request_timeout": 20,
            }
        },
    )
    assert gw.all_providers["agnes"]._request_timeout.read == 20.0  # noqa: SLF001


async def test_per_provider_timeout_flows_into_http_client() -> None:
    """构造参数必须真正进到 httpx client，否则只是改了个没人读的属性。"""
    provider = OpenAICompatibleProvider(
        provider_name="t",
        api_key="k",
        api_base="https://example.invalid/v1",
        model="m",
        request_timeout=20,
    )
    assert provider._async_client.timeout.read == 20.0  # noqa: SLF001


def test_system_yaml_chain_head_is_agnes() -> None:
    cfg = yaml.safe_load((ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))
    chain = cfg["llm"]["fallback_chain"]
    assert chain[0] == "agnes"
    assert "zhipu" in chain  # 降级位必须还在


def test_provider_json_agrees_with_yaml_chain_head() -> None:
    """链有两个真源（system.yaml 运行时 / llm_providers.json 控制台），此处钉住一致。

    结构性统一属另一批次；本用例只防「改了一处忘另一处」的既发事故。
    """
    import json

    yaml_chain = yaml.safe_load((ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))[
        "llm"
    ]["fallback_chain"]
    data = json.loads((ROOT / "config" / "llm_providers.json").read_text(encoding="utf-8"))
    assert data["fallback_chain"][: len(yaml_chain)] == yaml_chain


def test_chain_head_hang_leaves_budget_for_degrade() -> None:
    """链首必须**显式**配短超时：挂死后仍要给降级位留出可用窗口。

    实测链尾 zhipu ~1s（最慢 9s），编排层整链 30s 一到就吐兜底句，
    所以链首挂死时长必须严格小于预算，且剩余 > 降级位实测尾延迟。
    """
    import json

    data = json.loads((ROOT / "config" / "llm_providers.json").read_text(encoding="utf-8"))
    head = data["fallback_chain"][0]
    head_timeout = (data["providers"][head] or {}).get("request_timeout")
    assert head_timeout is not None, "链首 provider 未配置 request_timeout（挂死会烧穿整链预算）"
    remaining = CHAIN_BUDGET - float(head_timeout)
    assert remaining >= 8.0, f"降级窗口仅 {remaining}s，不足以让链尾出词"

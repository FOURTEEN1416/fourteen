"""供应商「实配档位 ↔ 对外描述」一致性静态门禁（2026-09-21 ③ 自问自答根治批次）。

背景（LLM 透明硬约束）：``config/llm_providers.json`` 的 zhipu 描述曾写着
GLM-4.7-Flash，而网关实际调用 ``glm-4-flash`` —— 用户按描述去申请额度、
排查时按描述去怀疑模型，口径与实配分家即等于对用户伪装模型身份。
本文件不联网，只做三件事：
1. 每个供应商的 ``description``/``guide`` 必须提到自己**当前配置的 model**；
2. fallback 链的三处真源（``DEFAULT_FALLBACK_CHAIN``、``config/system.yaml``、
   ``llm_providers.json``）不得各说各话，``auto`` 教程里的链序必须与实际一致；
3. ``get_llm_names()``（API/前端展示用）不得出现网关不会用的档位。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from llm_provider import get_llm_names
from llm_provider.multi_provider_gateway import (
    DEFAULT_FALLBACK_CHAIN,
    DEFAULT_PROVIDER_CONFIG,
)

ROOT = Path(__file__).resolve().parents[1]


def _norm(text: str) -> str:
    return re.sub(r"[\s\-_/()（）]", "", text.lower())


def _model_tokens(model: str) -> list[str]:
    """只认**完整档位名**（glm-4.5-flash）。

    曾用「前缀也算命中」，实测被放过：描述里合规地写着「glm-4.5-air 需付费余额」
    就足以让一条把档位改成 GLM-4.7 的错误描述过关 —— 前缀匹配等于没有门禁。
    """
    return [_norm(model)]


@pytest.fixture(scope="module")
def providers_config() -> dict:
    return json.loads((ROOT / "config" / "llm_providers.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def system_llm() -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "system.yaml").read_text(encoding="utf-8"))
    return cfg["llm"]


def _norm_description(provider: dict) -> str:
    return _norm(str(provider.get("description", "")))


def _norm_guide(provider: dict) -> str:
    guide = provider.get("guide") or {}
    chunks = [guide.get("free_quota", "")]
    for key in ("steps", "tips", "warnings"):
        chunks.extend(guide.get(key) or [])
    return _norm(" ".join(str(c) for c in chunks))


def _all_text(provider: dict) -> str:
    return _norm_description(provider) + _norm_guide(provider)


class TestDescriptionMatchesModel:
    """描述文本必须说得出自己实配的模型档位。"""

    def test_json_provider_description_names_configured_model(self, providers_config):
        offenders = []
        for key, provider in providers_config["providers"].items():
            model = str(provider.get("model", ""))
            if not model:
                continue
            tokens = _model_tokens(model)
            if not any(tok in _norm_description(provider) for tok in tokens):
                offenders.append(f"{key}.description 未提及实配 {model}")
            if provider.get("guide") and not any(tok in _norm_guide(provider) for tok in tokens):
                offenders.append(f"{key}.guide 未提及实配 {model}")
        assert not offenders, "供应商描述与实配档位漂移（违反 LLM 透明）：" + "；".join(offenders)

    def test_default_provider_config_description_names_configured_model(self):
        offenders = []
        for key, cfg in DEFAULT_PROVIDER_CONFIG.items():
            model = str(cfg.get("model", ""))
            if not any(tok in _norm(cfg.get("description", "")) for tok in _model_tokens(model)):
                offenders.append(f"{key}: description 未提及 {model}")
        assert not offenders, "DEFAULT_PROVIDER_CONFIG 描述与实配档位漂移：" + "；".join(offenders)

    def test_json_and_default_config_agree_on_model(self, providers_config):
        offenders = [
            f"{key}: json={providers_config['providers'][key].get('model')} "
            f"vs default={DEFAULT_PROVIDER_CONFIG[key]['model']}"
            for key in providers_config["providers"]
            if key in DEFAULT_PROVIDER_CONFIG
            and providers_config["providers"][key].get("model")
            and providers_config["providers"][key]["model"] != DEFAULT_PROVIDER_CONFIG[key]["model"]
        ]
        assert not offenders, "两处档位真源不一致：" + "；".join(offenders)


class TestThinkingSwitchWired:
    """GLM-4.5-Flash 默认开思考：不关就会把「客服腔」换成「空回复」。"""

    def test_zhipu_thinking_disabled_in_json(self, providers_config):
        extra = providers_config["providers"]["zhipu"].get("extra_payload") or {}
        assert (extra.get("thinking") or {}).get("type") == "disabled"

    def test_zhipu_thinking_disabled_in_default_config(self):
        extra = DEFAULT_PROVIDER_CONFIG["zhipu"].get("extra_payload") or {}
        assert (extra.get("thinking") or {}).get("type") == "disabled"

    def test_zhipu_guide_documents_the_switch(self, providers_config):
        assert "thinking" in _all_text(providers_config["providers"]["zhipu"])


class TestFallbackChainSingleSource:
    def test_system_yaml_chain_equals_code_default(self, system_llm):
        assert list(system_llm["fallback_chain"]) == list(DEFAULT_FALLBACK_CHAIN)

    def test_json_chain_is_superset_tail_of_default(self, providers_config):
        chain = list(providers_config["fallback_chain"])
        assert chain[: len(DEFAULT_FALLBACK_CHAIN)] == list(DEFAULT_FALLBACK_CHAIN)

    def test_auto_guide_shows_the_real_chain_order(self, providers_config):
        aliases = {"agnes": "Agnes", "zhipu": "智谱", "xunfei": "讯飞", "baidu": "百度"}
        auto = providers_config["special_options"]["auto"]["guide"]
        text = " ".join(list(auto.get("steps") or []) + list(auto.get("tips") or []))
        positions = []
        for key in DEFAULT_FALLBACK_CHAIN:
            alias = aliases.get(key)
            assert alias, f"链上 {key} 缺少展示别名，请补进本用例 aliases"
            pos = text.find(alias)
            assert pos >= 0, f"auto 教程未提及链上供应商 {key}（{alias}）"
            positions.append(pos)
        assert positions == sorted(positions), (
            f"auto 教程链序与 DEFAULT_FALLBACK_CHAIN 不一致：{DEFAULT_FALLBACK_CHAIN} → {text}"
        )


class TestDisplayedNamesMatchGateway:
    """``get_llm_names`` 是 API/前端展示口径，不能列出网关根本不会调用的档位。"""

    @pytest.mark.parametrize("provider", sorted(DEFAULT_PROVIDER_CONFIG))
    def test_configured_model_is_offered(self, provider):
        model = DEFAULT_PROVIDER_CONFIG[provider]["model"]
        assert model in get_llm_names(provider), f"{provider} 展示档位不含实配 {model}"


def test_intro_page_free_key_guide_matches_reality():
    """未登录引导页（免费 Key 获取引导）不得再宣称未实配的档位/链首。"""
    page = (ROOT / "frontend" / "src" / "pages" / "IntroPage.tsx").read_text(encoding="utf-8")
    assert "4.7" not in page, "引导页出现未实配的 GLM-4.7 档位"
    assert "GLM-4.5-Flash" in page, "引导页未说明智谱实配档位"
    assert "智谱 AI（首选）" not in page, "引导页仍把智谱标为链首（实际链首是 Agnes）"
    agnes_pos, zhipu_pos = page.find("链首"), page.find("降级位")
    assert 0 <= agnes_pos < zhipu_pos, "引导页链首/降级位次序与 fallback_chain 相反"

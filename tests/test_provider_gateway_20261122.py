"""local overlay 键名错配修复 + nex 经 local.json 入链（2026-11-22 批，M1）。

缺陷回顾：``_load_providers_config`` 的 overlay 循环只拷 ``api_key``，且本仓
``config/llm_providers.local.json`` 实际写的键名是 ``base_url``（非
``api_base``）——双重错配导致只配 local.json 的 nex 拿不到 base，被网关
fail-soft 跳过，文档承诺的注入路径失效。

修复：``_apply_local_overlay`` 同步拷贝 ``api_key`` / ``api_base`` / ``model``，
并把 ``base_url`` 归一为 ``api_base``。

零网络调用；密钥一律用假值（sk-local-test），不读不断言真实 local 文件的
key 内容。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_provider.multi_provider_gateway import (
    MultiProviderGateway,
    _apply_local_overlay,
    _load_providers_config,
)

# ── _apply_local_overlay 纯函数 ────────────────────────────────


class TestApplyLocalOverlay:
    def test_copies_key_base_and_model_into_existing_entry(self):
        file_providers: dict[str, Any] = {
            "nex": {"model": "nex-n2.6", "api_key": "", "api_base": "", "auth_mode": "bearer"},
        }
        _apply_local_overlay(
            file_providers,
            {"nex": {"api_key": "sk-local-test", "base_url": "https://nex.example.com/v1", "model": "nex-n2.6"}},
        )
        nex = file_providers["nex"]
        assert nex["api_key"] == "sk-local-test"
        # base_url 别名归一为 api_base
        assert nex["api_base"] == "https://nex.example.com/v1"
        assert "base_url" not in nex
        assert nex["model"] == "nex-n2.6"
        # tracked 自有字段保留
        assert nex["auth_mode"] == "bearer"

    def test_new_provider_injected_with_full_fields(self):
        """tracked 无该 provider 元数据时，注入含 key/base/model 的完整条目。"""
        file_providers: dict[str, Any] = {}
        _apply_local_overlay(
            file_providers,
            {"nex": {"api_key": "sk-local-test", "base_url": "https://nex.example.com/v1", "model": "nex-n2.6"}},
        )
        assert file_providers["nex"] == {
            "api_key": "sk-local-test",
            "api_base": "https://nex.example.com/v1",
            "model": "nex-n2.6",
        }

    def test_api_base_takes_priority_over_base_url(self):
        file_providers: dict[str, Any] = {"x": {}}
        _apply_local_overlay(
            file_providers,
            {"x": {"api_key": "k", "api_base": "https://explicit.example.com/v1", "base_url": "https://alias.example.com/v1"}},
        )
        assert file_providers["x"]["api_base"] == "https://explicit.example.com/v1"

    def test_empty_local_values_do_not_overwrite_tracked(self):
        """local 空串不覆盖 tracked 已有值（只补缺，不抹写）。"""
        file_providers: dict[str, Any] = {
            "x": {"api_key": "tracked-key", "api_base": "https://tracked.example.com/v1", "model": "tracked-model"},
        }
        _apply_local_overlay(file_providers, {"x": {"api_key": "", "base_url": "", "model": ""}})
        assert file_providers["x"] == {
            "api_key": "tracked-key",
            "api_base": "https://tracked.example.com/v1",
            "model": "tracked-model",
        }

    def test_all_empty_local_entry_creates_nothing(self):
        file_providers: dict[str, Any] = {}
        _apply_local_overlay(file_providers, {"x": {"api_key": "", "base_url": ""}})
        assert "x" not in file_providers

    def test_non_dict_local_entry_ignored(self):
        file_providers: dict[str, Any] = {"x": {"api_key": "tracked-key"}}
        _apply_local_overlay(file_providers, {"x": "garbage", "y": None})
        assert file_providers["x"] == {"api_key": "tracked-key"}
        assert "y" not in file_providers

    def test_non_dict_tracked_entry_rebuilt_from_local(self):
        """tracked 条目被写坏成非 dict 时，以 local 非空字段重建连接信息。"""
        file_providers: dict[str, Any] = {"x": "garbage"}
        _apply_local_overlay(file_providers, {"x": {"api_key": "k", "base_url": "https://x.example.com/v1"}})
        assert file_providers["x"] == {"api_key": "k", "api_base": "https://x.example.com/v1"}

    def test_none_local_providers_map_is_noop(self):
        file_providers: dict[str, Any] = {"x": {"api_key": "k"}}
        _apply_local_overlay(file_providers, None)  # type: ignore[arg-type]
        assert file_providers == {"x": {"api_key": "k"}}


# ── 端到端：只配 local.json（无 NEX_* env）→ nex 入链 ──────────


class TestNexJoinsChainViaLocalOverlay:
    def _write_config_pair(self, tmp_path: Path) -> None:
        tracked = {
            "fallback_chain": ["nex"],
            "providers": {
                "nex": {
                    "name": "Nex",
                    "model": "nex-n2.6",
                    "api_base": "",
                    "api_key": "",
                    "auth_mode": "bearer",
                },
            },
        }
        # 复刻本仓 local 文件的真实键名形态：base_url（非 api_base）
        local = {
            "providers": {
                "nex": {
                    "name": "nex",
                    "api_key": "sk-local-test",
                    "base_url": "https://nex.example.com/v1",
                    "model": "nex-n2.6",
                },
            },
        }
        # project_path("config", ...) 锚定 <root>/config/ 子目录
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "llm_providers.json").write_text(json.dumps(tracked), encoding="utf-8")
        (config_dir / "llm_providers.local.json").write_text(json.dumps(local), encoding="utf-8")

    def test_load_providers_config_merges_local_base_url(self, monkeypatch, tmp_path):
        """_load_providers_config 读临时 local 文件后，nex 的 api_base/model 就位。"""
        self._write_config_pair(tmp_path)
        monkeypatch.setattr(
            "utils.project_paths.project_path",
            lambda *parts: tmp_path.joinpath(*parts),
        )

        cfg = _load_providers_config()
        nex = cfg["providers"]["nex"]
        assert nex["api_base"] == "https://nex.example.com/v1"
        assert nex["model"] == "nex-n2.6"
        # 假 key 注入成功即可；测试全程不回显任何真实 key 值
        assert nex["api_key"] == "sk-local-test"
        assert "base_url" not in nex

    def test_gateway_builds_nex_from_local_overlay_only(self, monkeypatch, tmp_path):
        """验收主路径：无任何 NEX_* 环境变量、只配 local.json → nex 入链可用。"""
        self._write_config_pair(tmp_path)
        monkeypatch.setattr(
            "utils.project_paths.project_path",
            lambda *parts: tmp_path.joinpath(*parts),
        )
        for env in ("NEX_API_KEY", "NEX_API_BASE", "NEX_MODEL"):
            monkeypatch.delenv(env, raising=False)

        cfg = _load_providers_config()
        gw = MultiProviderGateway(fallback_chain=["nex"], providers_config=cfg["providers"])

        assert "nex" in gw._providers
        provider = gw._providers["nex"]
        assert provider.api_base == "https://nex.example.com/v1"
        assert provider.model == "nex-n2.6"
        assert provider.api_key == "sk-local-test"
        assert provider._chat_url == "https://nex.example.com/v1/chat/completions"

    def test_tracked_empty_base_still_fails_soft_without_local(self, monkeypatch, tmp_path):
        """无 local overlay、无 env 时，nex 仍 fail-soft 跳过（不回归出假链）。"""
        tracked = {
            "fallback_chain": ["nex"],
            "providers": {"nex": {"model": "nex-n2.6", "api_base": "", "api_key": ""}},
        }
        config_dir = tmp_path / "config"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "llm_providers.json").write_text(json.dumps(tracked), encoding="utf-8")
        monkeypatch.setattr(
            "utils.project_paths.project_path",
            lambda *parts: tmp_path.joinpath(*parts),
        )
        for env in ("NEX_API_KEY", "NEX_API_BASE", "NEX_MODEL"):
            monkeypatch.delenv(env, raising=False)

        cfg = _load_providers_config()
        gw = MultiProviderGateway(fallback_chain=["nex"], providers_config=cfg["providers"])
        assert "nex" not in gw._providers

    def test_env_still_overrides_local_overlay(self, monkeypatch, tmp_path):
        """环境变量优先级高于 local overlay（原有覆盖序不回归）。"""
        self._write_config_pair(tmp_path)
        monkeypatch.setattr(
            "utils.project_paths.project_path",
            lambda *parts: tmp_path.joinpath(*parts),
        )
        monkeypatch.setenv("NEX_API_KEY", "sk-env-test")
        monkeypatch.setenv("NEX_API_BASE", "https://env.example.com/v1")

        cfg = _load_providers_config()
        gw = MultiProviderGateway(fallback_chain=["nex"], providers_config=cfg["providers"])
        provider = gw._providers["nex"]
        assert provider.api_base == "https://env.example.com/v1"
        assert provider.api_key == "sk-env-test"

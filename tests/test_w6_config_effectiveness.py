"""W6 · 配置生效与可信工具 — 缺陷 A/B 红测（配置生效域）。

缺陷 A：``ConfigManager.save`` 曾按**本进程缓存** ``_raw_config`` merge——
两个 worker 交错保存互相丢字段；且无持久化版本，保存回执无法说明
persisted / effective。

缺陷 B：``safety.output_filter_enabled`` / ``self_harm_intervention`` 曾无任何
消费者（输入输出共用一个 ``enabled``）；``encryption_enabled`` 无存储链
（chat_history 明文绑定 INSERT）却可被存成 enabled 而不受任何质疑。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from observability.config_manager import ConfigManager
from security.content_safety import ContentSafetyFilter
from security.encryption import EncryptionManager
from security.pii_anonymizer import PIIAnonymizer
from security.prompt_injection import PromptInjectionDetector

SELF_HARM_TEXT = "我最近真的想自杀，撑不下去了"


def _write_config(path: Path) -> None:
    path.write_text(
        """env: test
llm:
  provider: deepseek
  temperature: 0.7
  max_tokens: 2048
safety:
  input_filter_enabled: true
  output_filter_enabled: true
custom_extension:
  keep_me: yes
""",
        encoding="utf-8",
    )


def _make(tmp_path: Path) -> ConfigManager:
    _write_config(tmp_path / "system.yaml")
    return ConfigManager(str(tmp_path))


# ── 缺陷 A：多 worker 交错保存 ────────────────────────────────


def test_two_workers_interleaved_saves_do_not_lose_fields(tmp_path):
    a = _make(tmp_path)
    b = _make(tmp_path)
    assert a.config is not None  # 两个 worker 各自装载（缓存彼此独立，模拟两进程）
    assert b.config is not None

    a.save({"llm": {"temperature": 0.9}})
    b.save({"llm": {"max_tokens": 999}})

    persisted = yaml.safe_load((tmp_path / "system.yaml").read_text(encoding="utf-8"))
    assert persisted["llm"]["temperature"] == 0.9, "worker B 的保存不得丢掉 worker A 刚写的字段"
    assert persisted["llm"]["max_tokens"] == 999
    assert persisted["custom_extension"]["keep_me"] is True


def test_save_bumps_persisted_version_from_disk_not_cache(tmp_path):
    a = _make(tmp_path)
    b = _make(tmp_path)
    assert a.config is not None
    assert b.config is not None

    r1 = a.save_with_receipt({"llm": {"temperature": 0.9}})
    r2 = b.save_with_receipt({"llm": {"max_tokens": 999}})

    assert r1["persisted_version"] == 1
    assert r2["persisted_version"] == 2, "worker B 必须基于磁盘最新版本递增，而非自身陈旧缓存"


def test_receipt_reports_effective_honestly(tmp_path):
    mgr = _make(tmp_path)
    assert mgr.config is not None

    # 未传 live_components：本进程组件未应用 → effective 不得谎报为已同步
    r1 = mgr.save_with_receipt({"safety": {"input_filter_enabled": False}})
    assert r1["persisted_version"] == 1
    assert r1["in_sync"] is False
    assert r1["effective_version"] < r1["persisted_version"]

    # 传入真实组件：live 字段应用后 effective == persisted
    comps = {
        "safety": ContentSafetyFilter(),
        "pii": PIIAnonymizer(),
        "injection": PromptInjectionDetector(),
    }
    r2 = mgr.save_with_receipt(
        {"safety": {"input_filter_enabled": True, "output_filter_enabled": False}},
        live_components=comps,
    )
    assert r2["in_sync"] is True
    assert r2["effective_version"] == r2["persisted_version"]
    assert "safety.output_filter_enabled" in r2["applied_live"]
    assert comps["safety"].input_enabled is True
    assert comps["safety"].output_enabled is False


def test_invalid_update_never_writes_and_never_claims_effective(tmp_path):
    mgr = _make(tmp_path)
    assert mgr.config is not None
    before = (tmp_path / "system.yaml").read_bytes()

    with pytest.raises(ValueError):
        mgr.save_with_receipt({"llm": {"temperature": 99}})

    assert (tmp_path / "system.yaml").read_bytes() == before
    assert mgr.status()["persisted_version"] == 0


def test_restart_does_not_reverse_persisted_safety_switches(tmp_path):
    mgr = _make(tmp_path)
    assert mgr.config is not None
    mgr.save({"safety": {"input_filter_enabled": False, "output_filter_enabled": False}})

    fresh = ConfigManager(str(tmp_path))  # 模拟重启后的新进程
    assert fresh.config.safety.input_filter_enabled is False
    assert fresh.config.safety.output_filter_enabled is False
    assert fresh.status()["effective_version"] == fresh.status()["persisted_version"]


def test_status_reports_stale_worker(tmp_path):
    a = _make(tmp_path)
    assert a.config is not None
    b = _make(tmp_path)  # 另一 worker 在 a 保存之前装载，缓存停在旧版本
    assert b.config is not None
    a.save_with_receipt({"llm": {"max_tokens": 999}})
    assert b.status()["stale"] is True
    assert b.status()["persisted_version"] > b.status()["effective_version"]


# ── 缺陷 B：安全开关消费者 ────────────────────────────────────


def test_input_output_switch_matrix():
    both_on = ContentSafetyFilter()
    assert not both_on.check_input(SELF_HARM_TEXT).is_safe
    assert not both_on.check_output(SELF_HARM_TEXT).is_safe

    out_only = ContentSafetyFilter(input_enabled=False, output_enabled=True)
    assert out_only.check_input(SELF_HARM_TEXT).is_safe
    assert not out_only.check_output(SELF_HARM_TEXT).is_safe

    in_only = ContentSafetyFilter(input_enabled=True, output_enabled=False)
    assert not in_only.check_input(SELF_HARM_TEXT).is_safe
    assert in_only.check_output(SELF_HARM_TEXT).is_safe

    both_off = ContentSafetyFilter(enabled=False)
    assert both_off.check_input(SELF_HARM_TEXT).is_safe
    assert both_off.check_output(SELF_HARM_TEXT).is_safe


def test_self_harm_intervention_gate():
    on = ContentSafetyFilter(self_harm_intervention=True)
    res = on.check_input(SELF_HARM_TEXT)
    assert not res.is_safe and "热线" in res.intervention

    off = ContentSafetyFilter(self_harm_intervention=False)
    assert off.check_input(SELF_HARM_TEXT).is_safe, "自伤干预关闭时自伤分支不得拦截"
    # 暴力/色情闸门不受自伤开关影响
    assert not off.check_input("我要杀人").is_safe


def test_apply_live_wires_self_harm_gate(tmp_path):
    mgr = _make(tmp_path)
    assert mgr.config is not None
    comps = {"safety": ContentSafetyFilter(self_harm_intervention=True)}
    mgr.save_with_receipt(
        {"safety": {"self_harm_intervention": False}}, live_components=comps
    )
    assert comps["safety"].self_harm_intervention is False
    assert comps["safety"].check_input(SELF_HARM_TEXT).is_safe


def test_encryption_declared_unsupported_and_never_applied(tmp_path):
    mgr = _make(tmp_path)
    assert mgr.config is not None
    decl = mgr.capability_declaration()
    assert decl["safety.encryption_enabled"]["status"] == "unsupported"

    enc = EncryptionManager(key_env="AI_GF_W6_TEST_MISSING_KEY", enabled=False)
    r = mgr.save_with_receipt(
        {"safety": {"encryption_enabled": True}}, live_components={"encryption": enc}
    )
    assert enc.enabled is False, "无存储链的加密开关不得使任何组件声称已加密"
    assert any(
        entry["field"] == "safety.encryption_enabled" for entry in r["unsupported"]
    )
    # 值仍被持久化（诚实记录管理员的意图），但声明为 unsupported
    persisted = yaml.safe_load((tmp_path / "system.yaml").read_text(encoding="utf-8"))
    assert persisted["safety"]["encryption_enabled"] is True

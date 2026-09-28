"""W4 缺陷 G 停用 → 2026-09-28 历遍批整删：memory_ext 防复活契约升级为「不存在」。

原契约（W4）：enabled=false + `_init_memory_ext` 置 components None。
历遍批裁决（用户令「扫描死代码…修复和完善」）：第二记忆真源整包删除
（memory_ext/ 包、装配桩 `_init_memory_ext`、system.yaml `memory_ext` 段）。
本文件钉住删除不可逆——任何复活（重建包/重加配置段/重加装配桩）即红。
"""

from __future__ import annotations

from pathlib import Path

import yaml


def test_memory_ext_package_deleted():
    assert not Path("memory_ext").exists(), (
        "memory_ext 第二记忆真源已于 2026-09-28 整删，不得复活（详见 DELETION_LOG）"
    )


def test_config_no_memory_ext_section():
    data = yaml.safe_load(Path("config/system.yaml").read_text(encoding="utf-8"))
    assert "memory_ext" not in data, "system.yaml 不得再出现 memory_ext 段（含禁用形态）"


def test_init_mixin_no_memory_ext_stub():
    src = Path("orchestrator/_init_mixin.py").read_text(encoding="utf-8")
    assert "_init_memory_ext" not in src, "装配桩 _init_memory_ext 已整删，不得复活"
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    assert not any("memory_ext" in ln for ln in code_lines), (
        "orchestrator 代码行不得再引用 memory_ext（注释留痕除外）"
    )

"""W4 · memory_ext 第二套记忆真源停用（缺陷 G / 包任务 7）。

钉住：
1. `config/system.yaml` memory_ext.enabled=false（默认不得两套并行）；
2. 装配层 `_init_memory_ext` 不再构造 MemoryEnhancer，components["memory_ext"] is None；
3. 主记忆真源仍是 shisi/memory（StructuredMemory/SemanticMemory），不把两套都接上。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import yaml


def test_config_memory_ext_disabled():
    cfg_path = Path("config/system.yaml")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert data.get("memory_ext", {}).get("enabled") is False, (
        "memory_ext 默认必须禁用（第二套 RAG 记忆真源）"
    )


def test_init_memory_ext_never_builds_second_store():
    from orchestrator._init_mixin import _InitPhasesMixin as OrchestratorInitMixin

    src = inspect.getsource(OrchestratorInitMixin._init_memory_ext)
    assert "MemoryEnhancer" not in src, "不得再装配 MemoryEnhancer"
    assert "components[\"memory_ext\"] = None" in src or "components['memory_ext'] = None" in src


def test_init_memory_ext_sets_none_on_instance():
    class _Fake:
        components = {"llm": object()}
        def _run_async(self, *a, **k):
            raise AssertionError("不得 initialize 第二套库")

    from orchestrator._init_mixin import _InitPhasesMixin as OrchestratorInitMixin

    inst = _Fake()
    OrchestratorInitMixin._init_memory_ext(inst, None, {})
    assert inst.components.get("memory_ext") is None

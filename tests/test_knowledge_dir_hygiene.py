"""知识目录卫生 — 测试隔离开关 + 孤儿审计/清理能力（红测先行）。

缺陷背景（角色域 2026-10-12 批 item 2，本地取证）：
- conftest.isolate_runtime_state_files 隔离了 scheduler/affinity/ase 等状态文件
  却缺 knowledge：测试单例直写真实 data/knowledge（testcid/default 索引
  mtime=10-12 19:15/19:17 污染持续中）；
- 本地孤儿索引 552 个（总数 593 − 现役 41 索引）、孤儿源 544 个（总数 546 −
  现役 2 store）持续膨胀；rebuild 脚本现状只扫顶层索引（孤儿源完全不审计），
  且无 --root 参数（测试无法用 tmp 根）。

钉住的行为：
- 红1 单例构造时读 AI_GF_KNOWLEDGE_INDEX_DIR 重定向 index_dir（含 sources
  子目录）；未设置时与现默认逐字节一致（主控只需在 conftest 加两行 monkeypatch）；
- 红2 脚本 --root 指定项目根（默认 "."，现行为不变）；--dry-run 分列孤儿索引
  与孤儿源且不删；--prune-orphan-sources 显式 flag 才删孤儿源（含其 .lock），
  现役源及其 .lock 保留；执行模式删除孤儿索引为既有行为。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rebuild_knowledge_index.py"
CID = "hygc01"


def _load_script():
    spec = importlib.util.spec_from_file_location("rebuild_knowledge_index", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── 红1 · 单例 env 隔离开关 ─────────────────────────────────


class TestSingletonEnvRedirect:
    def test_env_redirects_index_dir_and_source_root(self, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch):
        import shisi.knowledge.character_knowledge_service as cks

        monkeypatch.setenv("AI_GF_KNOWLEDGE_INDEX_DIR", str(tmp_path / "kb"))
        monkeypatch.setattr(cks, "_knowledge_service", None)
        svc = cks.get_knowledge_service()
        assert svc._index_dir == tmp_path / "kb", (
            "env 设置时单例必须重定向 index_dir（现状恒真实 data/knowledge→红）")
        assert svc._source_store.root == tmp_path / "kb" / "sources", (
            "sources 子目录必须随 index_dir 一体重定向")

    def test_env_unset_keeps_default_behavior(self, monkeypatch: pytest.MonkeyPatch):
        import shisi.knowledge.character_knowledge_service as cks

        monkeypatch.delenv("AI_GF_KNOWLEDGE_INDEX_DIR", raising=False)
        monkeypatch.setattr(cks, "_knowledge_service", None)
        svc = cks.get_knowledge_service()
        assert svc._index_dir == cks._DEFAULT_INDEX_DIR, "未设 env 时行为必须与现默认一致"
        assert svc._source_store.root == cks._DEFAULT_INDEX_DIR / "sources"


# ── 红2 · 脚本 --root + 孤儿分列审计 + 显式 prune ───────────


def _make_tmp_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    chars = root / "config" / "characters"
    chars.mkdir(parents=True)
    (chars / f"{CID}.json").write_text(json.dumps(
        {"id": CID, "name": "试卡", "description": "她信守约定，以传信为业。"}),
        encoding="utf-8")
    know = root / "data" / "knowledge"
    src = know / "sources"
    src.mkdir(parents=True)
    # 现役：索引 + 源 + 锁
    (know / f"{CID}.json").write_text("{}", encoding="utf-8")
    (src / f"{CID}.json").write_text("{}", encoding="utf-8")
    (src / f"{CID}.json.lock").write_text("", encoding="utf-8")
    # 孤儿：索引 + 源 + 锁
    (know / "orphan_idx.json").write_text("{}", encoding="utf-8")
    (src / "orphan_src.json").write_text("{}", encoding="utf-8")
    (src / "orphan_src.json.lock").write_text("", encoding="utf-8")
    return root, know, src


class TestRebuildScriptOrphanAudit:
    def test_dry_run_lists_both_categories_and_deletes_nothing(
            self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        mod = _load_script()
        root, know, src = _make_tmp_root(tmp_path)

        mod.main(["--root", str(root), "--dry-run"])
        out = capsys.readouterr().out

        assert "孤儿索引审计" in out and "orphan_idx" in out, (
            "dry-run 必须分列孤儿索引（现状 dry-run 完全不审计→红）")
        assert "孤儿源审计" in out and "orphan_src" in out, (
            "现状只扫顶层索引、完全不扫 sources/→红")
        assert (know / "orphan_idx.json").exists()
        assert (src / "orphan_src.json").exists()
        assert (know / f"{CID}.json").exists() and (src / f"{CID}.json").exists()

    def test_prune_orphan_sources_explicit_flag_deletes_only_orphans(
            self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        mod = _load_script()
        root, know, src = _make_tmp_root(tmp_path)

        mod.main(["--root", str(root), "--prune-orphan-sources"])

        assert not (src / "orphan_src.json").exists(), "显式 flag 才删孤儿源"
        assert not (src / "orphan_src.json.lock").exists()
        assert (src / f"{CID}.json").exists(), "现役源必须保留"
        assert (src / f"{CID}.json.lock").exists(), "现役源 .lock 必须保留"
        assert not (know / "orphan_idx.json").exists(), "执行模式删孤儿索引为既有行为"
        assert (know / f"{CID}.json").exists()

    def test_without_flag_execute_keeps_orphan_sources(
            self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """不带 --prune-orphan-sources 的执行模式不得动孤儿源（源材料不可丢）。"""
        mod = _load_script()
        root, know, src = _make_tmp_root(tmp_path)

        mod.main(["--root", str(root)])

        assert (src / "orphan_src.json").exists(), "未显式授权不得删孤儿源"
        assert not (know / "orphan_idx.json").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])

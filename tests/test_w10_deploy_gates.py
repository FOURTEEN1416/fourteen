"""W10 · 干净部署与发布判据断链根治 — deploy 脚本失败门禁契约。

修复的缺陷（对照 W10 工作单 D）：
- setup.sh 先建 data/cache/logs 再判空 → fresh host 永远跳过 git clone；
  无差别 chmod 640 会去掉 venv 脚本执行位；
- start.sh 导入不存在的 AsyncSessionLocal、except 后 exit(0)、探测 /health
  （实际是 /api/health //api/ready）、健康失败只 warning 仍成功退出；
- deploy.sh 无 alembic 时 init_db 错误被吞（|| true）、restart 后无 ready
  验证仍打印 Completed Successfully、残留已删除脚本的过时提示；
- seed.py 空卡目录 return [] 后解包二元组崩溃 / print_report 空结果返回
  None 解包崩溃 / 缺卡不阻断发布。

shell 胶水层用静态契约（语法 + 结构序）钉住；可行为化的核心（seed）用
子进程真跑。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
DEPLOY = _PROJECT_ROOT / "deploy"


def _read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


def _line_index(src: str, needle: str) -> int:
    for i, line in enumerate(src.splitlines()):
        if needle in line:
            return i
    raise AssertionError(f"未找到: {needle}")


# ────────────────────────── setup.sh ──────────────────────────


class TestSetupScript:
    def test_clone_happens_before_data_dirs(self):
        """fresh host 必须先 clone 再建 data 子目录，否则判空永远跳过 clone。"""
        src = _read("setup.sh")
        clone_idx = _line_index(src, "git clone")
        mkdir_idx = _line_index(src, 'mkdir -p "${APP_DIR}/data"')
        assert clone_idx < mkdir_idx, (
            "mkdir data 在 git clone 之前 → APP_DIR 非空 → fresh host 永远跳过 clone"
        )

    def test_blanket_chmod_excludes_venv(self):
        """全树 chmod 640 不得作用于 .venv（会去掉 venv 脚本执行位）。"""
        src = _read("setup.sh")
        assert ".venv" in src, "chmod 必须显式排除 .venv"
        chmod_idx = _line_index(src, "chmod 640")
        prune_line = src.splitlines()[chmod_idx - 2 : chmod_idx + 2]
        assert any(".venv" in ln for ln in prune_line), (
            f"chmod 640 附近必须可见 .venv 排除逻辑: {prune_line}"
        )

    def test_pip_install_failure_aborts(self):
        """依赖安装失败必须中止（set -e 下不得有 || true 式吞错）。"""
        src = _read("setup.sh")
        assert 'pip install -e "${APP_DIR}"' in src
        for line in src.splitlines():
            if "pip install -e" in line:
                assert "||" not in line, f"pip install 不得吞错: {line}"


# ────────────────────────── start.sh ──────────────────────────


class TestStartScript:
    def test_db_check_uses_real_engine_not_ghost_import(self):
        """不得导入不存在的 AsyncSessionLocal（真名 _engine/_async_session）。"""
        src = _read("start.sh")
        assert "AsyncSessionLocal" not in src, "导入不存在的符号 = DB 检查永远走异常分支"
        assert "_engine" in src, "必须用真实引擎做连接检查"

    def test_db_check_failure_exits_nonzero(self):
        src = _read("start.sh")
        db_block = src[src.index("[1/5]") : src.index("[2/5]")]
        assert "exit(0)" not in db_block, "DB 检查失败 exit(0) = 吞掉断链"
        assert "except Exception" not in db_block or "exit(1)" in db_block or "sys.exit(1)" in db_block

    def test_probes_ready_endpoint(self):
        """启动探针必须打 /api/ready（/health 根本不存在）。"""
        src = _read("start.sh")
        assert "/api/ready" in src
        assert '"/health"' not in src and ":8000/health" not in src

    def test_health_failure_exits_nonzero(self):
        src = _read("start.sh")
        tail = src[src.index("HEALTHY=false"):] if "HEALTHY=false" in src else src
        assert "exit 1" in tail, "健康检查失败必须 exit 1，不得只 warning"

    def test_syntax_ok(self):
        proc = subprocess.run(["bash", "-n", str(DEPLOY / "start.sh")], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


# ────────────────────────── deploy.sh ──────────────────────────


class TestDeployScript:
    def test_migration_failure_not_swallowed(self):
        """init_db 失败不得 `|| true` 吞掉——迁移失败必须阻断发布。"""
        src = _read("deploy.sh")
        for line in src.splitlines():
            if "init_db" in line:
                assert "|| true" not in line, f"init_db 吞错: {line}"
                assert "2>/dev/null" not in line, f"init_db 静默: {line}"

    def test_ready_gate_after_restart(self):
        """restart 后必须验证 /api/ready 才能宣布发布成功；失败退出非 0。"""
        src = _read("deploy.sh")
        restart_idx = _line_index(src, "systemctl restart")
        # ready 门取 restart 之后的首次出现（头部注释里的提及不算）
        ready_idx = src.index("/api/ready", restart_idx)
        success_idx = src.index("Completed Successfully", restart_idx)
        assert restart_idx < ready_idx < success_idx, "ready 门必须在 restart 后、成功宣言前"

    def test_no_stale_script_hint(self):
        src = _read("deploy.sh")
        assert "deploy_ai_girlfriend.ps1" not in src, "引用 2026-08-28 已删除的脚本"

    def test_syntax_ok(self):
        proc = subprocess.run(["bash", "-n", str(DEPLOY / "deploy.sh")], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


# ────────────────────────── seed.py（行为级） ──────────────────────────


class TestSeedScript:
    def test_build_returns_tuple_even_when_empty(self, tmp_path):
        """空卡目录必须返回 (结果, 耗时) 二元组——旧行为 return [] 会让
        调用方解包崩溃。"""
        import importlib.util

        (tmp_path / "config" / "characters").mkdir(parents=True)
        (tmp_path / "data").mkdir()
        spec = importlib.util.spec_from_file_location("w10_seed", DEPLOY / "seed.py")
        assert spec is not None and spec.loader is not None
        seed = importlib.util.module_from_spec(spec)
        import os

        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            spec.loader.exec_module(seed)
            result = seed.build_knowledge_indexes(rebuild=True)
        finally:
            os.chdir(old_cwd)
        assert isinstance(result, tuple), f"空卡目录返回 {type(result).__name__}，应为 tuple"
        results, elapsed = result
        assert results == [] and elapsed == 0.0

    def test_print_report_handles_empty(self, tmp_path):
        import importlib.util
        import os

        spec = importlib.util.spec_from_file_location("w10_seed2", DEPLOY / "seed.py")
        seed = importlib.util.module_from_spec(spec)
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            spec.loader.exec_module(seed)
            out = seed.print_report([], None)
        finally:
            os.chdir(old_cwd)
        assert out == ([], []), "空结果必须返回 (low, errors) 而非 None"

    def test_missing_cards_blocks_with_nonzero_exit(self, tmp_path):
        """缺卡必须阻断发布：seed.py 空卡目录退出非 0 并给出明确指引。"""
        (tmp_path / "config" / "characters").mkdir(parents=True)
        proc = subprocess.run(
            [sys.executable, str(DEPLOY / "seed.py"), "--audit-only"],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=300,
            env={"SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
                 "PATH": __import__("os").environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"},
        )
        assert proc.returncode != 0, f"缺卡应退出非 0，输出：{proc.stdout[-800:]}"
        assert "角色卡" in (proc.stdout + proc.stderr)

    def test_cards_present_audit_only_passes(self, tmp_path):
        (tmp_path / "config" / "characters").mkdir(parents=True)
        (tmp_path / "config" / "characters" / "card_a.json").write_text(
            json.dumps({"id": "card_a", "name": "甲"}), encoding="utf-8")
        os_environ = __import__("os").environ
        proc = subprocess.run(
            [sys.executable, str(DEPLOY / "seed.py"), "--audit-only"],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=300,
            env={"SYSTEMROOT": os_environ.get("SYSTEMROOT", ""),
                 "PATH": os_environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"},
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


# ────────────────────────── 备份入口与文档口径 ──────────────────────────


class TestBackupEntryDocs:
    def test_crontab_mentions_exit_semantics_and_restore(self):
        src = (DEPLOY / "crontab.example").read_text(encoding="utf-8")
        assert "partial" in src and "restore.sh" in src

    def test_restore_sh_exists_and_delegates(self):
        src = _read("restore.sh")
        assert "restore_manager.py" in src

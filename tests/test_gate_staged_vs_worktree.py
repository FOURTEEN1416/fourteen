"""并发 index 防护（scripts/gate_staged_vs_worktree.py + install_native_gate.py +
gate_native_hook_installed.py）的回归用例。

事故原型（2026-09-22 W1，LOG 在案）：四窗共享主检出 `.git/index`，本窗 `git add`
后、`git commit` 前，他窗并发 add 把落库内容换向（UPDATE 参数 SET/WHERE 对调），
而磁盘工作树仍是已验证的正确版——提交时**没有任何机制**发现 index≠工作树。
防护分两层：原生钩子链真拦截（工作树未被 autostash 拉平的时点）+ 框架自检防失踪。

全部用例在 tmp 独立 git 仓库跑，不触碰真实检出与真实 index/.git/hooks。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
GATE = str(SCRIPTS / "gate_staged_vs_worktree.py")
INSTALLER = str(SCRIPTS / "install_native_gate.py")
CHECKER = str(SCRIPTS / "gate_native_hook_installed.py")

sys.path.insert(0, str(SCRIPTS))
from install_native_gate import MARKER_BEGIN  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
    )
    assert r.returncode == 0, f"git {' '.join(args)} 失败: {r.stderr}"
    return r.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-b", "main", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _run_gate(repo: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, GATE], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", env=env,
    )


def test_clean_stage_passes(tmp_path):
    repo = _repo(tmp_path)
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    assert _run_gate(repo).returncode == 0


def test_diverged_index_blocked(tmp_path):
    """事故形态复现：add 之后工作树与 index 分叉（他窗污染 index / add 后又改文件）。"""
    repo = _repo(tmp_path)
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")
    # 工作树改成 B 之前，index 先被换成"坏内容"（模拟他窗 add 换向后的落库版）
    (repo / "f.txt").write_text("BAD\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    # 本窗把工作树恢复为正确版——此刻 index(BAD) ≠ 工作树(GOOD)，门禁必须拒
    (repo / "f.txt").write_text("GOOD\n", encoding="utf-8")
    r = _run_gate(repo)
    assert r.returncode == 1, f"index 与工作树分叉时门禁必须拒绝提交，实得 rc={r.returncode}"
    assert "f.txt" in r.stderr, "须点名分叉文件"


def test_readd_clears_the_gate(tmp_path):
    """门禁给出的正解可走通：重新 add（index 回到工作树内容）后放行。"""
    repo = _repo(tmp_path)
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "f.txt").write_text("B\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    (repo / "f.txt").write_text("C\n", encoding="utf-8")  # add 后又改 → 分叉
    assert _run_gate(repo).returncode == 1
    _git(repo, "add", "f.txt")  # 门禁提示的处置：重新 add
    assert _run_gate(repo).returncode == 0


def test_untracked_and_untouched_files_do_not_trigger(tmp_path):
    """未跟踪文件、以及"只 add 了 A、B 脏但不 staged"——都不该拦（正常部分提交）。"""
    repo = _repo(tmp_path)
    (repo / "a.txt").write_text("A\n", encoding="utf-8")
    (repo / "b.txt").write_text("B\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "b.txt").write_text("B2\n", encoding="utf-8")  # 脏但未 staged
    (repo / "new.txt").write_text("N\n", encoding="utf-8")  # 未跟踪
    (repo / "a.txt").write_text("A2\n", encoding="utf-8")
    _git(repo, "add", "a.txt")  # staged==工作树
    assert _run_gate(repo).returncode == 0


def test_hunk_partial_escape_hatch(tmp_path):
    """故意的部分 hunk 提交：AI_GF_ALLOW_DIRTY_STAGE=1 放行但仍打印警告。"""
    repo = _repo(tmp_path)
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "f.txt").write_text("B\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    (repo / "f.txt").write_text("C\n", encoding="utf-8")
    r = _run_gate(repo, {"AI_GF_ALLOW_DIRTY_STAGE": "1"})
    assert r.returncode == 0
    assert "WARN" in r.stderr and "f.txt" in r.stderr, "放行必须留下可见警告"


# ── 原生钩子安装器 / 完整性自检 / 真·端到端 ─────────────────────────


def _install_gate(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, INSTALLER, "--repo", str(repo), "--gate-script", GATE],
        capture_output=True, text=True, encoding="utf-8",
    )


def _hook_text(repo: Path) -> str:
    return (repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")


def test_installer_creates_and_is_idempotent(tmp_path):
    repo = _repo(tmp_path)
    # 模拟 pre-commit 框架生成的模板钩子（含 templated 头与 exec 行）
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(
        "#!/bin/sh\n#!/usr/bin/env bash\n# start templated\n"
        "INSTALL_PYTHON='python'\nARGS=(hook-impl)\n# end templated\n"
        'exec "$INSTALL_PYTHON" -mpre_commit "${ARGS[@]}"\n',
        encoding="utf-8", newline="\n",
    )
    assert _install_gate(repo).returncode == 0
    once = _hook_text(repo)
    assert once.count(MARKER_BEGIN) == 1
    # 门禁块必须在 exec 之前（否则永远跑不到）
    assert once.index(MARKER_BEGIN) < once.index("exec ")
    assert _install_gate(repo).returncode == 0
    assert _hook_text(repo).count(MARKER_BEGIN) == 1, "重复安装不得叠加"


def test_installer_creates_hook_when_absent(tmp_path):
    repo = _repo(tmp_path)
    assert _install_gate(repo).returncode == 0
    assert MARKER_BEGIN in _hook_text(repo)


def test_checker_detects_overwrite_by_precommit_install(tmp_path):
    """`pre-commit install` 覆写原生钩子 → 自检必须报警（防静默失踪）。"""
    repo = _repo(tmp_path)
    _install_gate(repo)
    r = subprocess.run(
        [sys.executable, CHECKER], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
    )
    assert r.returncode == 0, r.stderr
    # 模拟覆写：无标记的框架模板
    (repo / ".git" / "hooks" / "pre-commit").write_text(
        "#!/bin/sh\nexec python -m pre_commit hook-impl\n", encoding="utf-8", newline="\n"
    )
    r = subprocess.run(
        [sys.executable, CHECKER], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
    )
    assert r.returncode == 1
    assert "install_native_gate.py" in r.stderr, "报警必须给出可执行的重装命令"


def test_e2e_real_git_commit_blocked_then_passes(tmp_path):
    """端到端（真 `git commit`）：分叉即拒、重新 add 后放行、逃生键可越。"""
    repo = _repo(tmp_path)
    assert _install_gate(repo).returncode == 0
    (repo / "f.txt").write_text("A\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "base")

    (repo / "f.txt").write_text("BAD\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    (repo / "f.txt").write_text("GOOD\n", encoding="utf-8")  # index≠工作树
    r = subprocess.run(
        ["git", "commit", "-m", "should-be-blocked"],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode != 0, f"门禁必须让提交失败，实得 rc={r.returncode}"
    assert "拒绝提交" in (r.stderr + r.stdout) and "f.txt" in (r.stderr + r.stdout)
    # 未产生提交
    assert len(_git(repo, "log", "--oneline").splitlines()) == 1

    # 正解走通：重新 add → 提交成功
    _git(repo, "add", "f.txt")
    r = subprocess.run(
        ["git", "commit", "-m", "ok"],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
    )
    assert r.returncode == 0, r.stderr + r.stdout

    # 逃生键：故意的分叉提交在显式授权下放行（index≠HEAD 才有东西可提交，
    # 工作树再分叉出去——模拟"只暂存部分改动"的正常手法）
    (repo / "f.txt").write_text("STAGED\n", encoding="utf-8")
    _git(repo, "add", "f.txt")
    (repo / "f.txt").write_text("HUNKED\n", encoding="utf-8")
    env = {**os.environ, "AI_GF_ALLOW_DIRTY_STAGE": "1"}
    r = subprocess.run(
        ["git", "commit", "-m", "partial"],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    assert "WARN" in (r.stderr + r.stdout), "放行必须留可见警告"

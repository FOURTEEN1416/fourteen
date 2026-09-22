"""把 staged-vs-worktree 门禁**幂等**接入原生 `.git/hooks/pre-commit` 链。

背景（2026-09-22 W1 并发事故）：四窗共享主检出的同一个 `.git/index`，
`git add` 与 `git commit` 之间他窗可改写 index，落库内容与被验证的工作树脱钩。
唯一的机制拦截点是**原生 pre-commit 钩子**（git 在提交前直接执行、工作树未经
pre-commit 框架 autostash 拉平）。本脚本在既有钩子（pre-commit 框架生成的模板）
的 templated 头之后插入带标记的门禁块：先跑门禁、`exit 1` 即拒提交，再交棒框架钩子。

- 幂等：重复执行只替换标记块，不叠加；
- 兼容：`pre-commit install` 会整体覆盖本文件——覆盖后由框架内的
  `native-gate-installed` 自检钩子拒绝提交并给出重跑本脚本的指引（不会静默失踪）；
- `core.hooksPath` 一经设置即尊重（经 `git rev-parse --git-path hooks` 解析）。

用法（项目根）：`python scripts/install_native_gate.py`
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
from pathlib import Path

MARKER_BEGIN = "# BEGIN gate-staged-vs-worktree (scripts/install_native_gate.py)"
MARKER_END = "# END gate-staged-vs-worktree"

_TEMPLATE_HEAD = """#!/bin/sh
# 本文件由 scripts/install_native_gate.py 维护（门禁块）；框架钩子本体在下方原始文件。
"""


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8"
    )
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败: {r.stderr.strip()}")
    return r.stdout.strip()


def hooks_dir(repo: Path) -> Path:
    raw = _git(repo, "rev-parse", "--git-path", "hooks")
    p = Path(raw)
    return p if p.is_absolute() else repo / p


def _gate_block(gate_script: Path, python_exe: str) -> str:
    exe = python_exe.replace("\\", "/")
    script = gate_script.as_posix()
    return (
        f"{MARKER_BEGIN}\n"
        "# staged 与工作树分叉即拒提交（多窗共享 index 并发防护，W1 事故驱动）。\n"
        "# 故意的部分 hunk 提交：AI_GF_ALLOW_DIRTY_STAGE=1 git commit ...（见脚本文档）。\n"
        f'"{exe}" "{script}" || exit 1\n'
        f"{MARKER_END}\n"
    )


def _extract_install_python(lines: list[str]) -> str:
    for line in lines:
        if line.startswith("INSTALL_PYTHON="):
            return line.split("=", 1)[1].strip().strip("'\"")
    return "python"


def install(repo: Path, gate_script: Path) -> str:
    hook = hooks_dir(repo) / "pre-commit"
    if hook.exists():
        original = hook.read_text(encoding="utf-8", errors="replace")
    else:
        original = _TEMPLATE_HEAD + "exit 0  # (pre-commit 框架钩子未安装)\n"

    # 剥掉既有标记块（幂等替换而非叠加）
    out: list[str] = []
    in_block = False
    for line in original.splitlines():
        if line.strip() == MARKER_BEGIN:
            in_block = True
            continue
        if in_block:
            if line.strip() == MARKER_END:
                in_block = False
            continue
        out.append(line)

    block = _gate_block(gate_script, _extract_install_python(out))

    # 插入点：`# end templated` 之后（框架 exec 之前）；无模板头则跟 shebang 之后；再没有则置首
    anchor = next(
        (i for i, line in enumerate(out) if line.strip() == "# end templated"),
        None,
    )
    if anchor is None:
        shebangs = [i for i, line in enumerate(out) if line.startswith("#!")]
        anchor = max(shebangs) if shebangs else -1
    head = "\n".join(out[: anchor + 1])
    tail = "\n".join(out[anchor + 1 :])
    merged = (head + "\n" + block + tail) if head else (block + tail)

    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(merged if merged.endswith("\n") else merged + "\n", encoding="utf-8", newline="\n")
    with contextlib.suppress(OSError):  # Windows 无 exec 位语义
        hook.chmod(hook.stat().st_mode | 0o755)
    return str(hook)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=".", help="仓库根（默认当前目录）")
    ap.add_argument(
        "--gate-script",
        default=None,
        help="门禁脚本路径（默认 <repo>/scripts/gate_staged_vs_worktree.py）",
    )
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    gate = Path(args.gate_script) if args.gate_script else repo / "scripts" / "gate_staged_vs_worktree.py"
    if not gate.exists():
        raise SystemExit(f"门禁脚本不存在: {gate}")
    hook = install(repo, gate.resolve())
    print(f"[gate] 已接入原生钩子: {hook}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

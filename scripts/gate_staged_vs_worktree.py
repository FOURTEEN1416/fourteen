"""并发 index 门禁：待提交的 staged 内容必须与工作树内容逐文件一致。

由**原生** `.git/hooks/pre-commit` 链调用（安装：`scripts/install_native_gate.py`）。
不接进 pre-commit 框架钩子——框架 autostash 在钩子运行前把未暂存改动收起、
工作树被 `git checkout -- .` 拉平到 index，此处要抓的分叉必然失明（4.6.0 实测，
autostash 全局构建且无 skip_build_git_stash 逃生键）。原生钩子在 git 提交前直接
执行、看到的是**真实**工作树。

根因（2026-09-22 W1 并发事故，LOG 在案）：多窗共享主检出的**同一个 `.git/index`**，
`git add` 与 `git commit` 之间他窗的 add 可任意改写 index——本次实锤为落库版
UPDATE 参数被换向（SET=POINTS/WHERE=SHISI），与磁盘上已验证正确的工作树内容脱钩。
本门禁把「事后 git show 核对」变成「提交时机制必拦」：

- staged 集 = `git diff --cached --name-only`（index vs HEAD，本次要提交的文件）
- 脏集     = `git diff --name-only`（工作树 vs index，add 之后又被改/被换的内容）
- 交集非空 → 拒绝提交，逐个列名。

确属**故意的部分 hunk 提交**（如主控「移除他窗行→暂存→还原」规程）时，
用 `AI_GF_ALLOW_DIRTY_STAGE=1 git commit ...` 显式放行（打印警告不阻断）。
仅依赖标准库 + git CLI，`language: system` 安全。
"""

from __future__ import annotations

import os
import subprocess
import sys


def _name_only(*args: str) -> list[str]:
    r = subprocess.run(
        ["git", *args], capture_output=True, text=True, encoding="utf-8"
    )
    if r.returncode != 0:
        # git 层失败必须可见——静默返回空集等于门禁自杀（谎言家族教训）
        print(f"[gate] `{' '.join(args)}` 失败: {r.stderr.strip()}", file=sys.stderr)
        raise SystemExit(1)
    return [line for line in r.stdout.splitlines() if line.strip()]


def main() -> int:
    staged = set(_name_only("diff", "--cached", "--name-only"))
    dirty = set(_name_only("diff", "--name-only"))
    overlap = sorted(staged & dirty)
    if not overlap:
        return 0
    if os.environ.get("AI_GF_ALLOW_DIRTY_STAGE") == "1":
        print(
            "[gate][WARN] AI_GF_ALLOW_DIRTY_STAGE=1 放行——staged 与工作树不一致仍被提交：",
            file=sys.stderr,
        )
        for p in overlap:
            print(f"  - {p}", file=sys.stderr)
        return 0
    print(
        "[gate] 拒绝提交：以下文件在 `git add` 之后 index 与工作树出现分叉\n"
        "       （多窗共享 index 被并发改写 / 暂存后又编辑了文件）：",
        file=sys.stderr,
    )
    for p in overlap:
        print(f"  - {p}", file=sys.stderr)
    print(
        "[gate] 处置：确认工作树是对的 → 重新 `git add <上列文件>` 后再 commit；\n"
        "       确属故意的部分 hunk 提交 → `AI_GF_ALLOW_DIRTY_STAGE=1 git commit ...`。\n"
        "       提交后仍建议 `git show HEAD -- <关键文件>` 举证落库内容。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

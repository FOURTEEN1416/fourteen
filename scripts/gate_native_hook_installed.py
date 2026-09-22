"""框架自检钩子：原生 pre-commit 链里的门禁标记必须**仍然在位**。

真拦截由原生钩子承担（`scripts/install_native_gate.py` 注入的标记块——pre-commit
框架 autostash 会拉平工作树，分叉在框架内不可见，故只能原生层拦）。
本钩子防的是**防护静默失踪**：任何窗口重跑 `pre-commit install` 都会整体覆写
`.git/hooks/pre-commit`、门禁块随之蒸发——若无自检，之后就只剩"以为有防护"。
失踪即拒提交，并给出一条可执行的重装命令（不让人猜）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from install_native_gate import MARKER_BEGIN, hooks_dir  # noqa: E402


def main() -> int:
    repo = Path.cwd()
    hook = hooks_dir(repo) / "pre-commit"
    if not hook.exists():
        print(
            "[gate] 拒绝提交：原生 pre-commit 钩子不存在——并发 index 门禁无从执行。\n"
            "       修复：python scripts/install_native_gate.py",
            file=sys.stderr,
        )
        return 1
    text = hook.read_text(encoding="utf-8", errors="replace")
    if MARKER_BEGIN not in text:
        print(
            "[gate] 拒绝提交：原生钩子中门禁标记缺失（多半是 `pre-commit install` 覆写）。\n"
            "       staged-vs-worktree 分叉此刻无人拦截——修复：\n"
            "       python scripts/install_native_gate.py",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

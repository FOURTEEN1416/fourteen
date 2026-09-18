#!/usr/bin/env python3
"""CI 门禁本地复现 — 单一真源。

设计目标：消除「CI 一份 bash grep、本地一份复刻」的双实现漂移。
`.github/workflows/ci.yml` 的 ff-* job 与本地 pre-commit local hook
**调用同一份脚本**，因此本地拦截的口径与 CI 逐字一致。

覆盖门禁（编号沿用 CI job 命名）：
  FF-0003  前端 pages/ 与 hooks/ 禁止 import 旧 API 文件
  FF-0006  frontend/src/api/client.ts 只做实例装配 + re-export（禁止函数定义）
  FF-0007  Zustand store 禁止直接 import API
  ADR-00xx docs/adr/ 存在性 + Supersedes 引用完整性

用法：
  python scripts/ci_gates.py

退出码：0 = 全部通过；1 = 存在违规。
仅依赖标准库，任意 Python 3.10+ 均可运行（无需项目依赖）。
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

# 门禁函数签名：() -> (是否通过, 输出行列表)
GateFunc = Callable[[], "tuple[bool, list[str]]"]

# ── FF-0003：页面/钩子禁止 import 的旧 API 文件 ──
FF0003_PATTERN = "|".join(
    [
        r"from.*'\.\./api/characterApi",
        r"from.*'\.\./api/memoryApi",
        r"from.*'\.\./api/personaCardApi",
        r"from.*'\.\./api/voiceApi",
        r"from.*'\.\./api/oldClient",
        r"from.*shisiClient",
        r"from.*trainingApi",
    ]
)

# ── FF-0006：client.ts 禁止函数定义（只允许 re-export）──
FF0006_PATTERN = r"export (async )?function|export const.*=.*\(.*\)"

# ── FF-0007：store 禁止直接 import API ──
FF0007_PATTERN = r"from.*\.\./api/"

# ── ADR：Supersedes 引用提取（等价于 grep -oP 'Supersedes ADR-\K\d+'）──
ADR_SUPERSEDES_PATTERN = r"Supersedes ADR-(\d+)"


def rel(path: Path) -> str:
    """转为仓库相对 POSIX 路径，保证跨平台输出一致。"""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def scan_files(files: list[Path], pattern: str) -> list[str]:
    """逐行匹配，返回 '相对路径:行号:内容' 形式的命中列表。"""
    rx = re.compile(pattern)
    hits: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if rx.search(line):
                hits.append(f"{rel(path)}:{lineno}:{line.strip()}")
    return hits


def glob_all(base: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """递归收集 base 下指定后缀的文件（base 不存在时返回空列表）。"""
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file() and p.suffix in suffixes)


def gate_ff0003() -> tuple[bool, list[str]]:
    files = glob_all(FRONTEND_SRC / "pages", (".ts", ".tsx")) + glob_all(
        FRONTEND_SRC / "hooks", (".ts", ".tsx")
    )
    hits = scan_files(files, FF0003_PATTERN)
    detail = ["❌ FF-0003 违规：页面/钩子直接 import 了旧 API 文件", *hits]
    return (not hits), (detail if hits else ["✅ FF-0003: 无旧 API import"])


def gate_ff0006() -> tuple[bool, list[str]]:
    target = FRONTEND_SRC / "api" / "client.ts"
    hits = scan_files([target] if target.is_file() else [], FF0006_PATTERN)
    detail = [
        "❌ FF-0006 违规：client.ts 包含函数定义，应移到领域文件中",
        *hits,
    ]
    return (not hits), (detail if hits else ["✅ FF-0006: client.ts 纯 re-export"])


def gate_ff0007() -> tuple[bool, list[str]]:
    store_dir = FRONTEND_SRC / "store"
    files = sorted(p for p in store_dir.glob("*.ts")) if store_dir.is_dir() else []
    hits = scan_files(files, FF0007_PATTERN)
    detail = ["❌ FF-0007 违规：Zustand store 直接 import API", *hits]
    return (not hits), (detail if hits else ["✅ FF-0007: Store 无 API 直接调用"])


def gate_adr() -> tuple[bool, list[str]]:
    adr_dir = REPO_ROOT / "docs" / "adr"
    if not adr_dir.is_dir() or not any(adr_dir.iterdir()):
        return False, ["❌ docs/adr/ 目录为空或不存在"]

    lines = ["✅ docs/adr/ 存在且有 ADR 文件"]
    rx = re.compile(ADR_SUPERSEDES_PATTERN)
    for adr_file in sorted(adr_dir.glob("*.md")):
        try:
            text = adr_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ref in set(rx.findall(text)):
            # 原 CI 脚本写作 [ ! -f "ADR-$n-*.md" ]——`-f` 不展开通配符，
            # 该断言恒为真、检查形同虚设。此处改用 glob 真实匹配。
            if not list(adr_dir.glob(f"ADR-{ref}-*.md")):
                lines.append(
                    f"⚠️  {rel(adr_file)} 引用了 ADR-{ref}，但找不到完全匹配的文件名"
                )
    lines.append("✅ ADR 引用完整性检查完成")
    return True, lines


GATES: list[tuple[str, str, GateFunc]] = [
    ("FF-0003", "页面/钩子禁止 import 旧 API", gate_ff0003),
    ("FF-0006", "client.ts 只做 re-export", gate_ff0006),
    ("FF-0007", "store 禁止直接 import API", gate_ff0007),
    ("ADR", "ADR 目录与引用完整性", gate_adr),
]


def main(argv: list[str]) -> int:
    """argv[0] 为脚本路径，其后为可选门禁 ID；不传则跑全部门禁。"""
    wanted = {arg.upper() for arg in argv[1:]}
    known = {gate_id for gate_id, _, _ in GATES}
    unknown = wanted - known
    if unknown:
        print(f"❌ 未知门禁 ID：{sorted(unknown)}；可选：{sorted(known)}")
        return 2

    selected = [gate for gate in GATES if not wanted or gate[0] in wanted]
    print(f"════ CI 门禁检查（{rel(Path(__file__))}）════")
    failed: list[str] = []
    for index, (gate_id, title, func) in enumerate(selected, start=1):
        passed, lines = func()
        print(f"\n[{index}/{len(selected)}] {gate_id} {title}")
        for line in lines:
            print(f"  {line}")
        if not passed:
            failed.append(f"{gate_id} {title}")

    print("\n════ 汇总 ════")
    if failed:
        print(f"❌ {len(failed)}/{len(selected)} 门禁未通过：")
        for title in failed:
            print(f"   - {title}")
        return 1
    print(f"✅ {len(selected)}/{len(selected)} 门禁全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

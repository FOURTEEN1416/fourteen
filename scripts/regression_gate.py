#!/usr/bin/env python3
"""回归门禁：四分块 pytest（串行）→ vitest → tsc → ruff。

用法:
    python scripts/regression_gate.py

退出码:
    0  全部子项通过
    1  任一子项非 0（含超时 124、启动失败 127）

纯标准库实现，无第三方依赖。
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
TAIL_LINES = 3
# 2026-10-31 门禁首轮失败根因：400s 对当前 276 测试文件的四分块不够
# （块1 335s / 块3 351s / 块4 307s，负载高时贴边超时）。600s 保留约 70% 余量，
# 四块全跑仍可控制在 workflow 25 分钟总预算内（实测总和约 20 分钟）。
PYTEST_TIMEOUT = 600
# 门禁总预算（秒）：workflow 的 world.run 上限是 25 分钟（1_500_000ms）。
# 留 2 分钟收尾/输出余量，超预算的项直接判失败并在 stderr 留下原因，
# 避免整个 gate 被外层静默杀掉、修复员拿不到任何证据。
GATE_BUDGET_SECONDS = 1380
VITEST_TIMEOUT = 180
TSC_TIMEOUT = 60
RUFF_TIMEOUT = 30
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Result:
    """一次子项执行的结果。"""

    def __init__(self, name: str, cmd: list[str], cwd: str, timeout: int) -> None:
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.timeout = timeout
        self.code = 0
        self.output = ""
        self.elapsed = 0.0

    @property
    def status(self) -> str:
        if self.code == 124:
            return "TIMEOUT"
        return "PASS" if self.code == 0 else "FAIL"

    def lines(self) -> list[str]:
        return [ln.rstrip() for ln in self.output.splitlines() if ln.strip()]

    def summary_line(self) -> str:
        lines = self.lines()
        return lines[-1] if lines else "(无输出)"

    def tail(self, n: int = TAIL_LINES) -> list[str]:
        lines = self.lines()
        return lines[-n:] if lines else []


class Item:
    """一个待执行的子项（命令 + 工作目录 + 超时）。"""

    def __init__(self, name: str, cmd: list[str], cwd: str, timeout: int) -> None:
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.timeout = timeout

    def run(self) -> Result:
        result = Result(self.name, self.cmd, self.cwd, self.timeout)
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                    # 子进程输出不缓冲：超时被杀时仍能拿到已跑到哪一步的证据
                    "PYTHONUNBUFFERED": "1",
                },
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            result.code = 127
            result.elapsed = time.monotonic() - started
            result.output = f"[无法启动] {self.cmd[0]}: {exc}"
            return result
        try:
            out, _ = proc.communicate(timeout=self.timeout)
            result.code = proc.returncode
            result.output = _decode(out or b"")
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            try:
                out, _ = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                out = b""
            result.code = 124
            result.output = _decode(out or b"") + (
                f"\n[超时] {self.timeout}s 内未结束，已终止进程树"
            )
        result.elapsed = time.monotonic() - started
        return result


def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Windows 下杀整棵进程树（cmd/npm 会派生 node），其它平台杀进程。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        return
    proc.kill()


def collect_test_files() -> list[str]:
    """递归收集 tests/ 下的 test_*.py，按相对路径字母序。"""
    tests_dir = os.path.join(ROOT, "tests")
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if fnmatch.fnmatch(name, "test_*.py"):
                rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
                found.append(rel.replace("\\", "/"))
    return sorted(found)


def split_blocks(files: list[str], n: int = 4) -> list[list[str]]:
    """按字母序切成 n 个连续的块（块大小差不超过 1）。"""
    return [files[i * len(files) // n : (i + 1) * len(files) // n] for i in range(n)]


def find_tool(*names: str) -> str | None:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    return None


def _fmt_cmd(cmd: list[str]) -> str:
    shown = subprocess.list2cmdline(cmd)
    return shown if len(shown) <= 240 else shown[:237] + "..."


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/")


def _frontend_items() -> tuple[list[Item], list[Result]]:
    """构造 vitest / tsc 子项；缺 npm 或缺依赖时提前生成失败结果。"""
    items: list[Item] = []
    failed: list[Result] = []
    npm = find_tool("npm.cmd", "npm")
    npx = find_tool("npx.cmd", "npx")

    if npm is None:
        failed.append(_failed_result("vitest", ["npm", "test", "--", "--run"], FRONTEND, 127,
                                     "[缺少工具] 未找到 npm（where npm 为空）"))
    else:
        items.append(Item("vitest", [npm, "test", "--", "--run"], FRONTEND, VITEST_TIMEOUT))

    if npx is None:
        failed.append(_failed_result("tsc", ["npx", "tsc", "--noEmit"], FRONTEND, 127,
                                     "[缺少工具] 未找到 npx（where npx 为空）"))
    elif not os.path.isdir(os.path.join(FRONTEND, "node_modules")):
        failed.append(_failed_result("tsc", [npx, "tsc", "--noEmit"], FRONTEND, 127,
                                     "[缺少依赖] frontend/node_modules 不存在，请先 npm install"))
    else:
        items.append(Item("tsc", [npx, "tsc", "--noEmit"], FRONTEND, TSC_TIMEOUT))
    return items, failed


def _failed_result(name: str, cmd: list[str], cwd: str, code: int, message: str) -> Result:
    result = Result(name, cmd, cwd, 0)
    result.code = code
    result.output = message
    return result


def build_items() -> tuple[list[Item | Result], list[str]]:
    """按执行顺序返回（已失败结果 | 待执行子项）与测试文件清单。"""
    files = collect_test_files()
    items: list[Item | Result] = []
    for index, block in enumerate(split_blocks(files), start=1):
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *block]
        if block:
            items.append(Item(f"pytest-块{index}", cmd, ROOT, PYTEST_TIMEOUT))
        else:
            failed = _failed_result(
                f"pytest-块{index}", cmd, ROOT, 1, "[空块] tests/ 下没有可运行的 test_*.py"
            )
            items.append(failed)

    frontend_items, frontend_failures = _frontend_items()
    items.extend(frontend_items)
    items.extend(frontend_failures)

    ruff_cmd = [sys.executable, "-m", "ruff", "check", "."]
    items.append(Item("ruff", ruff_cmd, ROOT, RUFF_TIMEOUT))
    return items, files


def report(result: Result) -> None:
    print("\n" + "-" * 72)
    print(f"[{result.name}] exit={result.code}  状态={result.status}  耗时={result.elapsed:.1f}s")
    print(f"  $ {_fmt_cmd(result.cmd)}")
    print(f"  cwd={_rel(result.cwd)}")
    print(f"  最后 {TAIL_LINES} 行:")
    for line in result.tail() or ["(无输出)"]:
        print(f"    {line}")
    print(f"  summary: {result.summary_line()}")


def emit_failure_digest(results: list[Result]) -> None:
    """把失败项摘要写到 stderr。

    2026-10-31 门禁首轮失败根因（第二层）：workflow 的修复员只读
    ``gate.stderr[-3000:]``，而本脚本此前把所有报告打到 stdout → 失败输出
    永远是空串，修复员无法定位。stderr 摘要必须自带失败项名称、命令、
    退出码与最后若干行（含 pytest FAILED/超时行），且控制在该窗口内。
    """
    failed = [r for r in results if r.code != 0]
    if not failed:
        return
    print("\n" + "=" * 72, file=sys.stderr)
    print("门禁失败摘要（stdout 的 stderr 副本，供自动化修复员读取）", file=sys.stderr)
    for result in failed:
        print("-" * 72, file=sys.stderr)
        print(
            f"[{result.name}] exit={result.code} 状态={result.status} "
            f"耗时={result.elapsed:.1f}s",
            file=sys.stderr,
        )
        print(f"  $ {_fmt_cmd(result.cmd)}", file=sys.stderr)
        print(f"  cwd={_rel(result.cwd)}", file=sys.stderr)
        print(f"  summary: {result.summary_line()}", file=sys.stderr)
        for line in result.tail(10):
            print(f"    {line}", file=sys.stderr)
    print("-" * 72, file=sys.stderr)
    print(
        f"失败 {len(failed)} 项：" + "、".join(r.name for r in failed),
        file=sys.stderr,
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    items, files = build_items()
    print(f"仓库: {ROOT}")
    print(f"测试文件: {len(files)} 个（tests/ 递归匹配 test_*.py，按字母序分 4 块串行）")

    deadline = time.monotonic() + GATE_BUDGET_SECONDS
    results: list[Result] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, Result):
            results.append(item)
            report(item)
            continue
        remaining = int(deadline - time.monotonic())
        if remaining <= 30:
            result = _failed_result(
                item.name,
                item.cmd,
                item.cwd,
                124,
                f"[预算] 门禁总预算 {GATE_BUDGET_SECONDS}s 耗尽（剩余 {remaining}s），未执行",
            )
            results.append(result)
            report(result)
            continue
        # 单项超时不得超过剩余总预算，保证 gate 自己先于 world.run 退出
        item.timeout = min(item.timeout, remaining)
        print(f"\n>>> [{index}/{len(items)}] 运行 {item.name}（超时 {item.timeout}s）...", flush=True)
        print(
            f"[gate] 开始 {item.name}（超时 {item.timeout}s）",
            file=sys.stderr,
            flush=True,
        )
        result = item.run()
        results.append(result)
        report(result)
        print(
            f"[gate] {result.name} {result.status} exit={result.code} {result.elapsed:.1f}s",
            file=sys.stderr,
            flush=True,
        )

    failed = [r for r in results if r.code != 0]
    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    for result in results:
        print(
            f"  {result.status:<7} exit={result.code:<3} {result.elapsed:7.1f}s  {result.name}"
        )
        print(f"          {result.summary_line()}")
    print("-" * 72)
    print(f"总计 {len(results)} 项：{len(results) - len(failed)} 通过 / {len(failed)} 失败")
    print("门禁结果:", "PASS" if not failed else "FAIL")
    emit_failure_digest(results)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())

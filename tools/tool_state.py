"""工具/插件运行时开关 — 唯一 owner。

背景（W6 缺陷 F/G）：
- 插件开关写 ``plugins/plugins.json`` 后**全仓零消费者**——关了等于没关；
- 工具禁用只改本进程 ``ToolRegistry``，跨 worker 不生效、重启即恢复，
  且列表端点只显示启用项，禁用后刷新即从 UI 消失无法恢复。

本模块把两类开关收进一个 gitignored 的运行时状态文件
``data/runtime_switches.json``（原 tracked 的 ``plugins/plugins.json`` 被
生产覆写会造成服务器工作树 dirty、阻断后续 git pull，一并随迁删除）：

```json
{
  "tools_disabled": ["image_gen"],
  "plugins": {"weather": {"enabled": false, "toggled_at": "..."}}
}
```

读侧带 mtime 缓存（dispatch 热路径零磁盘 IO，跨 worker 改动经 mtime 失效）；
写侧进程内锁 + 跨进程 OS 文件锁 + 原子替换。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from utils.project_paths import project_path

_STATE_PATH = project_path("data", "runtime_switches.json")

_lock = threading.Lock()
_cache: tuple[str, int, int, dict[str, Any]] | None = None  # (path, mtime_ns, size, data)


def _read_unlocked(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}


def _stat_key(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _load() -> dict[str, Any]:
    """带缓存的只读视图；调用方不得修改返回值。"""
    global _cache
    path = Path(_STATE_PATH)
    key = _stat_key(path)
    with _lock:
        if key is None:
            return {}
        if _cache is not None and _cache[0] == str(path) and (_cache[1], _cache[2]) == key:
            return _cache[3]
        data = _read_unlocked(path)
        _cache = (str(path), key[0], key[1], data)
        return data


@contextlib.contextmanager
def _os_file_lock(path: Path):
    """跨进程排他锁（msvcrt/fcntl），与 orchestrator.session_locks 同构。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        deadline = time.monotonic() + 10.0
        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"runtime switch lock timeout: {path}") from None
                time.sleep(0.05)
        yield
    finally:
        if locked:
            with contextlib.suppress(OSError):
                if sys.platform == "win32":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _mutate(mutator) -> None:
    """读-改-写：跨进程锁内基于磁盘最新内容，写后刷新本进程缓存。"""
    global _cache
    path = Path(_STATE_PATH)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with _lock:
        with _os_file_lock(lock_path):
            data = _read_unlocked(path)
            mutator(data)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, path)
            except Exception:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_name)
                raise
        key = _stat_key(path)
        if key is not None:
            _cache = (str(path), key[0], key[1], data)


# ── 工具开关（缺陷 G）─────────────────────────────────────────


def is_tool_disabled(name: str) -> bool:
    return str(name) in _load().get("tools_disabled", [])


def disabled_tools() -> list[str]:
    raw = _load().get("tools_disabled", [])
    return [str(x) for x in raw] if isinstance(raw, list) else []


def set_tool_disabled(name: str, disabled: bool) -> None:
    def _apply(data: dict[str, Any]) -> None:
        names = [str(x) for x in data.get("tools_disabled", []) if x != name]
        if disabled:
            names.append(str(name))
        data["tools_disabled"] = names

    _mutate(_apply)


# ── 插件开关（缺陷 F）─────────────────────────────────────────


def plugin_states() -> dict[str, dict[str, Any]]:
    raw = _load().get("plugins", {})
    return dict(raw) if isinstance(raw, dict) else {}


def is_plugin_enabled(name: str, default: bool = True) -> bool:
    entry = plugin_states().get(name)
    if not isinstance(entry, dict):
        return default
    return bool(entry.get("enabled", default))


def set_plugin_enabled(name: str, enabled: bool) -> None:
    def _apply(data: dict[str, Any]) -> None:
        plugins = data.get("plugins")
        if not isinstance(plugins, dict):
            plugins = {}
        entry = plugins.get(name)
        if not isinstance(entry, dict):
            entry = {}
        entry["enabled"] = bool(enabled)
        entry["toggled_at"] = time.time()
        plugins[name] = entry
        data["plugins"] = plugins

    _mutate(_apply)

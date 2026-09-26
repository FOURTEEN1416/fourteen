#!/usr/bin/env python3
"""应用数据一致备份管理器 — manifest + 在线一致快照 + 退出码语义（W10）。

唯一样板：deploy/backup.sh 与 crontab.example 均委托本模块；shell 层不再自带
第二套备份逻辑。

修复的缺陷（对照 2026-09-27 W10 工作单 A）：
- 旧 deploy/backup.sh 只按 DATABASE_URL 备一个主库，忽略 APP_DATABASE_URL 优先级；
  本模块与 api/runtime_config.get_database_url 平价（tests 有平价契约钉死）。
- 旧脚本缺 sqlite3 CLI 时裸 cp 主文件、WAL/SHM 错名落盘（恢复时无有效 WAL）；
  本模块一律走 Python 标准库备份 API（.backup），在线一致、不依赖 CLI 二进制。
- 旧脚本缺 Chroma 目录视为成功跳过、tar 失败仅 warn 后仍打印 Backup Complete；
  本模块缺项必须失败或明确 partial，退出码：0=complete / 2=partial / 1=failed。
- 覆盖面扩为应用全状态：users 库（含通道元数据）、记忆库（chat_history/事实/
  画像/提醒）、事件账本、向量源、角色卡（gitignore，不入 git）、知识索引、
  运行时状态文件与机密配置，并记录每库表行数与按会话键的业务行归属。

用法:
    python deploy/backup_manager.py --project-root /opt/ai-girlfriend \
        [--output-dir DIR] [--retention-days 30] [--dry-run] [--allow-partial]
        [--require a,b,c] [--test]
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2

MANIFEST_NAME = "manifest.json"
RUN_PREFIX = "backup-"

# DB URL 同步→异步驱动映射（与 api/runtime_config 平价；此处反向只用于取路径）
_SYNC_DRIVER_MAP = {
    "postgresql+asyncpg://": "postgresql://",
    "sqlite+aiosqlite:///": "sqlite:///",
}

# 目录型组件：名字 → 项目根相对路径（存在即备份；vector_store 缺失记 partial）
DIR_COMPONENTS: dict[str, str] = {
    "vector_store": "data/chroma_db",
    "character_cards": "config/characters",
    "knowledge_index": "data/knowledge",
}

# 运行时状态：与 data/ 下真正持久化的调度/主动/好感/微信状态一一对应（非 git）
STATE_FILES = (
    "scheduler_config.json",
    "proactive_state.json",
    "affinity_state.json",
    "wechat_state.json",
    "wechat_context_tokens.json",
)
STATE_DIRS = ("ase_states", "wechat_sessions", "stickers")

# 默认硬性要求：缺任一即整体 failed（角色卡被 gitignore，备份是唯一副本）
DEFAULT_REQUIRE = ("users_db", "memory_db", "character_cards")
# 信息型组件：缺失只记录，不影响整体状态
OPTIONAL_COMPONENTS = frozenset({"runtime_secrets", "knowledge_index"})


# ────────────────────────── DB URL 解析（与 runtime_config 平价） ──────────────────────────


def _to_sync_url(url: str) -> str:
    for async_prefix, sync_prefix in _SYNC_DRIVER_MAP.items():
        if url.startswith(async_prefix):
            return sync_prefix + url[len(async_prefix):]
    return url


def resolve_database_url(getenv: Callable[[str], str | None]) -> str:
    """与 api/runtime_config.get_database_url 同语义（平价契约由测试钉死）。

    优先级：APP_DATABASE_URL > DATABASE_URL > 默认 sqlite data/users.db；
    输出异步驱动形态的 URL。
    """
    url = (getenv("APP_DATABASE_URL") or "").strip() or (getenv("DATABASE_URL") or "").strip()
    if not url:
        return "sqlite+aiosqlite:///data/users.db"
    for sync_prefix, async_prefix in {
        "postgresql://": "postgresql+asyncpg://",
        "postgres://": "postgresql+asyncpg://",
        "mysql://": "mysql+aiomysql://",
        "sqlite:///": "sqlite+aiosqlite:///",
    }.items():
        if url.startswith(sync_prefix):
            return async_prefix + url[len(sync_prefix):]
    return url


def sqlite_path_from_url(url: str, project_root: Path) -> Path | None:
    """从异步/同步 SQLite URL 提取库文件路径；非 SQLite 返回 None。"""
    sync_url = _to_sync_url(url)
    if not sync_url.startswith("sqlite:///"):
        return None
    raw = sync_url[len("sqlite:///"):]
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    return path


def is_postgres_url(url: str) -> bool:
    return _to_sync_url(url).startswith(("postgresql://", "postgres://"))


# ────────────────────────── 通用工具 ──────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _log(msg: str) -> None:
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", file=sys.stderr)


def _default_git_rev(project_root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(project_root), capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:  # noqa: BLE001 — git 不可用不阻断备份
        pass
    return None


# ────────────────────────── tar 打包 ──────────────────────────


def _make_tar(src: Path, dest: Path) -> None:
    """目录 → tar.gz（arcname=目录名）。测试通过替换本函数模拟 tar 失败。"""
    with tarfile.open(dest, "w:gz") as tf:
        tf.add(str(src), arcname=src.name)


def _make_state_tar(data_dir: Path, dest: Path) -> int:
    """data/ 下状态文件 + 状态目录 → 单个 tar（arcname 相对 data/）。返回成员数。"""
    members: list[Path] = []
    for name in STATE_FILES:
        p = data_dir / name
        if p.is_file():
            members.append(p)
    for name in STATE_DIRS:
        p = data_dir / name
        if p.is_dir():
            members.append(p)
    with tarfile.open(dest, "w:gz") as tf:
        for m in members:
            tf.add(str(m), arcname=m.name)
    return len(members)


def _make_secrets_tar(project_root: Path, dest: Path) -> int:
    """非 git 机密配置（.env / llm_providers.local.json）→ 单个 tar。"""
    candidates = [
        (project_root / ".env", ".env"),
        (project_root / "config" / "llm_providers.local.json", "config/llm_providers.local.json"),
    ]
    members = [(src, arc) for src, arc in candidates if src.is_file()]
    if not members:
        return 0
    with tarfile.open(dest, "w:gz") as tf:
        for src, arc in members:
            tf.add(str(src), arcname=arc)
    return len(members)


# ────────────────────────── SQLite 一致快照与清点 ──────────────────────────


def _snapshot_sqlite(src: Path, dest: Path) -> str:
    """在线一致快照：备份 API 读到的是含 WAL 已提交内容的一致快照。返回 method。"""
    src_con = sqlite3.connect(f"{src.as_uri()}?mode=ro", uri=True)
    try:
        dst_con = sqlite3.connect(dest)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()
    return "sqlite_backup_api"


def _inspect_sqlite_copy(db: Path) -> dict[str, Any]:
    """对备份副本做完整性 + 全表行数 + 按会话键归属清点（清单即产物的事实）。"""
    con = sqlite3.connect(db)
    try:
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        tables: dict[str, int] = {}
        ownership: dict[str, dict[str, int]] = {}
        names = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for t in names:
            tables[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]  # noqa: S608 — 表名来自 sqlite_master 白名单
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            owner_col = "session_key" if "session_key" in cols else ("session_id" if "session_id" in cols else ("user_key" if "user_key" in cols else None))
            if owner_col:
                rows = con.execute(f'SELECT "{owner_col}", COUNT(*) FROM "{t}" GROUP BY "{owner_col}"').fetchall()  # noqa: S608
                ownership[t] = {str(k): int(v) for k, v in rows}
        return {"integrity": integrity, "tables": tables, "ownership": ownership}
    finally:
        con.close()


def _component_from_sqlite(name: str, src: Path, run_dir: Path, artifact: str) -> dict[str, Any]:
    comp: dict[str, Any] = {"kind": "sqlite", "source": str(src), "artifact": artifact}
    if not src.is_file():
        comp["status"] = "absent"
        return comp
    dest = run_dir / artifact
    try:
        comp["method"] = _snapshot_sqlite(src, dest)
        comp["bytes"] = dest.stat().st_size
        comp["sha256"] = _sha256(dest)
        info = _inspect_sqlite_copy(dest)
        comp["integrity"] = info["integrity"]
        comp["tables"] = info["tables"]
        comp["ownership"] = info["ownership"]
        comp["status"] = "ok" if info["integrity"] == "ok" else "failed"
        if comp["status"] == "failed":
            comp["error"] = f"integrity={info['integrity']}"
    except Exception as exc:  # noqa: BLE001 — 单组件失败不吞，整体语义由退出码表达
        dest.unlink(missing_ok=True)
        comp["status"] = "failed"
        comp["error"] = f"{type(exc).__name__}: {exc}"
    return comp


def _component_from_dir(name: str, src: Path, run_dir: Path, artifact: str) -> dict[str, Any]:
    comp: dict[str, Any] = {"kind": "dir", "source": str(src), "artifact": artifact}
    if not src.is_dir():
        comp["status"] = "absent"
        return comp
    dest = run_dir / artifact
    try:
        _make_tar(src, dest)
        comp["bytes"] = dest.stat().st_size
        comp["sha256"] = _sha256(dest)
        files = [p for p in src.rglob("*") if p.is_file()]
        comp["file_count"] = len(files)
        comp["total_bytes"] = sum(p.stat().st_size for p in files)
        comp["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        comp["status"] = "failed"
        comp["error"] = f"{type(exc).__name__}: {exc}"
    return comp


def _component_postgres(url: str, run_dir: Path) -> dict[str, Any]:
    comp: dict[str, Any] = {"kind": "postgres_dump", "source": _to_sync_url(url), "artifact": "users_db.sql.gz"}
    dest = run_dir / comp["artifact"]
    tmp = run_dir / "users_db.sql"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            proc = subprocess.run(["pg_dump", "--no-owner", "--no-acl", _to_sync_url(url)], stdout=fh, stderr=subprocess.PIPE, text=True, timeout=600)
        if proc.returncode != 0:
            raise OSError(f"pg_dump failed: {proc.stderr.strip()[:400]}")
        with open(tmp, "rb") as fin, gzip.open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        tmp.unlink(missing_ok=True)
        comp["bytes"] = dest.stat().st_size
        comp["sha256"] = _sha256(dest)
        comp["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        comp["status"] = "failed"
        comp["error"] = f"{type(exc).__name__}: {exc}"
    return comp


# ────────────────────────── 主流程 ──────────────────────────


def _plan_targets(project_root: Path, getenv: Callable[[str], str | None]) -> dict[str, dict[str, Any]]:
    """dry-run 用：只读存在性探测，不打开任何库、不写任何文件。"""
    url = resolve_database_url(getenv)
    targets: dict[str, dict[str, Any]] = {}
    users_path = sqlite_path_from_url(url, project_root)
    if is_postgres_url(url):
        targets["users_db"] = {"kind": "postgres_dump", "source": _to_sync_url(url)}
        targets["users_db"]["present"] = True  # 连通性留给真实备份判定
    else:
        targets["users_db"] = {"kind": "sqlite", "source": str(users_path), "present": bool(users_path and users_path.is_file())}
    for name, rel in (
        ("memory_db", "data/sqlite.db"),
        ("agent_plane_db", "data/agent_plane.db"),
    ):
        p = project_root / rel
        targets[name] = {"kind": "sqlite", "source": str(p), "present": p.is_file()}
    for name, rel in DIR_COMPONENTS.items():
        p = project_root / rel
        targets[name] = {"kind": "dir", "source": str(p), "present": p.is_dir()}
    targets["runtime_state"] = {"kind": "state_tar", "present": any((project_root / "data" / f).is_file() for f in STATE_FILES) or any((project_root / "data" / d).is_dir() for d in STATE_DIRS)}
    targets["runtime_secrets"] = {"kind": "secrets_tar", "present": (project_root / ".env").is_file() or (project_root / "config" / "llm_providers.local.json").is_file()}
    return targets


def run_backup(
    project_root: Path,
    output_dir: Path,
    retention_days: int = 30,
    dry_run: bool = False,
    allow_partial: bool = False,
    test_only: bool = False,
    require: tuple[str, ...] = DEFAULT_REQUIRE,
    getenv: Callable[[str], str | None] | None = None,
) -> tuple[int, dict[str, Any]]:
    """执行备份。返回 (退出码, manifest)。dry-run 返回 (退出码, plan)。"""
    getenv = getenv or os.environ.get
    project_root = project_root.resolve()
    url = resolve_database_url(getenv)

    if test_only:
        return _run_connection_test(project_root, url)

    if dry_run:
        plan = {
            "dry_run": True,
            "project_root": str(project_root),
            "output_dir": str(output_dir),
            "database_url": url,
            "targets": _plan_targets(project_root, getenv),
        }
        required_missing = [n for n in require if not plan["targets"][n]["present"]]
        optional_absent = [n for n, t in plan["targets"].items() if not t["present"] and n not in require and n not in OPTIONAL_COMPONENTS]
        if required_missing:
            plan["verdict"] = "failed"
            code = EXIT_FAILED
        elif optional_absent:
            plan["verdict"] = "partial"
            code = EXIT_PARTIAL
        else:
            plan["verdict"] = "complete"
            code = EXIT_OK
        return code, plan

    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = output_dir / f"{RUN_PREFIX}{run_id}"
    while run_dir.exists():
        run_dir = output_dir / f"{RUN_PREFIX}{run_id}-{os.getpid()}"
    run_dir.mkdir(parents=True)

    components: dict[str, dict[str, Any]] = {}

    # ── 主库（APP_DATABASE_URL 优先级与 runtime_config 平价） ──
    users_path = sqlite_path_from_url(url, project_root)
    if is_postgres_url(url):
        components["users_db"] = _component_postgres(url, run_dir)
    else:
        assert users_path is not None
        components["users_db"] = _component_from_sqlite("users_db", users_path, run_dir, "users.db")

    # ── 记忆库 / 事件账本 ──
    components["memory_db"] = _component_from_sqlite("memory_db", project_root / "data" / "sqlite.db", run_dir, "sqlite.db")
    components["agent_plane_db"] = _component_from_sqlite("agent_plane_db", project_root / "data" / "agent_plane.db", run_dir, "agent_plane.db")

    # ── 目录组件（向量源 / 角色卡 / 知识索引） ──
    for name, rel in DIR_COMPONENTS.items():
        components[name] = _component_from_dir(name, project_root / rel, run_dir, f"{name}.tar.gz")

    # ── 运行时状态 ──
    state_tar = run_dir / "runtime_state.tar.gz"
    try:
        member_count = _make_state_tar(project_root / "data", state_tar)
        if member_count == 0:
            state_tar.unlink(missing_ok=True)
            components["runtime_state"] = {"kind": "state_tar", "source": str(project_root / "data"), "status": "absent"}
        else:
            components["runtime_state"] = {
                "kind": "state_tar", "source": str(project_root / "data"), "artifact": "runtime_state.tar.gz",
                "bytes": state_tar.stat().st_size, "sha256": _sha256(state_tar),
                "member_count": member_count, "status": "ok",
            }
    except Exception as exc:  # noqa: BLE001
        state_tar.unlink(missing_ok=True)
        components["runtime_state"] = {
            "kind": "state_tar", "source": str(project_root / "data"), "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }

    # ── 机密配置（非 git；缺失为正常状态，只记录） ──
    secrets_tar = run_dir / "runtime_secrets.tar.gz"
    try:
        member_count = _make_secrets_tar(project_root, secrets_tar)
        if member_count == 0:
            secrets_tar.unlink(missing_ok=True)
            components["runtime_secrets"] = {"kind": "secrets_tar", "status": "absent", "optional": True}
        else:
            _best_effort_600(secrets_tar)
            components["runtime_secrets"] = {
                "kind": "secrets_tar", "artifact": "runtime_secrets.tar.gz",
                "bytes": secrets_tar.stat().st_size, "sha256": _sha256(secrets_tar),
                "member_count": member_count, "optional": True, "status": "ok",
            }
    except Exception as exc:  # noqa: BLE001
        secrets_tar.unlink(missing_ok=True)
        components["runtime_secrets"] = {"kind": "secrets_tar", "status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    # ── 整体语义：required 缺/败 → failed；失败(非 allow_partial) → failed；有缺席/降级 → partial ──
    failed_required = [n for n in require if components.get(n, {}).get("status") in ("absent", "failed")]
    failed_any = [n for n, c in components.items() if c.get("status") == "failed"]
    absent_notable = [n for n, c in components.items() if c.get("status") == "absent" and n not in OPTIONAL_COMPONENTS]

    if failed_required or (failed_any and not allow_partial):
        status = "failed"
    elif failed_any or absent_notable:
        status = "partial"
    else:
        status = "complete"

    manifest: dict[str, Any] = {
        "schema_version": 1,
        "tool": "deploy/backup_manager.py",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "project_root": str(project_root),
        "database_url": url,
        "git_rev": _default_git_rev(project_root),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "retention_days": retention_days,
        "require": list(require),
        "status": status,
        "components": components,
    }
    (run_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 清理超期备份目录（按目录 mtime）
    _cleanup_old_runs(output_dir, retention_days)

    if status == "failed":
        _log(f"备份失败 failed_required={failed_required} failed={failed_any}")
        return EXIT_FAILED, manifest
    if status == "partial":
        _log(f"备份部分完成 partial absent={absent_notable} failed={failed_any}")
        return EXIT_PARTIAL, manifest
    _log(f"备份完成 complete → {run_dir}")
    return EXIT_OK, manifest


def _best_effort_600(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def _cleanup_old_runs(output_dir: Path, retention_days: int) -> int:
    cutoff = datetime.now().timestamp() - retention_days * 86400
    removed = 0
    for d in output_dir.glob(f"{RUN_PREFIX}*"):
        if d.is_dir():
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    return removed


def _run_connection_test(project_root: Path, url: str) -> tuple[int, dict[str, Any]]:
    """--test：仅连通性验证，不产出备份。"""
    result: dict[str, Any] = {"test_only": True, "database_url": url}
    if is_postgres_url(url):
        try:
            proc = subprocess.run(["pg_isready"], capture_output=True, text=True, timeout=15)
            ok = proc.returncode == 0
            result["postgres"] = "ok" if ok else f"failed: {proc.stderr.strip()[:200]}"
        except Exception as exc:  # noqa: BLE001
            result["postgres"] = f"failed: {exc}"
            ok = False
        _log(f"连接测试 {'通过' if ok else '失败'}")
        return (EXIT_OK if ok else EXIT_FAILED), result
    path = sqlite_path_from_url(url, project_root)
    if path is None:
        result["error"] = "unresolved sqlite path"
        return EXIT_FAILED, result
    if not path.is_file():
        result["error"] = f"sqlite db not found: {path}"
        _log(f"连接测试失败：库文件不存在 {path}")
        return EXIT_FAILED, result
    try:
        con = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            con.execute("SELECT 1")
        finally:
            con.close()
        result["sqlite"] = "ok"
        result["path"] = str(path)
        _log(f"连接测试通过 {path}")
        return EXIT_OK, result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        _log(f"连接测试失败 {path}: {exc}")
        return EXIT_FAILED, result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="应用数据一致备份（manifest + 在线一致快照）")
    parser.add_argument("--project-root", default=os.environ.get("PROJECT_ROOT", str(Path(__file__).resolve().parent.parent)))
    parser.add_argument("--output-dir", default=None, help="默认 DB_BACKUP_DIR 或 <project-root>/backups")
    parser.add_argument("--retention-days", type=int, default=int(os.environ.get("DB_RETENTION_DAYS", "30")))
    parser.add_argument("--dry-run", action="store_true", help="只读探测并输出计划 JSON，不写任何文件")
    parser.add_argument("--allow-partial", action="store_true", help="组件失败时降级为 partial（仍非 0），不再硬失败")
    parser.add_argument("--test", action="store_true", help="仅测试数据库连接")
    parser.add_argument("--require", default=",".join(DEFAULT_REQUIRE), help="缺失即硬失败的组件清单（逗号分隔）")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir or os.environ.get("DB_BACKUP_DIR") or (Path(args.project_root) / "backups"))
    require = tuple(t.strip() for t in args.require.split(",") if t.strip())

    code, payload = run_backup(
        project_root=Path(args.project_root),
        output_dir=output_dir,
        retention_days=args.retention_days,
        dry_run=args.dry_run,
        allow_partial=args.allow_partial,
        test_only=args.test,
        require=require,
    )
    if args.dry_run:
        # dry-run：stdout 恰好一行计划 JSON（机器可读），日志走 stderr
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps({"status": payload.get("status"), "run_id": payload.get("run_id")}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())

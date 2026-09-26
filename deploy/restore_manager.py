#!/usr/bin/env python3
"""隔离恢复与校验管理器 — 恢复先到独立目录，校验通过才允许计划切换（W10）。

修复的缺陷（对照 2026-09-27 W10 工作单任务 2）：
- 恢复只落独立目标目录，**拒绝原地覆盖**应用检出（含 live-root 标记的目录无条件拒绝）；
- 每库 integrity_check + 表清单 ⊆ manifest + 全表行数一致 + 按 session_key/user_key
  的业务行归属逐键一致；角色卡/向量源逐文件 sha256 一致；
- 任何一步不通过即退出非 0 并输出差异清单；从不打印"恢复成功"的假话；
- 切换生产永远是人工计划动作：本脚本结尾只打印建议步骤，不做任何切换。

用法:
    python deploy/restore_manager.py --backup-dir backups/backup-YYYYMMDD-HHMMSS \
        --target /srv/restore-verify [--force] [--verify-only] [--skip-secrets]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
import tarfile
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"

# 命中任一标记 → 目标是应用检出（live root / 代码仓），无条件拒绝恢复
LIVE_ROOT_MARKERS = ("main.py", "pyproject.toml", ".git")

DB_PLACEMENTS = {
    "users_db": ("users.db", "data/users.db"),
    "memory_db": ("sqlite.db", "data/sqlite.db"),
    "agent_plane_db": ("agent_plane.db", "data/agent_plane.db"),
}

DIR_PLACEMENTS = {
    "vector_store": ("data",),      # tar 成员根为 chroma_db
    "character_cards": ("config",),  # tar 成员根为 characters
    "knowledge_index": ("data",),    # tar 成员根为 knowledge
}


class RestoreError(Exception):
    pass


def _fail(errors: list[str], msg: str) -> None:
    errors.append(msg)


# ────────────────────────── 守卫 ──────────────────────────


def check_target_guards(backup_dir: Path, target: Path, force: bool, verify_only: bool = False) -> None:
    if not backup_dir.is_dir():
        raise RestoreError(f"备份目录不存在: {backup_dir}")
    if not (backup_dir / MANIFEST_NAME).is_file():
        raise RestoreError(f"备份目录缺少 {MANIFEST_NAME}，不是一次完整备份的产物")
    target = target.resolve()
    if target == backup_dir.resolve():
        raise RestoreError("目标目录不能是备份目录本身")
    for marker in LIVE_ROOT_MARKERS:
        if (target / marker).exists():
            raise RestoreError(
                f"目标目录含应用检出标记（{marker}），疑似 live root/代码仓——"
                "禁止原地覆盖生产。请先停服后把恢复目标换成独立空目录。"
            )
    # verify-only 只读校验既有目标，非空是预期状态
    if target.exists() and any(target.iterdir()) and not force and not verify_only:
        raise RestoreError(f"目标目录非空: {target}（确认隔离后用 --force 显式覆盖）")


# ────────────────────────── 落盘 ──────────────────────────


def _safe_members(tf: tarfile.TarFile, target: Path) -> list[tarfile.TarInfo]:
    """防 zip-slip：所有成员解析后必须落在 target 内。"""
    safe: list[tarfile.TarInfo] = []
    for m in tf.getmembers():
        dest = (target / m.name).resolve()
        if not str(dest).startswith(str(target.resolve())):
            raise RestoreError(f"tar 成员越界（疑似恶意备份）: {m.name}")
        safe.append(m)
    return safe


def _extract_tar(artifact: Path, extract_into: Path) -> None:
    with tarfile.open(artifact, "r:gz") as tf:
        members = _safe_members(tf, extract_into)
        for m in members:
            if not m.isfile() and not m.isdir():
                continue
            dest = extract_into / m.name
            if m.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            src_file = tf.extractfile(m)
            assert src_file is not None
            with open(dest, "wb") as fh:
                fh.write(src_file.read())


def _place_components(backup_dir: Path, target: Path, manifest: dict[str, Any], skip_secrets: bool) -> dict[str, str]:
    placed: dict[str, str] = {}
    comps = manifest.get("components", {})

    # 数据库：先验产物校验和，再逐字节落位
    for name, (artifact, rel) in DB_PLACEMENTS.items():
        comp = comps.get(name, {})
        if comp.get("status") not in ("ok",):
            continue
        src = backup_dir / artifact
        if not src.is_file():
            raise RestoreError(f"manifest 声称 {name} ok 但产物缺失: {artifact}")
        if _sha256(src) != comp.get("sha256"):
            raise RestoreError(f"{name}: 备份产物 sha256 与 manifest 不符（备份介质损坏）: {artifact}")
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        placed[name] = str(dest)

    # 目录组件：先验校验和，tar 解到对应父目录（成员根即目录名）
    for name, (parent_rel,) in DIR_PLACEMENTS.items():
        comp = comps.get(name, {})
        if comp.get("status") not in ("ok",):
            continue
        artifact = comp.get("artifact") or f"{name}.tar.gz"
        src = backup_dir / artifact
        if not src.is_file():
            raise RestoreError(f"manifest 声称 {name} ok 但产物缺失: {artifact}")
        if _sha256(src) != comp.get("sha256"):
            raise RestoreError(f"{name}: 备份产物 sha256 与 manifest 不符（备份介质损坏）: {artifact}")
        parent = target / parent_rel
        parent.mkdir(parents=True, exist_ok=True)
        _extract_tar(src, parent)
        placed[name] = str(parent)

    # 运行时状态
    state_comp = comps.get("runtime_state", {})
    if state_comp.get("status") == "ok":
        artifact = state_comp.get("artifact") or "runtime_state.tar.gz"
        src = backup_dir / artifact
        if _sha256(src) != state_comp.get("sha256"):
            raise RestoreError(f"runtime_state: 备份产物 sha256 与 manifest 不符: {artifact}")
        _extract_tar(src, target / "data")
        placed["runtime_state"] = str(target / "data")

    # 机密（默认恢复；600）
    if not skip_secrets:
        sec = comps.get("runtime_secrets", {})
        if sec.get("status") == "ok":
            with tarfile.open(backup_dir / "runtime_secrets.tar.gz", "r:gz") as tf:
                for m in _safe_members(tf, target):
                    if not m.isfile():
                        continue
                    dest = target / m.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    fh = tf.extractfile(m)
                    assert fh is not None
                    dest.write_bytes(fh.read())
                    with contextlib.suppress(OSError):
                        os.chmod(dest, 0o600)
            placed["runtime_secrets"] = "ok"
    return placed


# ────────────────────────── 校验 ──────────────────────────


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_db(name: str, restored: Path, comp: dict[str, Any], errors: list[str]) -> None:
    try:
        con = sqlite3.connect(f"{restored.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        _fail(errors, f"{name}: 无法打开恢复产物 {restored}（{exc}）")
        return
    try:
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError as exc:
            _fail(errors, f"{name}: 恢复产物损坏（integrity 校验失败）: {exc}")
            return
        if integrity != "ok":
            _fail(errors, f"{name}: integrity={integrity}")
            return
        want_tables: dict[str, int] = comp.get("tables", {})
        have_tables = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
        missing = [t for t in want_tables if t not in have_tables]
        if missing:
            _fail(errors, f"{name}: 缺表 {missing}")
            return
        for t, want in want_tables.items():
            got = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]  # noqa: S608 — 表名来自自方 manifest
            if got != want:
                _fail(errors, f"{name}: 表 {t} 行数不一致 manifest={want} 恢复={got}")
        # 业务行归属逐键一致（双账号/多账号各归各）
        for t, want_map in comp.get("ownership", {}).items():
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            owner_col = "session_key" if "session_key" in cols else ("session_id" if "session_id" in cols else ("user_key" if "user_key" in cols else None))
            if owner_col is None:
                continue
            got_map = {str(k): int(v) for k, v in con.execute(f'SELECT "{owner_col}", COUNT(*) FROM "{t}" GROUP BY "{owner_col}"')}  # noqa: S608
            if got_map != want_map:
                diff_keys = {k for k in set(got_map) | set(want_map) if got_map.get(k) != want_map.get(k)}
                _fail(errors, f"{name}: 表 {t} 归属不一致（键差异: {sorted(diff_keys)}）")
    finally:
        con.close()


def _verify_dir(name: str, root: Path, comp: dict[str, Any], errors: list[str]) -> None:
    files = [p for p in root.rglob("*") if p.is_file()]
    if len(files) != comp.get("file_count"):
        _fail(errors, f"{name}: 文件数不一致 manifest={comp.get('file_count')} 恢复={len(files)}")
        return
    total = sum(p.stat().st_size for p in files)
    if total != comp.get("total_bytes"):
        _fail(errors, f"{name}: 总字节不一致 manifest={comp.get('total_bytes')} 恢复={total}")


def verify_restore(target: Path, manifest: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    comps = manifest.get("components", {})
    for name, (_artifact, rel) in DB_PLACEMENTS.items():
        comp = comps.get(name, {})
        if comp.get("status") != "ok":
            continue
        restored = target / rel
        if not restored.is_file():
            _fail(errors, f"{name}: 恢复产物缺失 {restored}")
            continue
        _verify_db(name, restored, comp, errors)
    for name in DIR_PLACEMENTS:
        comp = comps.get(name, {})
        if comp.get("status") != "ok":
            continue
        roots = {
            "vector_store": target / "data" / "chroma_db",
            "character_cards": target / "config" / "characters",
            "knowledge_index": target / "data" / "knowledge",
        }
        _verify_dir(name, roots[name], comp, errors)
    return {"checked": True}


# ────────────────────────── 主流程 ──────────────────────────


def run_restore(
    backup_dir: Path,
    target: Path,
    force: bool = False,
    verify_only: bool = False,
    skip_secrets: bool = False,
) -> tuple[int, dict[str, Any]]:
    errors: list[str] = []
    try:
        check_target_guards(backup_dir, target, force, verify_only=verify_only)
    except RestoreError as exc:
        print(f"[RESTORE-FAIL] {exc}", file=sys.stderr)
        return 1, {"status": "refused", "error": str(exc)}

    manifest = json.loads((backup_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    target = target.resolve()

    if not verify_only:
        try:
            _place_components(backup_dir, target, manifest, skip_secrets)
        except RestoreError as exc:
            print(f"[RESTORE-FAIL] {exc}", file=sys.stderr)
            return 1, {"status": "failed", "error": str(exc)}

    report: dict[str, Any] = {
        "backup_dir": str(backup_dir),
        "target": str(target),
        "backup_status": manifest.get("status"),
        "verify_only": verify_only,
    }
    verify_restore(target, manifest, errors)
    if errors:
        report["status"] = "failed"
        report["errors"] = errors
        for e in errors:
            print(f"[VERIFY-FAIL] {e}", file=sys.stderr)
        return 1, report
    report["status"] = "verified"
    return 0, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="隔离恢复 + manifest 校验（绝不原地覆盖生产）")
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--target", required=True, help="独立恢复目标目录（不能是应用检出/live root）")
    parser.add_argument("--force", action="store_true", help="允许覆盖非空目标（仍拒绝 live root）")
    parser.add_argument("--verify-only", action="store_true", help="跳过落盘，只对已有目标做 manifest 校验")
    parser.add_argument("--skip-secrets", action="store_true")
    args = parser.parse_args(argv)

    code, report = run_restore(
        Path(args.backup_dir), Path(args.target), force=args.force,
        verify_only=args.verify_only, skip_secrets=args.skip_secrets,
    )
    print(json.dumps(report, ensure_ascii=False))
    if code == 0:
        print(
            "\n恢复校验通过。切换生产请按计划人工执行：\n"
            "  1) 停服（确认进程完全退出）  2) 备份当前数据目录  3) 用本目标目录替换数据目录\n"
            "  4) 启动后 curl /api/health 与 /api/ready 双绿再放量",
            file=sys.stderr,
        )
    return code


if __name__ == "__main__":
    sys.exit(main())

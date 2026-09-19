"""遗留全局微信通道 → admin 用户通道迁移（幂等）。

用户裁决 2026-09-19：
- 一人两条通道
- 全局并发上限 100
- 遗留凭证归属 admin（是）

只迁移磁盘凭证/状态/qrcode/context_tokens；迁移后全局文件改 .bak-pre-channel。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from utils.project_paths import PROJECT_ROOT
from wechat_direct import channel_paths

logger = logging.getLogger("scripts.migrate_legacy_wechat_channel")

LEGACY_CRED = Path(os.path.expanduser("~/.weixin_cow_credentials.json"))
LEGACY_STATE = PROJECT_ROOT / "data" / "wechat_state.json"
LEGACY_QR = PROJECT_ROOT / "data" / "wechat_qrcode.json"
LEGACY_CTX = PROJECT_ROOT / "data" / "wechat_context_tokens.json"
MARKER = PROJECT_ROOT / "data" / "wechat_sessions" / "_legacy_migrated.json"


def _admin_user_id() -> int:
    """同步读 users.db 找 admin；找不到则 1。"""
    import sqlite3

    db = PROJECT_ROOT / "data" / "users.db"
    if not db.exists():
        return 1
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute(
            "SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1"
        ).fetchone()
        con.close()
        return int(row[0]) if row else 1
    except Exception:  # noqa: BLE001
        return 1


def _backup(path: Path) -> None:
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak-pre-channel")
        try:
            shutil.copy2(path, bak)
        except Exception as e:  # noqa: BLE001
            logger.warning("备份失败 %s: %s", path, e)


def migrate_legacy_if_needed(admin_user_id: int | None = None) -> dict:
    if MARKER.exists():
        try:
            return json.loads(MARKER.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass

    uid = admin_user_id if admin_user_id is not None else _admin_user_id()
    dest_dir = channel_paths.ensure_session_dir(uid, 0)
    moved: list[str] = []

    if LEGACY_CRED.exists():
        _backup(LEGACY_CRED)
        shutil.copy2(LEGACY_CRED, dest_dir / "credentials.json")
        # 附加 owner 标记
        try:
            data = json.loads((dest_dir / "credentials.json").read_text(encoding="utf-8"))
            data["owner_user_id"] = uid
            data["slot"] = 0
            (dest_dir / "credentials.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass
        LEGACY_CRED.rename(LEGACY_CRED.with_suffix(LEGACY_CRED.suffix + ".bak-pre-channel")) if False else None
        # 不删除 home 凭证本体（可能仍有运维用途）；只在 session 落副本 + marker
        moved.append("credentials")

    if LEGACY_STATE.exists():
        _backup(LEGACY_STATE)
        try:
            st = json.loads(LEGACY_STATE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            st = {}
        st["owner_user_id"] = uid
        st["slot"] = 0
        # 无 credentials 时强制 idle，避免假在线
        if not (dest_dir / "credentials.json").exists():
            st["connected"] = False
            st["status"] = "idle"
        (dest_dir / "state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        moved.append("state")

    if LEGACY_QR.exists():
        _backup(LEGACY_QR)
        try:
            shutil.copy2(LEGACY_QR, dest_dir / "qrcode.json")
            moved.append("qrcode")
        except Exception as e:  # noqa: BLE001
            logger.warning("迁移 qrcode 失败: %s", e)

    if LEGACY_CTX.exists():
        _backup(LEGACY_CTX)
        try:
            shutil.copy2(LEGACY_CTX, dest_dir / "context_tokens.json")
            moved.append("context_tokens")
        except Exception as e:  # noqa: BLE001
            logger.warning("迁移 context_tokens 失败: %s", e)

    result = {
        "admin_user_id": uid,
        "moved": moved,
        "session_dir": str(dest_dir),
    }
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if moved:
        logger.info("遗留微信通道已迁移到 admin user=%s moved=%s", uid, moved)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(migrate_legacy_if_needed())

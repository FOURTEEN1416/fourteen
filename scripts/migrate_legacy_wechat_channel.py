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
    # 磁盘凭证必须落到 wechat_channel_sessions，否则 admin 列表/状态库面为空
    try:
        _sync_disk_sessions_to_db_sync()
    except Exception as e:  # noqa: BLE001
        logger.warning("同步通道会话到 DB 失败（忽略）: %s", e)
    return result


def _load_env_for_api_import() -> None:
    """脚本入口先加载 .env，再 import api.*（auth_jwt 在 import 时读 JWT_SECRET）。"""
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass
    # 最低限度：若环境仍无 JWT_SECRET，脚本侧注入占位仅用于 DB 同步（不启动 HTTP）
    os.environ.setdefault("JWT_SECRET", "script-only-not-for-http-use-32chars-minimum!!")


def _sync_disk_sessions_to_db_sync() -> int:
    """CLI/脚本入口（进程内无运行中事件循环时可用）。"""
    import asyncio

    return asyncio.run(_sync_disk_sessions_async())


async def _sync_disk_sessions_async() -> int:
    """把 data/wechat_sessions/*/slot*/credentials.json 同步进 wechat_channel_sessions。"""
    _load_env_for_api_import()
    import json as _json
    from datetime import datetime, timezone

    from sqlalchemy import select

    from api.database import WechatChannelSession, _async_session, init_db

    await init_db()
    n = 0
    async with _async_session() as session:
        root = channel_paths.sessions_root()
        if not root.exists():
            return 0
        for user_dir in root.iterdir():
            if not user_dir.is_dir() or not user_dir.name.isdigit():
                continue
            uid = int(user_dir.name)
            for slot in channel_paths.list_user_slots_with_credentials(uid):
                cred_path = channel_paths.credentials_path(uid, slot)
                state_path = channel_paths.state_path(uid, slot)
                bot_id = ""
                status = "idle"
                try:
                    cred = _json.loads(cred_path.read_text(encoding="utf-8"))
                    bot_id = str(cred.get("bot_id") or "")
                except Exception:  # noqa: BLE001
                    pass
                try:
                    st = _json.loads(state_path.read_text(encoding="utf-8"))
                    if st.get("connected") and bot_id:
                        status = "connected"
                    elif st.get("status"):
                        status = str(st.get("status"))
                except Exception:  # noqa: BLE001
                    pass
                result = await session.execute(
                    select(WechatChannelSession).where(
                        WechatChannelSession.user_id == uid,
                        WechatChannelSession.slot == slot,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    row = WechatChannelSession(user_id=uid, slot=slot)
                    session.add(row)
                row.bot_id = bot_id or row.bot_id
                row.status = status
                if status == "connected":
                    row.last_connected_at = datetime.now(timezone.utc)
                n += 1
        await session.commit()
    return n


async def sync_disk_sessions_to_db() -> int:
    """异步入口，供 FastAPI lifespan 调用。

    旧实现在这里同步调 `_sync_disk_sessions_to_db_sync()`（内部 `asyncio.run`），
    在运行中的事件循环里必抛 RuntimeError——生产 app.log 每次 worker 启动
    刷一条「同步微信通道会话到 DB 失败（忽略）」，该启动同步从未成功。
    """
    return await _sync_disk_sessions_async()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _load_env_for_api_import()
    print(migrate_legacy_if_needed())
    print({"synced_rows": _sync_disk_sessions_to_db_sync()})

"""每人独立微信通道 API — JWT 鉴权，只操作当前登录用户自己的通道。

用户裁决 2026-09-19：
- 一人最多 2 条微信通道
- 全局并发上限 WECHAT_MAX_CHANNELS（默认 100）
- 旧全局 /api/channels/wechat/* 用户侧废弃（改 admin-only）
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Security
from pydantic import BaseModel
from sqlalchemy import select

from api.auth_jwt import get_current_user, get_current_user_id, require_role
from api.database import User, WechatChannelSession, get_db
from api.deps import deps
from wechat_direct import channel_paths
from wechat_direct.connector_registry import (
    ChannelQuotaError,
    ChannelSlotError,
    get_registry,
)
from wechat_direct.peer_character import (
    build_character_menu,
    get_peer_preference,
    list_owner_characters,
    set_peer_preference,
)

logger = logging.getLogger("api.wechat_channel")

router = APIRouter(prefix="/api/wechat/channel", tags=["wechat-channel"])
admin_router = APIRouter(prefix="/api/admin/wechat", tags=["admin-wechat"])

_bearer = None  # 通道 API 统一走 JWT Bearer（get_current_user_id）

QRCODE_EXPIRY_SECONDS = 600


class ChannelConnectRequest(BaseModel):
    slot: int | None = None


class PeerPreferenceRequest(BaseModel):
    character_card_id: str


async def _upsert_session_row(db, user_id: int, slot: int, **fields) -> None:
    result = await db.execute(
        select(WechatChannelSession).where(
            WechatChannelSession.user_id == int(user_id),
            WechatChannelSession.slot == int(slot),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = WechatChannelSession(user_id=int(user_id), slot=int(slot))
        db.add(row)
    for k, v in fields.items():
        if hasattr(row, k):
            setattr(row, k, v)
    await db.commit()


def _safe_channel_payload(st: dict) -> dict:
    """对外状态：永不返回 token/凭证。"""
    return {
        "connected": bool(st.get("connected")),
        "status": st.get("status") or ("connected" if st.get("connected") else "idle"),
        "bot_id": st.get("bot_id") or "",
        "owner_user_id": st.get("owner_user_id"),
        "slot": st.get("slot", 0),
        "uptime_seconds": st.get("uptime_seconds", 0),
        "messages_today": st.get("messages_today", 0),
        "reconnect_attempts": st.get("reconnect_attempts", 0),
        "last_activity": st.get("last_activity"),
        "nickname": st.get("nickname", ""),
    }


@router.get("")
async def get_my_channel(
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    """我的微信通道状态（主槽位优先 connected）。

    2026-09-21：以磁盘/registry 真源回写 DB，消除「凭证已连、库里 waiting_qr」
    的状态脱节（生产串台观感来源之一）。
    """
    registry = get_registry()
    primary = registry.primary_status(user_id)
    statuses = registry.status_for_user(user_id)
    for st in statuses:
        slot = int(st.get("slot", 0))
        connected = bool(st.get("connected"))
        status = "connected" if connected else (
            st.get("status") or "idle"
        )
        if status in ("waiting_qr", "scanned") and connected:
            status = "connected"
        await _upsert_session_row(
            db,
            user_id,
            slot,
            status=status,
            bot_id=str(st.get("bot_id") or ""),
        )
    channels = [_safe_channel_payload(s) for s in statuses]
    return {
        **_safe_channel_payload(primary),
        "channels": channels,
        "max_per_user": channel_paths.MAX_CHANNELS_PER_USER,
        "max_global": channel_paths.max_channels(),
        "message": "仅显示你自己的微信通道",
    }


@router.get("/list")
async def list_my_channels(
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    statuses = get_registry().status_for_user(user_id)
    for st in statuses:
        slot = int(st.get("slot", 0))
        connected = bool(st.get("connected"))
        status = "connected" if connected else (st.get("status") or "idle")
        if status in ("waiting_qr", "scanned") and connected:
            status = "connected"
        await _upsert_session_row(
            db, user_id, slot,
            status=status,
            bot_id=str(st.get("bot_id") or ""),
        )
    channels = [_safe_channel_payload(s) for s in statuses]
    return {"channels": channels, "max_per_user": channel_paths.MAX_CHANNELS_PER_USER}


@router.post("/connect")
async def connect_my_channel(
    req: ChannelConnectRequest | None = None,
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """触发**我的**微信扫码（不触碰他人通道）。"""
    slot = (req.slot if req else None)
    try:
        result = get_registry().start_login(
            user_id,
            slot=slot,
            user_manager=deps.gf,
        )
    except (ChannelQuotaError, ChannelSlotError) as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.exception("启动用户通道失败 user=%s", user_id)
        raise HTTPException(status_code=500, detail=f"启动连接失败: {e}") from e
    await _upsert_session_row(
        db, user_id, result.get("slot", 0), status="waiting_qr", last_error=""
    )
    return result


@router.get("/qrcode")
async def my_qrcode(user_id: int = Security(get_current_user_id)):
    """我的二维码；他人二维码不可见。"""
    from wechat_direct.wechat_connector import load_session_qrcode

    statuses = get_registry().status_for_user(user_id)
    slot = 0
    for st in statuses:
        if st.get("status") in ("waiting_qr", "scanned") or not st.get("connected"):
            slot = int(st.get("slot", 0))
            break
    data = load_session_qrcode(user_id, slot)
    url = data.get("qrcode_url", "") or ""
    ts = float(data.get("timestamp") or 0)
    fresh = bool(url) and (time.time() - ts) < QRCODE_EXPIRY_SECONDS
    qr_image = ""
    if fresh:
        from api.qrcode_store import _generate_qr_image

        qr_image = _generate_qr_image(url) or ""
    return {
        "status": data.get("status", "idle"),
        "qrcode_url": url if fresh else "",
        "qr_image": qr_image,
        "timestamp": ts,
        "is_expired": (time.time() - ts) >= QRCODE_EXPIRY_SECONDS if ts else True,
        "owner_user_id": user_id,
        "slot": slot,
        "message": "请使用你自己的微信扫码登录" if fresh else "等待二维码生成...",
    }


@router.post("/disconnect")
async def disconnect_my_channel(
    req: ChannelConnectRequest | None = None,
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    slot = int(req.slot) if req and req.slot is not None else 0
    ok = get_registry().disconnect(user_id, slot)
    await _upsert_session_row(db, user_id, slot, status="idle", bot_id="")
    return {
        "status": "disconnected",
        "ok": ok,
        "owner_user_id": user_id,
        "slot": slot,
        "message": "已断开你的微信通道" if ok else "该槽位没有在线通道",
    }


@router.post("/reconnect")
async def reconnect_my_channel(
    req: ChannelConnectRequest | None = None,
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    slot = int(req.slot) if req and req.slot is not None else None
    slots = [slot] if slot is not None else list(range(channel_paths.MAX_CHANNELS_PER_USER))
    for s in slots:
        path = channel_paths.credentials_path(user_id, s)
        if path.exists():
            path.unlink()
        get_registry().disconnect(user_id, s)
        await _upsert_session_row(db, user_id, s, status="idle", bot_id="")
    connect_req = ChannelConnectRequest(slot=slot)
    return await connect_my_channel(connect_req, user_id, db, user=None)


@router.get("/peers/{peer_wxid}/character")
async def get_peer_character(
    peer_wxid: str,
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    pref = await get_peer_preference(db, user_id, peer_wxid)
    return {"owner_user_id": user_id, "peer_wxid": peer_wxid, "character_card_id": pref}


@router.put("/peers/{peer_wxid}/character")
async def put_peer_character(
    peer_wxid: str,
    req: PeerPreferenceRequest,
    user_id: int = Security(get_current_user_id),
    db=Depends(get_db),
):
    await set_peer_preference(db, user_id, peer_wxid, req.character_card_id)
    await db.commit()
    # P1-审查 item28：旧实现只写 DB，运行中进程的绑定缓存与用户实例从不刷新
    # （_resolve_character_id 仅在实例首建时读缓存）→ 控制台换角色不重启永不生效。
    gf = deps.gf
    if gf is not None:
        try:
            await gf.upsert_binding(
                f"pref:{int(user_id)}:{peer_wxid}",
                {
                    "wxid": peer_wxid,
                    "user_id": int(user_id),
                    "character_card_id": req.character_card_id,
                },
            )
            gf.set_user_character(f"{int(user_id)}:{peer_wxid}", req.character_card_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("好友角色热更失败 owner=%s peer=%s: %s", user_id, peer_wxid, e)
    return {
        "status": "ok",
        "owner_user_id": user_id,
        "peer_wxid": peer_wxid,
        "character_card_id": req.character_card_id,
    }


@router.get("/characters")
async def my_characters_for_peers(user_id: int = Security(get_current_user_id)):
    cards = list_owner_characters(user_id)
    return {"characters": cards, "menu_preview": build_character_menu(cards)}


# ═══════════════════════════════════════════════
# Admin 运维面
# ═══════════════════════════════════════════════


@admin_router.get("/channels")
async def admin_list_channels(
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db=Depends(get_db),
):
    """全局通道摘要（无 token）。"""
    result = await db.execute(select(WechatChannelSession))
    rows = result.scalars().all()
    live = {(u, s): c for u, s, c in get_registry().all()}
    items = []
    for row in rows:
        conn = live.get((row.user_id, row.slot))
        connected = bool(getattr(conn, "token", "")) if conn else row.status == "connected"
        items.append(
            {
                **row.to_dict(),
                "connected": connected,
                "bot_id": getattr(conn, "bot_id", row.bot_id) if conn else row.bot_id,
            }
        )
    # 磁盘有凭证但库无行的，也列出
    root = channel_paths.sessions_root()
    known = {(i["user_id"], i["slot"]) for i in items}
    if root.exists():
        for user_dir in root.iterdir():
            if not user_dir.is_dir() or not user_dir.name.isdigit():
                continue
            uid = int(user_dir.name)
            for slot in channel_paths.list_user_slots_with_credentials(uid):
                if (uid, slot) in known:
                    continue
                st = get_registry().primary_status(uid) if slot == 0 else {}
                items.append(
                    {
                        "user_id": uid,
                        "slot": slot,
                        "status": "connected" if st.get("connected") else "idle",
                        "bot_id": st.get("bot_id", ""),
                        "connected": bool(st.get("connected")),
                        "source": "disk",
                    }
                )
    return {
        "channels": items,
        "online": get_registry().online_count(),
        "max_global": channel_paths.max_channels(),
    }


@admin_router.post("/channels/{user_id}/{slot}/force-disconnect")
async def admin_force_disconnect(
    user_id: int,
    slot: int,
    admin: tuple[int, User] = Depends(require_role("admin")),
    db=Depends(get_db),
):
    if slot not in (0, 1):
        raise HTTPException(status_code=400, detail="slot 仅支持 0/1")
    ok = get_registry().disconnect(user_id, slot)
    await _upsert_session_row(db, user_id, slot, status="idle", bot_id="")
    logger.warning(
        "管理员强制断开微信通道 target_user=%s slot=%s admin=%s ok=%s",
        user_id, slot, admin[0], ok,
    )
    return {"status": "disconnected", "ok": ok, "user_id": user_id, "slot": slot}

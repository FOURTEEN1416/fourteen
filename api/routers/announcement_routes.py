"""网站公告 API（V2-5，2026-10-29 批 4）。

调研结论为**自建不引库**（无适配 FastAPI+React 的活跃独立公告库，
headless CMS 过重；Open WebUI BannerModel 字段 + Mastodon 时间窗 +
Discourse per-user dismiss 为范式来源，见 docs/reports/2026-10-28-frontend-ux-audit.md）。

- 读面 ``GET /api/announcements/active``：任何登录用户；只回时间窗内 enabled
  行 + 本人已读标记（per-user dismiss 落库，跨设备一致）。
- 已读 ``POST /api/announcements/{id}/read``：幂等（唯一约束 ignore）。
- 管理面 ``/api/admin/announcements``：CRUD + 启停，全部落操作员审计（NEW-21）。

新表纪律：announcements / announcement_reads 均为**纯新增表**，
``Base.metadata.create_all`` 幂等创建，无 ALTER / 无数据迁移。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Security
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.audit import record_operator_audit
from api.auth import verify_api_key_dep
from api.auth_jwt import get_current_user_id, require_role
from api.database import Announcement, AnnouncementRead, User, get_db

logger = logging.getLogger("api.routers.announcements")

router = APIRouter(tags=["announcements"])
admin_router = APIRouter(prefix="/api/admin/announcements", tags=["admin-announcements"])


def _parse_dt(value: str | None) -> datetime | None:
    """前端传 ISO 串（可带 Z）→ naive UTC datetime（库口径 _utcnow 一致）。"""
    if not value:
        return None
    raw = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(400, f"时间格式非法: {value!r}（应为 ISO 8601）") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _in_window(row: Announcement, now: datetime) -> bool:
    if row.starts_at is not None and row.starts_at > now:
        return False
    return not (row.ends_at is not None and row.ends_at < now)


class AnnouncementUpsertRequest(BaseModel):
    type: str = Field("banner", pattern=r"^(banner|modal)$")
    title: str = Field(..., min_length=1, max_length=255)
    content_md: str = Field("", max_length=8000)
    link: str = Field("", max_length=512)
    link_label: str = Field("", max_length=64)
    dismissible: bool = True
    starts_at: str | None = None
    ends_at: str | None = None
    enabled: bool = True


@router.get("/api/announcements/active")
async def list_active_announcements(
    _auth: bool = Security(verify_api_key_dep),
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """当前时间窗内生效的公告（含本人已读标记）。未读在前（新→旧）。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await db.execute(
        select(Announcement)
        .where(Announcement.enabled.is_(True))
        .order_by(Announcement.id.desc())
        .limit(50)
    )
    rows = [r for r in result.scalars().all() if _in_window(r, now)]
    reads: set[int] = set()
    if rows:
        read_result = await db.execute(
            select(AnnouncementRead.announcement_id).where(
                AnnouncementRead.user_id == user_id,
                AnnouncementRead.announcement_id.in_([r.id for r in rows]),
            )
        )
        reads = {int(a) for a in read_result.scalars().all()}
    items: list[dict[str, Any]] = []
    for r in rows:
        item = r.to_dict()
        item["read"] = r.id in reads
        items.append(item)
    items.sort(key=lambda x: (x["read"], -x["id"]))
    return {"announcements": items}


@router.post("/api/announcements/{announcement_id}/read")
async def mark_announcement_read(
    announcement_id: int,
    _auth: bool = Security(verify_api_key_dep),
    user_id: int = Security(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """标记公告已读（幂等；唯一约束冲突静默成功）。"""
    exists = await db.get(Announcement, announcement_id)
    if not exists:
        raise HTTPException(404, "公告不存在")
    already = await db.execute(
        select(AnnouncementRead.id).where(
            AnnouncementRead.user_id == user_id,
            AnnouncementRead.announcement_id == announcement_id,
        )
    )
    if already.scalar_one_or_none() is None:
        db.add(AnnouncementRead(user_id=user_id, announcement_id=announcement_id))
        await db.commit()
    return {"status": "ok"}


# ═══════════════════════════════════════════════════════
# Admin 管理面
# ═══════════════════════════════════════════════════════


@admin_router.get("")
async def admin_list_announcements(
    limit: int = Query(50, ge=1, le=200),
    _admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """全部公告（含未启用/窗外），新在前。"""
    result = await db.execute(
        select(Announcement).order_by(Announcement.id.desc()).limit(limit)
    )
    return {"announcements": [r.to_dict() for r in result.scalars().all()]}


@admin_router.post("")
async def admin_create_announcement(
    req: AnnouncementUpsertRequest,
    admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = Announcement(
        type=req.type,
        title=req.title,
        content_md=req.content_md,
        link=req.link,
        link_label=req.link_label,
        dismissible=req.dismissible,
        starts_at=_parse_dt(req.starts_at),
        ends_at=_parse_dt(req.ends_at),
        enabled=req.enabled,
        created_by=admin[0],
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await record_operator_audit(
        db, admin_id=admin[0], action="announcement_create", target=f"announcement={row.id}",
        detail={"title": row.title},
    )
    return row.to_dict()


@admin_router.put("/{announcement_id}")
async def admin_update_announcement(
    announcement_id: int,
    req: AnnouncementUpsertRequest,
    admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Announcement, announcement_id)
    if not row:
        raise HTTPException(404, "公告不存在")
    row.type = req.type
    row.title = req.title
    row.content_md = req.content_md
    row.link = req.link
    row.link_label = req.link_label
    row.dismissible = req.dismissible
    row.starts_at = _parse_dt(req.starts_at)
    row.ends_at = _parse_dt(req.ends_at)
    row.enabled = req.enabled
    await db.commit()
    await record_operator_audit(
        db, admin_id=admin[0], action="announcement_update", target=f"announcement={row.id}",
        detail={"enabled": row.enabled},
    )
    return row.to_dict()


@admin_router.delete("/{announcement_id}")
async def admin_delete_announcement(
    announcement_id: int,
    admin: tuple[int, User] = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(Announcement, announcement_id)
    if not row:
        raise HTTPException(404, "公告不存在")
    await db.delete(row)
    await db.commit()
    await record_operator_audit(
        db, admin_id=admin[0], action="announcement_delete", target=f"announcement={announcement_id}",
    )
    return {"status": "deleted", "id": announcement_id}

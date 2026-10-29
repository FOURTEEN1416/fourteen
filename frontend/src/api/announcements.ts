/**
 * 网站公告 API（V2-5，2026-10-29）— /api/announcements/* + /api/admin/announcements/*
 */
import client from './client'

export interface AnnouncementItem {
  id: number
  type: 'banner' | 'modal'
  title: string
  content_md: string
  link: string
  link_label: string
  dismissible: boolean
  starts_at: string | null
  ends_at: string | null
  enabled: boolean
  read?: boolean
  created_at?: string | null
}

export interface AnnouncementUpsertRequest {
  type: 'banner' | 'modal'
  title: string
  content_md: string
  link: string
  link_label: string
  dismissible: boolean
  starts_at: string | null
  ends_at: string | null
  enabled: boolean
}

/** GET /api/announcements/active — 时间窗内生效公告 + 本人已读标记（未读在前） */
export function listActiveAnnouncements(): Promise<{ announcements: AnnouncementItem[] }> {
  return client.get('/announcements/active').then(r => r.data as { announcements: AnnouncementItem[] })
}

/** POST /api/announcements/{id}/read — 标记已读（幂等） */
export function markAnnouncementRead(id: number): Promise<{ status: string }> {
  return client.post(`/announcements/${id}/read`).then(r => r.data as { status: string })
}

/** GET /api/admin/announcements — 全量（含未启用/窗外） */
export function adminListAnnouncements(limit = 50): Promise<{ announcements: AnnouncementItem[] }> {
  return client.get('/admin/announcements', { params: { limit } }).then(r => r.data as { announcements: AnnouncementItem[] })
}

/** POST /api/admin/announcements */
export function adminCreateAnnouncement(req: AnnouncementUpsertRequest): Promise<AnnouncementItem> {
  return client.post('/admin/announcements', req).then(r => r.data as AnnouncementItem)
}

/** PUT /api/admin/announcements/{id} */
export function adminUpdateAnnouncement(id: number, req: AnnouncementUpsertRequest): Promise<AnnouncementItem> {
  return client.put(`/admin/announcements/${id}`, req).then(r => r.data as AnnouncementItem)
}

/** DELETE /api/admin/announcements/{id} */
export function adminDeleteAnnouncement(id: number): Promise<{ status: string; id: number }> {
  return client.delete(`/admin/announcements/${id}`).then(r => r.data as { status: string; id: number })
}

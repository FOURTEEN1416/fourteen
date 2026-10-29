/**
 * 系统管理 API — /api/admin/* 端点
 *
 * 管理后台功能：用户管理（仅 admin 角色可用）
 */
import client from './client'

// ════════════════════════════════════════════════════
//  类型定义
// ════════════════════════════════════════════════════

export type UserRole = 'admin' | 'editor' | 'viewer'

/** 系统用户对象（由 GET /api/admin/users 返回） */
export interface AdminUser {
  id: number
  email: string
  username: string
  display_name: string
  role: UserRole
  is_active: boolean
  is_verified: boolean
  created_at: string
  last_login_at: string | null
}

/** GET /api/admin/users 响应 */
export interface AdminListUsersResponse {
  users: AdminUser[]
  total: number
  page: number
  page_size: number
}

/** POST /api/admin/users 请求体 */
export interface AdminCreateUserRequest {
  email: string
  username: string
  password: string
  display_name: string
  role: UserRole
}

/** PUT /api/admin/users/{user_id} 请求体 */
export interface AdminUpdateUserRequest {
  email?: string
  username?: string
  password?: string
  display_name?: string
  role?: UserRole
  is_active?: boolean
}

/** POST /api/admin/users 响应 */
export interface AdminCreateUserResponse {
  id: number
  email: string
  username: string
  display_name: string
  role: UserRole
  is_active: boolean
  is_verified: boolean
  created_at: string
}

// ════════════════════════════════════════════════════
//  API 函数
// ════════════════════════════════════════════════════

/**
 * GET /api/admin/users — 获取系统用户列表
 *
 * @param page      页码（从 1 开始）
 * @param pageSize  每页数量
 * @param search    搜索关键词（匹配 email / username / display_name）
 * @param role      按角色过滤
 */
export function adminListUsers(
  page = 1,
  pageSize = 20,
  search?: string,
  role?: string,
): Promise<AdminListUsersResponse> {
  const params: Record<string, string | number> = { page, page_size: pageSize }
  if (search) params.search = search
  if (role) params.role = role
  return client.get('/admin/users', { params }).then(r => r.data as AdminListUsersResponse)
}

/**
 * PUT /api/admin/users/{userId} — 更新用户信息
 */
export function adminUpdateUser(
  userId: number,
  data: AdminUpdateUserRequest,
): Promise<AdminUser> {
  return client.put(`/admin/users/${userId}`, data).then(r => r.data as AdminUser)
}

/**
 * DELETE /api/admin/users/{userId} — 删除用户
 */
export function adminDeleteUser(userId: number): Promise<{ status: string; user_id: number }> {
  return client.delete(`/admin/users/${userId}`).then(r => r.data as { status: string; user_id: number })
}

/**
 * POST /api/admin/users — 创建新用户
 */
export function adminCreateUser(data: AdminCreateUserRequest): Promise<AdminCreateUserResponse> {
  return client.post('/admin/users', data).then(r => r.data as AdminCreateUserResponse)
}

/** GET /api/admin/users/{user_id} 响应（单用户详情，NEW-11 下钻用） */
export type AdminUserDetail = AdminUser & { updated_at?: string }

/**
 * GET /api/admin/users/{userId} — 单用户详情
 */
export function adminGetUser(userId: number): Promise<AdminUserDetail> {
  return client.get(`/admin/users/${userId}`).then(r => r.data as AdminUserDetail)
}

// ════════════════════════════════════════════════════
//  全局运维视图（V2-1，D20-⑥）+ 操作员审计（NEW-21）
// ════════════════════════════════════════════════════

/** GET /api/admin/global-view 响应 */
export interface GlobalViewResponse {
  enabled: boolean
}

/** GET /api/admin/global-view — 读当前操作员的全局运维视图开关 */
export function getGlobalView(): Promise<GlobalViewResponse> {
  return client.get('/admin/global-view').then(r => r.data as GlobalViewResponse)
}

/** POST /api/admin/global-view — 显式开关全局运维视图（带审计） */
export function setGlobalView(enabled: boolean): Promise<{ status: string; enabled: boolean }> {
  return client.post('/admin/global-view', { enabled }).then(r => r.data as { status: string; enabled: boolean })
}

/** 操作员审计条目（NEW-21：不含用户内容，target 只落归属标识） */
export interface OperatorAuditEntry {
  id: number
  admin_id: number
  action: string
  target: string
  detail: Record<string, unknown>
  created_at: string | null
}

/** GET /api/admin/audit — 操作员审计流水 */
export function listOperatorAudit(limit = 50): Promise<{ entries: OperatorAuditEntry[] }> {
  return client.get('/admin/audit', { params: { limit } }).then(r => r.data as { entries: OperatorAuditEntry[] })
}

// ════════════════════════════════════════════════════
//  通道运维（NEW-11：后端 wechat_channel_routes 已就绪，前端首次接线）
// ════════════════════════════════════════════════════

/** GET /api/admin/wechat/channels 单行（连接状态摘要） */
export interface AdminChannelInfo {
  user_id: number
  slot: number
  status: string
  bot_id: string
  connected: boolean
  source?: string
}

/** GET /api/admin/wechat/channels — 全局通道摘要（无 token 明文） */
export function adminListChannels(): Promise<{
  channels: AdminChannelInfo[]
  online: number
  max_global: number
}> {
  return client
    .get('/admin/wechat/channels')
    .then(r => r.data as { channels: AdminChannelInfo[]; online: number; max_global: number })
}

/** POST /api/admin/wechat/channels/{user_id}/{slot}/force-disconnect — 强制断开 */
export function adminForceDisconnect(
  userId: number,
  slot: number,
): Promise<{ status: string; ok: boolean; user_id: number; slot: number }> {
  return client
    .post(`/admin/wechat/channels/${userId}/${slot}/force-disconnect`)
    .then(r => r.data as { status: string; ok: boolean; user_id: number; slot: number })
}

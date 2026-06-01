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

/**
 * 用户管理 API — /api/users/* 端点
 *
 * 后端 GET /api/users 返回字段:
 *   user_id, nickname, character_card_id, affinity_level, affinity_name,
 *   primary_emotion, total_chats, created_at, last_active, is_active
 *
 * 前端 UsersPage 需要映射到 User 接口。
 */
import client from './client'

// ════════════════════════════════════════════════════
//  类型
// ════════════════════════════════════════════════════

/** 后端返回的原始用户数据 */
export interface BackendUser {
  user_id: string
  nickname: string
  character_card_id: string | null
  affinity_level: number
  affinity_name: string
  primary_emotion: string
  total_chats: number
  created_at: string
  last_active: string
  is_active: boolean
}

/** 后端 GET /api/users 响应 */
export interface ListUsersResponse {
  users: BackendUser[]
  total: number
}

/** 前端通用的用户展示接口（映射后端字段） */
export interface UserDisplay {
  id: string
  name: string
  online: boolean
  characterCount: number
  lastActive: string
  affinityLevel: number
  affinityName: string
  totalChats: number
}

/** 用户详情（GET /api/users/{user_id}） */
export interface UserDetailResponse {
  user_id: string
  nickname: string
  character_card_id: string | null
  emotion: Record<string, unknown>
  total_chats: number
  created_at: string
  last_active: string
  is_active: boolean
}

/** 用户聊天记录（GET /api/users/{user_id}/chat） */
export interface UserChatHistoryResponse {
  messages: Array<{
    role: string
    content: string
    emotion_tag?: string
    created_at: string
  }>
  user_id: string
}

/** 用户情感状态（GET /api/users/{user_id}/emotion） */
export interface UserEmotionResponse {
  user_id: string
  emotion: Record<string, unknown>
}

// ════════════════════════════════════════════════════
//  API 函数
// ════════════════════════════════════════════════════

/** GET /api/users — 获取用户列表 */
export function listUsers(): Promise<ListUsersResponse> {
  return client.get('/users').then(r => r.data as ListUsersResponse)
}

/** GET /api/users/{userId} — 获取用户详情 */
export function getUserDetail(userId: string): Promise<UserDetailResponse> {
  return client.get(`/users/${userId}`).then(r => r.data as UserDetailResponse)
}

/** GET /api/users/{userId}/chat — 获取用户聊天记录 */
export function getUserChatHistory(userId: string, limit = 50): Promise<UserChatHistoryResponse> {
  return client.get(`/users/${userId}/chat`, { params: { limit } }).then(r => r.data as UserChatHistoryResponse)
}

/** GET /api/users/{userId}/emotion — 获取用户情感状态 */
export function getUserEmotion(userId: string): Promise<UserEmotionResponse> {
  return client.get(`/users/${userId}/emotion`).then(r => r.data as UserEmotionResponse)
}

/** POST /api/users/{userId}/role — 为用户设置角色卡 */
export function setUserRole(userId: string, cardId: string): Promise<{ status: string; user_id: string; character_card_id: string }> {
  return client.post(`/users/${userId}/role`, null, { params: { card_id: cardId } }).then(r => r.data as { status: string; user_id: string; character_card_id: string })
}

/** POST /api/users/{userId}/reset — 重置用户 */
export function resetUser(userId: string): Promise<{ status: string; user_id: string }> {
  return client.post(`/users/${userId}/reset`).then(r => r.data as { status: string; user_id: string })
}

/** DELETE /api/users/{userId} — 删除用户 */
export function deleteUser(userId: string): Promise<{ status: string; user_id: string }> {
  return client.delete(`/users/${userId}`).then(r => r.data as { status: string; user_id: string })
}

// ════════════════════════════════════════════════════
//  辅助映射函数
// ════════════════════════════════════════════════════

/** 后端 BackendUser → 前端 UserDisplay 转换 */
export function toUserDisplay(bu: BackendUser): UserDisplay {
  return {
    id: bu.user_id,
    name: bu.nickname,
    online: bu.is_active,
    characterCount: bu.character_card_id ? 1 : 0,  // 后端只返回当前角色卡，前端可扩展
    lastActive: bu.last_active,
    affinityLevel: bu.affinity_level,
    affinityName: bu.affinity_name,
    totalChats: bu.total_chats,
  }
}

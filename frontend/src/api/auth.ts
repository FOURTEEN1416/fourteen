/**
 * 认证 API — /api/auth/* 端点
 *
 * 用户注册 / 登录 / 刷新令牌 / 登出 / 个人信息
 *
 * Option A: refreshToken 由 httpOnly cookie 自动传送，前端不手动传参。
 * 保留 refresh_token 参数做向后兼容（方便 curl / 脚本调用）。
 */
import client from './client'

// ── 类型 ──────────────────────────────────────────

export interface UserInfo {
  id: number
  email: string
  username: string
  display_name: string
  avatar_url: string
  role: 'admin' | 'editor' | 'viewer'
  is_active: boolean
  is_verified: boolean
  created_at: string
  last_login_at: string | null
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: UserInfo
}

export interface LoginRequest {
  login: string    // email or username
  password: string
}

export interface RegisterRequest {
  email: string
  username: string
  password: string
  display_name?: string
}

export interface RegisterInviteRequest {
  invite_code: string
  email: string
  username: string
  password: string
  display_name?: string
}

// ── API 函数 ──────────────────────────────────────

/** POST /api/auth/register — 注册新用户 */
export function register(data: RegisterRequest): Promise<TokenResponse> {
  return client.post('/auth/register', data).then(r => r.data as TokenResponse)
}

/** POST /api/auth/register-invite — 使用邀请码注册 */
export function registerWithInvite(data: RegisterInviteRequest): Promise<TokenResponse> {
  return client.post('/auth/register-invite', data).then(r => r.data as TokenResponse)
}

/** POST /api/auth/login — 登录 */
export function login(data: LoginRequest): Promise<TokenResponse> {
  return client.post('/auth/login', data).then(r => r.data as TokenResponse)
}

/** POST /api/auth/refresh — 刷新 access token
 *
 * 优先使用 httpOnly cookie（浏览器自动发送），
 * 也接受 refresh_token 参数做向后兼容。
 */
export function refreshToken(refresh_token?: string): Promise<TokenResponse> {
  const body = refresh_token ? { refresh_token } : {}
  return client.post('/auth/refresh', body).then(r => r.data as TokenResponse)
}

/** POST /api/auth/logout — 登出
 *
 * 优先使用 httpOnly cookie（浏览器自动发送），
 * 也接受 refresh_token 参数做向后兼容。
 */
export function logout(refresh_token?: string): Promise<void> {
  const body = refresh_token ? { refresh_token } : {}
  return client.post('/auth/logout', body).then(() => undefined)
}

/** GET /api/auth/me — 获取当前用户信息 */
export function getMe(): Promise<UserInfo> {
  return client.get('/auth/me').then(r => r.data as UserInfo)
}

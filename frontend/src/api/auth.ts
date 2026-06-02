/**
 * 认证 API — /api/auth/* 端点
 *
 * 用户注册 / 登录 / 刷新令牌 / 登出 / 个人信息
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

// ── API 函数 ──────────────────────────────────────

export interface RegisterInviteRequest {
  invite_code: string
  email: string
  username: string
  password: string
  display_name?: string
}

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

/** POST /api/auth/refresh — 刷新 access token */
export function refreshToken(refresh_token: string): Promise<TokenResponse> {
  return client.post('/auth/refresh', { refresh_token }).then(r => r.data as TokenResponse)
}

/** POST /api/auth/logout — 登出 */
export function logout(refresh_token: string): Promise<void> {
  return client.post('/auth/logout', { refresh_token }).then(() => undefined)
}

/** GET /api/auth/me — 获取当前用户信息 */
export function getMe(): Promise<UserInfo> {
  return client.get('/auth/me').then(r => r.data as UserInfo)
}

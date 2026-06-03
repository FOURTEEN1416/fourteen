/**
 * 用户认证状态管理
 *
 * Option A: accessToken 存内存闭包（不可持久化），refreshToken 存 httpOnly cookie（后端控制）。
 * 遵循 FF-0007：Zustand store 禁止直接 import API。
 *
 * 只存：user / isAuthenticated / isInitialized（可持久化 user 用于恢复会话）
 * 内存：accessToken（页面刷新后通过 refresh cookie 重新获取）
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// 遵循 FF-0007：不 import API 类型，自行维护镜像类型
type UserRole = 'admin' | 'editor' | 'viewer'

export interface UserInfo {
  id: number
  email: string
  username: string
  display_name: string
  avatar_url: string
  role: UserRole
  is_active: boolean
  is_verified: boolean
  created_at: string
  last_login_at: string | null
}

// ── 内存级 accessToken（不持久化，XSS 不可窃取） ──

let _accessToken: string | null = null

export function getAccessToken(): string | null {
  return _accessToken
}

export function setAccessToken(token: string | null): void {
  _accessToken = token
}

// ── Store 类型 ──

export interface AuthState {
  user: UserInfo | null
  /** @deprecated 请用 getAccessToken() 读取内存值 */
  accessToken: string | null
  isAuthenticated: boolean
  isInitialized: boolean

  // 纯动作（无 API 调用）
  setAuth: (user: UserInfo, accessToken: string) => void
  clearAuth: () => void
}

// ── Store ─────────────────────────────────────────

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      // 初始状态
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isInitialized: false,

      /** 登录/注册成功后设置认证信息 */
      setAuth: (user: UserInfo, accessToken: string) => {
        setAccessToken(accessToken)
        set({ user, accessToken, isAuthenticated: true })
      },

      /** 清除所有认证状态 */
      clearAuth: () => {
        setAccessToken(null)
        set({ user: null, accessToken: null, isAuthenticated: false })
        // 清除 persist 存储
        if (typeof window !== 'undefined') {
          try {
            localStorage.removeItem('auth-storage')
          } catch { /* ignore */ }
        }
      },
    }),
    {
      name: 'auth-storage',
      // 只持久化 user 信息（accessToken 不持久化——内存安全）
      partialize: (state) => ({
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
)

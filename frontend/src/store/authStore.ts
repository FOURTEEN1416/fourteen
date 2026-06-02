/**
 * 用户认证状态管理
 *
 * 纯状态 Store — 遵循 FF-0007（Zustand store 禁止直接 import API）。
 * API 调用逻辑在 useAuth hook 中处理。
 *
 * 只存：user / accessToken / refreshToken / isAuthenticated / isInitialized
 * 只提供：setTokens / clearAuth
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// 注意：不 import API 类型（遵循 FF-0007），自行维护镜像类型
// 必须与 api/auth.ts 的 UserInfo 保持一致
import type { UserRole } from '../api/admin'

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

export interface AuthState {
  // 状态
  user: UserInfo | null
  accessToken: string | null
  refreshToken: string | null
  isAuthenticated: boolean
  isInitialized: boolean

  // 纯动作（无 API 调用）
  setTokens: (access: string, refresh: string, user: UserInfo) => void
  clearAuth: () => void
}

// ── Store ─────────────────────────────────────────

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      // 初始状态
      user: null,
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,
      isInitialized: false,

      /** 直接设置 tokens（从 persist 恢复时使用） */
      setTokens: (access: string, refresh: string, user: UserInfo) => {
        set({
          accessToken: access,
          refreshToken: refresh,
          user,
          isAuthenticated: true,
        })
      },

      /** 清除所有认证状态 */
      clearAuth: () => {
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false,
        })
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
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
)

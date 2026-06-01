/**
 * 用户认证状态管理
 *
 * 使用 Zustand 管理 JWT token 和用户信息。
 * token 同步持久化到 localStorage，刷新页面后自动恢复。
 */
import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { UserInfo } from '../api/auth'
import * as authApi from '../api/auth'

// ── 类型 ──────────────────────────────────────────

interface AuthState {
  // 状态
  user: UserInfo | null
  accessToken: string | null
  refreshToken: string | null
  isAuthenticated: boolean
  isInitialized: boolean

  // 动作
  init: () => Promise<void>
  login: (login: string, password: string) => Promise<void>
  register: (data: { email: string; username: string; password: string; display_name?: string }) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<boolean>
  setTokens: (access: string, refresh: string, user: UserInfo) => void
  clearAuth: () => void
}

// ── Store ─────────────────────────────────────────

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      // 初始状态
      user: null,
      accessToken: null,
      refreshToken: null,
      isAuthenticated: false,
      isInitialized: false,

      /** 应用启动时初始化：检查已有 token 是否有效 */
      init: async () => {
        const { accessToken, refresh } = get()
        if (!accessToken) {
          set({ isInitialized: true })
          return
        }
        // 尝试刷新
        const ok = await refresh()
        if (!ok) {
          set({ isInitialized: true })
          return
        }
        set({ isInitialized: true })
      },

      /** 登录：调用 API → 保存 tokens + user */
      login: async (login: string, password: string) => {
        const res = await authApi.login({ login, password })
        set({
          accessToken: res.access_token,
          refreshToken: res.refresh_token,
          user: res.user,
          isAuthenticated: true,
        })
        // 把 access token 注入 axios 默认请求头
        _setAuthHeader(res.access_token)
      },

      /** 注册：调用 API → 保存 tokens + user */
      register: async (data) => {
        const res = await authApi.register(data)
        set({
          accessToken: res.access_token,
          refreshToken: res.refresh_token,
          user: res.user,
          isAuthenticated: true,
        })
        _setAuthHeader(res.access_token)
      },

      /** 登出：调用 API 吊销 token → 清除本地状态 */
      logout: async () => {
        const { refreshToken } = get()
        try {
          if (refreshToken) {
            await authApi.logout(refreshToken)
          }
        } catch {
          // 即使登出 API 失败也清除本地状态
        }
        get().clearAuth()
      },

      /** 刷新 token：用 refresh token 换取新的 access token */
      refresh: async (): Promise<boolean> => {
        const { refreshToken } = get()
        if (!refreshToken) return false
        try {
          const res = await authApi.refreshToken(refreshToken)
          set({
            accessToken: res.access_token,
            refreshToken: res.refresh_token,
            user: res.user,
            isAuthenticated: true,
          })
          _setAuthHeader(res.access_token)
          return true
        } catch {
          get().clearAuth()
          return false
        }
      },

      /** 直接设置 tokens（从 persist 恢复时使用） */
      setTokens: (access: string, refresh: string, user: UserInfo) => {
        set({
          accessToken: access,
          refreshToken: refresh,
          user,
          isAuthenticated: true,
        })
        _setAuthHeader(access)
      },

      /** 清除所有认证状态 */
      clearAuth: () => {
        set({
          user: null,
          accessToken: null,
          refreshToken: null,
          isAuthenticated: false,
        })
        _removeAuthHeader()
        // 清除 persist 存储
        if (typeof window !== 'undefined') {
          try {
            localStorage.removeItem('auth-storage')
          } catch { /* ignore */ }
        }
      },
    }),
    {
      name: 'auth-storage',              // localStorage key
      partialize: (state) => ({           // 只持久化 tokens 和 user
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
)

// ── 工具：标记认证头（client.ts 拦截器会自动从 localStorage 读取） ──
// client.ts 的请求拦截器会自动从 localStorage 读取 Bearer token，
// 因此不需要手动注入/移除 axios headers。

function _setAuthHeader(_token: string) {
  // 不需要操作 — 拦截器自动处理
}

function _removeAuthHeader() {
  // 不需要操作 — 拦截器自动处理
}

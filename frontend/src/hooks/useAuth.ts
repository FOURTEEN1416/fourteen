/**
 * 用户认证 Hook
 *
 * 封装认证 API 调用逻辑，更新 authStore 纯状态。
 * Option A: accessToken 存内存闭包，refreshToken 由 httpOnly cookie 管理。
 * 遵循 FF-0007：Zustand store 不直接 import API。
 *
 * 提供：login / register / logout / refresh / init
 * 读取：user / isAuthenticated / isInitialized（透传 store 状态）
 */
import * as authApi from '../api/auth'
import { useAuthStore, setAccessToken } from '../store/authStore'

// ── Hook ────────────────────────────────────────────

export function useAuth() {
  const { user, isAuthenticated, isInitialized } = useAuthStore()

  const init = async () => {
    // 检查是否有持久化的 user（说明之前登录过）
    const { user: storedUser } = useAuthStore.getState()
    if (!storedUser) {
      useAuthStore.setState({ isInitialized: true })
      return
    }
    // 尝试用 httpOnly cookie 自动刷新 accessToken
    const ok = await refresh()
    useAuthStore.setState({ isInitialized: true })
    return ok
  }

  const login = async (loginName: string, password: string) => {
    const res = await authApi.login({ login: loginName, password })
    useAuthStore.getState().setAuth(res.user, res.access_token)
  }

  const register = async (data: {
    email: string
    username: string
    password: string
    display_name?: string
  }) => {
    const res = await authApi.register(data)
    useAuthStore.getState().setAuth(res.user, res.access_token)
  }

  const registerWithInvite = async (data: {
    invite_code: string
    email: string
    username: string
    password: string
    display_name?: string
  }) => {
    const res = await authApi.registerWithInvite(data)
    useAuthStore.getState().setAuth(res.user, res.access_token)
  }

  const logout = async () => {
    try {
      // httpOnly cookie 由浏览器自动发送，无需传 refresh_token
      await authApi.logout()
    } catch {
      // 即使登出 API 失败也清除本地状态
    }
    useAuthStore.getState().clearAuth()
  }

  const refresh = async (): Promise<boolean> => {
    try {
      // httpOnly cookie 由浏览器自动发送
      const res = await authApi.refreshToken()
      setAccessToken(res.access_token)
      useAuthStore.setState({ user: res.user, isAuthenticated: true })
      return true
    } catch {
      useAuthStore.getState().clearAuth()
      return false
    }
  }

  return {
    init, login, register, registerWithInvite, logout, refresh,
    user, isAuthenticated, isInitialized,
  }
}

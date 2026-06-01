/**
 * 用户认证 Hook
 *
 * 封装认证 API 调用逻辑，更新 authStore 纯状态。
 * 遵循 FF-0007：Zustand store 不直接 import API。
 *
 * 提供：login / register / logout / refresh / init
 * 读取：user / isAuthenticated / isInitialized（透传 store 状态）
 */
import * as authApi from '../api/auth'
import { useAuthStore } from '../store/authStore'

// ── Hook ────────────────────────────────────────────

export function useAuth() {
  const { user, isAuthenticated, isInitialized } = useAuthStore()

  const init = async () => {
    const { accessToken } = useAuthStore.getState()
    if (!accessToken) {
      useAuthStore.setState({ isInitialized: true })
      return
    }
    const ok = await refresh()
    useAuthStore.setState({ isInitialized: true })
    return ok
  }

  const login = async (loginName: string, password: string) => {
    const res = await authApi.login({ login: loginName, password })
    useAuthStore.setState({
      accessToken: res.access_token,
      refreshToken: res.refresh_token,
      user: res.user,
      isAuthenticated: true,
    })
  }

  const register = async (data: {
    email: string
    username: string
    password: string
    display_name?: string
  }) => {
    const res = await authApi.register(data)
    useAuthStore.setState({
      accessToken: res.access_token,
      refreshToken: res.refresh_token,
      user: res.user,
      isAuthenticated: true,
    })
  }

  const logout = async () => {
    const { refreshToken } = useAuthStore.getState()
    try {
      if (refreshToken) {
        await authApi.logout(refreshToken)
      }
    } catch {
      // 即使登出 API 失败也清除本地状态
    }
    useAuthStore.getState().clearAuth()
  }

  const refresh = async (): Promise<boolean> => {
    const { refreshToken } = useAuthStore.getState()
    if (!refreshToken) return false
    try {
      const res = await authApi.refreshToken(refreshToken)
      useAuthStore.setState({
        accessToken: res.access_token,
        refreshToken: res.refresh_token,
        user: res.user,
        isAuthenticated: true,
      })
      return true
    } catch {
      useAuthStore.getState().clearAuth()
      return false
    }
  }

  return {
    init, login, register, logout, refresh,
    user, isAuthenticated, isInitialized,
  }
}

/**
 * 用户认证 Hook
 *
 * 封装认证 API 调用逻辑，更新 authStore 纯状态。
 * Option A: accessToken 存内存闭包，refreshToken 由 httpOnly cookie 管理。
 * 遵循 FF-0007：Zustand store 不直接 import API。
 *
 * 提供：login / register / registerWithInvite / logout
 * 读取：user / isAuthenticated
 *
 * 注：认证初始化逻辑已移至 App.tsx 的 <AuthInit> 组件，
 * 此处不再暴露 init/refresh，避免双份初始化路径。
 */
import * as authApi from '../api/auth'
import { useAuthStore } from '../store/authStore'

// ── Hook ────────────────────────────────────────────

export function useAuth() {
  const { user, isAuthenticated } = useAuthStore()

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

  return {
    login, register, registerWithInvite, logout,
    user, isAuthenticated,
  }
}

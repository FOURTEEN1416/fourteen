import { useEffect } from 'react'
import type React from 'react'
import { useAuthStore } from '../../store/authStore'
import { refreshToken } from '../../api/auth'

/** 认证初始化：App 启动时刷新会话一次。
 *
 * StrictMode 双挂载会触发两次 effect——后端 refresh 是旋转式 session
 * （旧 httpOnly cookie 立即失效），两个并发 refresh 必有一败 401 → 弹回登录页。
 * 模块级 in-flight 锁保证并发时只发一次；锁释放后（真正的再次挂载，如登录后
 * 重新进入）带新 cookie 重跑是无害的。
 */
let _initRefreshInFlight: Promise<void> | null = null

export default function AuthInit({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const { user } = useAuthStore.getState()
    if (!user) {
      useAuthStore.setState({ isInitialized: true })
      return
    }
    if (_initRefreshInFlight) return
    _initRefreshInFlight = (async () => {
      try {
        const res = await refreshToken()
        useAuthStore.getState().setAuth(res.user, res.access_token)
        useAuthStore.setState({ isInitialized: true, needsConsent: res.needs_consent ?? false })
      } catch {
        useAuthStore.getState().clearAuth()
        useAuthStore.setState({ isInitialized: true })
      }
    })().finally(() => {
      _initRefreshInFlight = null
    })
  }, [])
  return <>{children}</>
}

import { useEffect } from 'react'
import type React from 'react'
import { useAuthStore } from '../../store/authStore'
import { refreshToken } from '../../api/auth'
import { clearAccountScopedCache } from '../../api/queryClient'

/** 认证初始化：App 启动时刷新会话一次。
 *
 * StrictMode 双挂载会触发两次 effect——后端 refresh 是旋转式 session
 * （旧 httpOnly cookie 立即失效），两个并发 refresh 必有一败 401 → 弹回登录页。
 * 模块级 in-flight 锁保证并发时只发一次；锁释放后（真正的再次挂载，如登录后
 * 重新进入）带新 cookie 重跑是无害的。
 *
 * W1：启动恢复也是「认证主体可能变更」的入口——持久化的 user 与 cookie 实际
 * 归属的账号可能不是同一个（换人登录 / 共享浏览器）；主体一变或会话失效，
 * 私人查询缓存必须清空，不得跨账号留在内存里。
 */
let _initRefreshInFlight: Promise<void> | null = null

export default function AuthInit({ children }: { children: React.ReactNode }) {
  useEffect(() => {
    const { user } = useAuthStore.getState()
    if (!user) {
      useAuthStore.setState({ isInitialized: true })
      return
    }
    const persistedUserId = user.id
    if (_initRefreshInFlight) return
    _initRefreshInFlight = (async () => {
      try {
        const res = await refreshToken()
        if (res.user?.id !== persistedUserId) {
          // cookie 归属的账号 ≠ 持久化的账号：主体已更换 → 清私人缓存
          clearAccountScopedCache()
        }
        useAuthStore.getState().setAuth(res.user, res.access_token)
        useAuthStore.setState({ isInitialized: true, needsConsent: res.needs_consent ?? false })
      } catch {
        useAuthStore.getState().clearAuth()
        // 会话已失效：私人缓存不得继续驻留
        clearAccountScopedCache()
        useAuthStore.setState({ isInitialized: true })
      }
    })().finally(() => {
      _initRefreshInFlight = null
    })
  }, [])
  return <>{children}</>
}

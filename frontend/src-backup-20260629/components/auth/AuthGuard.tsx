/**
 * 路由守卫组件
 *
 * 包裹需要认证的路由：
 * - 未认证 → 跳转到 /login，并记住来源路径
 * - 正在初始化 auth → 显示加载状态
 * - 已认证 → 渲染子组件
 */
import { useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../../store/authStore'
import type { ReactNode } from 'react'

interface AuthGuardProps {
  children: ReactNode
  /** 可选：跳过未初始化时的 loading，直接跳转到 login（默认 false） */
  skipInitCheck?: boolean
}

export default function AuthGuard({ children }: AuthGuardProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { isAuthenticated, isInitialized } = useAuthStore()

  useEffect(() => {
    // 初始化完成后仍未认证 → 跳登录页
    if (isInitialized && !isAuthenticated) {
      navigate('/login', {
        replace: true,
        state: { from: location.pathname + location.search },
      })
    }
  }, [isInitialized, isAuthenticated, navigate, location])

  // 未初始化完成 → 显示加载
  if (!isInitialized) {
    return (
      <div className="flex items-center justify-center h-screen bg-dynamic">
        <div className="flex flex-col items-center gap-3">
          <span className="w-6 h-6 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-gray-400">验证身份中…</span>
        </div>
      </div>
    )
  }

  // 未认证 → 不渲染（跳转由 useEffect 处理）
  if (!isAuthenticated) {
    return null
  }

  // 已认证 → 渲染子组件
  return <>{children}</>
}

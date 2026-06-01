/**
 * 角色守卫组件
 *
 * 包裹需要特定角色才能访问的路由：
 * - 未初始化 → 显示加载状态
 * - 未认证 → 不渲染（由 AuthGuard 父级处理跳转）
 * - 角色不符 → 跳转到 fallback 路径
 * - 角色匹配 → 渲染子组件
 */
import { Navigate, Outlet } from 'react-router-dom'
import { useAuthStore } from '../../store/authStore'
import type { ReactNode } from 'react'

interface RoleGuardProps {
  children?: ReactNode
  /** 允许访问的角色列表，如 ['admin'] 或 ['admin', 'editor'] */
  roles: string[]
  /** 角色不符时的跳转路径（默认 '/'） */
  fallback?: string
}

export default function RoleGuard({ children, roles, fallback = '/' }: RoleGuardProps) {
  const { user, isAuthenticated, isInitialized } = useAuthStore()

  // 未初始化完成 → 显示加载
  if (!isInitialized) {
    return (
      <div className="flex items-center justify-center h-screen bg-dynamic">
        <div className="flex flex-col items-center gap-3">
          <span className="w-6 h-6 border-2 border-indigo-400 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-gray-400">验证权限中…</span>
        </div>
      </div>
    )
  }

  // 未认证 → 不渲染（由 AuthGuard 父级处理跳转）
  if (!isAuthenticated) {
    return null
  }

  // 角色不在允许列表中 → 跳转到 fallback
  if (!user || !roles.includes(user.role)) {
    return <Navigate to={fallback} replace />
  }

  // 已授权 → 渲染子组件（支持 children 和 Outlet 两种用法）
  return <>{children || <Outlet />}</>
}

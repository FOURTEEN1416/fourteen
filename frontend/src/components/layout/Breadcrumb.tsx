import { useState } from 'react'
import { useLocation, Link, useNavigate } from 'react-router-dom'
import { Menu, LogOut, UserRound } from 'lucide-react'
import MobileDrawer from './MobileDrawer'
import { useUnifiedCharacters } from '../../hooks/useQueries'
import { useAuth } from '../../hooks/useAuth'
import type { UnifiedCharacter } from '../../types/api'

interface Crumb {
  label: string
  to?: string
}

function useBreadcrumbs(): Crumb[] {
  const { pathname } = useLocation()
  const { data } = useUnifiedCharacters()
  const characters = data?.characters ?? []

  // 单段路径：面包屑显示**分组名**，页名交给页内 h1（2026-09-19 审美批次：消除
  // 面包屑与 h1 逐字重复）
  // /wechat
  if (pathname === '/wechat') return [{ label: '连接' }]

  // /settings/*
  const settingsMatch = pathname.match(/^\/settings\/(.+)/)
  if (settingsMatch) {
    // 2026-10-29 D20-⑦：删除三个死映射（general「通用设置」/ extensions「扩展管理」/
    // admin 侧 logs「日志审计」）——对应路由从未存在或被删除，全站零引用（App.tsx 实况
    // 只有 llm/voice/tools/security/account/logs 六个子路由）。
    const tabLabels: Record<string, string> = {
      llm: 'LLM 配置',
      voice: '语音引擎',
      security: '安全',
      account: '账号与数据',
      tools: '工具仪表盘',
      logs: '日志',
    }
    return [
      { label: '系统设置', to: '/settings/llm' },
      { label: tabLabels[settingsMatch[1]] || settingsMatch[1] },
    ]
  }

  // /admin/*
  const adminMatch = pathname.match(/^\/admin\/(.+)/)
  if (adminMatch) {
    const adminTabLabels: Record<string, string> = {
      users: '用户管理',
      providers: '供应商管理',
    }
    return [
      { label: '管理后台', to: '/admin/users' },
      { label: adminTabLabels[adminMatch[1]] || adminMatch[1] },
    ]
  }

  // /roles
  if (pathname === '/roles') return [{ label: '角色' }]

  // /templates
  if (pathname === '/templates') return [{ label: '角色' }]

  // /psych
  if (pathname === '/psych') return [{ label: '角色' }]

  // /roles/create
  if (pathname === '/roles/create') return [{ label: '角色配置', to: '/roles' }, { label: '创建角色' }]

  // /roles/:roleId/*
  const roleMatch = pathname.match(/^\/roles\/([^/]+)/)
  if (roleMatch) {
    const roleId = roleMatch[1]
    const char = characters.find((c: UnifiedCharacter) => c.id === roleId || c.name === roleId)
    const roleLabel = char ? char.name : roleId
    const tabInPath = pathname.split('/').pop() || ''
    const roleTabLabels: Record<string, string> = {
      settings: '角色设置',
      status: '状态中心',
      storyline: '剧情线',
    }
    const tabLabel = roleTabLabels[tabInPath]
    return [
      { label: '角色配置', to: '/roles' },
      { label: roleLabel, to: `/roles/${roleId}/settings` },
      ...(tabLabel ? [{ label: tabLabel }] : []),
    ]
  }

  return [{ label: '未知页面' }]
}

export default function Breadcrumb() {
  const crumbs = useBreadcrumbs()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const [loggingOut, setLoggingOut] = useState(false)

  const handleLogout = async () => {
    if (loggingOut) return
    setLoggingOut(true)
    try {
      await logout()
      navigate('/login', { replace: true })
    } finally {
      setLoggingOut(false)
    }
  }

  return (
    <>
      <nav className="mat-floating border-x-0 border-t-0 rounded-none px-4 sm:px-6 py-3 flex items-center gap-2 text-sm" aria-label="面包屑导航">
        {/* 移动端汉堡按钮：唤起全量导航抽屉（入口与桌面 Sidebar 一致） */}
        <button
          onClick={() => setDrawerOpen(true)}
          aria-label="打开菜单"
          aria-expanded={drawerOpen}
          className="md:hidden -ml-2 mr-1 w-11 h-11 shrink-0 flex items-center justify-center rounded-lg text-gray-500 hover:text-gray-700 hover:bg-white/50 transition-colors"
        >
          <Menu className="w-5 h-5" />
        </button>
        <div className="flex items-center gap-2 min-w-0 flex-1 overflow-hidden">
          {crumbs.map((crumb, i) => {
            const isLast = i === crumbs.length - 1
            return (
              <span key={crumb.label} className="flex items-center gap-1 shrink-0">
                {i > 0 && <span className="text-gray-400">/</span>}
                {crumb.to && !isLast ? (
                  <Link to={crumb.to} className="text-gray-400 hover:text-primary-500 transition-colors">
                    {crumb.label}
                  </Link>
                ) : (
                  <span className={isLast ? 'text-gray-700 font-medium' : 'text-gray-400'}>
                    {crumb.label}
                  </span>
                )}
              </span>
            )
          })}
        </div>
        {user && (
          <div className="flex items-center gap-1 shrink-0">
            <span className="hidden sm:flex items-center gap-1.5 text-xs text-gray-500 max-w-[10rem] truncate">
              <UserRound className="w-3.5 h-3.5 shrink-0" aria-hidden />
              {user.display_name || user.username}
            </span>
            <button
              type="button"
              onClick={() => void handleLogout()}
              aria-label="退出登录"
              title="退出登录"
              disabled={loggingOut}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs text-gray-500 hover:text-red-500 hover:bg-red-50/60 transition-colors disabled:opacity-50"
            >
              <LogOut className="w-3.5 h-3.5" aria-hidden />
              <span className="hidden sm:inline">退出登录</span>
            </button>
          </div>
        )}
      </nav>
      <MobileDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)} />
    </>
  )
}

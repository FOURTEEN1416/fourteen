import { useLocation, Link } from 'react-router-dom'
import { useUnifiedCharacters } from '../../hooks/useQueries'
import type { UnifiedCharacter } from '../../types/api'

interface Crumb {
  label: string
  to?: string
}

function useBreadcrumbs(): Crumb[] {
  const { pathname } = useLocation()
  const { data } = useUnifiedCharacters()
  const characters = data?.characters ?? []

  // /wechat
  if (pathname === '/wechat') return [{ label: '微信连接' }]

  // /settings/*
  const settingsMatch = pathname.match(/^\/settings\/(.+)/)
  if (settingsMatch) {
    const tabLabels: Record<string, string> = {
      general: '通用设置',
      llm: 'LLM 配置',
      voice: '语音引擎',
      security: '安全',
      extensions: '扩展管理',
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
      logs: '日志审计',
    }
    return [
      { label: '管理后台', to: '/admin/users' },
      { label: adminTabLabels[adminMatch[1]] || adminMatch[1] },
    ]
  }

  // /roles
  if (pathname === '/roles') return [{ label: '角色配置' }]

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

  return (
    <nav className="glass-card border-b border-white/30 px-6 py-3 flex items-center gap-2 text-sm" aria-label="面包屑导航">
      {crumbs.map((crumb, i) => {
        const isLast = i === crumbs.length - 1
        return (
          <span key={crumb.label} className="flex items-center gap-1">
            {i > 0 && <span className="text-gray-300">/</span>}
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
    </nav>
  )
}

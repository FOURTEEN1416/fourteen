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
  if (pathname === '/wechat') return [{ label: '微信控制台' }]

  // /users
  if (pathname === '/users') return [{ label: '用户管理' }]

  // /settings/*
  const settingsMatch = pathname.match(/^\/settings\/(.+)/)
  if (settingsMatch) {
    const tabLabels: Record<string, string> = {
      general: '通用设置',
      llm: 'LLM 配置',
      voice: '语音引擎',
      security: '安全',
      extensions: '扩展管理',
      logs: '日志',
    }
    return [
      { label: '系统设置', to: '/settings/llm' },
      { label: tabLabels[settingsMatch[1]] || settingsMatch[1] },
    ]
  }

  // /users/:userId/...
  const userMatch = pathname.match(/^\/users\/([^/]+)/)
  if (!userMatch) return [{ label: '未知页面' }]

  const userId = userMatch[1]

  // Try to find a character with this userId to get a name
  // For user-level pages, we'll use a generic label since we don't have user names in context
  const userCrumbs: Crumb[] = [
    { label: '用户管理', to: '/users' },
    { label: userId, to: `/users/${userId}` },
  ]

  const roleMatch = pathname.match(/\/roles\/([^/]+)/)
  if (!roleMatch) return userCrumbs

  const roleId = roleMatch[1]
  const char = characters.find((c: UnifiedCharacter) => c.id === roleId || c.name === roleId)

  if (pathname.includes('/roles/create')) {
    return [...userCrumbs, { label: '创建角色' }]
  }

  const tabInPath = pathname.split('/').pop() || ''
  const roleTabLabels: Record<string, string> = {
    settings: '角色设置',
    status: '状态中心',
    storyline: '剧情时间线',
  }

  const roleLabel = char ? char.name : roleId
  const tabLabel = roleTabLabels[tabInPath] || tabInPath

  if (tabLabel) {
    return [...userCrumbs, { label: `${roleLabel} · ${tabLabel}` }]
  }

  return [...userCrumbs, { label: roleLabel }]
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

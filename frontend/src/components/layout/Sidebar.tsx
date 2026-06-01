import { useState } from 'react'
import { NavLink, useLocation, Link } from 'react-router-dom'
import {
  MessageCircle, Users, PanelLeftClose, PanelLeft,
  Sparkles, Mic, Shield, FileText,
  Bot, Settings, Activity, Plus, ChevronDown, ChevronRight,
  User, Power,
} from 'lucide-react'
import { useChatStore } from '../../store/chatStore'
import { useAuthStore } from '../../store/authStore'
import { useCharacterBuilderStore } from '../../store/characterBuilderStore'

// ── Global nav items ──

interface NavItem {
  to: string
  icon: React.ComponentType<{ className?: string }>
  label: string
  end?: boolean
}

interface NavGroup {
  label: string
  items: NavItem[]
}

function buildGlobalNavGroups(isAdmin: boolean): NavGroup[] {
  return [
    {
      label: '微信接入',
      items: [
        { to: '/wechat', icon: MessageCircle, label: '微信控制台' },
      ],
    },
    {
      label: '用户管理',
      items: [
        { to: '/users', icon: Users, label: '用户列表' },
      ],
    },
    // 管理后台 — 仅 admin 可见
    ...(isAdmin ? [{
      label: '管理后台',
      items: [
        { to: '/admin/users', icon: Shield, label: '用户管理' },
        { to: '/admin/logs', icon: FileText, label: '日志审计' },
        { to: '/admin/config', icon: Settings, label: '系统配置' },
      ],
    }] : []),
    {
      label: '系统设置',
      items: [
        { to: '/settings/llm', icon: Sparkles, label: 'LLM 配置' },
        { to: '/settings/voice', icon: Mic, label: '语音引擎' },
        { to: '/settings/tools', icon: Power, label: '工具仪表盘' },
        { to: '/settings/security', icon: Shield, label: '安全' },
        { to: '/settings/logs', icon: FileText, label: '日志' },
      ],
    },
  ]
}

// ── Role-level nav items ──

const roleNavItems = [
  { to: 'settings', icon: Settings, label: '角色设置' },
  { to: 'status', icon: Activity, label: '状态中心' },
  { to: 'storyline', icon: FileText, label: '剧情线' },
]

// ── Helpers ──

function useRouteLevel(): {
  level: 'global' | 'user' | 'role'
  userId?: string
  roleId?: string
  isCreatePage?: boolean
} {
  const { pathname } = useLocation()
  const roleMatch = pathname.match(/^\/users\/([^/]+)\/roles\/([^/]+)/)
  if (roleMatch) {
    const rId = roleMatch[2]
    const isCreate = rId === 'create'
    return { level: isCreate ? 'role' : 'role', userId: roleMatch[1], roleId: isCreate ? '' : rId, isCreatePage: isCreate }
  }
  const userMatch = pathname.match(/^\/users\/([^/]+)/)
  if (userMatch) return { level: 'user', userId: userMatch[1] }
  return { level: 'global' }
}

// ── Persona Card (in sidebar for role pages) ──

function RolePersonaCard({ roleId, isCreatePage }: { roleId: string; isCreatePage: boolean }) {
  const builderPersona = useCharacterBuilderStore((s) => s.persona)
  const builderHasContent = useCharacterBuilderStore((s) => s.hasContent)

  // On create page, show builder state
  if (isCreatePage) {
    const p = builderPersona
    const hasContent = builderHasContent
    return (
      <div className="rounded-xl bg-white/60 border border-white/30 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <div className={`w-8 h-8 rounded-full bg-gradient-to-br flex items-center justify-center ${hasContent ? 'from-primary-300 to-accent-300' : 'from-primary-200 to-accent-200'}`}>
            <Bot className={`w-4 h-4 ${hasContent ? 'text-primary-700' : 'text-primary-600'}`} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-gray-700 truncate">
              {hasContent && p?.name ? p.name : '角色 #create'}
            </p>
            <p className="text-[10px] text-gray-400">
              {hasContent ? (p?.description?.slice(0, 20) || '正在构建...') : '身份 · 背景 · 性格'}
            </p>
          </div>
        </div>
        {hasContent && p && (
          <>
            {p.anchors.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {p.anchors.slice(0, 5).map(a => (
                  <span key={a} className="px-1.5 py-0.5 rounded-md bg-primary-50 text-[10px] text-primary-600 font-medium">{a}</span>
                ))}
              </div>
            )}
            {p.personality && Object.entries(p.personality).filter(([, v]) => v !== 0.5 && v !== 0.3).length > 0 && (
              <div className="space-y-1">
                {Object.entries(p.personality).filter(([, v]) => v !== 0.5 && v !== 0.3).slice(0, 3).map(([key, val]) => (
                  <div key={key} className="flex items-center gap-1.5">
                    <span className="text-[10px] text-gray-500 w-8">{TRAIT_LABELS[key] || key}</span>
                    <div className="flex-1 h-1 rounded-full bg-gray-100">
                      <div className="h-full rounded-full bg-primary-400" style={{ width: `${val * 100}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    )
  }

  // On settings/status/storyline, show role info (placeholder - will be loaded from API)
  const decodedId = roleId ? decodeURIComponent(roleId) : ''
  return (
    <div className="rounded-xl bg-white/60 border border-white/30 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-accent-300 to-rose-300 flex items-center justify-center">
          <Bot className="w-4 h-4 text-accent-700" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-gray-700 truncate">角色 #{decodedId}</p>
          <p className="text-[10px] text-gray-400">身份 · 背景 · 性格</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-1">
        <span className="px-1.5 py-0.5 rounded-md bg-gray-100/60 text-[10px] text-gray-500">温柔</span>
        <span className="px-1.5 py-0.5 rounded-md bg-gray-100/60 text-[10px] text-gray-500">细腻</span>
      </div>
    </div>
  )
}

const TRAIT_LABELS: Record<string, string> = {
  warmth: '温暖', playfulness: '俏皮', independence: '独立', jealousy: '吃醋', stubbornness: '固执',
}

// ── Main Component ──

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const [rolesExpanded, setRolesExpanded] = useState(true)
  const isConnected = useChatStore((s) => s.isConnected)
  const { user } = useAuthStore()
  const isAdmin = user?.role === 'admin'
  const globalNavGroups = buildGlobalNavGroups(isAdmin)
  const { level, userId, roleId, isCreatePage } = useRouteLevel()

  const atRoleLevel = level === 'role' && userId
  // showRoleBar is derived from route level — used implicitly by layout
  void (atRoleLevel || (level === 'user' && !roleId))

  return (
    <aside
      className={`hidden lg:flex flex-col shrink-0 transition-all duration-300 border-r border-white/20
        bg-white/60 backdrop-blur-2xl ${collapsed ? 'w-14' : 'w-52'}`}
    >
      {/* ── Header ── */}
      <div className={`flex items-center h-14 border-b border-white/20
        ${collapsed ? 'justify-center px-0' : 'px-4 justify-between'}`}>
        {!collapsed && (
          <Link to="/wechat" className="text-sm font-semibold bg-gradient-to-r from-primary-500 to-accent-500 bg-clip-text text-transparent hover:opacity-80 transition-opacity">
            十四
          </Link>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="text-gray-400 hover:text-gray-600 transition-colors"
          title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        >
          {collapsed ? <PanelLeft className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
        </button>
      </div>

      {/* ── Navigation ── */}
      <nav className="flex-1 overflow-y-auto py-3 space-y-3">
        {/* Back to global when in user/role level */}
        {(level === 'user' || level === 'role') && !collapsed && (
          <div className="px-3">
            <NavLink
              to="/users"
              end
              className={({ isActive }) =>
                `flex items-center gap-2 rounded-lg px-3 py-2 text-xs transition-all ${
                  isActive
                    ? 'bg-primary-50/80 text-primary-700 font-medium'
                    : 'text-gray-400 hover:text-gray-600 hover:bg-white/40'
                }`
              }
            >
              <ArrowLeftIcon className="w-3.5 h-3.5" />
              <span>返回用户列表</span>
            </NavLink>
          </div>
        )}

        {/* User context */}
        {(level === 'user' || level === 'role') && !collapsed && (
          <div className="px-3">
            <div className="flex items-center gap-2 px-1 py-1.5">
              <User className="w-4 h-4 text-primary-500" />
              <span className="text-sm font-medium text-gray-700 truncate">
                用户 #{userId}
              </span>
            </div>
          </div>
        )}

        {/* ═══ Create Role Button (between user and role functions) ═══ */}
        {atRoleLevel && !collapsed && (
          <div className="px-3">
            <Link
              to={`/users/${userId}/roles/create`}
              className={`flex items-center gap-2 rounded-lg px-3 py-2 text-xs transition-all ${
                isCreatePage
                  ? 'bg-primary-50/80 text-primary-700 font-medium shadow-sm'
                  : 'text-primary-600 hover:bg-primary-50/60'
              }`}
            >
              <Plus className="w-3.5 h-3.5" />
              <span>创建角色</span>
            </Link>
          </div>
        )}

        {/* ═══ ROLE LEVEL: Role functions + persona card + role list ═══ */}
        {atRoleLevel && !collapsed && (
          <>
            {/* Role function nav */}
            <div className="px-3 space-y-0.5">
              <div className="px-1 py-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                角色功能
              </div>
              {roleNavItems.map(({ to, icon: Icon, label }) => {
                const linkTo = isCreatePage
                  ? `/users/${userId}/roles/create/${to}`
                  : `/users/${userId}/roles/${roleId || 'create'}/${to}`
                return (
                  <NavLink
                    key={to}
                    to={linkTo}
                    end={to === 'settings'}
                    className={({ isActive }) =>
                      `flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs transition-all ${
                        isActive
                          ? 'bg-primary-50/80 text-primary-700 font-medium shadow-sm'
                          : 'text-gray-500 hover:text-gray-700 hover:bg-white/40'
                      }`
                    }
                  >
                    <Icon className="w-3.5 h-3.5 shrink-0" />
                    <span>{label}</span>
                  </NavLink>
                )
              })}
            </div>

            {/* Role persona card */}
            <div className="px-3 pt-1">
              <RolePersonaCard roleId={roleId || 'create'} isCreatePage={!!isCreatePage} />
            </div>

            {/* Character list (collapsible) */}
            <div className="px-3">
              <button
                onClick={() => setRolesExpanded(!rolesExpanded)}
                className="flex items-center gap-1.5 w-full px-1 py-1.5 text-[11px] font-semibold text-gray-400 uppercase tracking-wider hover:text-gray-600 transition-colors"
              >
                {rolesExpanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                角色列表
              </button>
              {rolesExpanded && (
                <div className="space-y-0.5 mt-0.5">
                  <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-gray-400">
                    <Bot className="w-3.5 h-3.5" />
                    <span>加载中...</span>
                  </div>
                </div>
              )}
            </div>
          </>
        )}

        {/* ═══ USER LEVEL (no role selected) ═══ */}
        {level === 'user' && !atRoleLevel && !collapsed && (
          <>
            <div className="px-3 space-y-0.5">
              <div className="px-1 py-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                角色管理
              </div>
              <Link
                to={`/users/${userId}/roles/create`}
                className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-primary-600 hover:bg-primary-50/60 transition-all"
              >
                <Plus className="w-3.5 h-3.5" />
                <span>创建角色</span>
              </Link>
            </div>
            <div className="px-3 space-y-0.5">
              <div className="px-1 py-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                我的角色
              </div>
              <div className="flex items-center gap-2 rounded-lg px-3 py-2 text-xs text-gray-400">
                <Bot className="w-3.5 h-3.5" />
                <span>暂无角色</span>
              </div>
            </div>
          </>
        )}

        {/* ═══ GLOBAL LEVEL ═══ */}
        {level === 'global' && (
          <>
            {globalNavGroups.map((group) => (
              <div key={group.label}>
                {!collapsed && (
                  <div className="px-4 py-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                    {group.label}
                  </div>
                )}
                <div className="space-y-0.5 px-1.5">
                  {group.items.map(({ to, icon: Icon, label, end }) => (
                    <NavLink
                      key={to}
                      to={to}
                      end={end}
                      className={({ isActive }) =>
                        [
                          'flex items-center gap-3 rounded-lg text-sm transition-all duration-200',
                          collapsed
                            ? 'justify-center w-10 h-10 mx-auto'
                            : 'px-3 py-2',
                          isActive
                            ? 'bg-primary-50/80 text-primary-700 font-medium shadow-sm'
                            : 'text-gray-500 hover:text-gray-700 hover:bg-white/40',
                        ].join(' ')
                      }
                      title={collapsed ? label : undefined}
                    >
                      <Icon className="w-4 h-4 shrink-0" />
                      {!collapsed && <span>{label}</span>}
                    </NavLink>
                  ))}
                </div>
              </div>
            ))}
          </>
        )}
      </nav>

      {/* ── Connection status ── */}
      {!collapsed && (
        <div className="px-4 py-3 border-t border-white/20">
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-400'}`} />
            {isConnected ? '已连接' : '未连接'}
          </div>
        </div>
      )}
    </aside>
  )
}

// ── Inline icon component ──
function ArrowLeftIcon({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  )
}

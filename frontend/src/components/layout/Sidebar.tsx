import { NavLink, Link } from 'react-router-dom'
import { useState } from 'react'
import { PanelLeftClose, PanelLeft } from 'lucide-react'
import { useChatStore } from '../../store/chatStore'
import { useAuthStore } from '../../store/authStore'
import { useActiveCharacter } from '../../hooks/useQueries'
import { buildGlobalNavGroups } from './navGroups'

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const isConnected = useChatStore((s) => s.isConnected)
  const { user } = useAuthStore()
  const isAdmin = user?.role === 'admin'
  const { activeCharacter } = useActiveCharacter()
  const activeRoleId = activeCharacter?.id
  const globalNavGroups = buildGlobalNavGroups(isAdmin, activeRoleId)

  return (
    <aside
      className={`hidden lg:flex flex-col shrink-0 transition-all duration-300 border-r border-white/20
        bg-white/60 backdrop-blur-2xl ${collapsed ? 'w-14' : 'w-52'}`}
    >
      {/* Header */}
      <div className={`flex items-center h-14 border-b border-white/20
        ${collapsed ? 'justify-center px-0' : 'px-4 justify-between'}`}>
        {!collapsed && (
          <Link to="/wechat" className="text-sm font-semibold bg-gradient-to-r from-pink-500 via-blue-500 to-green-500 bg-clip-text text-transparent hover:opacity-80 transition-opacity">
            唯一的你——十四
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

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 space-y-3">
        {globalNavGroups.map((group) => (
          <div key={group.label}>
            {!collapsed && (
              <div className="px-4 py-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                {group.label}
              </div>
            )}
            <div className="space-y-0.5 px-1.5">
              {group.items.map(({ to, icon: Icon, label, end, badge }) => (
                <NavLink
                  key={label + to}
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    [
                      'nav-item flex items-center gap-3 rounded-lg text-sm transition-all duration-200',
                      collapsed ? 'justify-center w-10 h-10 mx-auto' : 'px-3 py-2',
                      isActive
                        ? 'active text-primary-700 font-medium'
                        : 'text-gray-500 hover:text-gray-700',
                    ].join(' ')
                  }
                  title={collapsed ? label : undefined}
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  {!collapsed && (
                    <>
                      <span>{label}</span>
                      {badge && (
                        <span className="ml-auto text-[9px] rounded px-1 tag-yellow">
                          {badge}
                        </span>
                      )}
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* Connection status */}
      {!collapsed && (
        <div className="px-4 py-3 border-t border-white/20">
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <div className={`w-1.5 h-1.5 rounded-full pulse-ring ${isConnected ? 'bg-green-400' : 'bg-red-400'}`} />
            {isConnected ? '已连接' : '未连接'}
          </div>
        </div>
      )}
    </aside>
  )
}

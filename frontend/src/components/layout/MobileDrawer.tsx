import { useEffect } from 'react'
import { NavLink } from 'react-router-dom'
import { X } from 'lucide-react'
import { useChatStore } from '../../store/chatStore'
import { useAuthStore } from '../../store/authStore'
import { useActiveCharacter } from '../../hooks/useQueries'
import { buildGlobalNavGroups } from './navGroups'

interface MobileDrawerProps {
  open: boolean
  onClose: () => void
}

/** 移动端全量导航抽屉（<lg）：由 Breadcrumb 顶栏汉堡按钮唤起，入口与桌面 Sidebar 完全一致 */
export default function MobileDrawer({ open, onClose }: MobileDrawerProps) {
  const isConnected = useChatStore((s) => s.isConnected)
  const { user } = useAuthStore()
  const isAdmin = user?.role === 'admin'
  const { activeCharacter } = useActiveCharacter()
  const globalNavGroups = buildGlobalNavGroups(isAdmin, activeCharacter?.id)

  // Escape 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  return (
    <div
      className={`lg:hidden fixed inset-0 z-50 ${open ? '' : 'pointer-events-none'}`}
      aria-hidden={!open}
    >
      {/* 遮罩 */}
      <div
        className={`absolute inset-0 bg-black/40 backdrop-blur-sm transition-opacity duration-300 ${
          open ? 'opacity-100' : 'opacity-0'
        }`}
        onClick={onClose}
      />
      {/* 滑出面板 */}
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="导航菜单"
        className={`absolute left-0 top-0 bottom-0 w-72 max-w-[85vw] flex flex-col safe-area-x
          bg-white/80 backdrop-blur-2xl border-r border-white/40 shadow-xl
          transition-transform duration-300 ease-out ${open ? 'translate-x-0' : '-translate-x-full'}`}
      >
        {/* Header */}
        <div className="flex items-center justify-between h-14 px-4 border-b border-white/30 shrink-0">
          <span className="text-sm font-semibold bg-gradient-to-r from-pink-500 via-blue-500 to-green-500 bg-clip-text text-transparent">
            唯一的你——十四
          </span>
          <button
            onClick={onClose}
            aria-label="关闭菜单"
            className="w-11 h-11 -mr-2 flex items-center justify-center text-gray-400 hover:text-gray-600 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto py-3 space-y-3">
          {globalNavGroups.map((group) => (
            <div key={group.label}>
              <div className="px-4 py-1 text-[11px] font-semibold text-gray-400 uppercase tracking-wider">
                {group.label}
              </div>
              <div className="space-y-0.5 px-2">
                {group.items.map(({ to, icon: Icon, label, end, badge }) => (
                  <NavLink
                    key={label + to}
                    to={to}
                    end={end}
                    onClick={onClose}
                    className={({ isActive }) =>
                      [
                        'flex items-center gap-3 rounded-lg text-sm min-h-[44px] px-3 transition-colors',
                        isActive
                          ? 'active text-primary-700 font-medium bg-white/60'
                          : 'text-gray-500 hover:text-gray-700 hover:bg-white/40',
                      ].join(' ')
                    }
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    <span>{label}</span>
                    {badge && (
                      <span className="ml-auto text-[9px] rounded px-1 tag-yellow">{badge}</span>
                    )}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        {/* Footer：用户 + 连接状态 */}
        <div className="px-4 py-3 border-t border-white/30 space-y-1 shrink-0">
          {user && (
            <div className="text-xs text-gray-500 truncate">{user.email}</div>
          )}
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <div className={`w-1.5 h-1.5 rounded-full pulse-ring ${isConnected ? 'bg-green-400' : 'bg-red-400'}`} />
            {isConnected ? '已连接' : '未连接'}
          </div>
        </div>
      </aside>
    </div>
  )
}

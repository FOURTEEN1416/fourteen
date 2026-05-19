import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, Heart, Brain, GraduationCap, Smartphone,
  Settings, FileText, Shield, PanelLeftClose, PanelLeft,
} from 'lucide-react'
import { useChatStore } from '../../store/chatStore'

const navGroups = [
  {
    label: '管理',
    items: [
      { to: '/', icon: LayoutDashboard, label: '仪表盘', end: true },
      { to: '/training', icon: GraduationCap, label: '克隆工作台' },
      { to: '/channels', icon: Smartphone, label: '通道管理' },
    ],
  },
  {
    label: '监控',
    items: [
      { to: '/logs', icon: FileText, label: '日志' },
      { to: '/persona', icon: Heart, label: '人设' },
      { to: '/memory', icon: Brain, label: '记忆' },
    ],
  },
  {
    label: '系统',
    items: [
      { to: '/settings', icon: Settings, label: '设置' },
      { to: '/admin', icon: Shield, label: '管理' },
    ],
  },
]

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const isConnected = useChatStore((s) => s.isConnected)

  return (
    <aside className={`hidden lg:flex flex-col bg-slate-900/50 border-r border-slate-800 shrink-0 transition-all duration-200 ${collapsed ? 'w-14' : 'w-52'}`}>
      {/* Brand + toggle */}
      <div className={`flex items-center h-14 border-b border-slate-800/50 ${collapsed ? 'justify-center px-0' : 'px-4 justify-between'}`}>
        {!collapsed && <span className="text-sm font-semibold text-slate-100">小暖</span>}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="text-slate-500 hover:text-slate-300 transition-colors"
          title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        >
          {collapsed ? <PanelLeft className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3 space-y-4">
        {navGroups.map((group) => (
          <div key={group.label}>
            {!collapsed && (
              <div className="px-4 py-1 text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
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
                      'flex items-center gap-3 rounded-lg text-sm transition-colors',
                      collapsed
                        ? 'justify-center w-10 h-10 mx-auto'
                        : 'px-3 py-2',
                      isActive
                        ? 'bg-primary-600/15 text-primary-300 font-medium'
                        : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/30',
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
      </nav>

      {/* Connection status */}
      {!collapsed && (
        <div className="px-4 py-3 border-t border-slate-800/50">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-400'}`} />
            {isConnected ? '已连接' : '未连接'}
          </div>
        </div>
      )}
    </aside>
  )
}

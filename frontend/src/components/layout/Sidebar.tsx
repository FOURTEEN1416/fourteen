import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, MessageCircle, Users, Heart, PenTool, GraduationCap,
  Database, Smartphone, Activity, BarChart3, Brain, Sticker,
  Settings, Shield, FileText, PanelLeftClose, PanelLeft,
  ShieldAlert, BookOpen, Puzzle, Star,
} from 'lucide-react'
import { useChatStore } from '../../store/chatStore'

const navGroups = [
  {
    label: '对话',
    items: [
      { to: '/chat', icon: MessageCircle, label: '聊天', end: false },
    ],
  },
  {
    label: '总览',
    items: [
      { to: '/', icon: LayoutDashboard, label: '仪表盘', end: true },
    ],
  },
  {
    label: '角色与人设',
    items: [
      { to: '/characters', icon: Users, label: '角色管理', end: false },
      { to: '/persona', icon: Heart, label: '人设档案', end: false },
      { to: '/persona-editor', icon: PenTool, label: '人设编辑', end: false },
    ],
  },
  {
    label: '克隆训练',
    items: [
      { to: '/training', icon: GraduationCap, label: '克隆工作台', end: false },
      { to: '/clone-data', icon: Database, label: '数据管理', end: false },
    ],
  },
  {
    label: '监控分析',
    items: [
      { to: '/monitor', icon: Activity, label: '情感监控', end: false },
      { to: '/stats', icon: BarChart3, label: '对话统计', end: false },
      { to: '/memory', icon: Brain, label: '记忆浏览', end: false },
      { to: '/stickers', icon: Sticker, label: '表情包', end: false },
      { to: '/psych', icon: Brain, label: '心理画像', end: false },
      { to: '/favorites', icon: Star, label: '收藏记忆', end: false },
      { to: '/knowledge', icon: BookOpen, label: '知识库', end: false },
    ],
  },
  {
    label: '用户',
    items: [
      { to: '/users', icon: Users, label: '用户管理', end: false },
    ],
  },
  {
    label: '通道',
    items: [
      { to: '/channels', icon: Smartphone, label: '通道管理', end: false },
    ],
  },
  {
    label: '系统',
    items: [
      { to: '/settings', icon: Settings, label: '设置', end: false },
      { to: '/admin', icon: Shield, label: '管理面板', end: false },
      { to: '/safety', icon: ShieldAlert, label: '安全面板', end: false },
      { to: '/extensions', icon: Puzzle, label: '扩展管理', end: false },
      { to: '/logs', icon: FileText, label: '日志', end: false },
    ],
  },
]

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const isConnected = useChatStore((s) => s.isConnected)

  return (
    <aside className={`hidden lg:flex flex-col bg-white border-r border-gray-200 shrink-0 transition-all duration-200 ${collapsed ? 'w-14' : 'w-52'}`}>
      <div className={`flex items-center h-14 border-b border-gray-200/50 ${collapsed ? 'justify-center px-0' : 'px-4 justify-between'}`}>
        {!collapsed && <span className="text-sm font-semibold text-gray-800">十四</span>}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="text-gray-400 hover:text-gray-600 transition-colors"
          title={collapsed ? '展开侧边栏' : '收起侧边栏'}
        >
          {collapsed ? <PanelLeft className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto py-3 space-y-4">
        {navGroups.map((group) => (
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
                      'flex items-center gap-3 rounded-lg text-sm transition-colors',
                      collapsed
                        ? 'justify-center w-10 h-10 mx-auto'
                        : 'px-3 py-2',
                      isActive
                        ? 'bg-primary-100 text-primary-700 font-medium'
                        : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100',
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

      {!collapsed && (
        <div className="px-4 py-3 border-t border-gray-200/50">
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <div className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-400'}`} />
            {isConnected ? '已连接' : '未连接'}
          </div>
        </div>
      )}
    </aside>
  )
}

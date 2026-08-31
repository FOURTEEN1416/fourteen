import {
  MessageCircle, Sparkles, Mic, Shield, FileText,
  Settings, Activity, Library,
  User, Wrench, BarChart3, Server,
} from 'lucide-react'

export interface NavItem {
  to: string
  icon: React.ComponentType<{ className?: string }>
  label: string
  end?: boolean
  badge?: string
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

/** 全站导航清单（Sidebar 桌面侧栏与 MobileDrawer 移动抽屉共用，保证入口一致） */
export function buildGlobalNavGroups(isAdmin: boolean, activeRoleId?: string): NavGroup[] {
  return [
    {
      label: '连接',
      items: [{ to: '/wechat', icon: MessageCircle, label: '微信连接' }],
    },
    {
      label: '角色',
      items: [
        { to: '/roles', icon: Library, label: '角色配置' },
        ...(activeRoleId
          ? [
              { to: `/roles/${activeRoleId}/settings`, icon: Settings, label: '角色设置' },
              { to: `/roles/${activeRoleId}/status`, icon: Activity, label: '状态中心' },
              { to: `/roles/${activeRoleId}/storyline`, icon: BarChart3, label: '剧情线' },
            ]
          : []),
      ],
    },
    {
      label: '系统设置',
      items: [
        { to: '/settings/llm', icon: Sparkles, label: 'LLM 配置' },
        { to: '/settings/voice', icon: Mic, label: '语音引擎' },
        { to: '/settings/tools', icon: Wrench, label: '工具仪表盘' },
        { to: '/settings/security', icon: Shield, label: '安全' },
        { to: '/settings/logs', icon: FileText, label: '日志' },
      ],
    },
    {
      label: '管理后台',
      items: [
        ...(isAdmin ? [
          { to: '/admin/users', icon: User, label: '用户管理', badge: 'admin' },
          { to: '/admin/providers', icon: Server, label: '供应商管理', badge: 'admin' },
        ] : []),
      ],
    },
  ]
}

import {
  MessageCircle, Sparkles, Mic, Shield, FileText,
  Settings, Activity, Library, Brain, Wand2,
  User, Wrench, BarChart3, Server, Database, Compass,
} from 'lucide-react'

export interface NavItem {
  to: string
  icon: React.ComponentType<{ className?: string }>
  label: string
  end?: boolean
  badge?: string
  /** V2-3②（2026-10-29）：一句话说明——侧栏/抽屉副行渲染，消除「点了才知道是什么」 */
  desc?: string
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

/** 全站导航清单（Sidebar 桌面侧栏与 MobileDrawer 移动抽屉共用，保证入口一致） */
export function buildGlobalNavGroups(isAdmin: boolean, activeRoleId?: string): NavGroup[] {
  const groups: NavGroup[] = [
    {
      label: '连接',
      items: [{ to: '/wechat', icon: MessageCircle, label: '微信连接' }],
    },
    {
      label: '角色',
      items: [
        { to: '/roles', icon: Library, label: '角色配置' },
        { to: '/templates', icon: Wand2, label: '角色模板' },
        ...(activeRoleId
          ? [
              { to: `/roles/${activeRoleId}/settings`, icon: Settings, label: '角色设置' },
              { to: `/roles/${activeRoleId}/status`, icon: Activity, label: '状态中心' },
              { to: `/roles/${activeRoleId}/storyline`, icon: BarChart3, label: '剧情线' },
              { to: '/psych', icon: Brain, label: '心理画像' },
            ]
          : []),
      ],
    },
    {
      // V2-3①：旧名「系统设置」过重——6 项中 5 项实为个人数据/个性化
      // （BYOK/语音/偏好），与「这是系统管理面」的观感相悖。改「偏好与设置」。
      label: '偏好与设置',
      items: [
        { to: '/settings/account', icon: Database, label: '账号与数据', desc: '回复模式等个人偏好、数据导出' },
        { to: '/settings/llm', icon: Sparkles, label: 'LLM 配置', desc: '自带 API Key（BYOK）、测试连接' },
        { to: '/settings/voice', icon: Mic, label: '语音引擎', desc: '给角色选音色、克隆声音' },
        { to: '/settings/tools', icon: Wrench, label: '工具仪表盘', desc: '天气/提醒/搜索等能力状态' },
        { to: '/settings/security', icon: Shield, label: '安全', desc: '登录会话管理' },
        { to: '/settings/logs', icon: FileText, label: '日志', desc: '运行日志（普通用户仅见本人请求）' },
        { to: '/help', icon: Compass, label: '使用帮助', desc: '全部功能教学与导览重放' },
      ],
    },
    {
      label: '管理后台',
      items: isAdmin
        ? [
            { to: '/admin/users', icon: User, label: '用户管理', badge: 'admin' },
            { to: '/admin/providers', icon: Server, label: '供应商管理', badge: 'admin' },
          ]
        : [],
    },
  ]

  // 过滤掉空分组：非管理员时"管理后台"items 为空，不过滤会在桌面侧栏与
  // 移动抽屉里渲染出一个**只有标题、没有任何入口**的分组（视觉缺陷 + 误导）。
  return groups.filter((group) => group.items.length > 0)
}

import { NavLink } from 'react-router-dom'
import { MessageCircle, Library, Sparkles, Mic, Wrench, Shield, FileText, User } from 'lucide-react'
import { useAuthStore } from '../../store/authStore'

// 底部 tab 导航（<lg）：高频入口直达；全量入口（含角色子页/供应商管理）走 Breadcrumb 汉堡抽屉
const mobileItems = [
  { to: '/wechat', icon: MessageCircle, label: '微信' },
  { to: '/roles', icon: Library, label: '角色' },
  { to: '/settings/llm', icon: Sparkles, label: 'LLM' },
  { to: '/settings/voice', icon: Mic, label: '语音' },
  { to: '/settings/tools', icon: Wrench, label: '工具' },
  { to: '/settings/security', icon: Shield, label: '安全' },
  { to: '/settings/logs', icon: FileText, label: '日志' },
]

const adminItems = [
  { to: '/admin/users', icon: User, label: '管理' },
]

export default function MobileNav() {
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const items = isAdmin ? [...mobileItems, ...adminItems] : mobileItems

  return (
    <nav aria-label="底部导航" className="lg:hidden fixed bottom-0 left-0 right-0 z-40 bg-white/95 border-t border-gray-200 backdrop-blur-sm safe-area-bottom">
      <div className="flex overflow-x-auto">
        {items.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex flex-col items-center justify-center gap-0.5 py-2.5 px-3 min-w-[64px] min-h-[48px] text-[10px] transition-colors ${
                isActive
                  ? 'text-primary-300'
                  : 'text-gray-400 hover:text-gray-600'
              }`
            }
          >
            <Icon className="w-5 h-5" />
            <span className="truncate">{label}</span>
          </NavLink>
        ))}
      </div>
    </nav>
  )
}

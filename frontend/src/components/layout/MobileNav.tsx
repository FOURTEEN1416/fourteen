import { NavLink } from 'react-router-dom'
import { MessageCircle, Library, Sparkles, Mic, Wrench, Shield, FileText } from 'lucide-react'

// 修复：导航项对齐 ia-plan-A.html 信息架构
// 路由清单：/wechat /roles /settings/* /admin/logs
const mobileItems = [
  { to: '/wechat', icon: MessageCircle, label: '微信' },
  { to: '/roles', icon: Library, label: '角色' },
  { to: '/settings/llm', icon: Sparkles, label: 'LLM' },
  { to: '/settings/voice', icon: Mic, label: '语音' },
  { to: '/settings/tools', icon: Wrench, label: '工具' },
  { to: '/settings/security', icon: Shield, label: '安全' },
  { to: '/admin/logs', icon: FileText, label: '日志' },
]

export default function MobileNav() {
  return (
    <nav className="lg:hidden fixed bottom-0 left-0 right-0 z-50 bg-white/95 border-t border-gray-200 backdrop-blur-sm safe-area-bottom">
      <div className="flex overflow-x-auto">
        {mobileItems.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              `flex flex-col items-center gap-0.5 py-2 px-3 min-w-[60px] text-[10px] transition-colors ${
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

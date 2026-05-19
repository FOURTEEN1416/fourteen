import { NavLink } from 'react-router-dom'
import { LayoutDashboard, Heart, Brain, GraduationCap, Smartphone, Settings, FileText, Shield } from 'lucide-react'

const mobileItems = [
  { to: '/', icon: LayoutDashboard, label: '仪表盘', end: true },
  { to: '/training', icon: GraduationCap, label: '克隆' },
  { to: '/channels', icon: Smartphone, label: '通道' },
  { to: '/persona', icon: Heart, label: '人设' },
  { to: '/memory', icon: Brain, label: '记忆' },
  { to: '/settings', icon: Settings, label: '设置' },
  { to: '/logs', icon: FileText, label: '日志' },
  { to: '/admin', icon: Shield, label: '管理' },
]

export default function MobileNav() {
  return (
    <nav className="lg:hidden fixed bottom-0 left-0 right-0 z-50 bg-slate-900/95 border-t border-slate-800 backdrop-blur-sm safe-area-bottom">
      <div className="flex overflow-x-auto">
        {mobileItems.map(({ to, icon: Icon, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              `flex flex-col items-center gap-0.5 py-2 px-3 min-w-[60px] text-[10px] transition-colors ${
                isActive
                  ? 'text-primary-300'
                  : 'text-slate-500 hover:text-slate-300'
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

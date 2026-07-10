import { Shield } from 'lucide-react'
import type { UserRole } from '../../api/admin'

// ════════════════════════════════════════════════════
//  常量
// ════════════════════════════════════════════════════

export const ROLES: UserRole[] = ['admin', 'editor', 'viewer']

export const ROLE_LABELS: Record<UserRole, string> = {
  admin: '管理员',
  editor: '编辑者',
  viewer: '观察者',
}

const ROLE_BADGE: Record<UserRole, string> = {
  admin: 'bg-rose-50 text-rose-600 ring-1 ring-rose-200/50',
  editor: 'bg-amber-50 text-amber-600 ring-1 ring-amber-200/50',
  viewer: 'bg-sky-50 text-sky-600 ring-1 ring-sky-200/50',
}

// ════════════════════════════════════════════════════
//  角色徽章
// ════════════════════════════════════════════════════

export function RoleBadge({ role }: { role: UserRole }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${ROLE_BADGE[role]}`}>
      <Shield className="w-3 h-3" />
      {ROLE_LABELS[role]}
    </span>
  )
}

// ════════════════════════════════════════════════════
//  状态指示器
// ════════════════════════════════════════════════════

export function ActiveBadge({ active }: { active: boolean }) {
  if (active) {
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-green-600 bg-green-50 px-2 py-0.5 rounded-full ring-1 ring-green-200/50">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
        活跃
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full ring-1 ring-gray-200/50">
      <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />
      停用
    </span>
  )
}

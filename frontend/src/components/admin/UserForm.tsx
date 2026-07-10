import { Mail, User, KeyRound } from 'lucide-react'
import { ROLES, ROLE_LABELS } from './UserBadges'
import type { AdminCreateUserRequest, AdminUpdateUserRequest, UserRole } from '../../api/admin'

// ════════════════════════════════════════════════════
//  表单验证（导出）
// ════════════════════════════════════════════════════

export function validateEmail(v: string): string | null {
  if (!v.trim()) return '邮箱不能为空'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return '邮箱格式不正确'
  return null
}

export function validateUsername(v: string): string | null {
  if (!v.trim()) return '用户名不能为空'
  if (v.length < 3) return '用户名至少 3 个字符'
  return null
}

export function validatePassword(v: string, required: boolean): string | null {
  if (required && !v) return '密码不能为空'
  if (v && v.length < 6) return '密码至少 6 个字符'
  return null
}

// ════════════════════════════════════════════════════
//  表单字段（含标签 + 错误提示）
// ════════════════════════════════════════════════════

export function FormField({
  label, children, error, required,
}: {
  label: string
  children: React.ReactNode
  error?: string | null
  required?: boolean
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-500 mb-1.5">
        {label}
        {required && <span className="text-rose-400 ml-0.5">*</span>}
      </label>
      {children}
      {error && <p className="mt-1 text-[11px] text-rose-500">{error}</p>}
    </div>
  )
}

// ════════════════════════════════════════════════════
//  Props
// ════════════════════════════════════════════════════

export interface UserFormProps {
  mode: 'create' | 'edit'
  formData: AdminCreateUserRequest | AdminUpdateUserRequest
  errors: Partial<Record<string, string>>
  saving: boolean
  onChange: (data: AdminCreateUserRequest | AdminUpdateUserRequest) => void
  onSubmit: () => void
  onCancel: () => void
}

// ════════════════════════════════════════════════════
//  组件
// ════════════════════════════════════════════════════

export default function UserForm({
  mode, formData, errors, saving, onChange, onSubmit, onCancel,
}: UserFormProps) {
  const data = formData as Record<string, unknown>
  const set = (key: string, value: unknown) => onChange({ ...formData, [key]: value } as AdminCreateUserRequest & AdminUpdateUserRequest)

  const isCreate = mode === 'create'

  // 当 mode=create 时强制 role 回退
  const currentRole = (data.role as UserRole) || ROLES[2]
  const currentActive = data.is_active !== undefined ? Boolean(data.is_active) : true

  return (
    <div className="space-y-4">
      <FormField label="邮箱" error={errors.email} required={isCreate}>
        <div className="relative">
          <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
          <input
            type="email"
            value={(data.email as string) ?? ''}
            onChange={e => set('email', e.target.value)}
            placeholder="user@example.com"
            className="w-full pl-9 pr-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
          />
        </div>
      </FormField>

      <div className="grid grid-cols-2 gap-3">
        <FormField label="用户名" error={errors.username} required={isCreate}>
          <div className="relative">
            <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
            <input
              type="text"
              value={(data.username as string) ?? ''}
              onChange={e => set('username', e.target.value)}
              placeholder="username"
              className="w-full pl-9 pr-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
            />
          </div>
        </FormField>

        <FormField label="显示名称" error={undefined}>
          <input
            type="text"
            value={(data.display_name as string) ?? ''}
            onChange={e => set('display_name', e.target.value)}
            placeholder="可选"
            className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
          />
        </FormField>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <FormField label="角色" error={undefined} required={isCreate}>
          <select
            value={currentRole}
            onChange={e => set('role', e.target.value as UserRole)}
            className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
          >
            {ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
          </select>
        </FormField>

        {!isCreate && (
          <FormField label="状态" error={undefined}>
            <label className="flex items-center gap-2 px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg cursor-pointer hover:bg-white/80 transition-colors">
              <input
                type="checkbox"
                checked={currentActive}
                onChange={e => set('is_active', e.target.checked)}
                className="w-4 h-4 rounded border-gray-300 text-primary-500 focus:ring-primary-300"
              />
              <span className="text-gray-600">{currentActive ? '活跃' : '停用'}</span>
            </label>
          </FormField>
        )}
      </div>

      <FormField label={isCreate ? '密码' : '新密码（留空保持不变）'} error={errors.password} required={isCreate}>
        <div className="relative">
          <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
          <input
            type="password"
            value={(data.password as string) ?? ''}
            onChange={e => set('password', e.target.value)}
            placeholder={isCreate ? '至少 6 个字符' : '不修改则留空'}
            className="w-full pl-9 pr-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
          />
        </div>
      </FormField>

      <div className="flex gap-2 justify-end pt-2">
        <button
          onClick={onCancel}
          className="px-4 py-2 text-xs text-gray-500 bg-gray-100/80 rounded-lg hover:bg-gray-200/60 transition-colors"
        >
          取消
        </button>
        <button
          onClick={onSubmit}
          disabled={saving}
          className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 disabled:bg-primary-300 rounded-lg transition-colors"
        >
          {saving && <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
          {isCreate ? '创建' : '保存'}
        </button>
      </div>
    </div>
  )
}

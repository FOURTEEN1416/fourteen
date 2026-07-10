import { useMemo } from 'react'
import {
  Edit, Trash2, ChevronLeft, ChevronRight,
  ArrowUpDown, ArrowUp, ArrowDown, User,
} from 'lucide-react'
import { Skeleton, EmptyState } from '../shared'
import { RoleBadge, ActiveBadge } from './UserBadges'
import type { AdminUser } from '../../api/admin'

// ════════════════════════════════════════════════════
//  类型导出
// ════════════════════════════════════════════════════

export type SortField = 'id' | 'email' | 'username' | 'display_name' | 'role' | 'is_active' | 'created_at'
export type SortDir = 'asc' | 'desc'

// ════════════════════════════════════════════════════
//  常量
// ════════════════════════════════════════════════════

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const

// ════════════════════════════════════════════════════
//  列排序指示器
// ════════════════════════════════════════════════════

function SortIcon({ field, sortField, sortDir }: { field: SortField; sortField: SortField | null; sortDir: SortDir }) {
  if (field !== sortField) return <ArrowUpDown className="w-3 h-3 text-gray-300 group-hover:text-gray-500 transition-colors" />
  return sortDir === 'asc'
    ? <ArrowUp className="w-3 h-3 text-primary-500" />
    : <ArrowDown className="w-3 h-3 text-primary-500" />
}

// ════════════════════════════════════════════════════
//  格式化工具
// ════════════════════════════════════════════════════

function formatDateTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatRelative(iso: string | null): string {
  if (!iso) return '-'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return formatDateTime(iso)
}

// ════════════════════════════════════════════════════
//  表格列头（可排序）
// ════════════════════════════════════════════════════

function Th({
  children, sortable, field, current, dir, onSort, className,
}: {
  children: React.ReactNode
  sortable?: boolean
  field?: SortField
  current?: SortField | null
  dir?: SortDir
  onSort?: (f: SortField) => void
  className?: string
}) {
  if (!sortable || !field) {
    return <th className={`px-4 py-3 font-medium text-left ${className ?? ''}`}>{children}</th>
  }
  return (
    <th
      className="px-4 py-3 font-medium text-left cursor-pointer group select-none"
      onClick={() => onSort?.(field)}
    >
      <div className="flex items-center gap-1">
        {children}
        <SortIcon field={field} sortField={current ?? null} sortDir={dir ?? 'asc'} />
      </div>
    </th>
  )
}

// ════════════════════════════════════════════════════
//  Props
// ════════════════════════════════════════════════════

export interface UserTableProps {
  users: AdminUser[]
  total: number
  loading: boolean
  sortField: SortField | null
  sortDir: SortDir
  page: number
  pageSize: number
  hasFilter?: boolean
  onSort: (field: SortField) => void
  onPage: (page: number) => void
  onPageSize: (size: number) => void
  onEdit: (user: AdminUser) => void
  onDelete: (user: AdminUser) => void
  currentUserId?: number
}

// ════════════════════════════════════════════════════
//  组件
// ════════════════════════════════════════════════════

export default function UserTable({
  users, total, loading, sortField, sortDir, page, pageSize, hasFilter,
  onSort, onPage, onPageSize, onEdit, onDelete, currentUserId,
}: UserTableProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const pageNumbers = useMemo(() => {
    const pages: (number | '...')[] = []
    if (totalPages <= 7) {
      for (let i = 1; i <= totalPages; i++) pages.push(i)
    } else {
      pages.push(1)
      if (page > 3) pages.push('...')
      for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) pages.push(i)
      if (page < totalPages - 2) pages.push('...')
      pages.push(totalPages)
    }
    return pages
  }, [totalPages, page])

  // ═══ 加载骨架 ═══
  if (loading) {
    return (
      <div className="glass-card rounded-xl overflow-hidden">
        <div className="p-5 space-y-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex items-center gap-4">
              <Skeleton className="h-5 w-8" />
              <Skeleton className="h-5 flex-1" />
              <Skeleton className="h-5 w-24" />
              <Skeleton className="h-5 w-16" />
              <Skeleton className="h-5 w-16" />
              <Skeleton className="h-5 w-20" />
              <Skeleton className="h-5 w-8" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  // ═══ 空状态 ═══
  if (users.length === 0) {
    return (
      <div className="glass-card rounded-xl p-8">
        <EmptyState
          icon={<User className="w-10 h-10 text-gray-200" />}
          title={hasFilter ? '未找到匹配用户' : '暂无系统用户'}
          description={hasFilter ? '尝试修改搜索条件' : '新用户注册后将显示在这里'}
        />
      </div>
    )
  }

  return (
    <>
      {/* ── Desktop 表格 ── */}
      <div className="glass-card rounded-xl overflow-hidden hidden md:block">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/20 text-xs text-gray-400">
                <Th sortable field="id" current={sortField} dir={sortDir} onSort={onSort}>ID</Th>
                <Th sortable field="email" current={sortField} dir={sortDir} onSort={onSort}>邮箱</Th>
                <Th sortable field="username" current={sortField} dir={sortDir} onSort={onSort}>用户名</Th>
                <Th sortable field="display_name" current={sortField} dir={sortDir} onSort={onSort}>显示名称</Th>
                <Th sortable field="role" current={sortField} dir={sortDir} onSort={onSort}>角色</Th>
                <Th sortable field="is_active" current={sortField} dir={sortDir} onSort={onSort}>状态</Th>
                <Th sortable field="created_at" current={sortField} dir={sortDir} onSort={onSort}>创建时间</Th>
                <Th>最后登录</Th>
                <Th className="text-right">操作</Th>
              </tr>
            </thead>
            <tbody>
              {users.map((u, idx) => (
                <tr
                  key={u.id}
                  className="border-b border-white/10 hover:bg-white/40 transition-colors group"
                  style={{ animationDelay: `${idx * 30}ms` }}
                >
                  <td className="px-4 py-3 text-gray-500 font-mono text-xs">{u.id}</td>
                  <td className="px-4 py-3 text-gray-700 max-w-[200px] truncate" title={u.email}>{u.email}</td>
                  <td className="px-4 py-3 text-gray-700">{u.username}</td>
                  <td className="px-4 py-3 text-gray-700">{u.display_name || '-'}</td>
                  <td className="px-4 py-3"><RoleBadge role={u.role} /></td>
                  <td className="px-4 py-3"><ActiveBadge active={u.is_active} /></td>
                  <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">{formatDateTime(u.created_at)}</td>
                  <td className="px-4 py-3 text-gray-400 text-xs whitespace-nowrap">{formatRelative(u.last_login_at)}</td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={() => onEdit(u)}
                        className="p-1.5 rounded-lg text-gray-400 hover:text-primary-600 hover:bg-primary-50 transition-all"
                        title="编辑用户"
                      >
                        <Edit className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => onDelete(u)}
                        className="p-1.5 rounded-lg text-gray-400 hover:text-rose-600 hover:bg-rose-50 transition-all"
                        title="删除用户"
                        disabled={u.id === currentUserId}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Mobile 卡片列表 ── */}
      <div className="md:hidden space-y-3">
        {users.map(u => (
          <div key={u.id} className="glass-card rounded-xl p-4 space-y-3 animate-fade-in">
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-gray-800 truncate">{u.display_name || u.username}</span>
                  <span className="text-[10px] text-gray-400 font-mono shrink-0">#{u.id}</span>
                </div>
                <p className="text-xs text-gray-400 truncate mt-0.5">{u.email}</p>
              </div>
              <div className="flex gap-1 shrink-0">
                <button
                  onClick={() => onEdit(u)}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-primary-600 hover:bg-primary-50 transition-all"
                >
                  <Edit className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => onDelete(u)}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-rose-600 hover:bg-rose-50 transition-all"
                  disabled={u.id === currentUserId}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <RoleBadge role={u.role} />
              <ActiveBadge active={u.is_active} />
            </div>
            <div className="flex items-center justify-between text-[11px] text-gray-400 pt-1 border-t border-white/10">
              <span>创建于 {formatDateTime(u.created_at)}</span>
              <span>登录 {formatRelative(u.last_login_at)}</span>
            </div>
          </div>
        ))}
      </div>

      {/* ── 分页 ── */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 mt-4">
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <span>每页</span>
          <select
            value={pageSize}
            onChange={e => { onPageSize(Number(e.target.value)) }}
            className="px-2 py-1 bg-white/60 border border-white/30 rounded-lg text-gray-600 focus:outline-none"
          >
            {PAGE_SIZE_OPTIONS.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <span>条，共 {total} 条</span>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={() => onPage(Math.max(1, page - 1))}
            disabled={page <= 1}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/40 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
          >
            <ChevronLeft className="w-4 h-4" />
          </button>

          {pageNumbers.map((p, i) =>
            p === '...' ? (
              <span key={`ellipsis-${i}`} className="px-2 text-xs text-gray-300">...</span>
            ) : (
              <button
                key={p}
                onClick={() => onPage(p)}
                className={`min-w-[28px] h-7 text-xs font-medium rounded-lg transition-all ${
                  page === p
                    ? 'bg-primary-500 text-white shadow-sm'
                    : 'text-gray-500 hover:bg-white/40'
                }`}
              >
                {p}
              </button>
            ),
          )}

          <button
            onClick={() => onPage(Math.min(totalPages, page + 1))}
            disabled={page >= totalPages}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/40 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </>
  )
}

import { useState, useEffect, useMemo, useCallback } from 'react'
import { AnimatedPage, Skeleton, EmptyState, Modal, ConfirmDialog } from '../components/shared'
import {
  adminListUsers, adminCreateUser, adminUpdateUser, adminDeleteUser,
  type AdminUser, type UserRole,
  type AdminCreateUserRequest, type AdminUpdateUserRequest,
} from '../api/admin'
import { useAuthStore } from '../store/authStore'
import { useErrorStore } from '../store/errorStore'
import {
  Shield, UserPlus, Edit, Trash2, Search, X, ChevronLeft, ChevronRight,
  ArrowUpDown, ArrowUp, ArrowDown, Mail, User, KeyRound,
} from 'lucide-react'

// ════════════════════════════════════════════════════
//  常量
// ════════════════════════════════════════════════════

const ROLES: UserRole[] = ['admin', 'editor', 'viewer']
const ROLE_LABELS: Record<UserRole, string> = { admin: '管理员', editor: '编辑者', viewer: '观察者' }
const ROLE_BADGE: Record<UserRole, string> = {
  admin: 'bg-rose-50 text-rose-600 ring-1 ring-rose-200/50',
  editor: 'bg-amber-50 text-amber-600 ring-1 ring-amber-200/50',
  viewer: 'bg-sky-50 text-sky-600 ring-1 ring-sky-200/50',
}

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const
type SortField = 'id' | 'email' | 'username' | 'display_name' | 'role' | 'is_active' | 'created_at'
type SortDir = 'asc' | 'desc'

// ════════════════════════════════════════════════════
//  子组件：角色徽章
// ════════════════════════════════════════════════════

function RoleBadge({ role }: { role: UserRole }) {
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium ${ROLE_BADGE[role]}`}>
      <Shield className="w-3 h-3" />
      {ROLE_LABELS[role]}
    </span>
  )
}

// ════════════════════════════════════════════════════
//  子组件：状态指示器
// ════════════════════════════════════════════════════

function ActiveBadge({ active }: { active: boolean }) {
  return active
    ? <span className="inline-flex items-center gap-1 text-[11px] font-medium text-green-600 bg-green-50 px-2 py-0.5 rounded-full ring-1 ring-green-200/50">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
        活跃
      </span>
    : <span className="inline-flex items-center gap-1 text-[11px] font-medium text-gray-400 bg-gray-50 px-2 py-0.5 rounded-full ring-1 ring-gray-200/50">
        <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />
        停用
      </span>
}

// ════════════════════════════════════════════════════
//  Tooltip 列排序指示器
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
//  表单验证
// ════════════════════════════════════════════════════

function validateEmail(v: string): string | null {
  if (!v.trim()) return '邮箱不能为空'
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return '邮箱格式不正确'
  return null
}

function validateUsername(v: string): string | null {
  if (!v.trim()) return '用户名不能为空'
  if (v.length < 3) return '用户名至少 3 个字符'
  return null
}

function validatePassword(v: string, required: boolean): string | null {
  if (required && !v) return '密码不能为空'
  if (v && v.length < 6) return '密码至少 6 个字符'
  return null
}

// ════════════════════════════════════════════════════
//  主组件
// ════════════════════════════════════════════════════

export default function AdminUsersPage() {
  const { user } = useAuthStore()
  const addToast = useErrorStore(s => s.addToast)

  // ── 数据状态 ──
  const [users, setUsers] = useState<AdminUser[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // ── 查询参数 ──
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(20)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [roleFilter, setRoleFilter] = useState<string>('')

  // ── 排序 ──
  const [sortField, setSortField] = useState<SortField | null>('id')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // ── 弹窗状态 ──
  const [showCreate, setShowCreate] = useState(false)
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null)
  const [deletingUser, setDeletingUser] = useState<AdminUser | null>(null)
  const [saving, setSaving] = useState(false)

  // ── 表单状态（创建） ──
  const [createForm, setCreateForm] = useState<AdminCreateUserRequest>({
    email: '', username: '', password: '', display_name: '', role: 'viewer',
  })
  const [createErrors, setCreateErrors] = useState<Partial<Record<keyof AdminCreateUserRequest, string>>>({})

  // ── 表单状态（编辑） ──
  const [editForm, setEditForm] = useState<AdminUpdateUserRequest>({})
  const [editErrors, setEditErrors] = useState<Partial<Record<string, string>>>({})

  // ── 当前用户角色检查 ──
  const isAdmin = user?.role === 'admin'

  // ═════════════════════════════════════════════════
  //  数据加载
  // ═════════════════════════════════════════════════

  const fetchUsers = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await adminListUsers(page, pageSize, search || undefined, roleFilter || undefined)
      setUsers(res.users)
      setTotal(res.total)
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err as Error)?.message
        || '加载用户列表失败'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, search, roleFilter])

  // 挂载时拉数据（搜索/分页/筛选变化时重拉）
  /* eslint-disable react-hooks/set-state-in-effect -- data fetching on mount + filter change */
  useEffect(() => { fetchUsers() }, [fetchUsers])
  /* eslint-enable react-hooks/set-state-in-effect */

  // ── 搜索防抖 ──
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(searchInput)
      setPage(1)
    }, 400)
    return () => clearTimeout(timer)
  }, [searchInput])

  // ═════════════════════════════════════════════════
  //  排序逻辑
  // ═════════════════════════════════════════════════

  const sortedUsers = useMemo(() => {
    if (!sortField) return users
    return [...users].sort((a, b) => {
      const aVal = a[sortField]
      const bVal = b[sortField]
      if (aVal == null && bVal == null) return 0
      if (aVal == null) return 1
      if (bVal == null) return -1
      const cmp = typeof aVal === 'string' ? aVal.localeCompare(String(bVal)) : Number(aVal) - Number(bVal)
      return sortDir === 'asc' ? cmp : -cmp
    })
  }, [users, sortField, sortDir])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDir('asc')
    }
  }

  // ═════════════════════════════════════════════════
  //  CRUD 操作
  // ═════════════════════════════════════════════════

  const handleCreate = async () => {
    // 验证
    const errs: Partial<Record<keyof AdminCreateUserRequest, string>> = {}
    const emailErr = validateEmail(createForm.email)
    if (emailErr) errs.email = emailErr
    const userErr = validateUsername(createForm.username)
    if (userErr) errs.username = userErr
    const passErr = validatePassword(createForm.password, true)
    if (passErr) errs.password = passErr
    if (Object.keys(errs).length > 0) { setCreateErrors(errs); return }
    setCreateErrors({})

    setSaving(true)
    try {
      await adminCreateUser(createForm)
      addToast({ type: 'success', message: '用户创建成功' })
      setShowCreate(false)
      setCreateForm({ email: '', username: '', password: '', display_name: '', role: 'viewer' })
      fetchUsers()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '创建失败'
      addToast({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
  }

  const handleEdit = async () => {
    if (!editingUser) return
    const errs: Partial<Record<string, string>> = {}
    if (editForm.email) {
      const e = validateEmail(editForm.email)
      if (e) errs.email = e
    }
    if (editForm.password) {
      const p = validatePassword(editForm.password, false)
      if (p) errs.password = p
    }
    if (Object.keys(errs).length > 0) { setEditErrors(errs); return }
    setEditErrors({})

    setSaving(true)
    try {
      const cleanData: AdminUpdateUserRequest = {}
      if (editForm.email !== undefined) cleanData.email = editForm.email
      if (editForm.username !== undefined) cleanData.username = editForm.username
      if (editForm.display_name !== undefined) cleanData.display_name = editForm.display_name
      if (editForm.role !== undefined) cleanData.role = editForm.role
      if (editForm.is_active !== undefined) cleanData.is_active = editForm.is_active
      if (editForm.password) cleanData.password = editForm.password

      await adminUpdateUser(editingUser.id, cleanData)
      addToast({ type: 'success', message: '用户更新成功' })
      setEditingUser(null)
      setEditForm({})
      fetchUsers()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '更新失败'
      addToast({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!deletingUser) return
    setSaving(true)
    try {
      await adminDeleteUser(deletingUser.id)
      addToast({ type: 'success', message: `用户 ${deletingUser.display_name || deletingUser.username} 已删除` })
      setDeletingUser(null)
      // 如果当前页只剩一条且不是第一页，回退一页
      if (users.length === 1 && page > 1) setPage(p => p - 1)
      else fetchUsers()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '删除失败'
      addToast({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
  }

  // ── 编辑弹窗打开 ──
  const openEdit = (u: AdminUser) => {
    setEditingUser(u)
    setEditForm({
      email: u.email,
      username: u.username,
      display_name: u.display_name,
      role: u.role,
      is_active: u.is_active,
      password: '',
    })
    setEditErrors({})
  }

  // ═════════════════════════════════════════════════
  //  分页计算
  // ═════════════════════════════════════════════════

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

  // ═════════════════════════════════════════════════
  //  渲染
  // ═════════════════════════════════════════════════

  if (!isAdmin) {
    return (
      <AnimatedPage>
        <div className="bg-dynamic px-4 py-6 sm:px-6 lg:px-8 flex items-center justify-center min-h-[300px]">
          <EmptyState
            icon={<Shield className="w-12 h-12 text-rose-300" />}
            title="无权限访问"
            description="仅管理员可访问系统用户管理页面"
          />
        </div>
      </AnimatedPage>
    )
  }

  return (
    <AnimatedPage>
      <div className="bg-dynamic px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-7xl">
          {/* ═══ Header ═══ */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
            <div>
              <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                <Shield className="w-5 h-5 text-primary-500" />
                系统用户管理
              </h1>
              <p className="mt-0.5 text-sm text-gray-400">
                管理所有注册用户的角色和权限
                {!loading && (
                  <span className="ml-2 text-gray-300">
                    · 共 <span className="font-semibold text-gray-500">{total}</span> 位用户
                  </span>
                )}
              </p>
            </div>
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors shadow-sm"
            >
              <UserPlus className="w-4 h-4" />
              创建用户
            </button>
          </div>

          {/* ═══ 搜索 + 过滤 ═══ */}
          <div className="glass-card rounded-xl px-4 py-3 mb-4">
            <div className="flex flex-col sm:flex-row gap-3">
              {/* 搜索框 */}
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
                <input
                  type="text"
                  value={searchInput}
                  onChange={e => setSearchInput(e.target.value)}
                  placeholder="搜索邮箱、用户名或显示名称..."
                  className="w-full pl-9 pr-8 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 focus:border-primary-300 transition-all"
                />
                {searchInput && (
                  <button
                    onClick={() => { setSearchInput(''); setSearch(''); setPage(1) }}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-300 hover:text-gray-500 transition-colors"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>

              {/* 角色过滤 */}
              <select
                value={roleFilter}
                onChange={e => { setRoleFilter(e.target.value); setPage(1) }}
                className="px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
              >
                <option value="">全部角色</option>
                {ROLES.map(r => (
                  <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                ))}
              </select>
            </div>
          </div>

          {/* ═══ 错误状态 ═══ */}
          {error && !loading && (
            <div className="glass-card rounded-xl p-8">
              <EmptyState
                icon={<X className="w-10 h-10 text-rose-300" />}
                title="加载失败"
                description={error}
                action={
                  <button
                    onClick={fetchUsers}
                    className="px-4 py-2 text-xs font-medium text-primary-600 bg-primary-50 rounded-lg hover:bg-primary-100 transition-colors"
                  >
                    重试
                  </button>
                }
              />
            </div>
          )}

          {/* ═══ 表格 ═══ */}
          {loading ? (
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
          ) : sortedUsers.length === 0 ? (
            <div className="glass-card rounded-xl p-8">
              <EmptyState
                icon={<User className="w-10 h-10 text-gray-200" />}
                title={search || roleFilter ? '未找到匹配用户' : '暂无系统用户'}
                description={search || roleFilter ? '尝试修改搜索条件' : '新用户注册后将显示在这里'}
              />
            </div>
          ) : (
            <>
              {/* ── Desktop 表格 ── */}
              <div className="glass-card rounded-xl overflow-hidden hidden md:block">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-white/20 text-xs text-gray-400">
                        <Th sortable field="id" current={sortField} dir={sortDir} onSort={handleSort}>ID</Th>
                        <Th sortable field="email" current={sortField} dir={sortDir} onSort={handleSort}>邮箱</Th>
                        <Th sortable field="username" current={sortField} dir={sortDir} onSort={handleSort}>用户名</Th>
                        <Th sortable field="display_name" current={sortField} dir={sortDir} onSort={handleSort}>显示名称</Th>
                        <Th sortable field="role" current={sortField} dir={sortDir} onSort={handleSort}>角色</Th>
                        <Th sortable field="is_active" current={sortField} dir={sortDir} onSort={handleSort}>状态</Th>
                        <Th sortable field="created_at" current={sortField} dir={sortDir} onSort={handleSort}>创建时间</Th>
                        <Th>最后登录</Th>
                        <Th className="text-right">操作</Th>
                      </tr>
                    </thead>
                    <tbody>
                      {sortedUsers.map((u, idx) => (
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
                                onClick={() => openEdit(u)}
                                className="p-1.5 rounded-lg text-gray-400 hover:text-primary-600 hover:bg-primary-50 transition-all"
                                title="编辑用户"
                              >
                                <Edit className="w-4 h-4" />
                              </button>
                              <button
                                onClick={() => setDeletingUser(u)}
                                className="p-1.5 rounded-lg text-gray-400 hover:text-rose-600 hover:bg-rose-50 transition-all"
                                title="删除用户"
                                disabled={u.id === user?.id}
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
                {sortedUsers.map(u => (
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
                          onClick={() => openEdit(u)}
                          className="p-1.5 rounded-lg text-gray-400 hover:text-primary-600 hover:bg-primary-50 transition-all"
                        >
                          <Edit className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => setDeletingUser(u)}
                          className="p-1.5 rounded-lg text-gray-400 hover:text-rose-600 hover:bg-rose-50 transition-all"
                          disabled={u.id === user?.id}
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
                    onChange={e => { setPageSize(Number(e.target.value)); setPage(1) }}
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
                    onClick={() => setPage(p => Math.max(1, p - 1))}
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
                        onClick={() => setPage(p)}
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
                    onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/40 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </>
          )}

          {/* ═══ 创建用户弹窗 ═══ */}
          <Modal open={showCreate} title="创建用户" onClose={() => { setShowCreate(false); setCreateErrors({}) }} size="md">
            <div className="space-y-4">
              <FormField label="邮箱" error={createErrors.email} required>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
                  <input
                    type="email"
                    value={createForm.email}
                    onChange={e => setCreateForm(f => ({ ...f, email: e.target.value }))}
                    placeholder="user@example.com"
                    className="w-full pl-9 pr-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                  />
                </div>
              </FormField>

              <FormField label="用户名" error={createErrors.username} required>
                <div className="relative">
                  <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
                  <input
                    type="text"
                    value={createForm.username}
                    onChange={e => setCreateForm(f => ({ ...f, username: e.target.value }))}
                    placeholder="username"
                    className="w-full pl-9 pr-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                  />
                </div>
              </FormField>

              <div className="grid grid-cols-2 gap-3">
                <FormField label="显示名称" error={undefined}>
                  <input
                    type="text"
                    value={createForm.display_name}
                    onChange={e => setCreateForm(f => ({ ...f, display_name: e.target.value }))}
                    placeholder="可选"
                    className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                  />
                </FormField>

                <FormField label="角色" error={undefined} required>
                  <select
                    value={createForm.role}
                    onChange={e => setCreateForm(f => ({ ...f, role: e.target.value as UserRole }))}
                    className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                  >
                    {ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                  </select>
                </FormField>
              </div>

              <FormField label="密码" error={createErrors.password} required>
                <div className="relative">
                  <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
                  <input
                    type="password"
                    value={createForm.password}
                    onChange={e => setCreateForm(f => ({ ...f, password: e.target.value }))}
                    placeholder="至少 6 个字符"
                    className="w-full pl-9 pr-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                  />
                </div>
              </FormField>

              <div className="flex gap-2 justify-end pt-2">
                <button
                  onClick={() => { setShowCreate(false); setCreateErrors({}) }}
                  className="px-4 py-2 text-xs text-gray-500 bg-gray-100/80 rounded-lg hover:bg-gray-200/60 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleCreate}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 disabled:bg-primary-300 rounded-lg transition-colors"
                >
                  {saving && <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
                  创建
                </button>
              </div>
            </div>
          </Modal>

          {/* ═══ 编辑用户弹窗 ═══ */}
          <Modal
            open={!!editingUser}
            title={`编辑用户 — ${editingUser?.display_name || editingUser?.username || ''}`}
            onClose={() => { setEditingUser(null); setEditErrors({}) }}
            size="md"
          >
            {editingUser && (
              <div className="space-y-4">
                <FormField label="邮箱" error={editErrors.email}>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
                    <input
                      type="email"
                      value={editForm.email ?? ''}
                      onChange={e => setEditForm(f => ({ ...f, email: e.target.value }))}
                      className="w-full pl-9 pr-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                    />
                  </div>
                </FormField>

                <div className="grid grid-cols-2 gap-3">
                  <FormField label="用户名" error={undefined}>
                    <input
                      type="text"
                      value={editForm.username ?? ''}
                      onChange={e => setEditForm(f => ({ ...f, username: e.target.value }))}
                      className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                    />
                  </FormField>

                  <FormField label="显示名称" error={undefined}>
                    <input
                      type="text"
                      value={editForm.display_name ?? ''}
                      onChange={e => setEditForm(f => ({ ...f, display_name: e.target.value }))}
                      className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                    />
                  </FormField>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <FormField label="角色" error={undefined}>
                    <select
                      value={editForm.role ?? editingUser.role}
                      onChange={e => setEditForm(f => ({ ...f, role: e.target.value as UserRole }))}
                      className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                    >
                      {ROLES.map(r => <option key={r} value={r}>{ROLE_LABELS[r]}</option>)}
                    </select>
                  </FormField>

                  <FormField label="状态" error={undefined}>
                    <label className="flex items-center gap-2 px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg cursor-pointer hover:bg-white/80 transition-colors">
                      <input
                        type="checkbox"
                        checked={editForm.is_active ?? editingUser.is_active}
                        onChange={e => setEditForm(f => ({ ...f, is_active: e.target.checked }))}
                        className="w-4 h-4 rounded border-gray-300 text-primary-500 focus:ring-primary-300"
                      />
                      <span className="text-gray-600">
                        {editForm.is_active ?? editingUser.is_active ? '活跃' : '停用'}
                      </span>
                    </label>
                  </FormField>
                </div>

                <FormField label="新密码（留空保持不变）" error={editErrors.password}>
                  <div className="relative">
                    <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-300" />
                    <input
                      type="password"
                      value={editForm.password ?? ''}
                      onChange={e => setEditForm(f => ({ ...f, password: e.target.value }))}
                      placeholder="不修改则留空"
                      className="w-full pl-9 pr-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                    />
                  </div>
                </FormField>

                <div className="flex gap-2 justify-end pt-2">
                  <button
                    onClick={() => { setEditingUser(null); setEditErrors({}) }}
                    className="px-4 py-2 text-xs text-gray-500 bg-gray-100/80 rounded-lg hover:bg-gray-200/60 transition-colors"
                  >
                    取消
                  </button>
                  <button
                    onClick={handleEdit}
                    disabled={saving}
                    className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 disabled:bg-primary-300 rounded-lg transition-colors"
                  >
                    {saving && <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
                    保存
                  </button>
                </div>
              </div>
            )}
          </Modal>

          {/* ═══ 删除确认弹窗 ═══ */}
          <ConfirmDialog
            open={!!deletingUser}
            title="确认删除用户"
            message={
              deletingUser
                ? `确定要删除用户「${deletingUser.display_name || deletingUser.username}」(${deletingUser.email}) 吗？此操作不可撤销。${deletingUser.id === user?.id ? '\n\n你不能删除自己。' : ''}`
                : ''
            }
            confirmText="删除"
            cancelText="取消"
            variant="danger"
            onConfirm={handleDelete}
            onCancel={() => setDeletingUser(null)}
          />
        </div>
      </div>
    </AnimatedPage>
  )
}

// ════════════════════════════════════════════════════
//  辅助子组件
// ════════════════════════════════════════════════════

/** 表格列头（可排序） */
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

/** 表单字段（含标签 + 错误提示） */
function FormField({
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

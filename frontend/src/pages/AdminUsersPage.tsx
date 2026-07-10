import { useState, useEffect, useCallback } from 'react'
import { AnimatedPage, EmptyState, ConfirmDialog } from '../components/shared'
import {
  adminListUsers, adminCreateUser, adminUpdateUser, adminDeleteUser,
  type AdminUser,
  type AdminCreateUserRequest, type AdminUpdateUserRequest,
} from '../api/admin'
import { useAuthStore } from '../store/authStore'
import { useErrorStore } from '../store/errorStore'
import { Shield, UserPlus, Search, X } from 'lucide-react'
import { ROLES, ROLE_LABELS } from '../components/admin/UserBadges'
import UserTable, { type SortField, type SortDir } from '../components/admin/UserTable'
import UserModal from '../components/admin/UserModal'
import { validateEmail, validateUsername, validatePassword } from '../components/admin/UserForm'

// ════════════════════════════════════════════════════
//  主组件
// ════════════════════════════════════════════════════

export default function AdminUsersPage() {
  const user = useAuthStore(s => s.user)
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
  const hasFilter = !!search || !!roleFilter

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
  // 非 admin 用户不调用 API，避免不必要的 403/404
  /* eslint-disable react-hooks/set-state-in-effect -- data fetching on mount + filter change */
  useEffect(() => { if (isAdmin) fetchUsers() }, [fetchUsers, isAdmin])
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

  const handleSort = useCallback((field: SortField) => {
    setSortField(prevField => {
      if (prevField === field) {
        setSortDir(d => d === 'asc' ? 'desc' : 'asc')
        return prevField
      }
      setSortDir('asc')
      return field
    })
  }, [])

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
                  className="w-full pl-9 pr-8 py-2 text-sm input-macaron rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 focus:border-primary-300 transition-all"
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

          {/* ═══ 表格（加载 / 空 / 数据） ═══ */}
          {!error && (
            <UserTable
              users={users}
              total={total}
              loading={loading}
              sortField={sortField}
              sortDir={sortDir}
              page={page}
              pageSize={pageSize}
              hasFilter={hasFilter}
              onSort={handleSort}
              onPage={setPage}
              onPageSize={setPageSize}
              onEdit={openEdit}
              onDelete={setDeletingUser}
              currentUserId={user?.id}
            />
          )}

          {/* ═══ 创建用户弹窗 ═══ */}
          <UserModal
            open={showCreate}
            mode="create"
            formData={createForm}
            errors={createErrors}
            saving={saving}
            onChange={(data) => setCreateForm(data as AdminCreateUserRequest)}
            onSubmit={handleCreate}
            onClose={() => { setShowCreate(false); setCreateErrors({}) }}
          />

          {/* ═══ 编辑用户弹窗 ═══ */}
          <UserModal
            open={!!editingUser}
            mode="edit"
            initialData={editingUser}
            formData={editForm}
            errors={editErrors}
            saving={saving}
            onChange={(data) => setEditForm(data as AdminUpdateUserRequest)}
            onSubmit={handleEdit}
            onClose={() => { setEditingUser(null); setEditErrors({}) }}
          />

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


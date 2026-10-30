import { useState, useEffect, useCallback } from 'react'
import { AnimatedPage, EmptyState, ConfirmDialog } from '../components/shared'
import {
  adminListUsers, adminCreateUser, adminUpdateUser, adminDeleteUser, listOperatorAudit,
  getGlobalView, setGlobalView,
  type AdminUser, type OperatorAuditEntry,
  type AdminCreateUserRequest, type AdminUpdateUserRequest,
} from '../api/admin'
import { useAuthStore } from '../store/authStore'
import { useErrorStore } from '../store/errorStore'
import { useGlobalViewStore } from '../store/globalViewStore'
import { Shield, UserPlus, Search, X, Globe, ChevronDown, ChevronRight, Megaphone, Pencil, Trash2, Plus } from 'lucide-react'
import {
  adminListAnnouncements, adminCreateAnnouncement, adminUpdateAnnouncement, adminDeleteAnnouncement,
  type AnnouncementItem, type AnnouncementUpsertRequest,
} from '../api/announcements'
import { ROLES, ROLE_LABELS } from '../components/admin/UserBadges'
import UserTable, { type SortField, type SortDir } from '../components/admin/UserTable'
import UserModal from '../components/admin/UserModal'
import UserDetailDrawer from '../components/admin/UserDetailDrawer'
import { validateEmail, validateUsername, validatePassword } from '../components/admin/UserForm'

/** NEW-21：审计动作中文名（未收录动作回退原始键，保证新动作不显示为空） */
const AUDIT_LABELS: Record<string, string> = {
  global_view_on: '开启全局运维视图',
  global_view_off: '关闭全局运维视图',
  read_global_facts: '读取全部用户记忆',
  read_global_diary: '读取全部用户日记',
  read_proactive_history_all: '读取主动消息历史（全用户）',
  user_create: '创建用户',
  user_update: '更新用户',
  user_delete: '删除用户',
  provider_create: '新增供应商',
  provider_update: '修改供应商',
  provider_toggle: '启停供应商',
  provider_delete: '删除供应商',
  channel_force_disconnect: '强制断开微信通道',
}

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
  const [detailUser, setDetailUser] = useState<AdminUser | null>(null)
  const [saving, setSaving] = useState(false)

  // ── V2-1 全局运维视图 + NEW-21 审计 ──
  const gvEnabled = useGlobalViewStore(s => s.enabled)
  const gvLoaded = useGlobalViewStore(s => s.loaded)
  const gvSetEnabled = useGlobalViewStore(s => s.setEnabled)
  const [gvConfirm, setGvConfirm] = useState(false)
  const [auditEntries, setAuditEntries] = useState<OperatorAuditEntry[] | null>(null)
  const [auditOpen, setAuditOpen] = useState(false)
  const [auditLoading, setAuditLoading] = useState(false)

  // ── V2-5 网站公告管理 ──
  const EMPTY_ANN: AnnouncementUpsertRequest = {
    type: 'banner', title: '', content_md: '', link: '', link_label: '',
    dismissible: true, starts_at: null, ends_at: null, enabled: true,
  }
  const [annOpen, setAnnOpen] = useState(false)
  const [annList, setAnnList] = useState<AnnouncementItem[]>([])
  const [annLoading, setAnnLoading] = useState(false)
  const [annForm, setAnnForm] = useState<AnnouncementUpsertRequest | null>(null)
  const [annEditingId, setAnnEditingId] = useState<number | null>(null)
  const [annSaving, setAnnSaving] = useState(false)
  const [annDeleting, setAnnDeleting] = useState<AnnouncementItem | null>(null)

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

  // V2-1：进页确保全局视图开关状态已加载（横幅未挂载时由本页兜底拉取；
  // FF-0007：API 调用在组件内，结果经 gvSetEnabled 写回 store）
  useEffect(() => {
    if (isAdmin && !gvLoaded) {
      void getGlobalView()
        .then(res => gvSetEnabled(!!res.enabled))
        .catch(() => gvSetEnabled(false))
    }
  }, [isAdmin, gvLoaded, gvSetEnabled])

  // ═════════════════════════════════════════════════
  //  数据加载
  // ═════════════════════════════════════════════════

  // NEW-22：排序随请求下发——翻页/改每页大小都携带同一个全局排序键
  const fetchUsers = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await adminListUsers(
        page,
        pageSize,
        search || undefined,
        roleFilter || undefined,
        sortField ?? undefined,
        sortDir,
      )
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
  }, [page, pageSize, search, roleFilter, sortField, sortDir])

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

  // 排序真的落到数据上（NEW-22：排序由后端按全量结果集执行，前端不再重排当前页）。
  // 表头箭头 = 全局排序态；改排序会带新 sort/sort_dir 重拉，渲染顺序完全来自响应。
  const handleSort = useCallback((field: SortField) => {
    if (sortField === field) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
      return
    }
    setSortField(field)
    setSortDir('asc')
  }, [sortField])

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

  // ── NEW-21：审计流水加载 ──
  const loadAudit = async () => {
    if (auditOpen) { setAuditOpen(false); return }
    setAuditOpen(true)
    setAuditLoading(true)
    try {
      const res = await listOperatorAudit(50)
      setAuditEntries(res.entries)
    } catch {
      setAuditEntries([])
    } finally {
      setAuditLoading(false)
    }
  }

  // ── V2-1：全局运维视图开关（FF-0007：API 调用在组件内，写回 store 同步横幅） ──
  const handleToggleGlobalView = async (enabled: boolean) => {
    try {
      const res = await setGlobalView(enabled)
      gvSetEnabled(!!res.enabled)
      addToast({
        type: 'success',
        message: enabled ? '全局运维视图已开启（已留痕审计）' : '已关闭全局运维视图，数据面恢复本人范围',
      })
    } catch {
      addToast({ type: 'error', message: '开关未生效，请重试' })
    }
  }

  // ── V2-5：网站公告管理 ──
  const loadAnnouncements = async () => {
    setAnnLoading(true)
    try {
      const res = await adminListAnnouncements(50)
      setAnnList(res.announcements ?? [])
    } catch {
      setAnnList([])
    } finally {
      setAnnLoading(false)
    }
  }

  const toggleAnnPanel = () => {
    if (annOpen) { setAnnOpen(false); return }
    setAnnOpen(true)
    void loadAnnouncements()
  }

  const submitAnnouncement = async () => {
    if (!annForm || !annForm.title.trim()) {
      addToast({ type: 'warning', message: '公告标题不能为空' })
      return
    }
    setAnnSaving(true)
    try {
      // datetime-local 值（YYYY-MM-DDTHH:mm）→ ISO（后端可解析）；空串归 null
      const payload: AnnouncementUpsertRequest = {
        ...annForm,
        starts_at: annForm.starts_at ? new Date(annForm.starts_at).toISOString() : null,
        ends_at: annForm.ends_at ? new Date(annForm.ends_at).toISOString() : null,
      }
      if (annEditingId != null) {
        await adminUpdateAnnouncement(annEditingId, payload)
        addToast({ type: 'success', message: '公告已更新' })
      } else {
        await adminCreateAnnouncement(payload)
        addToast({ type: 'success', message: '公告已创建' })
      }
      setAnnForm(null)
      setAnnEditingId(null)
      await loadAnnouncements()
    } catch (err) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err instanceof Error ? err.message : '保存失败')
      addToast({ type: 'error', message: msg })
    } finally {
      setAnnSaving(false)
    }
  }

  const handleDeleteAnnouncement = async () => {
    if (!annDeleting) return
    try {
      await adminDeleteAnnouncement(annDeleting.id)
      addToast({ type: 'success', message: '公告已删除' })
      await loadAnnouncements()
    } catch {
      addToast({ type: 'error', message: '删除失败' })
    } finally {
      setAnnDeleting(null)
    }
  }

  // ═════════════════════════════════════════════════
  //  渲染
  // ═════════════════════════════════════════════════

  if (!isAdmin) {
    return (
      <AnimatedPage>
        <div className="px-4 py-6 sm:px-6 lg:px-8 flex items-center justify-center min-h-[300px]">
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
      <div className="px-4 py-6 sm:px-6 lg:px-8">
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
                  <span className="ml-2 text-gray-400">
                    · 共 <span className="font-semibold text-gray-500">{total}</span> 位用户
                  </span>
                )}
              </p>
            </div>
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex self-start items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors shadow-sm"
            >
              <UserPlus className="w-4 h-4" />
              创建用户
            </button>
          </div>

          {/* ═══ V2-1 全局运维视图 + NEW-21 操作员审计 ═══ */}
          <section className="glass-card rounded-xl px-4 py-3 mb-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <Globe className={`w-4 h-4 shrink-0 ${gvEnabled ? 'text-amber-500' : 'text-gray-400'}`} />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-700">
                    全局运维视图
                    {gvEnabled && (
                      <span className="ml-2 rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] text-amber-600">开启中</span>
                    )}
                  </p>
                  <p className="text-[10px] text-gray-400">
                    默认关闭：记忆/日记等数据面只显示你本人的范围；开启后显示所有用户数据（顶栏常驻横幅 + 操作留痕审计）
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => void loadAudit()}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs text-gray-500 hover:text-gray-700 rounded-lg hover:bg-white/60 transition-colors"
                >
                  {auditOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                  操作审计
                </button>
                <button
                  onClick={() => (gvEnabled ? void handleToggleGlobalView(false) : setGvConfirm(true))}
                  disabled={!gvLoaded}
                  className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors disabled:opacity-50 ${
                    gvEnabled
                      ? 'text-amber-700 bg-amber-100 hover:bg-amber-200'
                      : 'text-white bg-primary-500 hover:bg-primary-400'
                  }`}
                >
                  {gvEnabled ? '关闭全局视图' : '开启全局视图'}
                </button>
              </div>
            </div>

            {auditOpen && (
              <div className="mt-3 border-t border-white/40 pt-3">
                {auditLoading ? (
                  <p className="text-xs text-gray-400 py-2">加载中…</p>
                ) : !auditEntries || auditEntries.length === 0 ? (
                  <p className="text-xs text-gray-400 py-2">暂无操作审计记录</p>
                ) : (
                  <div className="space-y-1 max-h-56 overflow-y-auto">
                    {auditEntries.map(e => (
                      <div key={e.id} className="flex items-center gap-2 text-[11px] text-gray-500 rounded-lg bg-white/40 px-2.5 py-1.5">
                        <span className="font-medium text-gray-600 shrink-0">{AUDIT_LABELS[e.action] ?? e.action}</span>
                        {e.target ? <span className="font-mono text-gray-400 truncate">{e.target}</span> : null}
                        <span className="ml-auto shrink-0 text-gray-300 tabular-nums">
                          {e.created_at ? e.created_at.slice(0, 19).replace('T', ' ') : ''}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>

          {/* ═══ V2-5 网站公告管理 ═══ */}
          <section className="glass-card rounded-xl px-4 py-3 mb-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <Megaphone className="w-4 h-4 shrink-0 text-gray-400" />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-700">网站公告</p>
                  <p className="text-[10px] text-gray-400">
                    全站顶栏横幅（使用说明 / 用户导引 / 反馈渠道）；时间窗自动上下线，用户已读落库
                  </p>
                </div>
              </div>
              <button
                onClick={toggleAnnPanel}
                className="inline-flex items-center gap-1 px-3 py-1.5 text-xs text-gray-500 hover:text-gray-700 rounded-lg hover:bg-white/60 transition-colors"
              >
                {annOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                管理公告
              </button>
            </div>

            {annOpen && (
              <div className="mt-3 border-t border-white/40 pt-3 space-y-2">
                <button
                  onClick={() => { setAnnForm({ ...EMPTY_ANN }); setAnnEditingId(null) }}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  新建公告
                </button>

                {annForm && (
                  <div className="rounded-xl bg-white/50 border border-white/40 p-3 space-y-2" data-testid="announcement-form">
                    <div className="flex flex-wrap gap-2">
                      <select
                        value={annForm.type}
                        onChange={e => setAnnForm({ ...annForm, type: e.target.value as 'banner' | 'modal' })}
                        aria-label="公告类型"
                        className="rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700"
                      >
                        <option value="banner">横幅</option>
                        <option value="modal">弹窗（预留）</option>
                      </select>
                      <input
                        value={annForm.title}
                        onChange={e => setAnnForm({ ...annForm, title: e.target.value })}
                        placeholder="标题（必填）"
                        aria-label="公告标题"
                        className="flex-1 min-w-[12rem] rounded-lg border border-gray-200 px-3 py-1.5 text-xs"
                      />
                    </div>
                    <textarea
                      value={annForm.content_md}
                      onChange={e => setAnnForm({ ...annForm, content_md: e.target.value })}
                      placeholder="正文（纯文本；使用说明可指向 /intro，反馈渠道如 QQ 2053769154）"
                      aria-label="公告正文"
                      rows={3}
                      className="w-full rounded-lg border border-gray-200 px-3 py-1.5 text-xs"
                    />
                    <div className="flex flex-wrap gap-2">
                      <input
                        value={annForm.link}
                        onChange={e => setAnnForm({ ...annForm, link: e.target.value })}
                        placeholder="跳转链接（可选，如 /intro）"
                        aria-label="公告链接"
                        className="flex-1 min-w-[10rem] rounded-lg border border-gray-200 px-3 py-1.5 text-xs"
                      />
                      <input
                        value={annForm.link_label}
                        onChange={e => setAnnForm({ ...annForm, link_label: e.target.value })}
                        placeholder="链接文案（如 查看使用说明）"
                        aria-label="链接文案"
                        className="w-40 rounded-lg border border-gray-200 px-3 py-1.5 text-xs"
                      />
                    </div>
                    <div className="flex flex-wrap items-center gap-3 text-xs text-gray-600">
                      <label className="inline-flex items-center gap-1">
                        <input type="checkbox" checked={annForm.dismissible} onChange={e => setAnnForm({ ...annForm, dismissible: e.target.checked })} />
                        可关闭
                      </label>
                      <label className="inline-flex items-center gap-1">
                        <input type="checkbox" checked={annForm.enabled} onChange={e => setAnnForm({ ...annForm, enabled: e.target.checked })} />
                        启用
                      </label>
                      <label className="inline-flex items-center gap-1">
                        上线
                        <input type="datetime-local" value={annForm.starts_at ?? ''} onChange={e => setAnnForm({ ...annForm, starts_at: e.target.value || null })} className="rounded-lg border border-gray-200 px-2 py-1 text-xs" />
                      </label>
                      <label className="inline-flex items-center gap-1">
                        下线
                        <input type="datetime-local" value={annForm.ends_at ?? ''} onChange={e => setAnnForm({ ...annForm, ends_at: e.target.value || null })} className="rounded-lg border border-gray-200 px-2 py-1 text-xs" />
                      </label>
                    </div>
                    <div className="flex justify-end gap-2">
                      <button onClick={() => { setAnnForm(null); setAnnEditingId(null) }} className="rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50">取消</button>
                      <button onClick={() => void submitAnnouncement()} disabled={annSaving} className="rounded-lg bg-primary-500 px-4 py-1.5 text-xs font-medium text-white hover:bg-primary-400 disabled:opacity-50">
                        {annSaving ? '保存中…' : annEditingId != null ? '保存修改' : '创建公告'}
                      </button>
                    </div>
                  </div>
                )}

                {annLoading ? (
                  <p className="text-xs text-gray-400 py-2">加载中…</p>
                ) : annList.length === 0 ? (
                  <p className="text-xs text-gray-400 py-2">暂无公告</p>
                ) : (
                  <div className="space-y-1 max-h-64 overflow-y-auto">
                    {annList.map(a => (
                      <div key={a.id} className="flex items-center gap-2 text-xs text-gray-600 rounded-lg bg-white/40 px-2.5 py-1.5">
                        <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] ${a.enabled ? 'bg-green-100 text-green-600' : 'bg-gray-100 text-gray-400'}`}>
                          {a.enabled ? '启用' : '停用'}
                        </span>
                        <span className="font-medium text-gray-700 truncate">{a.title}</span>
                        <span className="text-[10px] text-gray-300 shrink-0">
                          {a.starts_at ? `自 ${a.starts_at.slice(0, 10)}` : ''}{a.ends_at ? ` 至 ${a.ends_at.slice(0, 10)}` : '（长期）'}
                        </span>
                        <span className="ml-auto flex items-center gap-1 shrink-0">
                          <button
                            onClick={() => {
                              setAnnEditingId(a.id)
                              setAnnForm({
                                type: a.type, title: a.title, content_md: a.content_md,
                                link: a.link, link_label: a.link_label, dismissible: a.dismissible,
                                starts_at: a.starts_at ? a.starts_at.slice(0, 16) : null,
                                ends_at: a.ends_at ? a.ends_at.slice(0, 16) : null,
                                enabled: a.enabled,
                              })
                            }}
                            className="p-1 rounded text-gray-400 hover:text-primary-600 hover:bg-primary-50"
                            title="编辑"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </button>
                          <button
                            onClick={() => setAnnDeleting(a)}
                            className="p-1 rounded text-gray-400 hover:text-rose-600 hover:bg-rose-50"
                            title="删除"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </section>

          {/* ═══ 搜索 + 过滤 ═══ */}
          <div className="glass-card rounded-xl px-4 py-3 mb-4">
            <div className="flex flex-col sm:flex-row gap-3">
              {/* 搜索框 */}
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  value={searchInput}
                  onChange={e => setSearchInput(e.target.value)}
                  placeholder="搜索邮箱、用户名或显示名称..."
                  className="w-full pl-9 pr-8 py-2 text-sm input-macaron rounded-lg text-gray-700 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-primary-300/50 focus:border-primary-300 transition-all"
                />
                {searchInput && (
                  <button
                    onClick={() => { setSearchInput(''); setSearch(''); setPage(1) }}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-500 transition-colors"
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
              onDetail={setDetailUser}
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

          {/* ═══ NEW-11 用户详情下钻抽屉 ═══ */}
          {detailUser && (
            <UserDetailDrawer user={detailUser} onClose={() => setDetailUser(null)} />
          )}

          {/* ═══ V2-1 开启全局视图确认（范围说明+审计提示） ═══ */}
          <ConfirmDialog
            open={!!annDeleting}
            title="确认删除公告"
            message={annDeleting ? `确定删除公告「${annDeleting.title}」吗？已读记录一并清除。` : ''}
            confirmText="删除"
            cancelText="取消"
            variant="danger"
            onConfirm={() => void handleDeleteAnnouncement()}
            onCancel={() => setAnnDeleting(null)}
          />

          <ConfirmDialog
            open={gvConfirm}
            title="开启全局运维视图"
            message="开启后，记忆 / 日记等数据面将显示【所有用户】的数据（排查问题用）。顶栏会常驻提示横幅，且每次全局读取都会记入操作审计。确认开启？"
            confirmText="开启"
            cancelText="取消"
            variant="danger"
            onConfirm={() => {
              setGvConfirm(false)
              void handleToggleGlobalView(true)
            }}
            onCancel={() => setGvConfirm(false)}
          />
        </div>
      </div>
    </AnimatedPage>
  )
}


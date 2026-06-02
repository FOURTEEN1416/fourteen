/**
 * 邀请码管理页面（Admin）
 *
 * 列出/创建/撤销邀请码
 */
import { useState, useEffect, useCallback } from 'react'
import { AnimatedPage, EmptyState, Modal, ConfirmDialog } from '../components/shared'
import {
  adminCreateInvites,
  adminListInvites,
  adminRevokeInvite,
  type InviteCodeItem,
  type InviteListResponse,
} from '../api/invites'
import { useErrorStore } from '../store/errorStore'
import {
  TicketPlus, TicketX, Copy, Check, RefreshCw, ChevronLeft, ChevronRight,
} from 'lucide-react'

// ════════════════════════════════════════════════════
//  常量
// ════════════════════════════════════════════════════

const STATUS_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'valid', label: '有效' },
  { value: 'used', label: '已使用' },
  { value: 'revoked', label: '已撤销' },
  { value: 'expired', label: '已过期' },
] as const

const STATUS_BADGE: Record<string, string> = {
  valid: 'bg-green-50 text-green-600 ring-1 ring-green-200/50',
  used: 'bg-gray-50 text-gray-500 ring-1 ring-gray-200/50',
  revoked: 'bg-rose-50 text-rose-600 ring-1 ring-rose-200/50',
  expired: 'bg-amber-50 text-amber-600 ring-1 ring-amber-200/50',
}

const STATUS_LABEL: Record<string, string> = {
  valid: '有效',
  used: '已使用',
  revoked: '已撤销',
  expired: '已过期',
}

// ════════════════════════════════════════════════════
//  子组件
// ════════════════════════════════════════════════════

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${STATUS_BADGE[status] || STATUS_BADGE.valid}`}
    >
      {STATUS_LABEL[status] || status}
    </span>
  )
}

function formatDateTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ════════════════════════════════════════════════════
//  主组件
// ════════════════════════════════════════════════════

export default function AdminInvitesPage() {
  const addToast = useErrorStore(s => s.addToast)

  // ── 数据 ──
  const [data, setData] = useState<InviteListResponse | null>(null)
  const [loading, setLoading] = useState(true)

  // ── 查询参数 ──
  const [page, setPage] = useState(1)
  const [pageSize] = useState(20)
  const [statusFilter, setStatusFilter] = useState('')

  // ── 创建弹窗 ──
  const [showCreate, setShowCreate] = useState(false)
  const [createCount, setCreateCount] = useState(5)
  const [createDays, setCreateDays] = useState(30)
  const [createNote, setCreateNote] = useState('')
  const [creating, setCreating] = useState(false)

  // ── 撤销确认 ──
  const [revokingCode, setRevokingCode] = useState<string | null>(null)

  // ── 复制反馈 ──
  const [copiedIndex, setCopiedIndex] = useState(-1)

  // ═════════════════════════════════════════════════
  //  数据加载
  // ═════════════════════════════════════════════════

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const res = await adminListInvites({
        page,
        page_size: pageSize,
        status: (statusFilter as 'valid' | 'used' | 'revoked' | 'expired') || undefined,
      })
      setData(res.data)
    } catch {
      addToast({ type: 'error', message: '加载邀请码列表失败' })
    } finally {
      setLoading(false)
    }
  }, [page, pageSize, statusFilter, addToast])

  useEffect(() => { fetchData() }, [fetchData])

  // ═════════════════════════════════════════════════
  //  创建邀请码
  // ═════════════════════════════════════════════════

  const handleCreate = async () => {
    setCreating(true)
    try {
      const res = await adminCreateInvites({
        count: createCount,
        expires_days: createDays,
        note: createNote || undefined,
      })
      addToast({ type: 'success', message: `成功创建 ${res.data.total} 个邀请码` })
      setShowCreate(false)
      setCreateCount(5)
      setCreateDays(30)
      setCreateNote('')
      fetchData()
    } catch {
      addToast({ type: 'error', message: '创建邀请码失败' })
    } finally {
      setCreating(false)
    }
  }

  // ═════════════════════════════════════════════════
  //  撤销邀请码
  // ═════════════════════════════════════════════════

  const handleRevoke = async () => {
    if (!revokingCode) return
    try {
      await adminRevokeInvite(revokingCode)
      addToast({ type: 'success', message: `邀请码 ${revokingCode} 已撤销` })
      setRevokingCode(null)
      fetchData()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '撤销失败'
      addToast({ type: 'error', message: msg })
    }
  }

  // ═════════════════════════════════════════════════
  //  复制
  // ═════════════════════════════════════════════════

  const copyCode = (code: string, idx: number) => {
    navigator.clipboard.writeText(code).then(() => {
      setCopiedIndex(idx)
      setTimeout(() => setCopiedIndex(-1), 1500)
    })
  }

  // 删除未使用的 copyAllNew 工具函数 (之前预留的"创建后自动复制"功能, 当前 UI 已用单条 copy 按钮替代)

  // ═════════════════════════════════════════════════
  //  分页
  // ═════════════════════════════════════════════════

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1

  // ═════════════════════════════════════════════════
  //  获取某个邀请码的状态
  // ═════════════════════════════════════════════════

  const getStatus = (item: InviteCodeItem): string => {
    if (item.is_revoked) return 'revoked'
    if (item.used_by != null) return 'used'
    if (item.expires_at && new Date(item.expires_at) < new Date()) return 'expired'
    return 'valid'
  }

  // ═════════════════════════════════════════════════
  //  渲染
  // ═════════════════════════════════════════════════

  return (
    <AnimatedPage>
      <div className="min-h-screen bg-dynamic px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-7xl">
          {/* ═══ Header ═══ */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
            <div>
              <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                <TicketPlus className="w-5 h-5 text-primary-500" />
                邀请码管理
              </h1>
              <p className="mt-0.5 text-sm text-gray-400">
                管理内测邀请码的创建和撤销
                {data && !loading && (
                  <span className="ml-2 text-gray-300">
                    · 有效 <span className="font-semibold text-green-500">{data.valid_count}</span>
                    {' · '}已使用 <span className="font-semibold text-gray-500">{data.used_count}</span>
                    {' · '}已撤销 <span className="font-semibold text-rose-500">{data.revoked_count}</span>
                    {' · '}已过期 <span className="font-semibold text-amber-500">{data.expired_count}</span>
                  </span>
                )}
              </p>
            </div>
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors shadow-sm"
            >
              <TicketPlus className="w-4 h-4" />
              创建邀请码
            </button>
          </div>

          {/* ═══ 筛选 ═══ */}
          <div className="glass-card rounded-xl px-4 py-3 mb-4">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-xs text-gray-400 font-medium">筛选</span>
              {STATUS_OPTIONS.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => { setStatusFilter(opt.value); setPage(1) }}
                  className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-all ${
                    statusFilter === opt.value
                      ? 'bg-primary-500 text-white shadow-sm'
                      : 'text-gray-500 bg-white/40 hover:bg-white/60'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
              {(statusFilter || page > 1) && (
                <button
                  onClick={() => { setStatusFilter(''); setPage(1) }}
                  className="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-600 transition-colors"
                >
                  重置
                </button>
              )}
            </div>
          </div>

          {/* ═══ 加载态 ═══ */}
          {loading && (
            <div className="glass-card rounded-xl p-8">
              <div className="flex items-center justify-center gap-2 text-sm text-gray-400">
                <RefreshCw className="w-4 h-4 animate-spin" />
                加载中...
              </div>
            </div>
          )}

          {/* ═══ 空态 ═══ */}
          {!loading && (!data || data.items.length === 0) && (
            <div className="glass-card rounded-xl p-8">
              <EmptyState
                icon={<TicketPlus className="w-10 h-10 text-gray-200" />}
                title={statusFilter ? '未找到匹配邀请码' : '暂无邀请码'}
                description={statusFilter ? '尝试修改筛选条件' : '创建邀请码以允许用户注册'}
                action={
                  !statusFilter ? (
                    <button
                      onClick={() => setShowCreate(true)}
                      className="px-4 py-2 text-xs font-medium text-primary-600 bg-primary-50 rounded-lg hover:bg-primary-100 transition-colors"
                    >
                      创建邀请码
                    </button>
                  ) : undefined
                }
              />
            </div>
          )}

          {/* ═══ 列表 ═══ */}
          {!loading && data && data.items.length > 0 && (
            <>
              {/* Desktop 表格 */}
              <div className="glass-card rounded-xl overflow-hidden hidden md:block">
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-white/20 text-xs text-gray-400">
                        <th className="px-4 py-3 font-medium text-left">邀请码</th>
                        <th className="px-4 py-3 font-medium text-left">状态</th>
                        <th className="px-4 py-3 font-medium text-left">备注</th>
                        <th className="px-4 py-3 font-medium text-left">创建时间</th>
                        <th className="px-4 py-3 font-medium text-left">过期时间</th>
                        <th className="px-4 py-3 font-medium text-left">使用人</th>
                        <th className="px-4 py-3 font-medium text-left">使用时间</th>
                        <th className="px-4 py-3 font-medium text-right">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.items.map((item, idx) => {
                        const status = getStatus(item)
                        return (
                          <tr
                            key={item.code}
                            className="border-b border-white/10 hover:bg-white/40 transition-colors group"
                          >
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-1.5">
                                <code className="text-xs font-mono font-semibold text-gray-700 tracking-wider">
                                  {item.code}
                                </code>
                                <button
                                  onClick={() => copyCode(item.code, idx)}
                                  className="p-1 rounded text-gray-300 hover:text-primary-500 transition-colors"
                                  title="复制邀请码"
                                >
                                  {copiedIndex === idx
                                    ? <Check className="w-3.5 h-3.5 text-green-500" />
                                    : <Copy className="w-3.5 h-3.5" />
                                  }
                                </button>
                              </div>
                            </td>
                            <td className="px-4 py-3"><StatusBadge status={status} /></td>
                            <td className="px-4 py-3 text-xs text-gray-400 max-w-[120px] truncate">
                              {item.note || '-'}
                            </td>
                            <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                              {formatDateTime(item.created_at)}
                            </td>
                            <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">
                              {formatDateTime(item.expires_at)}
                            </td>
                            <td className="px-4 py-3 text-xs text-gray-500">
                              {item.used_by != null ? `#${item.used_by}` : '-'}
                            </td>
                            <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap">
                              {formatDateTime(item.used_at)}
                            </td>
                            <td className="px-4 py-3 text-right">
                              {status === 'valid' && (
                                <button
                                  onClick={() => setRevokingCode(item.code)}
                                  className="p-1.5 rounded-lg text-gray-400 hover:text-rose-600 hover:bg-rose-50 transition-all opacity-0 group-hover:opacity-100"
                                  title="撤销邀请码"
                                >
                                  <TicketX className="w-4 h-4" />
                                </button>
                              )}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Mobile 卡片 */}
              <div className="md:hidden space-y-3">
                {data.items.map(item => {
                  const status = getStatus(item)
                  return (
                    <div key={item.code} className="glass-card rounded-xl p-4 space-y-2 animate-fade-in">
                      <div className="flex items-start justify-between">
                        <div>
                          <code className="text-sm font-mono font-semibold text-gray-700 tracking-wider">
                            {item.code}
                          </code>
                          <div className="mt-1">
                            <StatusBadge status={status} />
                          </div>
                        </div>
                        {status === 'valid' && (
                          <button
                            onClick={() => setRevokingCode(item.code)}
                            className="p-1.5 rounded-lg text-gray-400 hover:text-rose-600 hover:bg-rose-50 transition-all"
                            title="撤销"
                          >
                            <TicketX className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                      <div className="grid grid-cols-2 gap-1 text-[11px] text-gray-400">
                        <span>创建: {formatDateTime(item.created_at)}</span>
                        <span>过期: {formatDateTime(item.expires_at)}</span>
                        {item.note && <span className="col-span-2">备注: {item.note}</span>}
                      </div>
                    </div>
                  )
                })}
              </div>

              {/* 分页 */}
              <div className="flex flex-col sm:flex-row items-center justify-between gap-3 mt-4">
                <div className="text-xs text-gray-400">
                  共 {data.total} 条，第 {page}/{totalPages} 页
                </div>
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setPage(p => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/40 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
                    let pageNum: number
                    if (totalPages <= 7) {
                      pageNum = i + 1
                    } else if (page <= 4) {
                      pageNum = i + 1
                    } else if (page >= totalPages - 3) {
                      pageNum = totalPages - 6 + i
                    } else {
                      pageNum = page - 3 + i
                    }
                    return (
                      <button
                        key={pageNum}
                        onClick={() => setPage(pageNum)}
                        className={`min-w-[28px] h-7 text-xs font-medium rounded-lg transition-all ${
                          page === pageNum
                            ? 'bg-primary-500 text-white shadow-sm'
                            : 'text-gray-500 hover:bg-white/40'
                        }`}
                      >
                        {pageNum}
                      </button>
                    )
                  })}
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

          {/* ═══ 创建弹窗 ═══ */}
          <Modal open={showCreate} title="创建邀请码" onClose={() => setShowCreate(false)} size="sm">
            <div className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1.5">
                  数量 <span className="text-rose-400">*</span>
                </label>
                <input
                  type="number"
                  value={createCount}
                  onChange={e => setCreateCount(Math.max(1, Math.min(100, Number(e.target.value))))}
                  min={1}
                  max={100}
                  className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                />
                <p className="mt-1 text-[11px] text-gray-400">一次最多创建 100 个</p>
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1.5">
                  过期天数 <span className="text-rose-400">*</span>
                </label>
                <input
                  type="number"
                  value={createDays}
                  onChange={e => setCreateDays(Math.max(1, Math.min(365, Number(e.target.value))))}
                  min={1}
                  max={365}
                  className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1.5">备注</label>
                <input
                  type="text"
                  value={createNote}
                  onChange={e => setCreateNote(e.target.value)}
                  placeholder="例如：发放给内测用户"
                  maxLength={255}
                  className="w-full px-3 py-2 text-sm bg-white/60 border border-white/30 rounded-lg text-gray-700 placeholder:text-gray-300 focus:outline-none focus:ring-2 focus:ring-primary-300/50 transition-all"
                />
              </div>

              <div className="flex gap-2 justify-end pt-2">
                <button
                  onClick={() => setShowCreate(false)}
                  className="px-4 py-2 text-xs text-gray-500 bg-gray-100/80 rounded-lg hover:bg-gray-200/60 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleCreate}
                  disabled={creating}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 disabled:bg-primary-300 rounded-lg transition-colors"
                >
                  {creating && <span className="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
                  创建
                </button>
              </div>
            </div>
          </Modal>

          {/* ═══ 撤销确认弹窗 ═══ */}
          <ConfirmDialog
            open={!!revokingCode}
            title="撤销邀请码"
            message={`确定要撤销邀请码 ${revokingCode} 吗？此操作不可撤销。`}
            confirmText="撤销"
            cancelText="取消"
            variant="danger"
            onConfirm={handleRevoke}
            onCancel={() => setRevokingCode(null)}
          />
        </div>
      </div>
    </AnimatedPage>
  )
}

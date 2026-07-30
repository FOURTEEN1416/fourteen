/**
 * 供应商管理页（admin only）
 *
 * 功能：
 * - 列出所有供应商（含禁用的）
 * - 启用/禁用 toggle
 * - 编辑供应商配置 + 教程（弹窗）
 * - 添加新供应商（弹窗）
 * - 删除自定义供应商（预设不可删）
 */
import { useState, useEffect, useCallback } from 'react'
import { AnimatedPage, EmptyState, ConfirmDialog } from '../components/shared'
import {
  listAllProviders, createProvider, updateProvider, toggleProvider, deleteProvider,
  type ProviderOption, type ProviderUpdateRequest, type ProviderCreateRequest,
} from '../api/llmProviders'
import { useAuthStore } from '../store/authStore'
import { useErrorStore } from '../store/errorStore'
import { Shield, Plus, RefreshCw, Edit3, Trash2, BookOpen, ExternalLink, Loader2 } from 'lucide-react'
import ProviderEditModal from '../components/llm/ProviderEditModal'
import ProviderGuideModal from '../components/llm/ProviderGuideModal'

export default function AdminProvidersPage() {
  const user = useAuthStore(s => s.user)
  const addToast = useErrorStore(s => s.addToast)
  const isAdmin = user?.role === 'admin'

  // ── 数据状态 ──
  const [providers, setProviders] = useState<ProviderOption[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // ── 弹窗状态 ──
  const [showCreate, setShowCreate] = useState(false)
  const [editingProvider, setEditingProvider] = useState<ProviderOption | null>(null)
  const [guideProvider, setGuideProvider] = useState<ProviderOption | null>(null)
  const [deletingProvider, setDeletingProvider] = useState<ProviderOption | null>(null)
  const [togglingKey, setTogglingKey] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // ═════════════════════════════════════════════════
  //  数据加载
  // ═════════════════════════════════════════════════

  const fetchProviders = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listAllProviders()
      setProviders(res.providers ?? [])
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || (err as Error)?.message
        || '加载供应商列表失败'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }, [])

  /* eslint-disable react-hooks/set-state-in-effect -- mount fetching */
  useEffect(() => { if (isAdmin) fetchProviders() }, [fetchProviders, isAdmin])
  /* eslint-enable react-hooks/set-state-in-effect */

  // ═════════════════════════════════════════════════
  //  操作
  // ═════════════════════════════════════════════════

  const handleToggle = async (p: ProviderOption) => {
    if (p.is_special) {
      addToast({ type: 'warning', message: '特殊选项不支持启用/禁用' })
      return
    }
    setTogglingKey(p.key)
    try {
      await toggleProvider(p.key, !p.enabled)
      addToast({ type: 'success', message: `${p.name} 已${!p.enabled ? '启用' : '禁用'}` })
      fetchProviders()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '操作失败'
      addToast({ type: 'error', message: msg })
    } finally {
      setTogglingKey(null)
    }
  }

  const handleDelete = async () => {
    if (!deletingProvider) return
    setSaving(true)
    try {
      await deleteProvider(deletingProvider.key)
      addToast({ type: 'success', message: `${deletingProvider.name} 已删除` })
      setDeletingProvider(null)
      fetchProviders()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '删除失败'
      addToast({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
  }

  const handleCreate = async (data: ProviderCreateRequest) => {
    setSaving(true)
    try {
      await createProvider(data)
      addToast({ type: 'success', message: `供应商 ${data.name} 已创建` })
      setShowCreate(false)
      fetchProviders()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '创建失败'
      addToast({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
  }

  const handleEdit = async (key: string, data: ProviderUpdateRequest) => {
    setSaving(true)
    try {
      await updateProvider(key, data)
      addToast({ type: 'success', message: `${data.name} 已更新` })
      setEditingProvider(null)
      fetchProviders()
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '更新失败'
      addToast({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
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
            description="仅管理员可访问供应商管理页面"
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
                LLM 供应商管理
              </h1>
              <p className="mt-0.5 text-sm text-gray-400">
                管理供应商清单、申请教程、启用/禁用
                {!loading && (
                  <span className="ml-2 text-gray-300">
                    · 共 <span className="font-semibold text-gray-500">{providers.length}</span> 个
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={fetchProviders}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-gray-600 bg-white/60 hover:bg-white/80 border border-white/30 rounded-lg transition-colors"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                刷新
              </button>
              <button
                onClick={() => setShowCreate(true)}
                className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors shadow-sm"
              >
                <Plus className="w-4 h-4" />
                添加供应商
              </button>
            </div>
          </div>

          {/* ═══ 错误状态 ═══ */}
          {error && !loading && (
            <div className="glass-card rounded-xl p-8">
              <EmptyState
                icon={<RefreshCw className="w-10 h-10 text-rose-300" />}
                title="加载失败"
                description={error}
                action={
                  <button
                    onClick={fetchProviders}
                    className="px-4 py-2 text-xs font-medium text-primary-600 bg-primary-50 rounded-lg hover:bg-primary-100 transition-colors"
                  >
                    重试
                  </button>
                }
              />
            </div>
          )}

          {/* ═══ 加载中 ═══ */}
          {loading && (
            <div className="glass-card rounded-xl p-12 flex items-center justify-center">
              <Loader2 className="w-5 h-5 animate-spin text-primary-400" />
              <span className="ml-2 text-sm text-gray-400">加载中...</span>
            </div>
          )}

          {/* ═══ 供应商列表 ═══ */}
          {!loading && !error && (
            <div className="space-y-3">
              {providers.map((p) => (
                <div
                  key={p.key}
                  className={`glass-card rounded-xl p-4 ${p.enabled === false ? 'opacity-60' : ''}`}
                >
                  <div className="flex items-start gap-3">
                    {/* ── 左侧：信息 ── */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h3 className="text-sm font-semibold text-gray-800">{p.name}</h3>
                        <code className="text-[10px] px-1.5 py-0.5 bg-gray-100 text-gray-500 rounded font-mono">{p.key}</code>
                        {p.is_special && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-purple-100 text-purple-700 rounded">特殊</span>
                        )}
                        {p.is_preset && !p.is_special && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded">预设</span>
                        )}
                        {!p.is_preset && !p.is_special && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-green-100 text-green-700 rounded">自定义</span>
                        )}
                        {!p.is_special && p.enabled === false && (
                          <span className="text-[9px] px-1.5 py-0.5 bg-gray-200 text-gray-500 rounded">已禁用</span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-gray-500">{p.description}</p>
                      {!p.is_special && (
                        <div className="mt-2 flex items-center gap-3 flex-wrap text-[11px] text-gray-400">
                          {p.model && <span>模型: <code className="font-mono">{p.model}</code></span>}
                          {p.api_base && <span>地址: <code className="font-mono truncate">{p.api_base}</code></span>}
                          {p.guide?.free_quota && <span>额度: {p.guide.free_quota}</span>}
                        </div>
                      )}
                      {p.guide?.apply_url && (
                        <a
                          href={p.guide.apply_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-primary-600 hover:underline"
                        >
                          <ExternalLink className="w-3 h-3" />
                          {p.guide.apply_url}
                        </a>
                      )}
                    </div>

                    {/* ── 右侧：操作 ── */}
                    <div className="flex items-center gap-1 shrink-0">
                      {/* 教程按钮 */}
                      <button
                        onClick={() => setGuideProvider(p)}
                        className="p-1.5 text-gray-400 hover:text-primary-500 transition-colors"
                        title="查看教程"
                      >
                        <BookOpen className="w-4 h-4" />
                      </button>

                      {/* 编辑按钮（特殊选项也允许编辑教程） */}
                      <button
                        onClick={() => setEditingProvider(p)}
                        className="p-1.5 text-gray-400 hover:text-blue-500 transition-colors"
                        title="编辑"
                      >
                        <Edit3 className="w-4 h-4" />
                      </button>

                      {/* 启用/禁用开关（特殊选项不支持） */}
                      {!p.is_special && (
                        <button
                          onClick={() => handleToggle(p)}
                          disabled={togglingKey === p.key}
                          className={`px-2.5 py-1 text-[10px] font-medium rounded transition-colors ${
                            p.enabled
                              ? 'bg-amber-100 text-amber-700 hover:bg-amber-200'
                              : 'bg-green-100 text-green-700 hover:bg-green-200'
                          } disabled:opacity-50`}
                          title={p.enabled ? '点击禁用' : '点击启用'}
                        >
                          {togglingKey === p.key ? (
                            <Loader2 className="w-3 h-3 animate-spin" />
                          ) : p.enabled ? '禁用' : '启用'}
                        </button>
                      )}

                      {/* 删除按钮（特殊选项不可删，预设和自定义都可删） */}
                      {!p.is_special && (
                        <button
                          onClick={() => setDeletingProvider(p)}
                          className="p-1.5 text-gray-400 hover:text-rose-500 transition-colors"
                          title="删除"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* ═══ 创建供应商弹窗 ═══ */}
          <ProviderEditModal
            open={showCreate}
            mode="create"
            saving={saving}
            onSubmit={(data) => handleCreate(data as ProviderCreateRequest)}
            onClose={() => setShowCreate(false)}
          />

          {/* ═══ 编辑供应商弹窗 ═══ */}
          <ProviderEditModal
            open={!!editingProvider}
            mode="edit"
            initialData={editingProvider}
            saving={saving}
            onSubmit={(data) => editingProvider && handleEdit(editingProvider.key, data)}
            onClose={() => setEditingProvider(null)}
          />

          {/* ═══ 教程查看弹窗 ═══ */}
          <ProviderGuideModal
            provider={guideProvider}
            open={!!guideProvider}
            onClose={() => setGuideProvider(null)}
          />

          {/* ═══ 删除确认弹窗 ═══ */}
          <ConfirmDialog
            open={!!deletingProvider}
            title="确认删除供应商"
            message={
              deletingProvider
                ? deletingProvider.is_preset
                  ? `「${deletingProvider.name}」是项目预设供应商。删除后将从列表移除（同时从回退链移除），如需恢复可在「添加供应商」中用相同 key 重新添加。确认删除？`
                  : `确定要删除供应商「${deletingProvider.name}」(${deletingProvider.key}) 吗？此操作不可撤销。`
                : ''
            }
            confirmText="删除"
            cancelText="取消"
            variant="danger"
            onConfirm={handleDelete}
            onCancel={() => setDeletingProvider(null)}
          />
        </div>
      </div>
    </AnimatedPage>
  )
}

import { useState, useMemo, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatedPage, Skeleton, EmptyState } from '../components/shared'
import { listMyBindings, unbindWechat, type WechatBindingDTO } from '../api/wechat'
import { getAccessToken } from '../store/authStore'
import { Smartphone, Trash2, User, ExternalLink } from 'lucide-react'

const GRADIENTS = [
  'from-primary-400 to-accent-500',
  'from-pink-400 to-rose-500',
  'from-sky-400 to-cyan-500',
  'from-amber-400 to-orange-500',
  'from-emerald-400 to-teal-500',
  'from-violet-400 to-purple-500',
  'from-fuchsia-400 to-pink-500',
  'from-indigo-400 to-blue-500',
]

function gradientForWxid(wxid: string): string {
  let hash = 0
  for (let i = 0; i < wxid.length; i++) {
    hash = wxid.charCodeAt(i) + ((hash << 5) - hash)
  }
  return GRADIENTS[Math.abs(hash) % GRADIENTS.length]
}

// ---- Component ----

export default function UsersPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [bindings, setBindings] = useState<WechatBindingDTO[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 检查是否已登录
  const isLoggedIn = !!getAccessToken()

  // 加载绑定列表
  useEffect(() => {
    if (!isLoggedIn) return

    let cancelled = false
    // 用微任务避免 ESLint set-state-in-effect 误报
    queueMicrotask(() => {
      if (cancelled) return
      setLoading(true)
      setError(null)
      listMyBindings()
        .then((res) => {
          if (!cancelled) setBindings(res.data.bindings)
        })
        .catch((err: { response?: { data?: { detail?: string } }; message?: string }) => {
          if (!cancelled) setError(err?.response?.data?.detail || err?.message || '加载绑定失败')
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    })
    return () => { cancelled = true }
  }, [isLoggedIn])

  const filtered = useMemo(() => {
    if (!search.trim()) return bindings
    const q = search.trim().toLowerCase()
    return bindings.filter(
      (b) =>
        b.wxid.toLowerCase().includes(q) ||
        b.nickname.toLowerCase().includes(q) ||
        b.character_card_id.toLowerCase().includes(q),
    )
  }, [search, bindings])

  // 解绑
  const handleUnbind = async (wxid: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!window.confirm('确定解除绑定此微信？')) return
    try {
      await unbindWechat(wxid)
      setBindings((prev) => prev.filter((b) => b.wxid !== wxid))
    } catch {
      // toast 已由 client interceptor 处理
    }
  }

  // 未登录状态
  if (!isLoggedIn) {
    return (
      <AnimatedPage>
        <div className="px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto max-w-6xl">
            <EmptyState
              icon="🔒"
              title="请先登录"
              description="登录后即可查看绑定的微信账号"
            />
          </div>
        </div>
      </AnimatedPage>
    )
  }

  return (
    <AnimatedPage>
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-6xl">
          {/* Header */}
          <div className="mb-5">
            <h1 className="text-xl font-bold text-gray-800">我的微信</h1>
            <p className="mt-0.5 text-sm text-gray-400">
              共{' '}
              <span className="font-semibold text-gray-600">
                {loading ? '-' : bindings.length}
              </span>{' '}
              个绑定账号
            </p>
          </div>

          {/* Search bar */}
          <div className="glass-card mb-5 rounded-lg px-4 py-3">
            <div className="relative">
              <svg
                className="absolute left-0 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-300"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索微信昵称或 wxid..."
                className="input-macaron w-full border-0 bg-transparent pl-6 text-sm text-gray-700 placeholder:text-gray-300 focus:outline-none"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  className="absolute right-0 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-300 hover:text-gray-500"
                >
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              )}
            </div>
          </div>

          {/* Error state */}
          {error && !loading && (
            <EmptyState
              icon="⚠️"
              title="加载失败"
              description={error}
            />
          )}

          {/* Bindings grid */}
          {loading ? (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={`skeleton-${i}`} className="glass-card rounded-xl p-4 space-y-3">
                  <div className="flex items-center gap-3">
                    <Skeleton className="h-10 w-10 rounded-full" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-4 w-24" />
                      <Skeleton className="h-3 w-16" />
                    </div>
                  </div>
                  <div className="flex items-center justify-between pt-1">
                    <Skeleton className="h-3 w-20" />
                    <Skeleton className="h-3 w-12" />
                  </div>
                </div>
              ))}
            </div>
          ) : filtered.length === 0 && !error ? (
            <EmptyState
              icon="📱"
              title={search ? '未找到匹配' : '暂无绑定'}
              description={search ? '尝试修改搜索关键词' : '扫码连接微信后，将自动显示在这里'}
            />
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {filtered.map((binding) => {
                const gradient = gradientForWxid(binding.wxid)
                const displayName = binding.nickname || binding.wxid
                const initials = displayName.slice(0, 2)
                const hasCharacter = binding.character_card_id && binding.character_card_id !== 'default'

                return (
                  <div
                    key={binding.wxid}
                    onClick={() => navigate(`/bindings/${binding.wxid}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        navigate(`/bindings/${binding.wxid}`)
                      }
                    }}
                    className="glass-card glass-card-hover interactive group cursor-pointer rounded-xl p-4 text-left transition-all active:scale-[0.98]"
                  >
                    {/* Top row: avatar + name */}
                    <div className="flex items-center gap-3">
                      <div
                        className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br text-sm font-bold text-white shadow-sm ${gradient}`}
                      >
                        {initials}
                      </div>

                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-gray-800 group-hover:text-primary-600 transition-colors">
                          {binding.nickname || '微信用户'}
                        </p>
                        <p className="text-xs text-gray-400">
                          <code className="rounded bg-gray-100 px-0.5 text-[10px]">{binding.wxid}</code>
                        </p>
                      </div>
                    </div>

                    {/* Status row */}
                    <div className="mt-3 flex items-center justify-between border-t border-gray-100 pt-3">
                      <span className="inline-flex items-center gap-1 text-xs text-gray-500">
                        {hasCharacter ? (
                          <>
                            <User className="h-3 w-3" />
                            {binding.character_card_id}
                          </>
                        ) : (
                          <>
                            <Smartphone className="h-3 w-3" />
                            未选角色
                          </>
                        )}
                      </span>
                      <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                        <button
                          onClick={() => navigate(`/bindings/${binding.wxid}`)}
                          className="rounded-lg p-1.5 text-gray-300 transition hover:bg-primary-50 hover:text-primary-500"
                          title="设置角色"
                        >
                          <ExternalLink className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={(e) => handleUnbind(binding.wxid, e)}
                          className="rounded-lg p-1.5 text-gray-300 transition hover:bg-red-50 hover:text-red-500"
                          title="解除绑定"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </AnimatedPage>
  )
}

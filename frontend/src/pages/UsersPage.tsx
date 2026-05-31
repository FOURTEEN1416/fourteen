import { useState, useMemo, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { AnimatedPage, Skeleton, EmptyState } from '../components/shared'
import { listUsers, toUserDisplay, type UserDisplay } from '../api/users'

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

function getInitials(name: string): string {
  return name.slice(0, 2)
}

function gradientForUser(id: string): string {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = id.charCodeAt(i) + ((hash << 5) - hash)
  }
  return GRADIENTS[Math.abs(hash) % GRADIENTS.length]
}

function formatLastActive(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  return `${days} 天前`
}

// ---- Component ----

export default function UsersPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [users, setUsers] = useState<UserDisplay[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    listUsers()
      .then((res) => {
        if (cancelled) return
        setUsers(res.users.map(toUserDisplay))
        setTotal(res.total)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err?.response?.data?.detail || err?.message || '加载用户失败')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  const filteredUsers = useMemo(() => {
    if (!search.trim()) return users
    const q = search.trim().toLowerCase()
    return users.filter(
      (u) =>
        u.name.toLowerCase().includes(q) ||
        u.id.toLowerCase().includes(q),
    )
  }, [search, users])

  return (
    <AnimatedPage>
      <div className="min-h-screen bg-dynamic px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-6xl">
          {/* Header */}
          <div className="mb-5">
            <h1 className="text-xl font-bold text-gray-800">用户管理</h1>
            <p className="mt-0.5 text-sm text-gray-400">
              共{' '}
              <span className="font-semibold text-gray-600">
                {loading ? '-' : total}
              </span>{' '}
              位用户
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
                placeholder="搜索用户名称或 ID..."
                className="w-full border-0 bg-transparent pl-6 text-sm text-gray-700 placeholder:text-gray-300 focus:outline-none"
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

          {/* User grid */}
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
          ) : filteredUsers.length === 0 && !error ? (
            <EmptyState
              icon="👤"
              title={search ? '未找到匹配用户' : '暂无用户'}
              description={search ? '尝试修改搜索关键词' : '新用户注册后将显示在这里'}
            />
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {filteredUsers.map((user) => {
                const gradient = gradientForUser(user.id)
                const initials = getInitials(user.name)
                const lastActive = formatLastActive(user.lastActive)

                return (
                  <button
                    key={user.id}
                    onClick={() => navigate(`/users/${user.id}`)}
                    className="glass-card-hover group rounded-xl p-4 text-left transition-all active:scale-[0.98]"
                  >
                    {/* Top row: avatar + name */}
                    <div className="flex items-center gap-3">
                      <div
                        className={`relative flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br text-sm font-bold text-white shadow-sm ${gradient}`}
                      >
                        {initials}
                        <span
                          className={`absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-white ${
                            user.online ? 'bg-green-400' : 'bg-gray-300'
                          }`}
                        />
                      </div>

                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-gray-800 group-hover:text-primary-600 transition-colors">
                          {user.name}
                        </p>
                        <p className="text-xs text-gray-400">{user.affinityLevel} 级 · {user.affinityName}</p>
                      </div>
                    </div>

                    {/* Stats row */}
                    <div className="mt-3 flex items-center justify-between border-t border-gray-100 pt-3">
                      <span className="text-xs text-gray-500">
                        {user.totalChats} 条对话
                      </span>
                      <span className="text-xs text-gray-400">{lastActive}</span>
                    </div>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </AnimatedPage>
  )
}

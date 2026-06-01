import { useParams, useNavigate, useLocation, Outlet } from 'react-router-dom'
import { useUnifiedCharacters } from '../hooks/useQueries'
import { ArrowLeft, Bot, Plus, Settings, Activity } from 'lucide-react'
import type { UnifiedCharacter } from '../types/api'

// ═══ Helpers ═══

const TRAIT_LABELS: Record<string, string> = {
  warmth: '温暖',
  playfulness: '调皮',
  independence: '独立',
  jealousy: '吃醋',
  stubbornness: '固执',
}

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

function gradientForId(id: string): string {
  let hash = 0
  for (let i = 0; i < id.length; i++) {
    hash = id.charCodeAt(i) + ((hash << 5) - hash)
  }
  return GRADIENTS[Math.abs(hash) % GRADIENTS.length]
}

function getInitials(name: string): string {
  return name.slice(0, Math.min(2, name.length))
}

function topTraits(personality: Record<string, number>, count = 3): string[] {
  return Object.entries(personality)
    .sort(([, a], [, b]) => b - a)
    .slice(0, count)
    .map(([key]) => TRAIT_LABELS[key] || key)
}

// ═══ Character Card ═══

function CharacterCard({
  character,
  onClick,
}: {
  character: UnifiedCharacter
  onClick: () => void
}) {
  const gradient = gradientForId(character.id)
  const traits = topTraits(character.personality)

  return (
    <button
      onClick={onClick}
      className="glass-card-hover group relative rounded-xl p-4 text-left transition-all active:scale-[0.98] w-full"
    >
      {/* Active badge */}
      {character.is_active && (
        <span className="absolute right-3 top-3 flex items-center gap-1 rounded-full bg-green-50 px-2 py-0.5 text-[10px] font-medium text-green-600">
          <span className="h-1.5 w-1.5 rounded-full bg-green-400" />
          活跃
        </span>
      )}

      {/* Avatar + Name */}
      <div className="flex items-center gap-3">
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br text-sm font-bold text-white shadow-sm ${gradient}`}
        >
          {getInitials(character.name)}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-gray-800 group-hover:text-primary-600 transition-colors">
            {character.name}
          </p>
          {character.description && (
            <p className="truncate text-xs text-gray-400">{character.description}</p>
          )}
        </div>
      </div>

      {/* Personality traits */}
      {traits.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {traits.map((trait) => (
            <span
              key={trait}
              className="rounded-full bg-gray-100/80 px-2 py-0.5 text-[10px] text-gray-500"
            >
              {trait}
            </span>
          ))}
        </div>
      )}

      {/* Core anchors */}
      {character.core_anchors && character.core_anchors.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {character.core_anchors.slice(0, 3).map((anchor, i) => (
            <span
              key={i}
              className="rounded bg-primary-50/60 px-1.5 py-0.5 text-[10px] text-primary-600"
            >
              {anchor}
            </span>
          ))}
        </div>
      )}

      {/* Footer actions */}
      <div className="mt-3 flex items-center gap-2 border-t border-gray-100 pt-3">
        <span
          onClick={(e) => {
            e.stopPropagation()
            onClick()
          }}
          className="flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition-colors"
        >
          <Settings className="h-3 w-3" />
          设置
        </span>
        <span
          onClick={(e) => {
            e.stopPropagation()
            onClick()
          }}
          className="flex items-center gap-1 rounded-lg px-2 py-1 text-[10px] text-gray-400 hover:bg-gray-100 hover:text-gray-600 transition-colors"
        >
          <Activity className="h-3 w-3" />
          状态
        </span>
      </div>
    </button>
  )
}

// ═══ Main Component ═══

export default function UserWorkspace() {
  const { userId } = useParams<{ userId: string }>()
  const navigate = useNavigate()
  const location = useLocation()

  const { data, isLoading } = useUnifiedCharacters(userId)
  const characters = data?.characters ?? []
  const total = data?.total ?? characters.length

  // Check if we're at a child route (roles/create, roles/:roleId/*)
  const isChildRoute = location.pathname.includes('/roles/')

  // If at a child route, render Outlet
  if (isChildRoute) {
    return <Outlet />
  }

  // ── Index Route: Dashboard ──

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/users')}
              className="rounded-lg p-1.5 text-gray-300 transition hover:bg-gray-100 hover:text-gray-500"
              title="返回用户列表"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
            <div>
              <h1 className="text-lg font-bold text-gray-800">用户工作区</h1>
              <p className="text-xs text-gray-400">用户 ID: {userId}</p>
            </div>
          </div>

          <button
            onClick={() => navigate(`/users/${userId}/roles/create`)}
            className="flex items-center gap-1.5 rounded-lg bg-primary-500 px-3 py-2 text-xs font-medium text-white transition hover:bg-primary-600"
          >
            <Plus className="h-3.5 w-3.5" />
            创建角色
          </button>
        </div>

        {/* Stats row */}
        <div className="mb-6 grid grid-cols-3 gap-4">
          <div className="glass-card rounded-xl p-4">
            <div className="flex items-center gap-2">
              <Bot className="h-4 w-4 text-primary-500" />
              <span className="text-xs text-gray-500">角色总数</span>
            </div>
            <p className="mt-1 text-2xl font-bold text-gray-800">
              {isLoading ? '-' : total}
            </p>
          </div>
          <div className="glass-card rounded-xl p-4">
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-green-500" />
              <span className="text-xs text-gray-500">活跃角色</span>
            </div>
            <p className="mt-1 text-2xl font-bold text-gray-800">
              {isLoading ? '-' : characters.filter((c) => c.is_active).length}
            </p>
          </div>
          <div className="glass-card rounded-xl p-4">
            <div className="flex items-center gap-2">
              <Settings className="h-4 w-4 text-gray-400" />
              <span className="text-xs text-gray-500">创建时间</span>
            </div>
            <p className="mt-1 text-sm font-semibold text-gray-800">
              {isLoading
                ? '-'
                : characters.length > 0
                  ? new Date(
                      Math.min(
                        ...characters.map((c) => new Date(c.created_at).getTime()),
                      ),
                    ).toLocaleDateString('zh-CN')
                  : '暂无角色'}
            </p>
          </div>
        </div>

        {/* Character grid */}
        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={`skel-${i}`}
                className="glass-card rounded-xl p-4 animate-pulse space-y-3"
              >
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-full bg-gray-200/60" />
                  <div className="flex-1 space-y-1.5">
                    <div className="h-4 w-24 bg-gray-200/60 rounded" />
                    <div className="h-3 w-16 bg-gray-200/60 rounded" />
                  </div>
                </div>
                <div className="flex gap-1.5">
                  <div className="h-5 w-12 bg-gray-200/60 rounded-full" />
                  <div className="h-5 w-12 bg-gray-200/60 rounded-full" />
                </div>
              </div>
            ))}
          </div>
        ) : characters.length === 0 ? (
          <div className="glass-card rounded-xl p-12 text-center">
            <Bot className="mx-auto mb-3 h-10 w-10 text-gray-300" />
            <h3 className="text-sm font-semibold text-gray-600">暂无角色</h3>
            <p className="mt-1 text-xs text-gray-400">
              创建你的第一个 AI 角色开始对话
            </p>
            <button
              onClick={() => navigate(`/users/${userId}/roles/create`)}
              className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-xs font-medium text-white transition hover:bg-primary-600"
            >
              <Plus className="h-3.5 w-3.5" />
              创建角色
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {characters.map((character) => (
              <CharacterCard
                key={character.id}
                character={character}
                onClick={() =>
                  navigate(`/users/${userId}/roles/${character.id}/settings`)
                }
              />
            ))}
          </div>
        )}


      </div>
    </div>
  )
}

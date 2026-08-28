import { useNavigate } from 'react-router-dom'
import { useState } from 'react'
import { Plus, Sparkles } from 'lucide-react'
import AnimatedPage from '../components/shared/AnimatedPage'
import { useUnifiedCharacters } from '../hooks/useQueries'
import { activateCharacter } from '../api/characters'
import { useQueryClient } from '@tanstack/react-query'
import { sanitizeCharacterName } from '../utils/character'
import type { UnifiedCharacter } from '../types/api'

const tagClasses = ['tag-pink', 'tag-blue', 'tag-green']

function RoleCard({
  character,
  onActivate,
  activating,
}: {
  character: UnifiedCharacter
  onActivate: (id: string) => void
  activating: string | null
}) {
  return (
    <div
      className={`glass-card rounded-2xl p-4 transition-all hover:bg-white/40 ${
        character.is_active ? 'border-2 border-macaron-blue/40 relative' : 'cursor-pointer'
      }`}
    >
      <div className="text-sm font-semibold text-gray-800 mb-1">{sanitizeCharacterName(character.name)}</div>
      <div className="text-xs text-gray-500 mb-3 truncate">
        {character.description || '暂无描述'}
      </div>
      <div className="flex flex-wrap gap-1 mb-4">
        {character.core_anchors?.slice(0, 3).map((tag, i) => (
          <span key={tag} className={`${tagClasses[i % 3]} px-2 py-0.5 rounded text-[10px]`}>
            {tag}
          </span>
        )) || <span className="text-[10px] text-gray-300">无标签</span>}
      </div>

      {character.is_active ? (
        <div className="absolute top-3 right-3 text-[10px] px-2 py-0.5 rounded-full bg-macaron-blue text-white">
          当前活跃
        </div>
      ) : (
        <button
          onClick={() => onActivate(character.id)}
          disabled={activating === character.id}
          className="w-full py-1.5 rounded-lg text-xs border border-macaron-blue/40 text-macaron-blue-deep hover:bg-macaron-blue-light/30 transition-colors disabled:opacity-50"
        >
          {activating === character.id ? '激活中…' : '设为活跃'}
        </button>
      )}
    </div>
  )
}

export default function RolesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data, isLoading } = useUnifiedCharacters()
  const characters = data?.characters ?? []
  const [activatingId, setActivatingId] = useState<string | null>(null)

  const handleActivate = async (id: string) => {
    setActivatingId(id)
    try {
      await activateCharacter(id)
      await queryClient.invalidateQueries({ queryKey: ['characters'] })
    } catch {
      // 失败静默，实际项目可接入 toast
    } finally {
      setActivatingId(null)
    }
  }

  return (
    <AnimatedPage>
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-6xl">
          <div className="mb-6">
            <h1 className="text-xl font-bold text-gray-800">角色配置</h1>
            <p className="mt-0.5 text-sm text-gray-400">选择当前活跃角色，或创建新的自定义角色。</p>
          </div>

          {isLoading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="glass-card rounded-2xl p-4 h-40 animate-pulse bg-white/30" />
              ))}
            </div>
          ) : characters.length === 0 ? (
            <div className="glass-card rounded-2xl p-8 text-center text-gray-400">
              <Sparkles className="w-10 h-10 mx-auto mb-3 text-gray-300" />
              <p className="text-sm">暂无角色</p>
              <button
                onClick={() => navigate('/roles/create')}
                className="mt-4 btn-macaron rounded-xl px-5 py-2 text-xs font-medium text-white"
              >
                创建角色
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {characters.map((character) => (
                <RoleCard
                  key={character.id}
                  character={character}
                  onActivate={handleActivate}
                  activating={activatingId}
                />
              ))}
              <button
                onClick={() => navigate('/roles/create')}
                className="glass-card rounded-2xl p-4 flex flex-col items-center justify-center text-gray-400 hover:text-macaron-yellow-deep hover:bg-white/40 transition-all border-2 border-dashed border-white/50"
              >
                <Plus className="w-8 h-8 mb-2" />
                <span className="text-sm font-medium">创建角色</span>
              </button>
            </div>
          )}
        </div>
      </div>
    </AnimatedPage>
  )
}

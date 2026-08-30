import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useUnifiedCharacter } from '../hooks/useQueries'
import { sanitizeCharacterName } from '../utils/character'
import type { RoleSettingsTab } from '../types/framework'
import type { RoleSettingsCharacter } from '../types/framework'
import { SUB_TABS } from '../components/admin/RoleSettingsConstants'
import RoleSettingsTabs from '../components/admin/RoleSettingsTabs'

export default function RoleSettings() {
  const { userId, roleId } = useParams<{ userId: string; roleId: string }>()
  const [activeTab, setActiveTab] = useState<RoleSettingsTab>('basic')

  const characterId = roleId ? decodeURIComponent(roleId) : ''
  const { data: character, isLoading, error } = useUnifiedCharacter(characterId)

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-gray-400">加载角色中...</div>
      </div>
    )
  }

  if (error || !character) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-gray-400">角色不存在或加载失败</div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-6">
        {/* ── Character Header ── */}
        <div className="bg-white/70 backdrop-blur-sm border border-gray-200/60 rounded-2xl p-5 mb-5">
          <div className="flex items-center gap-4">
            {/* Avatar */}
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-primary-400 to-purple-500 flex items-center justify-center text-white text-xl font-bold shadow-sm shrink-0">
              {sanitizeCharacterName(character.name)[0]}
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="text-lg font-bold text-gray-800">{sanitizeCharacterName(character.name)}</h1>
              <p className="text-sm text-gray-500 truncate">{character.description}</p>
              <div className="flex items-center gap-3 mt-1.5">
                <span className="text-[11px] text-gray-400">ID: {characterId}</span>
                <span className="text-[11px] text-gray-400">用户: {userId || 'default'}</span>
                <span className="flex items-center gap-1 text-[11px] text-green-600">
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400" /> 活跃
                </span>
              </div>
            </div>
            <div className="text-right shrink-0 hidden sm:block">
              <p className="text-xs text-gray-400">最后更新</p>
              <p className="text-sm font-medium text-gray-700">{character.updated_at ? new Date(character.updated_at).toLocaleDateString() : '—'}</p>
            </div>
          </div>
        </div>

        {/* ── Tabs ── */}
        <div className="flex gap-1 p-1 bg-gray-100/60 rounded-2xl mb-5 overflow-x-auto">
          {SUB_TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`flex-1 min-w-[72px] shrink-0 flex items-center justify-center gap-1.5 px-3 py-2.5 text-sm font-medium rounded-xl transition-all duration-200 ${
                activeTab === t.key
                  ? 'tab-active'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>

        {/* ── Tab Content ── */}
        <RoleSettingsTabs tab={activeTab} character={character as unknown as RoleSettingsCharacter} />
      </div>
    </div>
  )
}

import { useNavigate, Link } from 'react-router-dom'
import { useMemo, useState } from 'react'
import { Plus, Search, Sparkles, AlertTriangle, Gift, X } from 'lucide-react'
import AnimatedPage from '../components/shared/AnimatedPage'
import { useUnifiedCharacters, queryKeys } from '../hooks/useQueries'
import { activateCharacter } from '../api/characters'
import { useQueryClient } from '@tanstack/react-query'
import { anchorTone, deriveCardSummary, sanitizeCharacterName } from '../utils/character'
import { useErrorStore } from '../store/errorStore'
import type { UnifiedCharacter } from '../types/api'

const INITIAL_NOTICE_KEY = 'initial-character-notice-done'

/** NEW-16（2026-10-29）：注册自动分发初始角色的一次性告知（可关闭并记住） */
function useInitialCharacterNotice(hasActive: boolean) {
  const [visible, setVisible] = useState(() => {
    try {
      return localStorage.getItem(INITIAL_NOTICE_KEY) !== 'done'
    } catch {
      return true
    }
  })
  const dismiss = () => {
    setVisible(false)
    try {
      localStorage.setItem(INITIAL_NOTICE_KEY, 'done')
    } catch {
      /* 隐私模式等场景下仅本次会话内消失 */
    }
  }
  return { show: visible && hasActive, dismiss }
}

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
      className={`glass-card rounded-2xl p-4 transition-all hover:bg-white/40 flex flex-col h-full stagger-item ${
        character.is_active ? 'border-2 border-macaron-blue/40 relative' : 'cursor-pointer'
      }`}
    >
      <div className="text-sm font-semibold text-gray-800 mb-1 pr-14 truncate">
        {sanitizeCharacterName(character.name)}
      </div>
      <div className="text-xs text-gray-500 mb-3 leading-relaxed line-clamp-2 min-h-[2rem]">
        {deriveCardSummary(character.description, sanitizeCharacterName(character.name), character.core_anchors ?? [])}
      </div>
      {/* 锚点可能是整段人格长文（角色卡实况），展示层截断保卡片等高，全文悬浮可见 */}
      <div className="flex flex-wrap gap-1 mb-4 min-h-[1.25rem] content-start">
        {(character.core_anchors?.slice(0, 3).map((tag) => (
          <span
            key={tag}
            title={tag}
            className={`tag-${anchorTone(tag)} px-2 py-0.5 rounded text-[10px] max-w-full inline-block truncate align-bottom`}
            style={{ maxWidth: '100%' }}
          >
            {tag.length > 14 ? `${tag.slice(0, 14)}…` : tag}
          </span>
        ))) ?? <span className="text-[10px] text-gray-400">无标签</span>}
      </div>

      {character.is_active ? (
        <>
          <div className="absolute top-3 right-3 text-[10px] px-2 py-0.5 rounded-full bg-macaron-blue text-text-primary">
            当前活跃
          </div>
          <div className="mt-auto flex items-center gap-2">
            <div className="flex-1 w-full py-1.5 rounded-lg text-xs border border-macaron-blue/20 bg-macaron-blue-light/30 text-macaron-blue-deep text-center">
              正在陪伴你
            </div>
            {/* NEW-10：活跃角色也给设置入口（统一卡片操作区） */}
            <Link
              to={`/roles/${character.id}/settings`}
              className="shrink-0 py-1.5 px-2.5 rounded-lg text-xs border border-gray-200 text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-colors"
              title="角色设置"
            >
              设置
            </Link>
          </div>
        </>
      ) : (
        <button
          onClick={() => onActivate(character.id)}
          disabled={activating === character.id}
          className="mt-auto w-full py-1.5 rounded-lg text-xs border border-macaron-blue/40 text-macaron-blue-deep hover:bg-macaron-blue-light/30 transition-colors disabled:opacity-50"
        >
          {activating === character.id ? '激活中…' : '设为活跃'}
        </button>
      )}
      {/* NEW-10：非活跃角色也有「设置」入口——编辑人格/语音不该要求先激活
          （激活是切换聊天对象的全局副作用；RoleSettings 页头本就支持未激活态） */}
      {!character.is_active && (
        <Link
          to={`/roles/${character.id}/settings`}
          className="mt-2 w-full py-1 rounded-lg text-[11px] text-gray-400 hover:text-gray-600 text-center transition-colors"
        >
          设置与详情 →
        </Link>
      )}
    </div>
  )
}

export default function RolesPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  // NEW-9：isError 不得伪装成「暂无角色」空态（用户会误以为角色全没了）
  const { data, isLoading, isError, refetch } = useUnifiedCharacters()
  const characters = data?.characters ?? []
  const hasActive = characters.some(c => c.is_active)
  const initialNotice = useInitialCharacterNotice(hasActive)
  const [activatingId, setActivatingId] = useState<string | null>(null)
  const [keyword, setKeyword] = useState('')

  const visible = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    const matched = kw
      ? characters.filter(
          (c) =>
            sanitizeCharacterName(c.name).toLowerCase().includes(kw) ||
            (c.description ?? '').toLowerCase().includes(kw) ||
            c.core_anchors?.some((a) => a.toLowerCase().includes(kw)),
        )
      : characters
    // 活跃角色置顶，其余按名字稳定排序
    return [...matched].sort((a, b) => Number(b.is_active) - Number(a.is_active))
  }, [characters, keyword])

  const handleActivate = async (id: string) => {
    setActivatingId(id)
    try {
      const res = await activateCharacter(id)
      // W11-D5：契约 {"status":"activated"}；非 activated 视为失败，不静默吞掉
      if (res?.status !== 'activated') {
        useErrorStore.getState().addToast({ type: 'warning', message: '切换角色未生效（后端未确认），请重试' })
        return
      }
      // W17 遗留①收口：真实缓存键带账号维度（queryKeys.characters.all），裸 ['characters'] 前缀不匹配
      await queryClient.invalidateQueries({ queryKey: queryKeys.characters.all })
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : '切换角色失败，请重试'
      useErrorStore.getState().addToast({ type: 'error', message })
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

          {/* NEW-16：注册自动分发并激活的初始角色——旧实现只有 4 秒 toast，此后无任何持久告知 */}
          {initialNotice.show && (
            <div
              className="mb-4 flex items-center gap-2 rounded-xl border border-macaron-yellow/40 bg-macaron-yellow-light/50 px-4 py-3 text-sm"
              data-testid="initial-character-notice"
            >
              <Gift className="h-4 w-4 shrink-0 text-macaron-yellow-deep" />
              <span className="text-gray-600">
                已经为你准备好了初始角色并设为活跃——可以直接去微信开始对话，也可以在这里切换其他角色。
              </span>
              <button
                onClick={initialNotice.dismiss}
                aria-label="知道了"
                className="ml-auto shrink-0 rounded-lg p-1 text-gray-400 hover:text-gray-600 hover:bg-white/60 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          )}

          {isLoading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="glass-card rounded-2xl p-4 h-40 animate-pulse bg-white/30" />
              ))}
            </div>
          ) : isError ? (
            <div className="glass-card rounded-2xl p-8 text-center">
              <AlertTriangle className="w-10 h-10 mx-auto mb-3 text-red-300" />
              <p className="text-sm text-gray-500">角色列表加载失败，请检查网络后重试</p>
              <button
                onClick={() => void refetch()}
                className="mt-4 btn-macaron rounded-xl px-5 py-2 text-xs font-medium"
              >
                重试
              </button>
            </div>
          ) : characters.length === 0 ? (
            <div className="glass-card rounded-2xl p-8 text-center text-gray-400">
              <Sparkles className="w-10 h-10 mx-auto mb-3 text-gray-400" />
              <p className="text-sm">暂无角色</p>
              {/* NEW-16：初始角色分发失败的静默分支——给一句显式引导（不阻断注册） */}
              <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-gray-400">
                注册时如未自动分配初始角色，可在这里创建一个开始使用
              </p>
              <div className="mt-4 flex items-center justify-center gap-2">
                <button
                  onClick={() => navigate('/roles/create')}
                  className="btn-macaron rounded-xl px-5 py-2 text-xs font-medium"
                >
                  创建角色
                </button>
                {/* NEW-10：空态并列模板入口 */}
                <Link
                  to="/templates"
                  className="rounded-xl border border-macaron-blue/40 px-5 py-2 text-xs font-medium text-macaron-blue-deep hover:bg-macaron-blue-light/30 transition-colors"
                >
                  从模板挑一个
                </Link>
              </div>
            </div>
          ) : (
            <>
              {characters.length > 6 && (
                <div className="mb-4 flex items-center gap-2 max-w-sm">
                  <Search className="w-4 h-4 text-gray-400 shrink-0" />
                  <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="搜索角色名、描述或标签…"
                    className="w-full rounded-xl bg-white/60 border border-white/50 px-3 py-2 text-xs outline-none focus:border-macaron-blue/50 transition-colors"
                  />
                </div>
              )}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {visible.map((character, i) => (
                  <div key={character.id} className="stagger-item h-full" style={{ animationDelay: `${Math.min(i, 12) * 60}ms` }}>
                    <RoleCard
                      character={character}
                      onActivate={handleActivate}
                      activating={activatingId}
                    />
                  </div>
                ))}
                {visible.length === 0 && (
                  <div className="glass-card rounded-2xl p-6 text-center text-xs text-gray-400 sm:col-span-2 lg:col-span-4">
                    没有匹配「{keyword}」的角色
                  </div>
                )}
                <button
                  onClick={() => navigate('/roles/create')}
                  className="glass-card rounded-2xl p-4 flex flex-col items-center justify-center text-gray-400 hover:text-macaron-yellow-deep hover:bg-white/40 transition-all border-2 border-dashed border-white/50"
                >
                  <Plus className="w-8 h-8 mb-2" />
                  <span className="text-sm font-medium">创建角色</span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </AnimatedPage>
  )
}

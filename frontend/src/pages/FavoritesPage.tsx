import { useState } from 'react'
import { useCharacters } from '../hooks/useQueries'
import { shisiClient } from '../api/shisiClient'
import Card from '../components/common/Card'
import Badge from '../components/common/Badge'
import Button from '../components/common/Button'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { Star, Trash2, Send } from 'lucide-react'
import type { CharacterState } from '../types/character'

interface FavoriteItem {
  id: string
  content?: string
  message?: string
  timestamp?: string
}

export default function FavoritesPage() {
  const { data: characters } = useCharacters()
  const [favs, setFavs] = useState<FavoriteItem[]>([])
  const [loading, setLoading] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [selectedChar, setSelectedChar] = useState('')
  const [forwardTarget, setForwardTarget] = useState('')

  const charList = (characters ?? []) as CharacterState[]
  const activeChar = charList.find((c: CharacterState) => c.is_active)

  const loadFavorites = async () => {
    setLoading(true)
    try {
      const cid = selectedChar || activeChar?.character_id
      if (!cid) { setLoading(false); return }
      const r = await shisiClient.memory.favorites(cid)
      setFavs(r as FavoriteItem[])
      setLoaded(true)
    } catch { setFavs([]) }
    setLoading(false)
  }

  const removeFavorite = async (id: string) => {
    try {
      const cid = selectedChar || activeChar?.character_id
      await shisiClient.memory.removeFavorite(id, cid)
      setFavs(favs.filter((f: FavoriteItem) => f.id !== id))
    } catch { /* ignore */ }
  }

  const forwardMemory = async (memoryId: string) => {
    if (!forwardTarget) return
    try {
      const cid = selectedChar || activeChar?.character_id
      if (!cid) return
      await shisiClient.memory.forward(cid, forwardTarget, memoryId)
      setForwardTarget('')
    } catch { /* ignore */ }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
        <Star className="w-4 h-4 text-yellow-500" />
        收藏记忆
      </h1>

      <Card>
        <div className="flex items-center gap-3">
          <select
            value={selectedChar}
            onChange={e => setSelectedChar(e.target.value)}
            className="text-sm border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-blue-400/30"
          >
            <option value="">选择角色</option>
            {charList.map((c: CharacterState) => (
              <option key={c.character_id} value={c.character_id}>{c.name}{c.is_active ? ' (当前)' : ''}</option>
            ))}
          </select>
          <Button size="sm" onClick={loadFavorites} loading={loading}>加载收藏</Button>
        </div>
      </Card>

      {!loaded ? (
        <Card>
          <EmptyState icon="⭐" title="选择角色后加载收藏记忆" description="收藏的记忆可在对话中快速引用" />
        </Card>
      ) : favs.length === 0 ? (
        <Card>
          <EmptyState icon="📭" title="暂无收藏记忆" description="在聊天中使用 /fav 命令收藏消息" />
        </Card>
      ) : (
        <div className="space-y-3">
          {favs.map((fav: FavoriteItem, i: number) => (
            <Card key={i}>
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-700">{fav.content || fav.message || JSON.stringify(fav)}</p>
                  {fav.timestamp && <p className="text-[10px] text-gray-400 mt-1">{fav.timestamp}</p>}
                </div>
                <div className="flex items-center gap-2 ml-3 shrink-0">
                  <button
                    onClick={() => removeFavorite(fav.id)}
                    className="text-gray-400 hover:text-red-500 transition-colors"
                    title="取消收藏"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                  <div className="flex items-center gap-1">
                    <select
                      value={forwardTarget}
                      onChange={e => setForwardTarget(e.target.value)}
                      className="text-[10px] border border-gray-200 rounded px-1 py-0.5"
                    >
                      <option value="">转发到...</option>
                      {charList.filter((c: CharacterState) => c.character_id !== (selectedChar || activeChar?.character_id)).map((c: CharacterState) => (
                        <option key={c.character_id} value={c.character_id}>{c.name}</option>
                      ))}
                    </select>
                    <button
                      onClick={() => forwardMemory(fav.id)}
                      disabled={!forwardTarget}
                      className="text-gray-400 hover:text-blue-500 transition-colors disabled:opacity-30"
                      title="转发记忆"
                    >
                      <Send className="w-4 h-4" />
                    </button>
                  </div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}

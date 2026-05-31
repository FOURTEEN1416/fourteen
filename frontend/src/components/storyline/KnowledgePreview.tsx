import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import client from '../../api/client'
import Badge from '../common/Badge'
import Button from '../common/Button'

interface KnowledgeStats {
  indexed: boolean
  total_chunks: number
  retriever_type: string
  sources: { name: string; count: number }[]
}

interface SearchResult {
  content: string
  source: string
  score: number
}

interface KnowledgePreviewProps {
  characterId: string
}

export default function KnowledgePreview({ characterId }: KnowledgePreviewProps) {
  const [expanded, setExpanded] = useState(false)
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searching, setSearching] = useState(false)

  const { data: stats, isLoading } = useQuery<KnowledgeStats>({
    queryKey: ['knowledge', characterId, 'stats'],
    queryFn: async () => {
      const r = await client.get(`/characters/${characterId}/knowledge/stats`)
      return r.data as KnowledgeStats
    },
    enabled: expanded,
    staleTime: 60 * 1000,
  })

  async function handleSearch() {
    if (!query.trim()) return
    setSearching(true)
    try {
      const r = await client.post(`/characters/${characterId}/knowledge/search`, { query: query.trim(), top_k: 5 })
      setSearchResults(r.data.results || [])
    } catch {
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }

  const sourceLabel: Record<string, string> = {
    personality: '性格',
    scenario: '场景',
    creator_notes: '创作笔记',
    world_info: '世界书',
    mes_example: '对话样例',
    description: '描述',
    'personality.core_anchors': '核心锚点',
  }

  return (
    <div className="border-t border-gray-100 pt-3 mt-3">
      <button
        type="button"
        className="flex items-center justify-between w-full text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 uppercase tracking-wider">知识库</span>
          {stats?.indexed && <Badge variant="default">{stats.total_chunks} 条</Badge>}
        </div>
        <span className="text-xs text-gray-400">{expanded ? '▲' : '▼'}</span>
      </button>

      {expanded && (
        <div className="mt-3 space-y-3">
          {isLoading ? (
            <div className="text-xs text-gray-400 animate-pulse">加载知识库…</div>
          ) : !stats?.indexed ? (
            <div className="bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 text-xs text-gray-500">
              暂无角色知识库，保存角色后将自动生成
            </div>
          ) : (
            <>
              {/* 来源统计 */}
              <div className="flex flex-wrap gap-1">
                {stats.sources.map(s => (
                  <Badge key={s.name} variant="default">
                    {sourceLabel[s.name] || s.name}: {s.count}
                  </Badge>
                ))}
              </div>

              {/* 检索测试 */}
              <div>
                <label className="block text-[10px] text-gray-500 mb-1">检索测试</label>
                <div className="flex gap-1.5">
                  <input
                    type="text" value={query} placeholder="输入关键词搜索角色知识"
                    onChange={e => setQuery(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleSearch()}
                    className="flex-1 border border-gray-200 rounded-lg px-2.5 py-1.5 text-xs"
                  />
                  <Button variant="secondary" size="sm" onClick={handleSearch} disabled={searching}>
                    {searching ? '搜索中' : '搜索'}
                  </Button>
                </div>
              </div>

              {/* 搜索结果 */}
              {searchResults.length > 0 && (
                <div className="space-y-1.5">
                  {searchResults.map((r, i) => (
                    <div key={i} className="bg-gray-50 border border-gray-200 rounded-lg px-2.5 py-2">
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <Badge variant="default">{sourceLabel[r.source] || r.source}</Badge>
                        <span className="text-[10px] text-gray-400">{(r.score * 100).toFixed(0)}%</span>
                      </div>
                      <p className="text-xs text-gray-700 line-clamp-2">{r.content}</p>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

import { useState } from 'react'
import { useMemoryFacts } from '../hooks/useAPI'
import { Search } from 'lucide-react'
import EmptyState from '../components/common/EmptyState'

const categories = ['', '偏好', '习惯', '个人信息', '日程']

export default function MemoryPage() {
  const { facts, loading, refetch } = useMemoryFacts()
  const [filter, setFilter] = useState('')
  const [searchText, setSearchText] = useState('')

  const filteredFacts = facts.filter((f) => {
    if (filter && f.type !== filter) return false
    if (searchText && !f.content.includes(searchText)) return false
    return true
  })

  const typeColors: Record<string, string> = {
    '偏好': 'bg-slate-700/40 text-slate-300 border-slate-600/30',
    '习惯': 'bg-slate-700/40 text-slate-300 border-slate-600/30',
    '个人信息': 'bg-slate-700/40 text-slate-300 border-slate-600/30',
    '日程': 'bg-slate-700/40 text-slate-300 border-slate-600/30',
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-slate-200 mb-6">记忆浏览器</h1>

      {/* Search & Filter */}
      <div className="flex items-center gap-3 mb-6">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <input
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="搜索记忆..."
            className="w-full bg-slate-800/50 text-slate-200 placeholder-slate-500 rounded-xl pl-9 pr-4 py-2 text-sm outline-none focus:ring-2 focus:ring-primary-500/30 border border-slate-700/50"
          />
        </div>

        <div className="flex gap-1">
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setFilter(cat)}
              className={`px-3 py-1.5 rounded-lg text-xs transition-colors ${
                filter === cat
                  ? 'bg-primary-600/30 text-primary-300 border border-primary-500/30'
                  : 'text-slate-500 hover:text-slate-300 border border-transparent'
              }`}
            >
              {cat || '全部'}
            </button>
          ))}
        </div>

        <button
          onClick={() => refetch()}
          className="text-xs text-slate-500 hover:text-slate-300 px-2"
        >
          刷新
        </button>
      </div>

      {/* Facts Grid */}
      {loading ? (
        <div className="text-sm text-slate-500">加载中...</div>
      ) : filteredFacts.length === 0 ? (
        <EmptyState
          icon="💬"
          title="暂无记忆数据"
          description="开始和小暖聊天，她会逐渐记住你的偏好和习惯"
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredFacts.map((fact, i) => (
            <div
              key={i}
              className="bg-slate-900/40 border border-slate-800/60 rounded-xl p-4 hover:border-slate-700/60 transition-colors"
            >
              <div className="flex items-center gap-2 mb-2">
                <span className={`text-[10px] px-2 py-0.5 rounded-full border ${typeColors[fact.type] || 'bg-slate-700/30 text-slate-400 border-slate-600/30'}`}>
                  {fact.type}
                </span>
                {fact.confidence && (
                  <span className="text-[10px] text-slate-600">
                    置信度 {Math.round(fact.confidence * 100)}%
                  </span>
                )}
              </div>
              <p className="text-sm text-slate-300">{fact.content}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

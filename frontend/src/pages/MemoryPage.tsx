import { useState } from 'react'
import { useMemoryFacts } from '../hooks/useQueries'
import { Search } from 'lucide-react'
import EmptyState from '../components/common/EmptyState'

const categories = ['', '偏好', '习惯', '个人信息', '日程']

const categoryColors: Record<string, string> = {
  '偏好': 'bg-blue-900/30 text-blue-400 border-blue-700/30',
  '习惯': 'bg-green-900/30 text-green-400 border-green-700/30',
  '个人信息': 'bg-purple-900/30 text-purple-400 border-purple-700/30',
  '日程': 'bg-orange-900/30 text-orange-400 border-orange-700/30',
}

export default function MemoryPage() {
  const { data: facts = [], isLoading: loading, refetch } = useMemoryFacts()
  const [filter, setFilter] = useState('')
  const [searchText, setSearchText] = useState('')

  const filteredFacts = facts.filter((f) => {
    const cat = f.category || f.type
    if (filter && cat !== filter) return false
    if (searchText && !f.content.includes(searchText)) return false
    return true
  })

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-gray-800 mb-6">记忆浏览器</h1>

      {/* Search & Filter */}
      <div className="flex items-center gap-3 mb-6">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            placeholder="搜索记忆..."
            className="w-full bg-gray-200/50 text-gray-800 placeholder-slate-500 rounded-xl pl-9 pr-4 py-2 text-sm outline-none focus:ring-2 focus:ring-primary-500/30 border border-gray-300/50"
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
                  : 'text-gray-400 hover:text-gray-700 border border-transparent'
              }`}
            >
              {cat || '全部'}
            </button>
          ))}
        </div>

        <button
          onClick={() => refetch()}
          className="text-xs text-gray-400 hover:text-gray-700 px-2"
        >
          刷新
        </button>
      </div>

      {/* Facts Grid */}
      {loading ? (
        <div className="text-sm text-gray-400">加载中...</div>
      ) : filteredFacts.length === 0 ? (
        <EmptyState
          icon="💬"
          title={searchText ? '暂无匹配的记忆' : '暂无记忆数据'}
          description={searchText ? '尝试其他搜索词' : '开始聊天，她会逐渐记住你的偏好和习惯'}
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredFacts.map((fact, i) => (
            <div
              key={i}
              className="bg-white/80 border border-gray-200 rounded-xl p-4 hover:border-gray-300/60 transition-colors"
            >
              <div className="flex items-center gap-2 mb-2">
                <span className={`text-[10px] px-2 py-0.5 rounded-full border ${categoryColors[fact.category || fact.type] || 'bg-gray-200/30 text-gray-500 border-gray-300'}`}>
                  {fact.category || fact.type}
                </span>
                {fact.confidence && (
                  <span className="text-[10px] text-gray-300">
                    置信度 {Math.round(fact.confidence * 100)}%
                  </span>
                )}
              </div>
              <p className="text-sm text-gray-700">{fact.content}</p>
              {(fact.source || fact.timestamp) && (
                <div className="flex gap-3 mt-2 text-[10px] text-gray-300">
                  {fact.source && <span>来源: {fact.source}</span>}
                  {fact.timestamp && <span>时间: {fact.timestamp}</span>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

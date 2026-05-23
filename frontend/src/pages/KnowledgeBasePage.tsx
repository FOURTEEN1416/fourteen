import { useState } from 'react'
import { useRAGStats } from '../hooks/useQueries'
import { api } from '../api/client'
import Card from '../components/common/Card'
import Button from '../components/common/Button'
import Skeleton from '../components/common/Skeleton'
import Badge from '../components/common/Badge'
import { Search, Upload, Database } from 'lucide-react'

export default function KnowledgeBasePage() {
  const { data: stats, isLoading } = useRAGStats()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<any[]>([])
  const [searching, setSearching] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')

  const doSearch = async () => {
    if (!query.trim()) return
    setSearching(true)
    try {
      const r = await api.ragSearch(query)
      setResults(r.data.results || [])
    } catch { setResults([]) }
    setSearching(false)
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    try {
      const r = await api.ragUpload(file)
      setUploadMsg(`已上传: ${r.data.filename} (${r.data.size} 字节)`)
    } catch { setUploadMsg('上传失败') }
  }

  if (isLoading || !stats) {
    return <div className="flex-1 p-6"><Skeleton lines={4} /></div>
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
        <Database className="w-4 h-4 text-blue-500" />
        知识库管理
      </h1>

      <Card>
        <div className="flex items-center gap-3">
          <Badge variant={stats.available ? 'success' : 'error'}>
            {stats.available ? '已就绪' : '不可用'}
          </Badge>
          <span className="text-xs text-gray-500">
            BM25: {stats.bm25_available ? '可用' : '不可用'}
          </span>
        </div>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <Upload className="w-4 h-4 text-gray-500" />
          上传文档
        </h2>
        <div className="flex items-center gap-3">
          <label className="cursor-pointer">
            <input type="file" accept=".txt,.md,.json" onChange={handleUpload} className="hidden" />
            <div className="px-3 py-2 border border-dashed border-gray-300 rounded-lg text-xs text-gray-500 hover:border-blue-400 hover:text-blue-500 transition-colors">
              选择文件 (.txt/.md/.json)
            </div>
          </label>
          {uploadMsg && <span className="text-xs text-green-600">{uploadMsg}</span>}
        </div>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <Search className="w-4 h-4 text-gray-500" />
          检索测试
        </h2>
        <div className="flex gap-2 mb-4">
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && doSearch()}
            placeholder="输入检索关键词..."
            className="flex-1 px-3 py-1.5 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-400/30"
          />
          <Button size="sm" onClick={doSearch} loading={searching}>搜索</Button>
        </div>
        {results.length > 0 && (
          <div className="space-y-2">
            {results.map((r, i) => (
              <div key={i} className="p-2 bg-gray-50 rounded-lg text-xs">
                <div className="text-gray-700">{r.content}</div>
                {r.score !== undefined && (
                  <div className="text-gray-400 mt-1">相关度: {(r.score * 100).toFixed(0)}%</div>
                )}
              </div>
            ))}
          </div>
        )}
        {results.length === 0 && query && !searching && (
          <p className="text-xs text-gray-400">无匹配结果</p>
        )}
      </Card>
    </div>
  )
}

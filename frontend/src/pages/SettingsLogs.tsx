import { useState, useMemo, useEffect, useCallback, useRef } from 'react'
import { Select } from '../components/shared'
import { logs } from '../api/system'
import type { LogEntry } from '../types/framework'

const levelOptions = [
  { value: 'ALL', label: 'ALL' },
  { value: 'DEBUG', label: 'DEBUG' },
  { value: 'INFO', label: 'INFO' },
  { value: 'WARN', label: 'WARN' },
  { value: 'ERROR', label: 'ERROR' },
]

const POLL_INTERVAL = 5000 // 5s

/** 后端日志记录 → 前端 LogEntry */
function toLogEntry(raw: Record<string, unknown>): LogEntry {
  return {
    timestamp: (raw.time as string) || '',
    level: ((raw.level as string)?.toUpperCase() as LogEntry['level']) || 'INFO',
    message: (raw.msg as string) || '',
  }
}

function sortLogs(entries: LogEntry[]): LogEntry[] {
  return [...entries].sort((a, b) => (b.timestamp || '').localeCompare(a.timestamp || ''))
}

function SettingsLogs() {
  const [allLogs, setAllLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [activeLevel, setActiveLevel] = useState('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [paused, setPaused] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const doFetch = useCallback(async () => {
    try {
      const res = await logs({ limit: 200 })
      const rawLogs: Record<string, unknown>[] = res.data?.logs ?? []
      setAllLogs(sortLogs(rawLogs.map(toLogEntry)))
    } catch {
      // 全局 interceptor 已自动显示 toast 通知
    } finally {
      setLoading(false)
    }
  }, [])

  // Initial fetch
  useEffect(() => {
    doFetch()
  }, [doFetch])

  // Polling (when not paused)
  useEffect(() => {
    if (paused) {
      if (pollRef.current) clearInterval(pollRef.current)
      return
    }
    pollRef.current = setInterval(doFetch, POLL_INTERVAL)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [paused, doFetch])

  const filteredLogs = useMemo(() => {
    return allLogs.filter(entry => {
      if (activeLevel !== 'ALL' && entry.level !== activeLevel) return false
      if (searchQuery && !entry.message.toLowerCase().includes(searchQuery.toLowerCase())) return false
      return true
    })
  }, [allLogs, activeLevel, searchQuery])

  function handleClear() {
    setAllLogs([])
  }

  function handleExport() {
    const blob = new Blob(
      [allLogs.map(e => `[${e.timestamp}] [${e.level}] ${e.message}`).join('\n')],
      { type: 'text/plain' },
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `logs-${new Date().toISOString().slice(0, 10)}.txt`
    a.click()
    URL.revokeObjectURL(url)
  }

  function levelColor(level: LogEntry['level']) {
    switch (level) {
      case 'DEBUG': return 'text-gray-400'
      case 'INFO': return 'text-primary-600'
      case 'WARN': return 'text-amber-600'
      case 'ERROR': return 'text-red-600'
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-700 mb-4">日志</h2>
        <p className="text-xs text-gray-400 mb-2">查看系统运行日志、调试信息与错误记录</p>
      </div>

      <div className="glass-card rounded-xl p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div className="w-40">
            <Select
              options={levelOptions}
              value={activeLevel}
              onChange={(v: string) => setActiveLevel(v)}
              placeholder="选择级别"
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={doFetch}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 transition-all"
              title="手动刷新"
            >
              刷新
            </button>
            <button
              onClick={handleClear}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-gray-100 text-gray-600 hover:bg-gray-200 transition-all"
            >
              清屏
            </button>
            <button
              onClick={() => setPaused(!paused)}
              className={`px-4 py-2 text-sm font-medium rounded-lg transition-all ${
                paused ? 'bg-amber-100 text-amber-600' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {paused ? '已暂停' : '暂停'}
            </button>
            <button
              onClick={handleExport}
              className="px-4 py-2 text-sm font-medium rounded-lg bg-primary-500 text-white hover:bg-primary-600 transition-all"
            >
              导出
            </button>
          </div>
        </div>

        <div>
          <input
            type="text"
            placeholder="搜索日志内容..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full glass-card rounded-lg px-3 py-2 text-sm text-gray-700 outline-none focus:ring-2 focus:ring-primary-400/50 placeholder:text-gray-400"
          />
        </div>
      </div>

      <div className="max-h-[520px] overflow-y-auto space-y-1 scroll-smooth">
        {loading ? (
          <div className="glass-card rounded-xl p-8 text-center">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary-500 border-t-transparent mx-auto" />
            <p className="text-gray-400 text-sm mt-2">加载日志中...</p>
          </div>
        ) : filteredLogs.length === 0 ? (
          <div className="glass-card rounded-xl p-8 text-center">
            <p className="text-gray-400 text-sm">暂无匹配的日志条目</p>
          </div>
        ) : (
          filteredLogs.map((entry, idx) => (
            <div
              key={`${entry.timestamp}-${idx}`}
              className="glass-card rounded-lg px-3 py-2 flex items-start gap-2 animate-slide-up"
              style={{ animationDelay: `${Math.min(idx * 20, 400)}ms` }}
            >
              <span className="text-xs font-mono text-gray-400 shrink-0 whitespace-nowrap">
                [{entry.timestamp}]
              </span>
              <span className={[
                'text-xs font-mono font-medium shrink-0 whitespace-nowrap',
                levelColor(entry.level),
              ].join(' ')}>
                [{entry.level}]
              </span>
              <span className="text-xs font-mono text-gray-700 break-all leading-relaxed">
                {entry.message}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

export default SettingsLogs

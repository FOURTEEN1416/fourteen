import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import { useSSE } from '../hooks/useSSE'
import { useLogStore } from '../store/logStore'
import type { LogEntry, LogLevel } from '../types/api'

const API_BASE = '/api'

export default function LogsPage() {
  const { entries, filter, search, setFilter, setSearch, appendEntry, setEntries, sseConnected, sseReconnecting } = useLogStore()
  const [pollingFallback, setPollingFallback] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const handleSSEMessage = useCallback((data: string) => {
    try {
      const entry = JSON.parse(data) as LogEntry
      appendEntry(entry)
    } catch { /* ignore malformed */ }
  }, [appendEntry])

  const { connected, reconnecting, connect, disconnect } = useSSE(`${API_BASE}/logs/stream`, {
    onMessage: handleSSEMessage,
    onError: () => { setPollingFallback(true) },
  })

  useEffect(() => {
    if (pollingFallback) {
      const fetchLogs = () => {
        api.logs({ limit: 200, level: filter || undefined, search: search || undefined })
          .then(({ data }) => setEntries((data as { logs: LogEntry[] }).logs))
          .catch(() => {})
      }
      fetchLogs()
      const interval = setInterval(fetchLogs, 5000)
      return () => clearInterval(interval)
    }
  }, [pollingFallback, filter, search, setEntries])

  useEffect(() => {
    if (!pollingFallback && entries.length === 0) {
      api.logs({ limit: 200 }).then(({ data }) => setEntries((data as { logs: LogEntry[] }).logs)).catch(() => {})
    }
  }, [pollingFallback, entries.length, setEntries])

  const filteredEntries = entries.filter((l) => {
    if (filter && l.level !== filter) return false
    if (search && !l.msg.toLowerCase().includes(search.toLowerCase()) && !l.module.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  const levelColor = (lvl: string) =>
    lvl === 'ERROR' || lvl === 'CRITICAL' ? 'text-red-400' :
    lvl === 'WARNING' ? 'text-yellow-400' :
    lvl === 'DEBUG' ? 'text-gray-400' : 'text-blue-400'

  const sseIndicator = sseConnected ? 'bg-green-400' : sseReconnecting ? 'bg-yellow-400' : 'bg-red-400'
  const sseLabel = sseConnected ? 'SSE 已连接' : sseReconnecting ? 'SSE 重连中' : 'SSE 断开'

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-base font-semibold text-gray-800">运行日志</h1>
        <div className="flex items-center gap-1.5 text-xs text-gray-400">
          <span className={`w-2 h-2 rounded-full ${sseIndicator}`} />
          {sseLabel}
        </div>
      </div>

      <div className="flex items-center gap-2 mb-4">
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="搜索日志..."
          className="bg-white/80 border border-gray-200 text-gray-800 placeholder-slate-500 rounded-lg px-3 py-1.5 text-xs w-48 outline-none" />
        <select value={filter} onChange={(e) => setFilter(e.target.value as LogLevel | '')}
          className="bg-white/80 border border-gray-200 text-gray-800 rounded-lg px-2 py-1.5 text-xs outline-none">
          <option value="">所有</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
      </div>

      <div className="bg-white/80 border border-gray-200 rounded-xl overflow-hidden">
        <div className="max-h-[60vh] overflow-y-auto font-mono text-xs">
          {filteredEntries.length === 0 ? (
            <div className="p-8 text-center text-gray-400 text-xs">暂无日志</div>
          ) : (
            filteredEntries.map((l, i) => (
              <div key={i} className="flex items-start gap-3 px-4 py-1.5 border-b border-gray-200/30 hover:bg-gray-200/20">
                <span className="text-gray-300 shrink-0 whitespace-nowrap">{l.time}</span>
                <span className={`shrink-0 w-10 ${levelColor(l.level)}`}>{l.level}</span>
                <span className="text-gray-400 shrink-0">[{l.module}]</span>
                <span className="text-gray-500 break-all">{l.msg}</span>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}

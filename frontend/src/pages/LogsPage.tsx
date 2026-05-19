import { useState, useEffect, useRef } from 'react'
import { api } from '../api/client'

interface LogEntry { time: string; level: string; module: string; msg: string }

export default function LogsPage() {
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [logs, setLogs] = useState<LogEntry[]>([])
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const fetchLogs = () => {
      api.logs({ limit: 200, level: filter, search: search || undefined })
        .then(({ data }) => setLogs((data as { logs: LogEntry[] }).logs))
        .catch(() => {})
    }
    fetchLogs()
    const interval = setInterval(fetchLogs, 5000)
    return () => clearInterval(interval)
  }, [filter, search])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [logs])

  const levelColor = (lvl: string) =>
    lvl === 'error' ? 'text-red-400' : lvl === 'warn' ? 'text-yellow-400' : lvl === 'debug' ? 'text-slate-500' : 'text-blue-400'

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-slate-200 mb-6">运行日志</h1>
      <div className="flex items-center gap-2 mb-4">
        <input value={search} onChange={e => setSearch(e.target.value)}
          placeholder="搜索日志..."
          className="bg-slate-900/40 border border-slate-800/60 text-slate-200 placeholder-slate-500 rounded-lg px-3 py-1.5 text-xs w-48 outline-none" />
        <select value={filter} onChange={e => setFilter(e.target.value)}
          className="bg-slate-900/40 border border-slate-800/60 text-slate-200 rounded-lg px-2 py-1.5 text-xs outline-none">
          <option value="all">所有</option>
          <option value="debug">DEBUG</option>
          <option value="info">INFO</option>
          <option value="warn">WARN</option>
          <option value="error">ERROR</option>
        </select>
      </div>
      <div className="bg-slate-900/40 border border-slate-800/60 rounded-xl overflow-hidden">
        <div className="max-h-[60vh] overflow-y-auto font-mono text-xs">
          {logs.length === 0 ? (
            <div className="p-8 text-center text-slate-500 text-xs">暂无日志（启动后端后会实时采集）</div>
          ) : (
            logs.map((l, i) => (
              <div key={i} className="flex items-start gap-3 px-4 py-1.5 border-b border-slate-800/30 hover:bg-slate-800/20">
                <span className="text-slate-600 shrink-0 whitespace-nowrap">{l.time}</span>
                <span className={`shrink-0 w-10 ${levelColor(l.level)}`}>{l.level.toUpperCase()}</span>
                <span className="text-slate-500 shrink-0">[{l.module}]</span>
                <span className="text-slate-400 break-all">{l.msg}</span>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}

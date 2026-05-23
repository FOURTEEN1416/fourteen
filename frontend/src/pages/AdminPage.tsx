import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useHealth } from '../hooks/useAPI'
import { Activity, Wifi, Database, Bot } from 'lucide-react'
import ProactiveEnginePanel from '../components/common/ProactiveEnginePanel'
import type { ProactiveEngineState } from '../types/api'

interface StatData {
  status: string
  emotion?: { current_emotion: string; intensity: number; energy: number; affinity: number }
  working_count?: number
  active_sessions?: number
  has_orchestrator?: boolean
}

export default function AdminPage() {
  const health = useHealth()
  const [stats, setStats] = useState<StatData | null>(null)
  const [tools, setTools] = useState<string[]>([])
  const [disabledTools, setDisabledTools] = useState<Set<string>>(new Set())
  const [proactiveState, setProactiveState] = useState<ProactiveEngineState | null>(null)

  useEffect(() => {
    api.stats().then(({ data }) => setStats(data as StatData)).catch(() => {})
    api.tools().then(({ data }) => setTools((data as { tools: string[] }).tools)).catch(() => {})
    api.proactiveState().then(({ data }) => {
      setProactiveState(data as ProactiveEngineState)
    }).catch(() => {})
  }, [])

  const handleToggleTool = async (name: string, enabled: boolean) => {
    try {
      await api.toggleTool(name, enabled)
      setDisabledTools(prev => {
        const next = new Set(prev)
        if (enabled) next.delete(name); else next.add(name)
        return next
      })
    } catch { /* toast handles it */ }
  }

  const statusColor = health?.status === 'healthy' ? 'text-green-400' : health?.status === 'degraded' ? 'text-yellow-400' : 'text-red-400'
  const activeTools = tools.filter(t => !disabledTools.has(t))

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-gray-800 mb-6">管理面板</h1>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard icon={Activity} label="系统状态" value={health?.status || '未知'} color={statusColor} />
        <StatCard icon={Wifi} label="API连接" value={stats?.has_orchestrator ? '正常' : '断开'} color={stats?.has_orchestrator ? 'text-green-400' : 'text-red-400'} />
        <StatCard icon={Database} label="活跃会话" value={String(stats?.active_sessions ?? 0)} color="text-blue-400" />
        <StatCard icon={Bot} label="已注册工具" value={String(activeTools.length)} color="text-accent-400" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* Health Checks */}
        <div className="bg-white/80 border border-gray-200 rounded-2xl p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">组件健康检查</h2>
          {health ? (
            <div className="space-y-2">
              {Object.entries(health.checks).map(([name, check]) => (
                <div key={name} className="flex items-center justify-between text-xs text-gray-500 bg-gray-200/30 rounded-lg px-3 py-2">
                  <span>{name}</span>
                  <span className={check?.connected !== false ? 'text-green-400' : 'text-red-400'}>
                    {check?.connected !== false ? '正常' : '异常'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-400">加载中...</p>
          )}
        </div>

        {/* Runtime Stats */}
        <div className="bg-white/80 border border-gray-200 rounded-2xl p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">运行时状态</h2>
          {stats ? (
            <div className="space-y-2 text-xs">
              <div className="flex justify-between text-gray-500 bg-gray-200/30 rounded-lg px-3 py-2">
                <span>当前情感</span>
                <span className="text-gray-800">{stats.emotion?.current_emotion || '-'}</span>
              </div>
              <div className="flex justify-between text-gray-500 bg-gray-200/30 rounded-lg px-3 py-2">
                <span>好感度</span>
                <span className="text-gray-800">{stats.emotion?.affinity ?? '-'}/8</span>
              </div>
              <div className="flex justify-between text-gray-500 bg-gray-200/30 rounded-lg px-3 py-2">
                <span>能量</span>
                <span className="text-gray-800">{stats.emotion ? `${Math.round(stats.emotion.energy * 100)}%` : '-'}</span>
              </div>
            </div>
          ) : (
            <p className="text-xs text-gray-400">加载中...</p>
          )}
        </div>
      </div>

      {/* Tool Controls */}
      <div className="bg-white/80 border border-gray-200 rounded-2xl p-4 mb-6">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">工具管理</h2>
        {tools.length === 0 ? (
          <p className="text-xs text-gray-400">暂无工具</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
            {tools.map((tool) => {
              const enabled = !disabledTools.has(tool)
              return (
                <div key={tool}
                  className="flex items-center justify-between bg-gray-200/30 rounded-lg px-3 py-2 text-xs">
                  <span className={enabled ? 'text-gray-700' : 'text-gray-300'}>{tool}</span>
                  <button
                    onClick={() => handleToggleTool(tool, !enabled)}
                    className={`w-7 h-4 rounded-full transition-colors ${
                      enabled ? 'bg-primary-500' : 'bg-gray-200'
                    }`}>
                    <div className={`w-3 h-3 bg-white rounded-full transition-transform ${
                      enabled ? 'translate-x-3.5' : 'translate-x-0.5'
                    }`} />
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Proactive Config */}
      <ProactiveEnginePanel state={proactiveState} />
    </div>
  )
}

function StatCard({ icon: Icon, label, value, color }: { icon: any; label: string; value: string; color: string }) {
  return (
    <div className="bg-white/80 border border-gray-200 rounded-2xl p-4">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-gray-200/60 flex items-center justify-center">
          <Icon className="w-5 h-5 text-gray-500" />
        </div>
        <div>
          <div className="text-[10px] text-gray-400 uppercase tracking-wider">{label}</div>
          <div className={`text-sm font-semibold ${color}`}>{value}</div>
        </div>
      </div>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useHealth } from '../hooks/useAPI'
import { Activity, Wifi, Database, Bot } from 'lucide-react'

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
  const [proactive, setProactive] = useState<Record<string, any> | null>(null)
  const [proactiveThreshold, setProactiveThreshold] = useState(4)

  useEffect(() => {
    api.stats().then(({ data }) => setStats(data as StatData)).catch(() => {})
    api.tools().then(({ data }) => setTools((data as { tools: string[] }).tools)).catch(() => {})
    api.proactiveState().then(({ data }) => {
      const p = data as Record<string, any>
      setProactive(p)
      if (p?.config?.threshold != null) setProactiveThreshold(p.config.threshold)
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

  const handleUpdateThreshold = async () => {
    try {
      await api.updateProactiveConfig({ threshold: proactiveThreshold })
      setProactive(prev => prev ? { ...prev, config: { ...prev.config, threshold: proactiveThreshold } } : prev)
    } catch { /* toast handles it */ }
  }

  const statusColor = health?.status === 'healthy' ? 'text-green-400' : health?.status === 'degraded' ? 'text-yellow-400' : 'text-red-400'
  const activeTools = tools.filter(t => !disabledTools.has(t))

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-slate-200 mb-6">管理面板</h1>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <StatCard icon={Activity} label="系统状态" value={health?.status || '未知'} color={statusColor} />
        <StatCard icon={Wifi} label="API连接" value={stats?.has_orchestrator ? '正常' : '断开'} color={stats?.has_orchestrator ? 'text-green-400' : 'text-red-400'} />
        <StatCard icon={Database} label="活跃会话" value={String(stats?.active_sessions ?? 0)} color="text-blue-400" />
        <StatCard icon={Bot} label="已注册工具" value={String(activeTools.length)} color="text-accent-400" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {/* Health Checks */}
        <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-4">
          <h2 className="text-sm font-semibold text-slate-300 mb-3">组件健康检查</h2>
          {health ? (
            <div className="space-y-2">
              {Object.entries(health.checks).map(([name, check]) => (
                <div key={name} className="flex items-center justify-between text-xs text-slate-400 bg-slate-800/30 rounded-lg px-3 py-2">
                  <span>{name}</span>
                  <span className={check?.connected !== false ? 'text-green-400' : 'text-red-400'}>
                    {check?.connected !== false ? '正常' : '异常'}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-slate-500">加载中...</p>
          )}
        </div>

        {/* Runtime Stats */}
        <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-4">
          <h2 className="text-sm font-semibold text-slate-300 mb-3">运行时状态</h2>
          {stats ? (
            <div className="space-y-2 text-xs">
              <div className="flex justify-between text-slate-400 bg-slate-800/30 rounded-lg px-3 py-2">
                <span>当前情感</span>
                <span className="text-slate-200">{stats.emotion?.current_emotion || '-'}</span>
              </div>
              <div className="flex justify-between text-slate-400 bg-slate-800/30 rounded-lg px-3 py-2">
                <span>好感度</span>
                <span className="text-slate-200">{stats.emotion?.affinity ?? '-'}/8</span>
              </div>
              <div className="flex justify-between text-slate-400 bg-slate-800/30 rounded-lg px-3 py-2">
                <span>能量</span>
                <span className="text-slate-200">{stats.emotion ? `${Math.round(stats.emotion.energy * 100)}%` : '-'}</span>
              </div>
            </div>
          ) : (
            <p className="text-xs text-slate-500">加载中...</p>
          )}
        </div>
      </div>

      {/* Tool Controls */}
      <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-4 mb-6">
        <h2 className="text-sm font-semibold text-slate-300 mb-3">工具管理</h2>
        {tools.length === 0 ? (
          <p className="text-xs text-slate-500">暂无工具</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
            {tools.map((tool) => {
              const enabled = !disabledTools.has(tool)
              return (
                <div key={tool}
                  className="flex items-center justify-between bg-slate-800/30 rounded-lg px-3 py-2 text-xs">
                  <span className={enabled ? 'text-slate-300' : 'text-slate-600'}>{tool}</span>
                  <button
                    onClick={() => handleToggleTool(tool, !enabled)}
                    className={`w-7 h-4 rounded-full transition-colors ${
                      enabled ? 'bg-primary-500' : 'bg-slate-700'
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
      <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-4">
        <h2 className="text-sm font-semibold text-slate-300 mb-3">主动消息配置</h2>
        <div className="flex items-center gap-3 mb-3">
          <span className="text-xs text-slate-400">触发阈值</span>
          <input
            type="range" min={1} max={10} step={0.5}
            value={proactiveThreshold}
            onChange={e => setProactiveThreshold(parseFloat(e.target.value))}
            className="w-32 h-1 bg-slate-700 rounded-full appearance-none cursor-pointer"
          />
          <span className="text-xs text-slate-500 w-6 text-right">{proactiveThreshold}</span>
          <button
            onClick={handleUpdateThreshold}
            className="text-[10px] px-2 py-1 bg-primary-600/30 text-primary-300 rounded-lg hover:bg-primary-600/50 transition-colors"
          >
            更新
          </button>
        </div>
        {proactive && (
          <div className="text-[10px] text-slate-500 bg-slate-800/20 rounded-lg p-2">
            <div>紧迫度: {proactive.urgency?.total ?? '-'}</div>
            <div>今日已发: {proactive.daily_count ?? 0}</div>
          </div>
        )}
      </div>
    </div>
  )
}

function StatCard({ icon: Icon, label, value, color }: { icon: any; label: string; value: string; color: string }) {
  return (
    <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-4">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-slate-800/60 flex items-center justify-center">
          <Icon className="w-5 h-5 text-slate-400" />
        </div>
        <div>
          <div className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</div>
          <div className={`text-sm font-semibold ${color}`}>{value}</div>
        </div>
      </div>
    </div>
  )
}

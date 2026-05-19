import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useHealth } from '../hooks/useAPI'
import {
  Activity, Smartphone, MessageCircle, GraduationCap,
  Heart, Brain, Settings, FileText, Wifi, RefreshCw,
  PlugZap, Play, ChevronRight,
} from 'lucide-react'

/* ── Type definitions ── */

interface DashboardStats {
  today_chats?: number
  recent_memories?: number
  affinity?: number
  energy?: number
  current_emotion?: string
}

interface WeChatStatus {
  connected: boolean
  uptime?: number
  qr_code?: string
}

interface TrainingStatus {
  status: 'idle' | 'extracting' | 'cleaning' | 'training' | 'done'
  progress?: number
  extracted_turns?: number
}

interface ProactiveState {
  threshold?: number
  daily_count?: number
  config?: { threshold?: number }
}

/* ── API_BASE for direct fetch calls ── */

const API_BASE = '/api'

/* ── Component ── */

export default function DashboardPage() {
  const navigate = useNavigate()
  const health = useHealth()

  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(null)
  const [wechatStatus, setWechatStatus] = useState<WeChatStatus | null>(null)
  const [trainingStatus, setTrainingStatus] = useState<TrainingStatus | null>(null)
  const [proactiveState, setProactiveState] = useState<ProactiveState | null>(null)
  const [proactiveThreshold, setProactiveThreshold] = useState(4)
  const [reconnecting, setReconnecting] = useState(false)

  const fetchDashboard = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/stats/dashboard`)
      if (res.ok) {
        const data = (await res.json()) as DashboardStats
        setDashboardStats(data)
      }
    } catch { /* endpoint may not exist yet */ }
  }, [])

  const fetchWeChatStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/channels/wechat/status`)
      if (res.ok) {
        const data = (await res.json()) as WeChatStatus
        setWechatStatus(data)
      }
    } catch { /* endpoint may not exist yet */ }
  }, [])

  const fetchTrainingStatus = useCallback(async () => {
    try {
      const { data } = await api.trainingStatus()
      setTrainingStatus(data as TrainingStatus)
    } catch { /* ignore */ }
  }, [])

  const fetchProactiveState = useCallback(async () => {
    try {
      const { data } = await api.proactiveState()
      const p = data as ProactiveState
      setProactiveState(p)
      if (p?.config?.threshold != null) setProactiveThreshold(p.config.threshold)
    } catch { /* ignore */ }
  }, [])

  // Initial fetch + polling
  useEffect(() => {
    fetchDashboard()
    fetchWeChatStatus()
    fetchTrainingStatus()
    fetchProactiveState()
    const t = setInterval(() => {
      fetchDashboard()
      fetchWeChatStatus()
      fetchTrainingStatus()
    }, 15000)
    return () => clearInterval(t)
  }, [fetchDashboard, fetchWeChatStatus, fetchTrainingStatus, fetchProactiveState])

  const handleReconnect = async () => {
    setReconnecting(true)
    try {
      const res = await fetch(`${API_BASE}/channels/wechat/reconnect`, { method: 'POST' })
      if (res.ok) {
        const data = (await res.json()) as WeChatStatus
        setWechatStatus(data)
      }
    } catch { /* ignore */ }
    setReconnecting(false)
  }

  const handleUpdateThreshold = async () => {
    try {
      await api.updateProactiveConfig({ threshold: proactiveThreshold })
      setProactiveState(prev => prev ? { ...prev, threshold: proactiveThreshold } : prev)
    } catch { /* toast handles it */ }
  }

  // Derive status
  const systemStatus = health?.status ?? 'unknown'
  const systemOk = systemStatus === 'healthy'
  const systemDegraded = systemStatus === 'degraded'
  const wechatConnected = wechatStatus?.connected ?? false
  const trainingActive = trainingStatus && trainingStatus.status !== 'idle' && trainingStatus.status !== 'done'

  const statusColor = systemOk ? 'text-green-400' : systemDegraded ? 'text-yellow-400' : 'text-red-400'
  const statusLabel = systemOk ? '健康' : systemDegraded ? '降级' : '异常'
  const wechatColor = wechatConnected ? 'text-green-400' : 'text-red-400'
  const wechatLabel = wechatConnected ? '已连接' : '未连接'

  const formatUptime = (seconds?: number): string => {
    if (!seconds) return '-'
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    if (h > 0) return `${h}小时${m}分钟`
    return `${m}分钟`
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      {/* Page Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-slate-200">管理仪表盘</h1>
      </div>

      {/* ── System Status Cards Row ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatusCard
          icon={Activity}
          label="系统状态"
          value={statusLabel}
          color={statusColor}
        />
        <StatusCard
          icon={Smartphone}
          label="微信连接"
          value={wechatLabel}
          color={wechatColor}
        />
        <StatusCard
          icon={MessageCircle}
          label="今日对话"
          value={String(dashboardStats?.today_chats ?? '-')}
          color="text-blue-400"
        />
        <StatusCard
          icon={GraduationCap}
          label="训练状态"
          value={trainingStatus ? (trainingStatus.status === 'idle' ? '空闲' : trainingStatus.status === 'done' ? '完成' : '训练中') : '-'}
          color={trainingActive ? 'text-accent-400' : 'text-slate-400'}
        />
      </div>

      {/* ── Two-column layout ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* ── WeChat Connection Panel ── */}
        <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-5">
          <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
            <Smartphone className="w-4 h-4 text-slate-400" />
            微信连接
          </h2>
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">状态</span>
              <span className={`flex items-center gap-1.5 font-medium ${wechatColor}`}>
                <span className={`w-2 h-2 rounded-full ${wechatConnected ? 'bg-green-400' : 'bg-red-400'}`} />
                {wechatLabel}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">在线时长</span>
              <span className="text-slate-300">{formatUptime(wechatStatus?.uptime)}</span>
            </div>
            <div className="flex gap-2 pt-2">
              <button
                onClick={handleReconnect}
                disabled={reconnecting}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-slate-700 text-slate-300 rounded-lg hover:bg-slate-800/60 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${reconnecting ? 'animate-spin' : ''}`} />
                重新连接
              </button>
              <button
                onClick={() => navigate('/channels')}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-slate-700 text-slate-300 rounded-lg hover:bg-slate-800/60 transition-colors"
              >
                <PlugZap className="w-3.5 h-3.5" />
                查看通道
              </button>
              <button
                onClick={() => navigate('/logs')}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-slate-700 text-slate-300 rounded-lg hover:bg-slate-800/60 transition-colors"
              >
                <FileText className="w-3.5 h-3.5" />
                查看日志
              </button>
            </div>
          </div>
        </div>

        {/* ── Training Quick Status ── */}
        <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-5">
          <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
            <GraduationCap className="w-4 h-4 text-slate-400" />
            克隆状态
          </h2>
          {trainingStatus ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-500">管线状态</span>
                <span className={`font-medium ${
                  trainingStatus.status === 'idle' ? 'text-slate-400' :
                  trainingStatus.status === 'done' ? 'text-green-400' :
                  'text-accent-400'
                }`}>
                  {trainingStatus.status === 'idle' ? '空闲' :
                   trainingStatus.status === 'extracting' ? '提取中' :
                   trainingStatus.status === 'cleaning' ? '清洗中' :
                   trainingStatus.status === 'training' ? '训练中' :
                   trainingStatus.status === 'done' ? '已完成' : trainingStatus.status}
                </span>
              </div>
              {trainingActive && trainingStatus.progress != null && (
                <div>
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>进度</span>
                    <span>{Math.round(trainingStatus.progress)}%</span>
                  </div>
                  <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-accent-500 rounded-full transition-all duration-500"
                      style={{ width: `${trainingStatus.progress}%` }}
                    />
                  </div>
                </div>
              )}
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-500">已提取对话</span>
                <span className="text-slate-300">{trainingStatus.extracted_turns ?? 0} 条</span>
              </div>
              <div className="flex gap-2 pt-1">
                <button
                  onClick={() => navigate('/training')}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors"
                >
                  <Play className="w-3.5 h-3.5" />
                  打开克隆工作台
                </button>
              </div>
            </div>
          ) : (
            <p className="text-xs text-slate-500">加载中...</p>
          )}
        </div>
      </div>

      {/* ── Emotion & Memory Stats ── */}
      <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-5">
        <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
          <Heart className="w-4 h-4 text-slate-400" />
          情感 & 记忆
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* Emotion */}
          <div className="bg-slate-800/30 rounded-xl p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">当前情感</div>
            <div className="text-sm font-medium text-slate-200">
              {dashboardStats?.current_emotion || '-'}
            </div>
          </div>
          {/* Affinity */}
          <div className="bg-slate-800/30 rounded-xl p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">好感度</div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-slate-200">
                {dashboardStats?.affinity != null ? `${dashboardStats.affinity}/8` : '-'}
              </span>
              {dashboardStats?.affinity != null && (
                <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden max-w-24">
                  <div
                    className="h-full rounded-full bg-accent-500"
                    style={{ width: `${(dashboardStats.affinity / 8) * 100}%` }}
                  />
                </div>
              )}
            </div>
          </div>
          {/* Energy */}
          <div className="bg-slate-800/30 rounded-xl p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">能量</div>
            <div className="text-sm font-medium text-slate-200">
              {dashboardStats?.energy != null ? `${Math.round(dashboardStats.energy * 100)}%` : '-'}
            </div>
          </div>
          {/* Recent memories */}
          <div className="bg-slate-800/30 rounded-xl p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">近期记忆</div>
            <div className="text-sm font-medium text-slate-200">
              {dashboardStats?.recent_memories != null ? `${dashboardStats.recent_memories} 条` : '-'}
            </div>
          </div>
        </div>
      </div>

      {/* ── Proactive Message Config ── */}
      <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-5">
        <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
          <Wifi className="w-4 h-4 text-slate-400" />
          主动消息配置
        </h2>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-400">触发阈值</span>
            <input
              type="range" min={1} max={10} step={0.5}
              value={proactiveThreshold}
              onChange={e => setProactiveThreshold(parseFloat(e.target.value))}
              className="w-28 h-1 bg-slate-700 rounded-full appearance-none cursor-pointer"
            />
            <span className="text-xs text-slate-500 w-6 text-right">{proactiveThreshold}</span>
          </div>
          <div className="text-xs text-slate-500">
            今日已发: {proactiveState?.daily_count ?? 0}
          </div>
          <button
            onClick={handleUpdateThreshold}
            className="text-xs px-3 py-1.5 bg-primary-600/30 text-primary-300 rounded-lg hover:bg-primary-600/50 transition-colors"
          >
            更新
          </button>
        </div>
      </div>

      {/* ── Quick Links ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <QuickLink icon={Heart} label="人设管理" to="/persona" onClick={navigate} />
        <QuickLink icon={Brain} label="记忆浏览" to="/memory" onClick={navigate} />
        <QuickLink icon={Settings} label="系统设置" to="/settings" onClick={navigate} />
        <QuickLink icon={FileText} label="运行日志" to="/logs" onClick={navigate} />
      </div>
    </div>
  )
}

/* ── Sub-components ── */

function StatusCard({
  icon: Icon, label, value, color,
}: {
  icon: React.FC<{ className?: string }>
  label: string
  value: string
  color: string
}) {
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

function QuickLink({
  icon: Icon, label, to, onClick,
}: {
  icon: React.FC<{ className?: string }>
  label: string
  to: string
  onClick: (path: string) => void
}) {
  return (
    <button
      onClick={() => onClick(to)}
      className="flex items-center gap-3 bg-slate-900/40 border border-slate-800/60 rounded-xl px-4 py-3 hover:bg-slate-800/30 transition-colors text-left"
    >
      <Icon className="w-4 h-4 text-slate-400 shrink-0" />
      <span className="text-sm text-slate-300">{label}</span>
      <ChevronRight className="w-3.5 h-3.5 text-slate-600 ml-auto" />
    </button>
  )
}

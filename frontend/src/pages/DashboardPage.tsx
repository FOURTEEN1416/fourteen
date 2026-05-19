import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useDashboardData } from '../hooks/useDashboardData'
import { useTrainingProgress } from '../hooks/useTrainingProgress'
import ProactiveEnginePanel from '../components/common/ProactiveEnginePanel'
import {
  Activity, Smartphone, MessageCircle, GraduationCap,
  Heart, Brain, Settings, FileText, Wifi, RefreshCw,
  PlugZap, Play, ChevronRight,
} from 'lucide-react'
import type { TrainingStatusEnum, WeChatStatus } from '../types/api'

export default function DashboardPage() {
  const navigate = useNavigate()
  const { stats, wechat: wechatStatus, health, proactive: proactiveState, loading, refetch } = useDashboardData()
  const { progress: trainingProgress } = useTrainingProgress()

  const systemStatus = health?.status ?? 'unknown'
  const systemOk = systemStatus === 'healthy'
  const systemDegraded = systemStatus === 'degraded'
  const wechatConnected = wechatStatus?.connected ?? false
  const trainingStatus = trainingProgress?.status ?? 'idle'
  const trainingActive: TrainingStatusEnum[] = ['extracting', 'cleaning', 'training']
  const isTraining = trainingActive.includes(trainingStatus as TrainingStatusEnum)

  const statusColor = systemOk ? 'text-green-400' : systemDegraded ? 'text-yellow-400' : 'text-red-400'
  const statusLabel = systemOk ? '健康' : systemDegraded ? '降级' : '异常'
  const wechatColor = wechatConnected ? 'text-green-400' : 'text-red-400'
  const wechatLabel = wechatConnected ? '已连接' : '未连接'

  const formatUptime = (seconds: number): string => {
    if (!seconds) return '-'
    const h = Math.floor(seconds / 3600)
    const m = Math.floor((seconds % 3600) / 60)
    if (h > 0) return `${h}小时${m}分钟`
    return `${m}分钟`
  }

  const handleReconnect = async () => {
    try {
      const { data } = await api.wechatReconnect()
      refetch()
    } catch { /* toast handles it */ }
  }

  const statusMap: Record<string, string> = {
    idle: '空闲', extracting: '提取中', cleaning: '清洗中',
    training: '训练中', done: '已完成', error: '失败', stopped: '已停止',
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-slate-200">管理仪表盘</h1>
        {loading && <span className="text-xs text-slate-500">刷新中...</span>}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatusCard icon={Activity} label="系统状态" value={statusLabel} color={statusColor} />
        <StatusCard icon={Smartphone} label="微信连接" value={wechatLabel} color={wechatColor} />
        <StatusCard icon={MessageCircle} label="今日对话" value={String(stats?.today_chats ?? '-')} color="text-blue-400" />
        <StatusCard icon={GraduationCap} label="训练状态" value={statusMap[trainingStatus] ?? '-'} color={isTraining ? 'text-accent-400' : 'text-slate-400'} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
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
              <span className="text-slate-300">{formatUptime(wechatStatus?.uptime_seconds ?? 0)}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">今日消息</span>
              <span className="text-slate-300">{wechatStatus?.messages_today ?? '-'}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">重连尝试</span>
              <span className="text-slate-300">{wechatStatus?.reconnect_attempts ?? '-'}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">心跳丢失</span>
              <span className="text-slate-300">{wechatStatus?.missed_heartbeats ?? '-'}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-500">最后活动</span>
              <span className="text-slate-300">{wechatStatus?.last_activity ?? '-'}</span>
            </div>
            <div className="flex gap-2 pt-2">
              <button onClick={handleReconnect} className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-slate-700 text-slate-300 rounded-lg hover:bg-slate-800/60 transition-colors">
                <RefreshCw className="w-3.5 h-3.5" />
                重新连接
              </button>
              <button onClick={() => navigate('/channels')} className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-slate-700 text-slate-300 rounded-lg hover:bg-slate-800/60 transition-colors">
                <PlugZap className="w-3.5 h-3.5" />
                查看通道
              </button>
              <button onClick={() => navigate('/logs')} className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-slate-700 text-slate-300 rounded-lg hover:bg-slate-800/60 transition-colors">
                <FileText className="w-3.5 h-3.5" />
                查看日志
              </button>
            </div>
          </div>
        </div>

        <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-5">
          <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
            <GraduationCap className="w-4 h-4 text-slate-400" />
            克隆状态
          </h2>
          {trainingProgress ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-500">管线状态</span>
                <span className={`font-medium ${trainingStatus === 'idle' ? 'text-slate-400' : trainingStatus === 'done' ? 'text-green-400' : 'text-accent-400'}`}>
                  {statusMap[trainingStatus] ?? trainingStatus}
                </span>
              </div>
              {isTraining && (
                <>
                  <div>
                    <div className="flex justify-between text-xs text-slate-400 mb-1">
                      <span>进度</span>
                      <span>{Math.round(trainingProgress.progress)}%</span>
                    </div>
                    <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
                      <div className="h-full bg-accent-500 rounded-full transition-all duration-500" style={{ width: `${trainingProgress.progress}%` }} />
                    </div>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-500">当前步骤</span>
                    <span className="text-slate-300">{trainingProgress.current_step}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-slate-500">Loss</span>
                    <span className="text-slate-300">{trainingProgress.loss > 0 ? trainingProgress.loss.toFixed(4) : '-'}</span>
                  </div>
                </>
              )}
              <div className="flex items-center justify-between text-xs">
                <span className="text-slate-500">已提取 / 已清洗</span>
                <span className="text-slate-300">{trainingProgress.extracted_turns} / {trainingProgress.cleaned_turns} 条</span>
              </div>
              {trainingProgress.error && (
                <div className="text-xs text-red-400 bg-red-900/20 rounded px-2 py-1">
                  错误: {trainingProgress.error}
                </div>
              )}
              <div className="flex gap-2 pt-1">
                <button onClick={() => navigate('/training')} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors">
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

      <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-5">
        <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
          <Heart className="w-4 h-4 text-slate-400" />
          情感 & 记忆
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-slate-800/30 rounded-xl p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">当前情感</div>
            <div className="text-sm font-medium text-slate-200">{stats?.current_emotion || '-'}</div>
          </div>
          <div className="bg-slate-800/30 rounded-xl p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">好感度</div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-slate-200">{stats?.affinity != null ? `${stats.affinity}/8` : '-'}</span>
              {stats?.affinity != null && (
                <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden max-w-24">
                  <div className="h-full rounded-full bg-accent-500" style={{ width: `${(stats.affinity / 8) * 100}%` }} />
                </div>
              )}
            </div>
          </div>
          <div className="bg-slate-800/30 rounded-xl p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">能量</div>
            <div className="text-sm font-medium text-slate-200">{stats?.energy != null ? `${Math.round(stats.energy * 100)}%` : '-'}</div>
          </div>
          <div className="bg-slate-800/30 rounded-xl p-3">
            <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">近期记忆</div>
            <div className="text-sm font-medium text-slate-200">{stats?.recent_memories != null ? `${stats.recent_memories} 条` : '-'}</div>
          </div>
        </div>
      </div>

      <ProactiveEnginePanel state={proactiveState} onConfigUpdated={refetch} />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <QuickLink icon={Heart} label="人设管理" to="/persona" onClick={navigate} />
        <QuickLink icon={Brain} label="记忆浏览" to="/memory" onClick={navigate} />
        <QuickLink icon={Settings} label="系统设置" to="/settings" onClick={navigate} />
        <QuickLink icon={FileText} label="运行日志" to="/logs" onClick={navigate} />
      </div>
    </div>
  )
}

function StatusCard({ icon: Icon, label, value, color }: { icon: React.FC<{ className?: string }>; label: string; value: string; color: string }) {
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

function QuickLink({ icon: Icon, label, to, onClick }: { icon: React.FC<{ className?: string }>; label: string; to: string; onClick: (path: string) => void }) {
  return (
    <button onClick={() => onClick(to)} className="flex items-center gap-3 bg-slate-900/40 border border-slate-800/60 rounded-xl px-4 py-3 hover:bg-slate-800/30 transition-colors text-left">
      <Icon className="w-4 h-4 text-slate-400 shrink-0" />
      <span className="text-sm text-slate-300">{label}</span>
      <ChevronRight className="w-3.5 h-3.5 text-slate-600 ml-auto" />
    </button>
  )
}

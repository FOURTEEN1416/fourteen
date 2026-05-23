import React, { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useDashboard, useHealth, useWechatStatus, useTrainingProgress, useProactiveState, useActiveCharacter, useAffinity } from '../hooks/useQueries'
import ProactiveEnginePanel from '../components/common/ProactiveEnginePanel'
import {
  Activity, Smartphone, MessageCircle, GraduationCap,
  Heart, Brain, Settings, FileText, Wifi, RefreshCw,
  PlugZap, Play, ChevronRight, Users, BarChart3,
  Database, PenTool, Sticker, Shield,
} from 'lucide-react'

const TRAINING_ACTIVE = ['extracting', 'cleaning', 'training']

const TRAINING_STATUS_MAP: Record<string, string> = {
  idle: '空闲', extracting: '提取中', cleaning: '清洗中',
  training: '训练中', done: '已完成', error: '失败', stopped: '已停止',
}

function formatUptime(seconds: number): string {
  if (!seconds) return '-'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}小时${m}分钟`
  return `${m}分钟`
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const { data: stats, isLoading: statsLoading } = useDashboard()
  const { data: health } = useHealth()
  const { data: wechatStatus } = useWechatStatus()
  const { data: trainingProgress } = useTrainingProgress()
  const { data: proactiveState } = useProactiveState()
  const { activeCharacter } = useActiveCharacter()
  const { data: affinity } = useAffinity(activeCharacter?.character_id)
  const loading = statsLoading

  const systemStatus = health?.status ?? 'unknown'
  const systemOk = systemStatus === 'healthy'
  const systemDegraded = systemStatus === 'degraded'
  const wechatConnected = wechatStatus?.connected ?? false
  const trainingStatus = trainingProgress?.status ?? 'idle'
  const isTraining = TRAINING_ACTIVE.includes(trainingStatus)

  const maxAffinity = affinity?.max ?? 8
  const currentAffinity = stats?.affinity ?? 0

  const statusColor = systemOk ? 'text-green-400' : systemDegraded ? 'text-yellow-400' : 'text-red-400'
  const statusLabel = systemOk ? '健康' : systemDegraded ? '降级' : '异常'
  const wechatColor = wechatConnected ? 'text-green-400' : 'text-red-400'
  const wechatLabel = wechatConnected ? '已连接' : '未连接'

  const handleReconnect = useCallback(async () => {
    try {
      await api.wechatReconnect()
    } catch { /* toast handles it */ }
  }, [])

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-800">管理仪表盘</h1>
        {loading && <span className="text-xs text-gray-400">刷新中...</span>}
      </div>

      {/* ── 状态概览卡片 ── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatusCard icon={Activity} label="系统状态" value={statusLabel} color={statusColor} />
        <StatusCard icon={Smartphone} label="微信连接" value={wechatLabel} color={wechatColor} />
        <StatusCard icon={MessageCircle} label="今日对话" value={String(stats?.today_chats ?? '-')} color="text-blue-400" />
        <StatusCard icon={GraduationCap} label="训练状态" value={TRAINING_STATUS_MAP[trainingStatus] ?? '-'} color={isTraining ? 'text-accent-400' : 'text-gray-500'} />
      </div>

      {/* ── 微信 & 克隆 双面板 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-white/80 border border-gray-200 rounded-2xl p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
            <Smartphone className="w-4 h-4 text-gray-500" />
            微信连接
          </h2>
          <div className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">状态</span>
              <span className={`flex items-center gap-1.5 font-medium ${wechatColor}`}>
                <span className={`w-2 h-2 rounded-full ${wechatConnected ? 'bg-green-400' : 'bg-red-400'}`} />
                {wechatLabel}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">在线时长</span>
              <span className="text-gray-700">{formatUptime(wechatStatus?.uptime_seconds ?? 0)}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">今日消息</span>
              <span className="text-gray-700">{wechatStatus?.messages_today ?? '-'}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">重连尝试</span>
              <span className="text-gray-700">{wechatStatus?.reconnect_attempts ?? '-'}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">心跳丢失</span>
              <span className="text-gray-700">{wechatStatus?.missed_heartbeats ?? '-'}</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-400">最后活动</span>
              <span className="text-gray-700">{wechatStatus?.last_activity ?? '-'}</span>
            </div>
            <div className="flex gap-2 pt-2">
              <button onClick={handleReconnect} className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-200/60 transition-colors">
                <RefreshCw className="w-3.5 h-3.5" />
                重新连接
              </button>
              <button onClick={() => navigate('/channels')} className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-200/60 transition-colors">
                <PlugZap className="w-3.5 h-3.5" />
                查看通道
              </button>
              <button onClick={() => navigate('/logs')} className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-200/60 transition-colors">
                <FileText className="w-3.5 h-3.5" />
                查看日志
              </button>
            </div>
          </div>
        </div>

        <div className="bg-white/80 border border-gray-200 rounded-2xl p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
            <GraduationCap className="w-4 h-4 text-gray-500" />
            克隆状态
          </h2>
          {trainingProgress ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-400">管线状态</span>
                <span className={`font-medium ${trainingStatus === 'idle' ? 'text-gray-500' : trainingStatus === 'done' ? 'text-green-400' : 'text-accent-400'}`}>
                  {TRAINING_STATUS_MAP[trainingStatus] ?? trainingStatus}
                </span>
              </div>
              {isTraining && (
                <>
                  <div>
                    <div className="flex justify-between text-xs text-gray-500 mb-1">
                      <span>进度</span>
                      <span>{Math.round(trainingProgress.progress)}%</span>
                    </div>
                    <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                      <div className="h-full bg-accent-500 rounded-full transition-all duration-500" style={{ width: `${trainingProgress.progress}%` }} />
                    </div>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-400">当前步骤</span>
                    <span className="text-gray-700">{trainingProgress.current_step}</span>
                  </div>
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-400">Loss</span>
                    <span className="text-gray-700">{trainingProgress.loss > 0 ? trainingProgress.loss.toFixed(4) : '-'}</span>
                  </div>
                </>
              )}
              <div className="flex items-center justify-between text-xs">
                <span className="text-gray-400">已提取 / 已清洗</span>
                <span className="text-gray-700">{trainingProgress.extracted_turns} / {trainingProgress.cleaned_turns} 条</span>
              </div>
              {trainingProgress.error && (
                <div className="text-xs text-red-400 bg-red-50 rounded px-2 py-1">
                  错误: {trainingProgress.error}
                </div>
              )}
              <div className="flex gap-2 pt-1">
                <button onClick={() => navigate('/training')} className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-primary-600 hover:bg-primary-500 text-white rounded-lg transition-colors">
                  <Play className="w-3.5 h-3.5" />
                  打开克隆工作台
                </button>
                <button onClick={() => navigate('/clone-data')} className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-200/60 transition-colors">
                  <Database className="w-3.5 h-3.5" />
                  数据管理
                </button>
              </div>
            </div>
          ) : (
            <p className="text-xs text-gray-400">加载中...</p>
          )}
        </div>
      </div>

      {/* ── 情感 & 记忆概览 ── */}
      <div className="bg-white/80 border border-gray-200 rounded-2xl p-5">
        <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
          <Heart className="w-4 h-4 text-gray-500" />
          情感 & 记忆
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-gray-200/30 rounded-xl p-3">
            <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">当前情感</div>
            <div className="text-sm font-medium text-gray-800">{stats?.current_emotion || '-'}</div>
          </div>
          <div className="bg-gray-200/30 rounded-xl p-3">
            <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">好感度</div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-800">{currentAffinity != null ? `${currentAffinity}/${maxAffinity}` : '-'}</span>
              {currentAffinity != null && (
                <div className="flex-1 h-1.5 bg-gray-200 rounded-full overflow-hidden max-w-24">
                  <div className="h-full rounded-full bg-accent-500" style={{ width: `${(currentAffinity / maxAffinity) * 100}%` }} />
                </div>
              )}
            </div>
          </div>
          <div className="bg-gray-200/30 rounded-xl p-3">
            <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">能量</div>
            <div className="text-sm font-medium text-gray-800">{stats?.energy != null ? `${Math.round(stats.energy * 100)}%` : '-'}</div>
          </div>
          <div className="bg-gray-200/30 rounded-xl p-3">
            <div className="text-[10px] text-gray-400 uppercase tracking-wider mb-1">近期记忆</div>
            <div className="text-sm font-medium text-gray-800">{stats?.recent_memories != null ? `${stats.recent_memories} 条` : '-'}</div>
          </div>
        </div>
      </div>

      {/* ── 功能导航快速入口 ── */}
      <Section title="角色与人设">
        <QuickLink icon={Users} label="角色管理" to="/characters" onClick={navigate} desc="管理多个角色、导入导出" />
        <QuickLink icon={Heart} label="人设档案" to="/persona" onClick={navigate} desc="查看性格雷达与演化日志" />
        <QuickLink icon={PenTool} label="人设编辑" to="/persona-editor" onClick={navigate} desc="编辑角色人设与对话风格" />
      </Section>

      <Section title="克隆训练">
        <QuickLink icon={GraduationCap} label="克隆工作台" to="/training" onClick={navigate} desc="提取数据、清洗、训练 LoRA" />
        <QuickLink icon={Database} label="数据管理" to="/clone-data" onClick={navigate} desc="管理已提取的聊天数据" />
        <QuickLink icon={Smartphone} label="通道管理" to="/channels" onClick={navigate} desc="微信连接与多通道控制" />
      </Section>

      <Section title="监控与分析">
        <QuickLink icon={Activity} label="情感监控" to="/monitor" onClick={navigate} desc="好感度、情感阶段与解锁事件" />
        <QuickLink icon={BarChart3} label="对话统计" to="/stats" onClick={navigate} desc="消息量、角色使用、情感分布" />
        <QuickLink icon={Brain} label="记忆浏览" to="/memory" onClick={navigate} desc="搜索与筛选长期记忆" />
        <QuickLink icon={Sticker} label="表情包" to="/stickers" onClick={navigate} desc="表情包管理与情感推荐" />
      </Section>

      <Section title="系统管理">
        <QuickLink icon={Settings} label="系统设置" to="/settings" onClick={navigate} desc="LLM、语音、通知等配置" />
        <QuickLink icon={Shield} label="管理面板" to="/admin" onClick={navigate} desc="组件健康、工具开关、模型配置" />
        <QuickLink icon={FileText} label="运行日志" to="/logs" onClick={navigate} desc="查看系统运行日志" />
      </Section>

      <ProactiveEnginePanel state={proactiveState ?? null} onConfigUpdated={() => {}} />
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">{title}</h3>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {children}
      </div>
    </div>
  )
}

const StatusCard = React.memo(function StatusCard({ icon: Icon, label, value, color }: { icon: React.FC<{ className?: string }>; label: string; value: string; color: string }) {
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
})

const QuickLink = React.memo(function QuickLink({ icon: Icon, label, to, onClick, desc }: { icon: React.FC<{ className?: string }>; label: string; desc: string; to: string; onClick: (path: string) => void }) {
  return (
    <button onClick={() => onClick(to)} className="flex items-start gap-3 bg-white/80 border border-gray-200 rounded-xl px-4 py-3 hover:bg-gray-50 hover:border-gray-300 transition-colors text-left group">
      <div className="w-9 h-9 rounded-lg bg-gray-100 flex items-center justify-center shrink-0 group-hover:bg-primary-50 transition-colors">
        <Icon className="w-4 h-4 text-gray-500 group-hover:text-primary-500 transition-colors" />
      </div>
      <div className="min-w-0">
        <div className="text-sm font-medium text-gray-700">{label}</div>
        <div className="text-[11px] text-gray-400 mt-0.5">{desc}</div>
      </div>
      <ChevronRight className="w-3.5 h-3.5 text-gray-300 ml-auto shrink-0 self-center" />
    </button>
  )
})

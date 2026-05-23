import { useState, useEffect } from 'react'
import { shisiClient } from '../api/shisiClient'
import { useErrorStore } from '../store/errorStore'
import Card from '../components/common/Card'
import Badge from '../components/common/Badge'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { Activity, Heart, ChevronDown, History, Unlock, Clock } from 'lucide-react'
import type { CharacterState } from '../types/character'
import type { EmotionStageProgress, AffinityProgress, WechatCommandHelp } from '../types/shisi'

const WECHAT_COMMANDS: WechatCommandHelp[] = [
  { command: '切换角色：[名]', description: '切换当前对话角色', example: '切换角色：椎名真昼' },
  { command: '好感度', description: '查看当前好感度与阶段', example: '好感度' },
  { command: '情感状态', description: '查看当前情感阶段', example: '情感状态' },
  { command: '生理指标', description: '查看角色生理数据', example: '生理指标' },
  { command: '发表情', description: '触发情感推荐表情', example: '发表情' },
  { command: '收藏', description: '收藏当前对话记忆', example: '收藏' },
  { command: '转发给：[名]', description: '转发记忆给其他角色', example: '转发给：十四' },
]

interface StageHistoryEntry {
  from_stage: string
  to_stage: string
  timestamp: string
  reason: string
}

interface UnlockEvent {
  name: string
  affinity_threshold: number
  unlocked_at: string
}

export default function MonitorPage() {
  const [characters, setCharacters] = useState<CharacterState[]>([])
  const [selected, setSelected] = useState('')
  const [affinity, setAffinity] = useState<AffinityProgress | null>(null)
  const [stage, setStage] = useState<EmotionStageProgress | null>(null)
  const [stageHistory, setStageHistory] = useState<StageHistoryEntry[]>([])
  const [unlockEvents, setUnlockEvents] = useState<UnlockEvent[]>([])
  const [loading, setLoading] = useState(true)
  const toast = useErrorStore.getState().addToast

  useEffect(() => { loadCharacters() }, [])

  async function loadCharacters() {
    try {
      setLoading(true)
      const data = await shisiClient.characters.list() as CharacterState[]
      setCharacters(data)
      if (data.length > 0) {
        const active = data.find(c => c.is_active) || data[0]
        setSelected(active.character_id)
        loadDetail(active.character_id)
      }
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '加载角色列表失败' })
    } finally { setLoading(false) }
  }

  async function loadDetail(cid: string) {
    try {
      const [aff, st] = await Promise.all([
        shisiClient.affinity.get(cid),
        shisiClient.emotionStage.get(cid),
      ])
      setAffinity(aff as AffinityProgress)
      setStage(st as EmotionStageProgress)
    } catch (e: any) {
      toast({ type: 'error', message: e?.message || '加载监控数据失败' })
    }
  }

  async function loadStageHistory(cid: string) {
    try {
      const data = await shisiClient.emotionStage.listStages() as any[]
      setStageHistory(data.map((s, i) => ({
        from_stage: i > 0 ? data[i - 1].name : '初始',
        to_stage: s.name,
        timestamp: new Date().toISOString(),
        reason: `好感度达到 ${s.min}`,
      })))
    } catch (e: any) {
      toast({ type: 'warning', message: '阶段历史加载失败' })
    }
  }

  async function loadUnlockEvents(cid: string) {
    try {
      const data = await shisiClient.affinity.get(cid) as any
      setUnlockEvents(data?.unlocks?.map((u: any) => ({
        name: u.name,
        affinity_threshold: u.affinity_threshold ?? 0,
        unlocked_at: u.unlocked_at ?? new Date().toISOString(),
      })) ?? [])
    } catch (e: any) {
      toast({ type: 'warning', message: '解锁事件加载失败' })
    }
  }

  function handleSelect(cid: string) {
    setSelected(cid)
    loadDetail(cid)
    loadStageHistory(cid)
    loadUnlockEvents(cid)
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <Activity className="w-4 h-4 text-gray-500" />
          好感度 / 情感阶段监控
        </h1>
        {characters.length > 0 && (
          <div className="relative">
            <select
              value={selected}
              onChange={e => handleSelect(e.target.value)}
              className="appearance-none bg-white/80 border border-gray-200 rounded-lg pl-3 pr-8 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
            >
              {characters.map(c => <option key={c.character_id} value={c.character_id}>{c.name}</option>)}
            </select>
            <ChevronDown className="w-3.5 h-3.5 text-gray-400 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
          </div>
        )}
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          <Card><Skeleton lines={4} /></Card>
          <Card><Skeleton lines={4} /></Card>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                <Heart className="w-4 h-4 text-gray-500" />
                好感度
              </h2>
              {affinity ? (
                <div className="space-y-3">
                  <div>
                    <div className="flex justify-between text-xs text-gray-500 mb-1.5">
                      <span>{affinity.affinity.toFixed(0)}</span>
                      <span>{affinity.max}</span>
                    </div>
                    <div className="w-full bg-gray-200/60 rounded-full h-2.5 overflow-hidden">
                      <div className="bg-accent-500 h-full rounded-full transition-all duration-500" style={{ width: `${affinity.percentage}%` }} />
                    </div>
                  </div>
                  <div className="text-xs text-gray-500">
                    已解锁: {affinity.unlocks.length > 0
                      ? affinity.unlocks.map(u => <Badge key={u.name} variant="success" className="mr-1">{u.name}</Badge>)
                      : <span className="text-gray-400">无</span>
                    }
                  </div>
                </div>
              ) : <p className="text-xs text-gray-400">加载中...</p>}
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                <Activity className="w-4 h-4 text-gray-500" />
                情感阶段
              </h2>
              {stage ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Badge variant="info">{stage.current_stage}</Badge>
                    <span className="text-xs text-gray-400">({stage.stage_index + 1}/{stage.total_stages})</span>
                  </div>
                  <div className="text-xs text-gray-500">
                    可用功能: {stage.features.map(f => <Badge key={f} className="mr-1">{f}</Badge>)}
                  </div>
                </div>
              ) : <p className="text-xs text-gray-400">加载中...</p>}
            </Card>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
                <History className="w-4 h-4 text-gray-500" />
                阶段变更历史
              </h2>
              {stageHistory.length === 0 ? (
                <EmptyState icon="📋" title="暂无变更记录" description="角色情感阶段变更后将在此显示" />
              ) : (
                <div className="space-y-2">
                  {stageHistory.map((h, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded-lg px-3 py-2">
                      <Clock className="w-3 h-3 text-gray-400 shrink-0" />
                      <span className="text-gray-500">{new Date(h.timestamp).toLocaleDateString()}</span>
                      <Badge variant="default">{h.from_stage}</Badge>
                      <span className="text-gray-400">→</span>
                      <Badge variant="info">{h.to_stage}</Badge>
                      <span className="text-gray-400 ml-auto">{h.reason}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
                <Unlock className="w-4 h-4 text-gray-500" />
                解锁事件
              </h2>
              {unlockEvents.length === 0 ? (
                <EmptyState icon="🔓" title="暂无解锁事件" description="好感度提升后解锁的功能将在此显示" />
              ) : (
                <div className="space-y-2">
                  {unlockEvents.map((u, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded-lg px-3 py-2">
                      <Unlock className="w-3 h-3 text-green-400 shrink-0" />
                      <Badge variant="success">{u.name}</Badge>
                      <span className="text-gray-400">好感度 {u.affinity_threshold}</span>
                      <span className="text-gray-400 ml-auto">{new Date(u.unlocked_at).toLocaleDateString()}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </>
      )}

      <Card>
        <h2 className="text-sm font-semibold text-gray-700 mb-4">微信交互指令帮助</h2>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-200/50">
                <th className="text-left py-2 text-gray-400 font-medium">指令</th>
                <th className="text-left py-2 text-gray-400 font-medium">说明</th>
                <th className="text-left py-2 text-gray-400 font-medium">示例</th>
              </tr>
            </thead>
            <tbody>
              {WECHAT_COMMANDS.map(c => (
                <tr key={c.command} className="border-b border-gray-200/30">
                  <td className="py-2 font-mono text-gray-700">{c.command}</td>
                  <td className="py-2 text-gray-600">{c.description}</td>
                  <td className="py-2 text-gray-400">{c.example}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}

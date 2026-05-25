import { useState } from 'react'
import { useCharacters, useAffinity, useEmotionStage, useEmotionStageList, useAffinityUnlocks } from '../hooks/useQueries'
import Card from '../components/common/Card'
import Badge from '../components/common/Badge'
import Skeleton from '../components/common/Skeleton'
import EmptyState from '../components/common/EmptyState'
import { Activity, Heart, ChevronDown, History, Unlock, Clock } from 'lucide-react'
import type { CharacterState } from '../types/character'

interface UnlockItem {
  name: string
  affinity_threshold?: number
  threshold?: number
  unlocked_at?: string
}

interface AffinityData {
  affinity: number
  max: number
  percentage: number
  unlocks: UnlockItem[]
}

interface EmotionStageData {
  current_stage: string
  stage_index: number
  total_stages: number
  features: string[]
}

interface AffinityUnlocksData {
  unlocks?: UnlockItem[]
}

export default function MonitorPage() {
  const { data: characters, isLoading: charsLoading } = useCharacters()
  const characterList = (characters ?? []) as CharacterState[]
  const [selected, setSelected] = useState('')

  const activeId = selected || (characterList.find((c: CharacterState) => c.is_active)?.character_id ?? characterList[0]?.character_id ?? '')

  const { data: affinity, isLoading: affLoading } = useAffinity(activeId)
  const { data: stage, isLoading: stageLoading } = useEmotionStage(activeId)
  useEmotionStageList()
  const { data: unlocksData, isLoading: unlocksLoading } = useAffinityUnlocks(activeId)

  const loading = charsLoading || affLoading || stageLoading || unlocksLoading

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <Activity className="w-4 h-4 text-gray-500" />
          好感度 / 情感阶段监控
        </h1>
        {characterList.length > 0 && (
          <div className="relative">
            <select
              value={selected || activeId}
              onChange={e => setSelected(e.target.value)}
              className="appearance-none bg-white/80 border border-gray-200 rounded-lg pl-3 pr-8 py-1.5 text-xs text-gray-700 focus:outline-none focus:ring-2 focus:ring-primary-400/30"
            >
              {characterList.map((c: CharacterState) => <option key={c.character_id} value={c.character_id}>{c.name}</option>)}
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
                      <span>{(affinity as unknown as AffinityData).affinity.toFixed(0)}</span>
                      <span>{(affinity as unknown as AffinityData).max}</span>
                    </div>
                    <div className="w-full bg-gray-200/60 rounded-full h-2.5 overflow-hidden">
                      <div className="bg-accent-500 h-full rounded-full transition-all duration-500" style={{ width: `${(affinity as unknown as AffinityData).percentage}%` }} />
                    </div>
                  </div>
                  <div className="text-xs text-gray-500">
                    已解锁: {(affinity as unknown as AffinityData).unlocks.length > 0
                      ? (affinity as unknown as AffinityData).unlocks.map((u: UnlockItem) => <Badge key={u.name} variant="success" className="mr-1">{u.name}</Badge>)
                      : <span className="text-gray-400">无</span>
                    }
                  </div>
                </div>
              ) : <p className="text-xs text-gray-400">暂无数据</p>}
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                <Activity className="w-4 h-4 text-gray-500" />
                情感阶段
              </h2>
              {stage ? (
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Badge variant="info">{(stage as EmotionStageData).current_stage}</Badge>
                    <span className="text-xs text-gray-400">({(stage as EmotionStageData).stage_index + 1}/{(stage as EmotionStageData).total_stages})</span>
                  </div>
                  <div className="text-xs text-gray-500">
                    可用功能: {(stage as EmotionStageData).features.map((f: string) => <Badge key={f} className="mr-1">{f}</Badge>)}
                  </div>
                </div>
              ) : <p className="text-xs text-gray-400">暂无数据</p>}
            </Card>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
                <History className="w-4 h-4 text-gray-500" />
                阶段变更历史
              </h2>
              <EmptyState icon="📋" title="暂无变更记录" description="角色情感阶段变更后将在此显示" />
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
                <Unlock className="w-4 h-4 text-gray-500" />
                解锁事件
              </h2>
              {!(unlocksData as AffinityUnlocksData | null)?.unlocks || (unlocksData as AffinityUnlocksData).unlocks?.length === 0 ? (
                <EmptyState icon="🔓" title="暂无解锁事件" description="好感度提升后解锁的功能将在此显示" />
              ) : (
                <div className="space-y-2">
                  {(unlocksData as AffinityUnlocksData).unlocks?.map((u: UnlockItem, i: number) => (
                    <div key={i} className="flex items-center gap-2 text-xs bg-gray-50 rounded-lg px-3 py-2">
                      <Unlock className="w-3 h-3 text-green-400 shrink-0" />
                      <Badge variant="success">{u.name}</Badge>
                      <span className="text-gray-400">好感度 {u.affinity_threshold ?? u.threshold}</span>
                      {u.unlocked_at && (
                        <span className="text-gray-400 ml-auto flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {new Date(u.unlocked_at).toLocaleDateString()}
                        </span>
                      )}
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
        <p className="text-xs text-gray-400 mb-3">以下指令供参考，具体支持情况以实际后端配置为准</p>
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
              {[
                { command: '切换角色：[名]', description: '切换当前对话角色', example: '切换角色：椎名真昼' },
                { command: '好感度', description: '查看当前好感度与阶段', example: '好感度' },
                { command: '情感状态', description: '查看当前情感阶段', example: '情感状态' },
                { command: '生理指标', description: '查看角色生理数据', example: '生理指标' },
                { command: '发表情', description: '触发情感推荐表情', example: '发表情' },
                { command: '收藏', description: '收藏当前对话记忆', example: '收藏' },
                { command: '转发给：[名]', description: '转发记忆给其他角色', example: '转发给：十四' },
              ].map(c => (
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

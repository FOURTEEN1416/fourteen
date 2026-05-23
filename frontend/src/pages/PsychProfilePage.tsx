import { useState } from 'react'
import { usePsychProfile, usePsychSnapshots, usePsychReset } from '../hooks/useQueries'
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer } from 'recharts'
import Skeleton from '../components/common/Skeleton'
import Card from '../components/common/Card'
import Badge from '../components/common/Badge'
import Button from '../components/common/Button'
import EmptyState from '../components/common/EmptyState'
import { Brain, TrendingUp, Clock, Zap } from 'lucide-react'

const OCEAN_LABELS: Record<string, string> = {
  openness: '开放性', conscientiousness: '尽责性', extraversion: '外向性',
  agreeableness: '宜人性', neuroticism: '神经质',
}
const OCEAN_DESC: Record<string, [string, string]> = {
  openness: ['保守务实', '开放好奇'],
  conscientiousness: ['随性自由', '认真自律'],
  extraversion: ['内向安静', '外向热情'],
  agreeableness: ['直率主见', '友善体贴'],
  neuroticism: ['情绪稳定', '敏感细腻'],
}
const STYLE_LABELS: Record<string, string> = {
  formality: '正式度', expressiveness: '表达欲', humor: '幽默感',
  directness: '直接度', sentiment: '正面倾向',
}
const PAD_LABELS: Record<string, string> = {
  pleasure: '愉悦度', arousal: '激活度', dominance: '支配度',
}

function TraitBar({ label, value, lowLabel, highLabel }: { label: string; value: number; lowLabel: string; highLabel: string }) {
  const pct = Math.round(value * 100)
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-gray-600">{label}</span>
        <span className="text-gray-500">{pct}%</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-gray-400 w-12 text-right shrink-0">{lowLabel}</span>
        <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${pct}%`,
              background: pct > 65 ? 'linear-gradient(90deg, #a78bfa, #7c3aed)' : pct > 35 ? 'linear-gradient(90deg, #60a5fa, #3b82f6)' : 'linear-gradient(90deg, #f472b6, #ec4899)',
            }}
          />
        </div>
        <span className="text-[10px] text-gray-400 w-12 shrink-0">{highLabel}</span>
      </div>
    </div>
  )
}

function PADBar({ label, value }: { label: string; value: number }) {
  const color = value > 0 ? '#22c55e' : value < 0 ? '#ef4444' : '#94a3b8'
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs">
        <span className="text-gray-600">{label}</span>
        <span className="text-gray-500" style={{ color }}>{value > 0 ? '+' : ''}{value.toFixed(2)}</span>
      </div>
      <div className="h-2 bg-gray-100 rounded-full overflow-hidden relative">
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-gray-300" />
        <div
          className="h-full rounded-full transition-all duration-500 absolute"
          style={{
            width: `${Math.abs(value) * 50}%`,
            background: color,
            left: value >= 0 ? '50%' : `${50 - Math.abs(value) * 50}%`,
          }}
        />
      </div>
    </div>
  )
}

export default function PsychProfilePage() {
  const { data: profile, isLoading: profLoading } = usePsychProfile()
  const { data: snapshots, isLoading: snapLoading } = usePsychSnapshots()
  const resetMutation = usePsychReset()
  const [showReset, setShowReset] = useState(false)

  if (profLoading || !profile) {
    return (
      <div className="flex-1 p-6 space-y-4">
        <Skeleton className="h-5 w-40" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Skeleton className="h-80" />
          <Skeleton className="h-80" />
        </div>
      </div>
    )
  }

  const unavailable = profile.status === 'unavailable' || profile.status === 'insufficient_data'

  const oceanRadar = Object.entries(profile.ocean || {}).map(([k, v]) => ({
    trait: OCEAN_LABELS[k] || k,
    value: (v as number) * 100,
  }))

  const isStable = profile.status === 'stable'

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <Brain className="w-4 h-4 text-purple-500" />
          对话对象心理画像
        </h1>
        <div className="flex items-center gap-2">
          {isStable && <Badge variant="success">稳定</Badge>}
          {profile.status === 'learning' && <Badge variant="warning">收集中</Badge>}
          {unavailable && <Badge variant="default">暂无数据</Badge>}
          <span className="text-xs text-gray-400">已采样 {profile.snapshots} 次</span>
        </div>
      </div>

      {unavailable ? (
        <Card>
          <EmptyState
            icon="🧠"
            title="暂无心理画像数据"
            description="需要先开启人格提取功能，聊几句后系统会自动分析对话对象的性格特征。"
          />
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-purple-500" />
                OCEAN 五大人格雷达
              </h2>
              <ResponsiveContainer width="100%" height={280}>
                <RadarChart data={oceanRadar}>
                  <PolarGrid stroke="#e5e7eb" />
                  <PolarAngleAxis dataKey="trait" tick={{ fill: '#6b7280', fontSize: 12 }} />
                  <PolarRadiusAxis angle={90} domain={[0, 100]} tick={false} axisLine={false} />
                  <Radar name="人格" dataKey="value" stroke="#7c3aed" fill="#a78bfa" fillOpacity={0.3} strokeWidth={2} />
                </RadarChart>
              </ResponsiveContainer>
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                <Zap className="w-4 h-4 text-amber-500" />
                人格特质详情
              </h2>
              <div className="space-y-3">
                {Object.entries(profile.ocean).map(([k, v]) => (
                  <TraitBar
                    key={k}
                    label={OCEAN_LABELS[k] || k}
                    value={v as number}
                    lowLabel={OCEAN_DESC[k]?.[0] || '低'}
                    highLabel={OCEAN_DESC[k]?.[1] || '高'}
                  />
                ))}
              </div>
            </Card>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-4">PAD 情感三轴</h2>
              <div className="space-y-4">
                {Object.entries(profile.pad || {}).map(([k, v]) => (
                  <PADBar key={k} label={PAD_LABELS[k] || k} value={v as number} />
                ))}
              </div>
              <div className="mt-4 pt-4 border-t border-gray-100">
                <div className="flex items-center gap-4 text-xs text-gray-500">
                  <span>● 绿=正向</span>
                  <span>● 红=负向</span>
                  <span>| 灰=中性</span>
                </div>
              </div>
            </Card>

            <Card>
              <h2 className="text-sm font-semibold text-gray-700 mb-4">风格向量</h2>
              <div className="space-y-3">
                {Object.entries(profile.style || {}).map(([k, v]) => (
                  <TraitBar
                    key={k}
                    label={STYLE_LABELS[k] || k}
                    value={v as number}
                    lowLabel={k === 'sentiment' ? '偏负面' : '低'}
                    highLabel={k === 'sentiment' ? '偏正面' : '高'}
                  />
                ))}
              </div>
            </Card>
          </div>

          <Card>
            <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
              <Clock className="w-4 h-4 text-blue-500" />
              画像稳定性
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
              <div>
                <div className="text-2xl font-bold text-purple-600">{Math.round(profile.stability * 100)}%</div>
                <div className="text-xs text-gray-500 mt-1">稳定度</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-blue-600">{profile.snapshots}</div>
                <div className="text-xs text-gray-500 mt-1">采样次数</div>
              </div>
              <div>
                <div className="text-sm font-medium text-gray-700">{profile.first_seen?.slice(0, 10) || '-'}</div>
                <div className="text-xs text-gray-500 mt-1">首次检测</div>
              </div>
              <div>
                <div className="text-sm font-medium text-gray-700">{profile.last_updated?.slice(0, 10) || '-'}</div>
                <div className="text-xs text-gray-500 mt-1">最近更新</div>
              </div>
            </div>
          </Card>

          <Card>
            <h2 className="text-sm font-semibold text-gray-700 mb-4">检测快照历史</h2>
            {snapLoading ? (
              <Skeleton lines={3} />
            ) : !snapshots?.length ? (
              <p className="text-sm text-gray-400">暂无检测记录</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-gray-500 border-b border-gray-100">
                      <th className="pb-2 font-medium">时间</th>
                      <th className="pb-2 font-medium">来源</th>
                      <th className="pb-2 font-medium">置信度</th>
                      <th className="pb-2 font-medium">触发消息</th>
                      <th className="pb-2 font-medium">OCEAN (O/C/E/A/N)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshots.slice(0, 20).map((s, i) => (
                      <tr key={i} className="border-b border-gray-50">
                        <td className="py-2 text-gray-600">{s.timestamp?.slice(0, 19) || '-'}</td>
                        <td className="py-2">
                          <Badge variant={s.source === 'pado' ? 'info' : s.source === 'rule' ? 'default' : 'success'}>
                            {s.source || '-'}
                          </Badge>
                        </td>
                        <td className="py-2 text-gray-600">{Math.round(s.confidence * 100)}%</td>
                        <td className="py-2 text-gray-500 max-w-[200px] truncate">{s.trigger_message || '-'}</td>
                        <td className="py-2 text-gray-600 font-mono text-[10px]">
                          {s.ocean ? `${(s.ocean.openness * 100).toFixed(0)}/${(s.ocean.conscientiousness * 100).toFixed(0)}/${(s.ocean.extraversion * 100).toFixed(0)}/${(s.ocean.agreeableness * 100).toFixed(0)}/${(s.ocean.neuroticism * 100).toFixed(0)}` : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <div className="flex justify-end">
            {!showReset ? (
              <button
                onClick={() => setShowReset(true)}
                className="text-xs text-gray-400 hover:text-red-500 transition-colors"
              >
                重置心理画像数据
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs text-red-500">确定要清除所有心理画像数据吗？</span>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => { resetMutation.mutate(); setShowReset(false) }}
                  disabled={resetMutation.isPending}
                >
                  {resetMutation.isPending ? '清除中...' : '确认清除'}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setShowReset(false)}>
                  取消
                </Button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

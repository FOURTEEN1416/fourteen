/**
 * 心理画像页（T2，2026-08-31）——G-21 零 UI 能力首次落 UI。
 *
 * 数据：usePsychProfile / usePsychSnapshots（后端 /api/psych/* 五端点在产）。
 * 合规边界：非医疗诊断，仅供自我参考（页内常驻免责条；热线 400-161-9995）。
 * 样式：现有设计系统（三色语义 + glass + LightOnly），零新依赖。
 */
import { useState } from 'react'
import { Brain, Activity, ShieldAlert, RefreshCw, Info } from 'lucide-react'
import AnimatedPage from '../components/shared/AnimatedPage'
import ConfirmDialog from '../components/shared/ConfirmDialog'
import {
  usePsychProfile,
  usePsychSnapshots,
  usePsychReset,
} from '../hooks/useQueries'
import { useActiveCharacter } from '../hooks/useQueries'
import { useErrorStore } from '../store/errorStore'
import type {
  OceanTraits,
  PadState,
  StyleVector,
  MentalHealthSnapshot,
  DepressionIndicators,
  AnxietyIndicators,
} from '../types/api'

type Signal = DepressionIndicators | AnxietyIndicators | null | undefined

const OCEAN_LABELS: Array<[keyof OceanTraits, string]> = [
  ['openness', '开放性'],
  ['conscientiousness', '尽责性'],
  ['extraversion', '外向性'],
  ['agreeableness', '宜人性'],
  ['neuroticism', '神经质'],
]

const HEXACO_LABELS: Array<[string, string]> = [
  ['honesty_humility', '诚实-谦逊'],
  ['emotionality', '情绪性'],
  ['extraversion', '外向性'],
  ['agreeableness', '宜人性'],
  ['conscientiousness', '尽责性'],
  ['openness', '开放性'],
]

function TraitBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100)
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-gray-600">{label}</span>
        <span className="font-mono text-gray-400">{pct}%</span>
      </div>
      <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden">
        <div
          className="h-full rounded-full bg-gradient-to-r from-macaron-blue to-macaron-blue-deep transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

function RiskBadge({ level }: { level: string }) {
  const map: Record<string, string> = {
    low: 'bg-macaron-mint-light text-macaron-mint-deep',
    moderate: 'bg-macaron-yellow-light text-macaron-yellow-deep',
    high: 'bg-red-50 text-red-500',
    critical: 'bg-red-100 text-red-600',
  }
  const zh: Record<string, string> = {
    low: '低', moderate: '中', high: '高', critical: '极高',
  }
  return (
    <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${map[level] ?? 'bg-gray-100 text-gray-400'}`}>
      {zh[level] ?? level}
    </span>
  )
}

function SignalCard({ title, signal }: { title: string; signal: Signal }) {
  return (
    <div className="rounded-xl bg-white/50 p-3">
      <p className="text-[11px] text-gray-400 mb-1">{title}</p>
      <p className="text-lg font-bold text-gray-700">
        {(signal?.total_score ?? 0).toFixed(2)}
        <span className="text-xs text-gray-400 font-normal"> / 1.0</span>
      </p>
      <p className="text-[11px] text-gray-400">
        档位 {signal?.level ?? '—'} · 命中维度 {signal?.items_hit ?? 0}/{signal?.items_total ?? 0} · 证据强度 {signal?.evidence ?? 0}
      </p>
      {(signal?.matched?.length ?? 0) > 0 && (
        <p className="text-[11px] text-gray-400 mt-1">命中词：{signal!.matched!.join('、')}</p>
      )}
    </div>
  )
}

export default function PsychProfilePage() {
  const { activeCharacter } = useActiveCharacter()
  const characterId = activeCharacter?.id
  const { data: profile, isLoading } = usePsychProfile(characterId)
  const { data: snapshots } = usePsychSnapshots(10, characterId)
  const resetMutation = usePsychReset()
  const [confirmReset, setConfirmReset] = useState(false)
  const { addToast } = useErrorStore.getState()

  const status = profile?.status ?? 'unavailable'
  const hasData = status === 'stable' || status === 'learning'
  const scopeCount = profile?.scope_count ?? 0

  function handleReset() {
    resetMutation.mutate(characterId, {
      onSuccess: (res) => addToast({
        type: 'success',
        message: `已清除 ${res?.cleared ?? 0} 份心理画像，角色会重新从对话中认识你`,
      }),
      onError: () => addToast({ type: 'error', message: '重置失败：画像可能仍保留，请重试' }),
    })
    setConfirmReset(false)
  }

  const mh: MentalHealthSnapshot | undefined = hasData ? profile?.mental_health : undefined

  return (
    <AnimatedPage>
      <div className="px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-4xl">
          {/* Header */}
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                <Brain className="w-5 h-5 text-macaron-blue-deep" />
                心理画像
              </h1>
              <p className="mt-0.5 text-sm text-gray-400">
                {activeCharacter ? `角色「${activeCharacter.name}」对你的理解` : '基于对话的心理特征建模'}
              </p>
            </div>
            {hasData && (
              <button
                onClick={() => setConfirmReset(true)}
                className="flex items-center gap-1.5 rounded-lg bg-gray-100 px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-200 transition-colors"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                重置画像
              </button>
            )}
          </div>

          {/* 免责条（常驻） */}
          <div className="mb-5 flex items-start gap-2 rounded-xl bg-macaron-yellow-light/60 border border-macaron-yellow/30 px-4 py-3">
            <Info className="w-4 h-4 text-macaron-yellow-deep shrink-0 mt-0.5" />
            <p className="text-xs text-gray-500 leading-relaxed">
              以下内容基于对话文本的统计分析，<span className="font-medium text-gray-700">仅供自我参考与陪伴体验，不构成任何医疗诊断或建议</span>。
              如有心理困扰请联系专业机构；全国心理援助热线 400-161-9995（24 小时）。
            </p>
          </div>

          {isLoading ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="glass-card rounded-2xl p-5 h-40 animate-pulse bg-white/30" />
              ))}
            </div>
          ) : !hasData ? (
            <div className="glass-card rounded-2xl p-10 text-center stagger-item">
              <Brain className="w-10 h-10 mx-auto mb-3 text-gray-400" />
              <p className="text-sm font-medium text-gray-600 mb-1">
                {status === 'insufficient_data' ? '对话样本还不够' : '画像尚未生成'}
              </p>
              <p className="text-xs text-gray-400 max-w-sm mx-auto leading-relaxed">
                {activeCharacter
                  ? `「${activeCharacter.name}」还没有从与你的对话中学到足够特征——多聊几轮即可逐步建立。`
                  : '多和角色聊几轮，系统会从对话文本中逐步学习你的性格特征、情绪模式与语言风格。'}
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {/* 元信息条 */}
              <div className="glass-card rounded-xl px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-gray-500">
                <span>状态：<span className="font-medium text-gray-700">{status === 'stable' ? '稳定' : '学习中'}</span></span>
                <span>稳定度：<span className="font-mono">{Math.round((profile?.stability ?? 0) * 100)}%</span></span>
                <span>快照数：<span className="font-mono">{profile?.snapshots ?? 0}</span></span>
                <span>样本窗口：{profile?.snapshots ?? 0} 次检测</span>
              </div>

              {/* 画像按「角色 × 会话」各存一份：这里显示的是哪一份、共几份 */}
              <div className="rounded-xl bg-white/40 border border-gray-100 px-4 py-2.5 text-[11px] text-gray-400 leading-relaxed space-y-1">
                <p>
                  当前显示：{profile?.scope ?? '—'}
                  {scopeCount > 1 ? `（该角色共 ${scopeCount} 份会话画像，取最近更新的一份）` : scopeCount === 1 ? '（该角色仅此 1 份会话画像）' : ''}
                </p>
                <p>画像按「角色 × 会话」分别学习，换角色或换会话不会共用同一份数据。</p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* OCEAN 大五 */}
                <div className="glass-card rounded-2xl p-5 stagger-item">
                  <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                    <Activity className="w-4 h-4 text-macaron-blue-deep" />
                    大五人格（OCEAN）
                  </h2>
                  <div className="space-y-3">
                    {OCEAN_LABELS.map(([key, label]) => (
                      <TraitBar key={key} label={label} value={profile?.ocean?.[key] ?? 0} />
                    ))}
                  </div>
                </div>

                {/* HEXACO（可选扩展） */}
                {profile?.hexaco && (
                  <div className="glass-card rounded-2xl p-5 stagger-item">
                    <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                      <Activity className="w-4 h-4 text-macaron-mint-deep" />
                      HEXACO 六维
                    </h2>
                    <div className="space-y-3">
                      {HEXACO_LABELS.map(([key, label]) => (
                        <TraitBar
                          key={key}
                          label={label}
                          value={(profile.hexaco as unknown as Record<string, number>)[key] ?? 0}
                        />
                      ))}
                    </div>
                  </div>
                )}

                {/* PAD 情绪三维 */}
                {profile?.pad && (
                  <div className="glass-card rounded-2xl p-5 stagger-item">
                    <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                      <Activity className="w-4 h-4 text-macaron-yellow-deep" />
                      情绪三维（PAD）
                    </h2>
                    <div className="space-y-3">
                      {(
                        [
                          ['pleasure', '愉悦度'],
                          ['arousal', '唤醒度'],
                          ['dominance', '支配度'],
                        ] as Array<[keyof PadState, string]>
                      ).map(([key, label]) => (
                        <TraitBar
                          key={key}
                          label={label}
                          value={((profile.pad?.[key] ?? 0) + 1) / 2}
                        />
                      ))}
                    </div>
                  </div>
                )}

                {/* 语言风格 */}
                {profile?.style && (
                  <div className="glass-card rounded-2xl p-5 stagger-item">
                    <h2 className="text-sm font-semibold text-gray-700 mb-4 flex items-center gap-2">
                      <Activity className="w-4 h-4 text-macaron-blue-deep" />
                      语言风格
                    </h2>
                    <div className="space-y-3">
                      {(
                        [
                          ['formality', '正式度'],
                          ['expressiveness', '表达力'],
                          ['humor', '幽默感'],
                          ['directness', '直接度'],
                          ['sentiment', '情感浓度'],
                        ] as Array<[keyof StyleVector, string]>
                      ).map(([key, label]) => (
                        <TraitBar key={key} label={label} value={profile.style?.[key] ?? 0} />
                      ))}
                    </div>
                  </div>
                )}

                {/* 情绪信号（关键词命中口径，非诊断） */}
                {mh && (
                  <div className="glass-card rounded-2xl p-5 stagger-item md:col-span-2">
                    <div className="flex items-center justify-between mb-4">
                      <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
                        <ShieldAlert className="w-4 h-4 text-macaron-yellow-deep" />
                        情绪信号筛查（非诊断）
                      </h2>
                      <RiskBadge level={mh.overall_risk ?? 'low'} />
                    </div>
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <SignalCard title="低落信号（关键词命中）" signal={mh.depression} />
                      <SignalCard title="焦虑信号（关键词命中）" signal={mh.anxiety} />
                    </div>
                    <p className="text-[11px] text-gray-400 mt-3">
                      {mh.caveat ?? '关键词命中的情绪信号强度分档，不是临床量表得分，不能作为诊断或筛查结论使用。'}
                    </p>
                  </div>
                )}
              </div>

              {/* 快照时间线 */}
              {(snapshots?.length ?? 0) > 0 && (
                <div className="glass-card rounded-2xl p-5 stagger-item">
                  <h2 className="text-sm font-semibold text-gray-700 mb-3">检测记录</h2>
                  <div className="space-y-2">
                    {snapshots!.slice(0, 5).map((s, i) => (
                      <div key={i} className="flex items-center justify-between rounded-lg bg-white/50 px-3 py-2 text-xs">
                        <span className="text-gray-400">{s.timestamp?.replace('T', ' ').slice(0, 16)}</span>
                        <span className="text-gray-500">
                          稳定度 {Math.round((s as unknown as { stability?: number }).stability ?? 0) * 100}%
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmReset}
        title="重置心理画像"
        message={
          activeCharacter
            ? `将清除角色「${activeCharacter.name}」名下已学习的 ${scopeCount || profile?.snapshots || 0} 份会话心理画像，该角色会重新从对话中认识你。其他角色的画像不受影响。此操作不可撤销。`
            : '将清除所有已学习的心理特征数据，角色会重新从对话中认识你。此操作不可撤销。'
        }
        confirmText="确认重置"
        onConfirm={handleReset}
        onCancel={() => setConfirmReset(false)}
      />
    </AnimatedPage>
  )
}

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import client from '../api/client'
import {
  useActiveCharacter,
  useAchievements,
  useDashboard,
  useEmotionDistribution,
  useEmotionState,
  useEmotionTrend,
  useMemoryFacts,
} from '../hooks/useQueries'
import type { AchievementsResponse, DashboardStats } from '../types/api'

function getAffinityLabel(affinity: number): string {
  if (affinity >= 70) return '亲密'
  if (affinity >= 30) return '熟悉'
  return '初识'
}

/** 成就四类的视觉语义（暖黄/海盐蓝/薄荷青体系内取色） */
const CATEGORY_META: Record<string, { label: string; chip: string }> = {
  companion: { label: '陪伴', chip: 'border-macaron-yellow bg-macaron-yellow/30 text-macaron-yellow-deep' },
  memory: { label: '记忆', chip: 'border-macaron-blue bg-macaron-blue/30 text-macaron-blue-deep' },
  interaction: { label: '互动', chip: 'border-macaron-mint bg-macaron-mint/30 text-macaron-mint-deep' },
  exploration: { label: '探索', chip: 'border-primary-300 bg-primary-50/60 text-primary-700' },
}

export default function StatusCenter() {
  const { activeCharacter } = useActiveCharacter()
  const { data: stats, isLoading: statsLoading } = useDashboard()
  const { data: emotionData } = useEmotionState()
  const { data: facts } = useMemoryFacts()
  const { data: achievements } = useAchievements(activeCharacter?.id)
  const { data: trendData } = useEmotionTrend(7)
  const { data: distData } = useEmotionDistribution(7)

  if (!activeCharacter) {
    return (
      <div className="p-6 max-w-4xl mx-auto text-center text-gray-400 text-sm">
        暂无活跃角色，请先创建或激活角色
      </div>
    )
  }

  const emotionLabel = emotionData?.primary?.type || stats?.current_emotion || '—'
  const affinityLabel = getAffinityLabel((stats as DashboardStats | undefined)?.affinity ?? 0)
  const memoryCount = (stats as DashboardStats | undefined)?.recent_memories ?? 0
  const recentFacts = facts ?? []

  return (
    <div className="p-4 sm:p-6 max-w-4xl mx-auto space-y-5">
      <h2 className="text-lg font-bold text-gray-700 flex items-center gap-2">
        <span className="section-bar" />
        状态中心
      </h2>

      <div className="grid grid-cols-3 gap-3 sm:gap-4">
        <div className="glass-card rounded-xl p-3 sm:p-4 text-center">
          <div className="text-xl sm:text-2xl font-bold text-macaron-yellow-deep">{emotionLabel}</div>
          <div className="text-xs text-gray-400 mt-1">当前情绪</div>
        </div>
        <div className="glass-card rounded-xl p-3 sm:p-4 text-center">
          <div className="text-xl sm:text-2xl font-bold text-macaron-blue-deep">{affinityLabel}</div>
          <div className="text-xs text-gray-400 mt-1">亲密等级</div>
        </div>
        <div className="glass-card rounded-xl p-3 sm:p-4 text-center">
          <div className="text-xl sm:text-2xl font-bold text-macaron-mint-deep">
            {statsLoading ? '—' : memoryCount}
          </div>
          <div className="text-xs text-gray-400 mt-1">记忆条目</div>
        </div>
      </div>

      <EmotionInsightCard trend={trendData?.trend ?? []} distribution={distData?.distribution ?? []} distTotal={distData?.total ?? 0} />

      <AchievementsCard data={achievements} />

      <DiaryCard />

      <MemorySystemCard characterId={activeCharacter.id} recentFacts={recentFacts} />
    </div>
  )
}

/** SP-1 收官（2026-09-01）：亲密度情绪趋势（会话内存态迷你折线）+ 情绪分布条。
 * 数据源 EmotionEngine 环形缓冲（上限 500），重启清零——空态诚实标注"会话内"。 */
function EmotionInsightCard({
  trend,
  distribution,
  distTotal,
}: {
  trend: Array<{ timestamp: string; primary_emotion: string; intensity: number }>
  distribution: Array<{ emotion: string; count: number }>
  distTotal: number
}) {
  const [open, setOpen] = useState(true)
  if (trend.length === 0 && distTotal === 0) return null

  // 迷你折线：intensity 0~1 → 40px 高 SVG polyline
  const W = 280
  const H = 40
  const points = trend
    .slice(-30)
    .map((t, i, arr) => {
      const x = arr.length > 1 ? (i / (arr.length - 1)) * W : W / 2
      const y = H - Math.max(0, Math.min(1, t.intensity)) * (H - 4) - 2
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  const maxCount = Math.max(1, ...distribution.map(d => d.count))
  const emotionColor: Record<string, string> = {
    开心: 'bg-macaron-yellow', 平常: 'bg-macaron-blue', 期待: 'bg-macaron-mint',
    害羞: 'bg-pink-300', 惊讶: 'bg-sky-300', 担忧: 'bg-indigo-300',
    生气: 'bg-red-300', 伤心: 'bg-slate-300', 委屈: 'bg-amber-300', 感动: 'bg-rose-300',
  }

  return (
    <div className="glass-card rounded-xl p-4">
      <button onClick={() => setOpen(v => !v)} className="w-full flex items-center gap-2 text-left">
        <span className="section-bar" />
        <h3 className="text-sm font-semibold text-gray-700 flex-1">情绪洞察</h3>
        <span className="text-[10px] text-gray-400">{open ? '收起' : '展开'} · 会话内</span>
      </button>
      {open && (
        <div className="mt-3 space-y-4">
          {trend.length > 1 && (
            <div>
              <p className="text-[10px] text-gray-400 mb-1">强度趋势（最近 {Math.min(30, trend.length)} 次互动）</p>
              <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-10" preserveAspectRatio="none">
                <polyline points={points} fill="none" stroke="currentColor"
                  className="text-macaron-yellow-deep" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
              </svg>
            </div>
          )}
          {distribution.length > 0 && (
            <div>
              <p className="text-[10px] text-gray-400 mb-1.5">情绪分布（共 {distTotal} 次）</p>
              <div className="space-y-1.5">
                {distribution.slice(0, 6).map(d => (
                  <div key={d.emotion} className="flex items-center gap-2">
                    <span className="text-xs text-gray-600 w-8 shrink-0">{d.emotion}</span>
                    <div className="flex-1 h-2 rounded-full bg-black/5 overflow-hidden">
                      <div
                        className={`h-full rounded-full ${emotionColor[d.emotion] ?? 'bg-gray-300'}`}
                        style={{ width: `${(d.count / maxCount) * 100}%` }}
                      />
                    </div>
                    <span className="text-[10px] text-gray-400 tabular-nums w-10 text-right">
                      {Math.round((d.count / distTotal) * 100)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** 角色成就（ADR-0014）： unlocked 彩色徽章 / locked 灰态 + 进度。读取即幂等重算。 */
function AchievementsCard({ data }: { data?: AchievementsResponse }) {
  const [open, setOpen] = useState(false)
  if (!data || data.total === 0) return null
  const unlocked = data.achievements.filter(a => a.unlocked)

  return (
    <div className="glass-card rounded-xl p-4">
      <button onClick={() => setOpen(v => !v)} className="w-full flex items-center gap-2 text-left">
        <span className="section-bar" />
        <h3 className="text-sm font-semibold text-gray-700 flex-1">成就</h3>
        <span className="text-[10px] text-gray-400">
          已解锁 {data.unlocked_count} / {data.total}
          {open ? ' · 收起' : ''}
        </span>
      </button>
      {(open || unlocked.length > 0) && (
        <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-2">
          {data.achievements.map(a => {
            const meta = CATEGORY_META[a.category] ?? CATEGORY_META.exploration
            if (!a.unlocked && !open) return null
            return (
              <div
                key={a.achievement_id}
                title={a.description}
                className={`rounded-lg border px-2.5 py-2 ${
                  a.unlocked ? meta.chip : 'border-gray-200 bg-gray-50/60 text-gray-400'
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <span className={`text-xs font-medium ${a.unlocked ? '' : 'text-gray-500'}`}>
                    {a.unlocked ? a.name : '？？？'}
                  </span>
                  <span className="text-[10px] opacity-70">{meta.label}</span>
                </div>
                <div className="mt-1 flex items-center gap-1.5">
                  <div className="flex-1 h-1 rounded-full bg-black/5 overflow-hidden">
                    <div
                      className={`h-full rounded-full ${a.unlocked ? 'bg-current' : 'bg-gray-300'}`}
                      style={{ width: `${Math.min(100, (a.progress / a.target) * 100)}%` }}
                    />
                  </div>
                  <span className="text-[10px] tabular-nums">
                    {a.progress}/{a.target}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** 记忆三层（GAP-2）：角色长期事实 / 珍藏收藏 / 当前工作记忆 + 最近沉淀列表。 */
function MemorySystemCard({
  characterId,
  recentFacts,
}: {
  characterId: string
  recentFacts: Array<{ content: string }>
}) {
  const { data: charFacts } = useQuery({
    queryKey: ['memory', 'char-facts', characterId],
    queryFn: () =>
      client
        .get(`/characters/${characterId}/memory/facts`)
        .then(r => (r.data as { total?: number }).total ?? 0),
    enabled: !!characterId,
    staleTime: 30 * 1000,
  })
  const { data: favorites } = useQuery({
    queryKey: ['memory', 'favorites', characterId],
    queryFn: () =>
      client
        .get(`/characters/${characterId}/favorites`)
        .then(r => (r.data as { total?: number }).total ?? 0)
        .catch(() => 0),
    enabled: !!characterId,
    staleTime: 30 * 1000,
  })
  const { data: workingCount } = useQuery({
    queryKey: ['memory', 'working'],
    queryFn: () =>
      client
        .get('/stats')
        .then(r => (r.data as { working_count?: number }).working_count ?? 0)
        .catch(() => 0),
    staleTime: 15 * 1000,
    refetchInterval: 30 * 1000,
  })

  const layers = [
    { label: '角色长期事实', value: charFacts ?? 0, hint: '该角色沉淀的事实记忆' },
    { label: '珍藏记忆', value: favorites ?? 0, hint: '被标记收藏的记忆' },
    { label: '工作记忆（会话）', value: workingCount ?? 0, hint: '当前会话上下文中的记忆' },
  ]

  return (
    <div className="glass-card rounded-xl p-4">
      <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
        <span className="section-bar" />
        记忆体系
      </h3>
      <div className="grid grid-cols-3 gap-2 mb-4">
        {layers.map(l => (
          <div key={l.label} title={l.hint} className="text-center p-2 rounded-lg bg-white/40">
            <p className="text-base font-bold text-gray-700 tabular-nums">{l.value}</p>
            <p className="text-[10px] text-gray-400 mt-0.5">{l.label}</p>
          </div>
        ))}
      </div>

      <p className="text-[10px] text-gray-400 mb-2">最近沉淀</p>
      <div className="space-y-2">
        {recentFacts.length > 0 ? (
          recentFacts.slice(0, 5).map((fact, idx) => (
            <div
              key={idx}
              className="bg-white/40 rounded-lg px-3 py-2 text-xs text-gray-600"
            >
              {fact.content}
            </div>
          ))
        ) : (
          <p className="py-4 text-center text-xs text-gray-400">
            还没有沉淀下来的记忆
          </p>
        )}
      </div>
    </div>
  )
}

/** 角色日记（候选 B）：daily_summaries 每日摘要，最近 5 条折叠展示。 */
function DiaryCard() {
  const [entries, setEntries] = useState<Array<{ date: string; summary: string }>>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let alive = true
    client.get('/memory/diary', { params: { limit: 5 } })
      .then((r: { data?: { entries?: Array<{ date: string; summary: string }> } }) => { if (alive) setEntries(r.data?.entries ?? []) })
      .catch(() => {})
    return () => { alive = false }
  }, [])

  if (entries.length === 0) return null

  return (
    <div className="glass-card rounded-xl p-4">
      <button onClick={() => setOpen(v => !v)} className="w-full flex items-center gap-2 text-left">
        <span className="section-bar" />
        <h3 className="text-sm font-semibold text-gray-700 flex-1">角色日记</h3>
        <span className="text-[10px] text-gray-400">{open ? '收起' : `${entries.length} 篇`}</span>
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {entries.map(e => (
            <div key={e.date} className="rounded-lg bg-white/50 border border-white/60 px-3 py-2">
              <p className="text-[10px] text-gray-400 mb-1">{e.date}</p>
              <p className="text-xs text-gray-600 leading-relaxed whitespace-pre-wrap">{e.summary}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

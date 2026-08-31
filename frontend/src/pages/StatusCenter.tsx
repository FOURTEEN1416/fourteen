import { useEffect, useState } from 'react'
import client from '../api/client'
import { useActiveCharacter, useDashboard, useEmotionState, useMemoryFacts } from '../hooks/useQueries'
import type { DashboardStats } from '../types/api'

function getAffinityLabel(affinity: number): string {
  if (affinity >= 70) return '亲密'
  if (affinity >= 30) return '熟悉'
  return '初识'
}

export default function StatusCenter() {
  const { activeCharacter } = useActiveCharacter()
  const { data: stats, isLoading: statsLoading } = useDashboard()
  const { data: emotionData } = useEmotionState()
  const { data: facts } = useMemoryFacts()

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

      <DiaryCard />

      <div className="glass-card rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
          <span className="section-bar" />
          最近记忆
        </h3>
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

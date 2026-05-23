import { useState } from 'react'
import { usePersonaProfile } from '../hooks/useAPI'
import { useEmotionTrend } from '../hooks/useAPI'
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend } from 'recharts'
import Skeleton from '../components/common/Skeleton'
import Button from '../components/common/Button'

const traitLabels: Record<string, string> = {
  warmth: '温暖', playfulness: '调皮', independence: '独立',
  jealousy: '吃醋', stubbornness: '固执',
  formality: '正式', emoji_freq: 'Emoji', sentence_length: '句子长度',
  emotional_expression: '情感表达', humor: '幽默',
  expressiveness: '表达欲', empathy: '共情', jealousy_tendency: '醋意',
}

export default function PersonaPage() {
  const { profile, log } = usePersonaProfile()
  const [trendDays, setTrendDays] = useState(7)
  const emotionTrend = useEmotionTrend(trendDays)

  if (!profile) {
    return (
      <div className="flex-1 p-6 space-y-4">
        <Skeleton className="h-5 w-32" />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Skeleton className="h-80" />
          <Skeleton className="h-80" />
        </div>
      </div>
    )
  }

  const radarData = Object.entries(profile.core_character).map(([key, value]) => ({
    trait: traitLabels[key] || key,
    value: value * 100,
  }))

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-gray-800 mb-6">十四的人设档案</h1>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Radar Chart */}
        <div className="bg-white/80 border border-gray-200 rounded-2xl p-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">性格雷达</h2>
          <ResponsiveContainer width="100%" height={300}>
            <RadarChart data={radarData}>
              <PolarGrid stroke="#334155" />
              <PolarAngleAxis dataKey="trait" tick={{ fill: '#94a3b8', fontSize: 12 }} />
              <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
              <Radar
                name="性格"
                dataKey="value"
                stroke="#94a3b8"
                fill="#64748b"
                fillOpacity={0.2}
                strokeWidth={2}
              />
            </RadarChart>
          </ResponsiveContainer>
        </div>

        {/* Character Traits */}
        <div className="bg-white/80 border border-gray-200 rounded-2xl p-6 space-y-5">
          <div>
            <h2 className="text-sm font-semibold text-gray-700 mb-4">性格维度</h2>
            <div className="space-y-3">
              {Object.entries(profile.core_character).map(([key, value]) => (
                <div key={key}>
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>{traitLabels[key] || key}</span>
                    <span>{Math.round(value * 100)}%</span>
                  </div>
                  <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-gray-300 rounded-full"
                      style={{ width: `${value * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h2 className="text-sm font-semibold text-gray-700 mb-4">说话风格</h2>
            <div className="space-y-3">
              {Object.entries(profile.speaking_style).map(([key, value]) => (
                <div key={key}>
                  <div className="flex justify-between text-xs text-gray-500 mb-1">
                    <span>{traitLabels[key] || key}</span>
                    <span>{Math.round(value * 100)}%</span>
                  </div>
                  <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-accent-500 rounded-full"
                      style={{ width: `${value * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Evolution Log */}
        <div className="lg:col-span-2 bg-white/80 border border-gray-200 rounded-2xl p-6">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">演化日志</h2>
          {log.length === 0 ? (
            <p className="text-xs text-gray-400">暂无演化记录</p>
          ) : (
            <div className="space-y-2 max-h-60 overflow-y-auto">
              {log.slice().reverse().map((entry, i) => (
                <div key={i} className="flex items-center gap-3 text-xs text-gray-500 bg-gray-200/30 rounded-lg px-3 py-2">
                  <span className="text-gray-400 shrink-0">
                    {new Date(entry.timestamp).toLocaleString('zh-CN')}
                  </span>
                  <span className="text-gray-700">{traitLabels[entry.dimension] || entry.dimension}</span>
                  <span className="text-gray-400">{entry.before.toFixed(2)} → {entry.after.toFixed(2)}</span>
                  <span className="text-primary-400">({entry.delta >= 0 ? '+' : ''}{entry.delta.toFixed(3)})</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Emotion Trend Chart */}
        <div className="lg:col-span-2 bg-white/80 border border-gray-200 rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-gray-700">情感趋势</h2>
            <div className="flex gap-1">
              <Button size="sm" variant={trendDays === 7 ? 'primary' : 'ghost'} onClick={() => setTrendDays(7)}>7天</Button>
              <Button size="sm" variant={trendDays === 30 ? 'primary' : 'ghost'} onClick={() => setTrendDays(30)}>30天</Button>
            </div>
          </div>
          {emotionTrend && emotionTrend.trend.length > 0 ? (
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={emotionTrend.trend}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="timestamp" tick={{ fill: '#64748b', fontSize: 10 }} tickFormatter={(v: string) => new Date(v).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })} />
                <YAxis domain={[0, 1]} tick={{ fill: '#64748b', fontSize: 10 }} />
                <Tooltip contentStyle={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 }} labelFormatter={(v: string) => new Date(v).toLocaleString('zh-CN')} />
                <Legend />
                <Line type="monotone" dataKey="intensity" stroke="#6366f1" strokeWidth={2} dot={false} name="情感强度" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-xs text-gray-400">暂无情感趋势数据</p>
          )}
        </div>
      </div>
    </div>
  )
}

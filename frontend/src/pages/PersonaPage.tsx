import { usePersonaProfile } from '../hooks/useAPI'
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer } from 'recharts'
import Skeleton from '../components/common/Skeleton'

const traitLabels: Record<string, string> = {
  warmth: '温暖', playfulness: '调皮', independence: '独立',
  jealousy: '吃醋', stubbornness: '固执',
  formality: '正式', emoji_freq: 'Emoji', sentence_length: '句子长度',
  emotional_expression: '情感表达', humor: '幽默',
  expressiveness: '表达欲', empathy: '共情', jealousy_tendency: '醋意',
}

export default function PersonaPage() {
  const { profile, log } = usePersonaProfile()

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
      <h1 className="text-base font-semibold text-slate-200 mb-6">小暖的人设档案</h1>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Radar Chart */}
        <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-6">
          <h2 className="text-sm font-semibold text-slate-300 mb-4">性格雷达</h2>
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
        <div className="bg-slate-900/40 border border-slate-800/60 rounded-2xl p-6 space-y-5">
          <div>
            <h2 className="text-sm font-semibold text-slate-300 mb-4">性格维度</h2>
            <div className="space-y-3">
              {Object.entries(profile.core_character).map(([key, value]) => (
                <div key={key}>
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>{traitLabels[key] || key}</span>
                    <span>{Math.round(value * 100)}%</span>
                  </div>
                  <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-slate-500 rounded-full"
                      style={{ width: `${value * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h2 className="text-sm font-semibold text-slate-300 mb-4">说话风格</h2>
            <div className="space-y-3">
              {Object.entries(profile.speaking_style).map(([key, value]) => (
                <div key={key}>
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>{traitLabels[key] || key}</span>
                    <span>{Math.round(value * 100)}%</span>
                  </div>
                  <div className="h-1.5 bg-slate-800 rounded-full overflow-hidden">
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
        <div className="lg:col-span-2 bg-slate-900/40 border border-slate-800/60 rounded-2xl p-6">
          <h2 className="text-sm font-semibold text-slate-300 mb-4">演化日志</h2>
          {log.length === 0 ? (
            <p className="text-xs text-slate-500">暂无演化记录</p>
          ) : (
            <div className="space-y-2 max-h-60 overflow-y-auto">
              {log.slice().reverse().map((entry, i) => (
                <div key={i} className="flex items-center gap-3 text-xs text-slate-400 bg-slate-800/30 rounded-lg px-3 py-2">
                  <span className="text-slate-500 shrink-0">
                    {new Date(entry.timestamp).toLocaleString('zh-CN')}
                  </span>
                  <span className="text-slate-300">{traitLabels[entry.dimension] || entry.dimension}</span>
                  <span className="text-slate-500">{entry.before.toFixed(2)} → {entry.after.toFixed(2)}</span>
                  <span className="text-primary-400">({entry.delta >= 0 ? '+' : ''}{entry.delta.toFixed(3)})</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

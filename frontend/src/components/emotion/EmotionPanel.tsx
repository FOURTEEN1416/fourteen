import { useEmotionState } from '../../hooks/useAPI'
import Badge from '../common/Badge'

const emotionColors: Record<string, string> = {
  '开心': 'emotion-amber',
  '伤心': 'emotion-blue',
  '生气': 'emotion-red',
  '撒娇': 'emotion-rose',
  '吃醋': 'emotion-purple',
  '傲娇': 'emotion-rose',
  '温柔': 'emotion-green',
  '调皮': 'emotion-amber',
  '疲惫': 'accent-400',
  '平常': 'accent-400',
}

const emotionEmojis: Record<string, string> = {
  '开心': '😊', '伤心': '😢', '生气': '😤', '撒娇': '🥺',
  '吃醋': '😒', '傲娇': '😏', '温柔': '💗', '调皮': '😝',
  '疲惫': '😮‍💨', '平常': '💬',
}

export default function EmotionPanel() {
  const { state, loading } = useEmotionState()

  if (loading || !state) return null

  const affinityLabel =
    state.affinity <= 1 ? '陌生人' :
    state.affinity <= 2 ? '认识' :
    state.affinity <= 3 ? '朋友' :
    state.affinity <= 4 ? '知己' :
    state.affinity <= 5 ? '暧昧' :
    state.affinity <= 6 ? '恋人' : '热恋'

  return (
    <div className="bg-white/80 border border-gray-200 rounded-xl p-4">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-3">情感状态</h3>

      <div className="flex items-center gap-3 mb-3">
        <div className="w-9 h-9 rounded-lg bg-gray-200 flex items-center justify-center text-base">
          {emotionEmojis[state.current_emotion] || '💬'}
        </div>
        <div>
          <div className="text-sm font-medium text-gray-800">{state.current_emotion}</div>
          <div className="text-[10px] text-gray-400">强度 {Math.round(state.intensity * 100)}%</div>
        </div>
      </div>

      <div className="space-y-2.5">
        <div>
          <div className="flex justify-between text-[10px] text-gray-400 mb-1">
            <span>能量</span>
            <span>{Math.round(state.energy * 100)}%</span>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-gray-400 rounded-full transition-all duration-500"
              style={{ width: `${state.energy * 100}%` }}
            />
          </div>
        </div>

        <div>
          <div className="flex justify-between text-[10px] text-gray-400 mb-1">
            <span>好感度</span>
            <Badge variant={state.affinity > 4 ? 'success' : 'default'}>{affinityLabel}</Badge>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-emotion-rose rounded-full transition-all duration-700"
              style={{ width: `${(state.affinity / 8) * 100}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import Badge from '../common/Badge'

const emotionEmojis: Record<string, string> = {
  '开心': '😊', '伤心': '😢', '生气': '😤', '撒娇': '🥺',
  '吃醋': '😒', '傲娇': '😏', '温柔': '💗', '调皮': '😝',
  '疲惫': '😮‍💨', '平常': '💬',
}

interface Props {
  characterId?: string
  maxAffinity?: number
}

export default function EmotionPanel({ maxAffinity }: Props) {
  const { data: state, isLoading } = useQuery({
    queryKey: ['emotion', 'state'],
    queryFn: () => api.emotionState().then(r => r.data as { current_emotion: string; intensity: number; energy: number; affinity: number }),
    refetchInterval: 10 * 1000,
  })

  if (isLoading || !state || state.affinity == null) return null

  const max = maxAffinity ?? 8

  return (
    <div className="bg-white/80 border border-gray-200 rounded-xl p-4">
      <h3 className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider mb-3">情感状态</h3>

      <div className="flex items-center gap-3 mb-3">
        <div className="w-9 h-9 rounded-lg bg-gray-200 flex items-center justify-center text-base">
          {emotionEmojis[state.current_emotion] || '💬'}
        </div>
        <div>
          <div className="text-sm font-medium text-gray-800">{state.current_emotion}</div>
          <div className="text-[10px] text-gray-400">强度 {state.intensity != null ? `${Math.round(state.intensity * 100)}%` : '-'}</div>
        </div>
      </div>

      <div className="space-y-2.5">
        <div>
          <div className="flex justify-between text-[10px] text-gray-400 mb-1">
            <span>能量</span>
            <span>{state.energy != null ? `${Math.round(state.energy * 100)}%` : '-'}</span>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-gray-400 rounded-full transition-all duration-500"
              style={{ width: `${(state.energy ?? 0) * 100}%` }}
            />
          </div>
        </div>

        <div>
          <div className="flex justify-between text-[10px] text-gray-400 mb-1">
            <span>好感度</span>
            <Badge variant={(state.affinity ?? 0) > 4 ? 'success' : 'default'}>{(state.affinity ?? 0).toFixed(0)} / {max}</Badge>
          </div>
          <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
            <div
              className="h-full bg-emotion-rose rounded-full transition-all duration-700"
              style={{ width: `${((state.affinity ?? 0) / max) * 100}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

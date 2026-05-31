import { useStorylineProgress } from '../../hooks/useQueries'
import Badge from '../common/Badge'

interface StorylineIndicatorProps {
  characterId: string
}

export default function StorylineIndicator({ characterId }: StorylineIndicatorProps) {
  const { data: progress, isLoading } = useStorylineProgress(characterId)

  if (isLoading) return null
  if (!progress || !progress.enabled) return null

  const stage = progress.current_stage
  const state = progress.state

  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-1">
      <Badge variant="info">剧情线</Badge>
      {stage && (
        <Badge variant="default">{stage.display_name}</Badge>
      )}
      {state && (
        <span className="text-[10px] text-gray-400">
          第{state.story_day + 1}天 {String(state.story_hour).padStart(2, '0')}:{String(state.story_minute).padStart(2, '0')}
        </span>
      )}
      {progress.progress_percent > 0 && progress.progress_percent < 100 && (
        <div className="w-16 h-1 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-gray-400 rounded-full transition-all"
            style={{ width: `${progress.progress_percent}%` }}
          />
        </div>
      )}
      {state?.is_ended && (
        <Badge variant="info">已结局</Badge>
      )}
    </div>
  )
}

import Badge from './Badge'
import type { UrgencyLevel } from '../../types/api'

interface UrgencyBadgeProps {
  urgency: number
  className?: string
}

function getUrgencyInfo(urgency: number): { level: UrgencyLevel; variant: 'error' | 'warning' | 'info' | 'success' } {
  if (urgency >= 8) return { level: '非常想找你', variant: 'error' }
  if (urgency >= 5) return { level: '有点想你', variant: 'warning' }
  if (urgency >= 3) return { level: '想找人说话', variant: 'info' }
  return { level: '还好', variant: 'success' }
}

export default function UrgencyBadge({ urgency, className = '' }: UrgencyBadgeProps) {
  const { level, variant } = getUrgencyInfo(urgency)
  return (
    <Badge variant={variant} className={className}>
      {level} ({urgency.toFixed(1)})
    </Badge>
  )
}

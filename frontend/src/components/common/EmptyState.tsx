import type { ReactNode } from 'react'

interface EmptyStateProps {
  icon?: string
  title?: string
  description?: string
  action?: ReactNode
  className?: string
}

export default function EmptyState({
  icon = '📭',
  title = '暂无数据',
  description,
  action,
  className = '',
}: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center py-12 px-4 ${className}`}>
      <span className="text-2xl mb-3">{icon}</span>
      <p className="text-sm text-gray-500 font-medium mb-1">{title}</p>
      {description && <p className="text-xs text-gray-400 mb-4 max-w-xs text-center">{description}</p>}
      {action && <div>{action}</div>}
    </div>
  )
}

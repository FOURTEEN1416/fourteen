import { Inbox } from 'lucide-react'

interface EmptyStateProps {
  icon?: React.ReactNode
  title: string
  description?: string
  action?: React.ReactNode
}

export default function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <div className="text-gray-200 mb-3">
        {icon || <Inbox className="w-10 h-10" />}
      </div>
      <p className="text-sm text-gray-400 font-medium">{title}</p>
      {description && <p className="text-xs text-gray-300 mt-1 max-w-[200px]">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

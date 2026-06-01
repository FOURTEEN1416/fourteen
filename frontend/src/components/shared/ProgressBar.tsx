interface ProgressBarProps {
  progress: number
  status?: 'idle' | 'active' | 'success' | 'error'
}

export default function ProgressBar({ progress, status = 'active' }: ProgressBarProps) {
  const pct = Math.min(100, Math.max(0, progress))
  const colors: Record<string, string> = {
    idle: 'bg-gray-200/50',
    active: 'bg-gradient-to-r from-primary-400 to-accent-400',
    success: 'bg-green-400',
    error: 'bg-red-400',
  }

  return (
    <div className="w-full h-1.5 bg-gray-200/50 rounded-full overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ease-out ${colors[status]}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

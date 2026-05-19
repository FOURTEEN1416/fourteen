interface SkeletonProps {
  className?: string
  lines?: number
}

export default function Skeleton({ className = '', lines = 1 }: SkeletonProps) {
  if (lines > 1) {
    return (
      <div className="space-y-2">
        {Array.from({ length: lines }).map((_, i) => (
          <div
            key={i}
            className={`h-3 bg-slate-800/60 rounded animate-skeleton ${
              i === lines - 1 ? 'w-3/4' : 'w-full'
            } ${className}`}
          />
        ))}
      </div>
    )
  }

  return (
    <div className={`bg-slate-800/60 rounded animate-skeleton ${className}`} />
  )
}

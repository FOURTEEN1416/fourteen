interface SkeletonProps {
  className?: string
  count?: number
}

export default function Skeleton({ className = 'h-4', count = 1 }: SkeletonProps) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className={`rounded bg-gray-200/60 animate-skeleton ${className}`}
        />
      ))}
    </>
  )
}

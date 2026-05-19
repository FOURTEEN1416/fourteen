import type { ReactNode } from 'react'

interface CardProps {
  children: ReactNode
  className?: string
  padding?: 'sm' | 'md' | 'lg'
  hover?: boolean
  onClick?: () => void
}

const paddings = {
  sm: 'p-3',
  md: 'p-4',
  lg: 'p-6',
}

export default function Card({ children, className = '', padding = 'md', hover, onClick }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={[
        'bg-slate-900/40 border border-slate-800/60 rounded-xl',
        paddings[padding],
        hover && 'hover:bg-slate-800/30 cursor-pointer transition-colors',
        onClick && 'cursor-pointer',
        className,
      ].filter(Boolean).join(' ')}
    >
      {children}
    </div>
  )
}

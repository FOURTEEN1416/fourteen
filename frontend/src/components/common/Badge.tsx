import type { ReactNode } from 'react'

interface BadgeProps {
  children: ReactNode
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info'
  className?: string
}

const variantStyles = {
  default: 'bg-slate-800/60 text-slate-400',
  success: 'bg-green-900/30 text-green-400',
  warning: 'bg-yellow-900/30 text-yellow-400',
  error: 'bg-red-900/30 text-red-400',
  info: 'bg-blue-900/30 text-blue-400',
}

export default function Badge({ children, variant = 'default', className = '' }: BadgeProps) {
  return (
    <span className={[
      'inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded-md',
      variantStyles[variant],
      className,
    ].filter(Boolean).join(' ')}>
      {children}
    </span>
  )
}

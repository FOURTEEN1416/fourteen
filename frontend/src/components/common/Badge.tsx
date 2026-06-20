import type { ReactNode } from 'react'

interface BadgeProps {
  children: ReactNode
  variant?: 'default' | 'success' | 'warning' | 'error' | 'info'
  className?: string
}

// 修复：改为浅色配色，与 index.css 的 @theme 浅色系一致（参考 shared/Badge.tsx 风格）
const variantStyles = {
  default: 'bg-gray-100/60 text-gray-500',
  success: 'bg-green-50 text-green-600',
  warning: 'bg-amber-50 text-amber-600',
  error: 'bg-red-50 text-red-600',
  info: 'bg-primary-50 text-primary-600',
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

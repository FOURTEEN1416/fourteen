import type { ReactNode, ButtonHTMLAttributes } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  children: ReactNode
}

const variantStyles = {
  primary: 'bg-primary-600 text-white hover:bg-primary-500 disabled:bg-slate-800 disabled:text-slate-600',
  secondary: 'border border-slate-700 text-slate-300 hover:bg-slate-800/60 hover:text-slate-200',
  ghost: 'text-slate-500 hover:text-slate-300 hover:bg-slate-800/30',
  danger: 'bg-red-900/30 text-red-400 border border-red-800/30 hover:bg-red-900/50',
}

const sizeStyles = {
  sm: 'px-2.5 py-1 text-xs rounded-md',
  md: 'px-3.5 py-1.5 text-sm rounded-lg',
  lg: 'px-5 py-2 text-sm rounded-lg',
}

export default function Button({
  variant = 'primary', size = 'md', loading, children, disabled, className = '', ...props
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={[
        'font-medium transition-colors outline-none focus:ring-2 focus:ring-primary-400/30',
        variantStyles[variant],
        sizeStyles[size],
        (disabled || loading) && 'opacity-50 cursor-not-allowed',
        className,
      ].filter(Boolean).join(' ')}
      {...props}
    >
      {loading ? (
        <span className="flex items-center gap-2">
          <span className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
          {children}
        </span>
      ) : children}
    </button>
  )
}

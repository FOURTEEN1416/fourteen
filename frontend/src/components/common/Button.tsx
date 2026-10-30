import type { ReactNode, ButtonHTMLAttributes } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  children: ReactNode
}

// 修复：改为浅色配色，与 index.css 的 @theme 浅色系一致（参考 shared/Badge.tsx 风格）
// NEW-31：primary 对齐 index.css 的 .btn-macaron（暖黄渐变 + 深墨字）。
// 旧 bg-primary-600 实心蓝是全站第三套按钮语言——同一「主 CTA」在页面里有三种长相；
// 现在基础 Button 与 .btn-macaron 同源：hover/active 的位移与阴影由该类自身提供，
// 这里不再叠 bg/hover:bg（会与渐变背景打架）。
// ⚠️ 层叠事实：`.btn-macaron`（index.css:333，**未分层**）会压过 Tailwind 放进
// @layer utilities 的普通声明（同属性下未分层 > 分层）——所以文字色与焦点环这两个
// 需要生效的类带 `!`（important 反向胜过未分层普通声明，已用 @tailwindcss/node
// 编译实证产出 `color: … !important` / `box-shadow: … !important`）。
const variantStyles = {
  primary: 'btn-macaron text-text-primary! disabled:text-gray-400!',
  secondary: 'border border-gray-300 text-gray-700 hover:bg-gray-100 hover:text-gray-800',
  ghost: 'text-gray-500 hover:text-gray-700 hover:bg-gray-100',
  danger: 'bg-red-50 text-red-600 border border-red-200 hover:bg-red-100',
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
        // focus:ring-2 带 `!`：无 .btn-macaron 的变体也照旧，有该类的变体则焦点环不再被
        // 它的未分层 box-shadow 压掉（见 variantStyles 上方层叠说明）。
        'font-medium transition-colors outline-none focus:ring-2! focus:ring-primary-400/30',
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

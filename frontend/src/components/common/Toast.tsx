import { useErrorStore } from '../../store/errorStore'

// 修复：改为浅色配色，与 index.css 的 @theme 浅色系一致（参考 shared/Badge.tsx 风格）
const typeStyles = {
  error: 'bg-red-50 border-red-200 text-red-700',
  warning: 'bg-amber-50 border-amber-200 text-amber-700',
  info: 'bg-blue-50 border-blue-200 text-blue-700',
  success: 'bg-green-50 border-green-200 text-green-700',
}

const typeIcons = {
  error: '✕',
  warning: '⚠',
  info: 'i',
  success: '✓',
}

export default function ToastContainer() {
  const { toasts, removeToast } = useErrorStore()

  if (toasts.length === 0) return null

  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 max-w-sm">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`flex items-start gap-2 px-3.5 py-2.5 rounded-lg border text-sm shadow-lg animate-toast-in ${typeStyles[toast.type]}`}
        >
          <span className="text-xs font-mono mt-0.5 shrink-0 w-4 text-center">
            {typeIcons[toast.type]}
          </span>
          <span className="flex-1">{toast.message}</span>
          <button
            onClick={() => removeToast(toast.id)}
            className="text-xs opacity-60 hover:opacity-100 shrink-0 mt-0.5"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

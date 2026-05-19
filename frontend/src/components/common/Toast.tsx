import { useErrorStore } from '../../store/errorStore'

const typeStyles = {
  error: 'bg-red-900/80 border-red-800/50 text-red-200',
  warning: 'bg-yellow-900/80 border-yellow-800/50 text-yellow-200',
  info: 'bg-blue-900/80 border-blue-800/50 text-blue-200',
  success: 'bg-green-900/80 border-green-800/50 text-green-200',
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

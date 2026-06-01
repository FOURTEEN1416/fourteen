import { useEffect, useState } from 'react'
import { CheckCircle, AlertCircle, X } from 'lucide-react'

export interface ToastData {
  id: string
  type: 'success' | 'error' | 'info'
  message: string
}

interface ToastProps {
  toast: ToastData
  onRemove: (id: string) => void
}

const icons = {
  success: <CheckCircle className="w-4 h-4 text-green-500" />,
  error: <AlertCircle className="w-4 h-4 text-red-500" />,
  info: <CheckCircle className="w-4 h-4 text-primary-500" />,
}

export default function Toast({ toast, onRemove }: ToastProps) {
  const [exiting, setExiting] = useState(false)

  useEffect(() => {
    const timer = setTimeout(() => setExiting(true), 3000)
    return () => clearTimeout(timer)
  }, [])

  const handleClose = () => setExiting(true)

  const handleEnd = () => { if (exiting) onRemove(toast.id) }

  return (
    <div
      onAnimationEnd={handleEnd}
      className={`flex items-center gap-2 px-4 py-2.5 glass-card rounded-lg shadow-lg text-xs
        ${exiting ? 'animate-toast-out' : 'animate-toast-in'}`}
    >
      {icons[toast.type]}
      <span className="text-gray-700">{toast.message}</span>
      <button onClick={handleClose} className="text-gray-300 hover:text-gray-500 ml-2">
        <X className="w-3 h-3" />
      </button>
    </div>
  )
}

export function ToastContainer({ toasts, onRemove }: { toasts: ToastData[]; onRemove: (id: string) => void }) {
  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <Toast key={t.id} toast={t} onRemove={onRemove} />
      ))}
    </div>
  )
}

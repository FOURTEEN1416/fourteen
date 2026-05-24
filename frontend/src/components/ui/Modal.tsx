import { useEffect, useRef } from 'react'
import { X } from 'lucide-react'

interface ModalProps {
  open: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  variant?: 'danger' | 'default'
  onConfirm: () => void
  onCancel: () => void
}

export default function Modal({ open, title, message, confirmText = '确定', cancelText = '取消', variant = 'default', onConfirm, onCancel }: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, onCancel])

  if (!open) return null

  return (
    <div ref={overlayRef} className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 animate-fade-in"
      onClick={(e) => { if (e.target === overlayRef.current) onCancel() }}>
      <div className="bg-white rounded-xl shadow-xl p-5 w-80 max-w-[90vw] animate-scale-in">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
          <button onClick={onCancel} className="text-gray-400 hover:text-gray-600" aria-label="关闭"><X className="w-4 h-4" /></button>
        </div>
        <p className="text-sm text-gray-500 mb-5">{message}</p>
        <div className="flex gap-2 justify-end">
          <button onClick={onCancel} className="px-3 py-1.5 text-sm text-gray-500 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors">{cancelText}</button>
          <button onClick={onConfirm}
            className={`px-3 py-1.5 text-sm text-white rounded-lg transition-colors ${variant === 'danger' ? 'bg-red-500 hover:bg-red-600' : 'bg-blue-500 hover:bg-blue-600'}`}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}

import Modal from './Modal'

interface ConfirmDialogProps {
  open: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  variant?: 'danger' | 'default'
  onConfirm: () => void
  onCancel: () => void
}

export default function ConfirmDialog({
  open, title, message, confirmText = '确定', cancelText = '取消', variant = 'default',
  onConfirm, onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal open={open} title={title} onClose={onCancel} size="sm">
      <p className="text-sm text-gray-500 mb-5">{message}</p>
      <div className="flex gap-2 justify-end">
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-xs text-gray-500 bg-gray-100/80 rounded-lg hover:bg-gray-200/60 transition-colors"
        >
          {cancelText}
        </button>
        <button
          onClick={onConfirm}
          className={`px-3 py-1.5 text-xs text-white rounded-lg transition-colors
            ${variant === 'danger' ? 'bg-red-500 hover:bg-red-600' : 'bg-primary-500 hover:bg-primary-400'}`}
        >
          {confirmText}
        </button>
      </div>
    </Modal>
  )
}

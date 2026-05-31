import { useState } from 'react'
import ConfirmDialog from './ConfirmDialog'

interface DangerButtonProps {
  label: string
  dialogTitle: string
  dialogMessage: string
  confirmText?: string
  onConfirm: () => void
  className?: string
}

export default function DangerButton({
  label,
  dialogTitle,
  dialogMessage,
  confirmText = '确认删除',
  onConfirm,
  className = '',
}: DangerButtonProps) {
  const [showConfirm, setShowConfirm] = useState(false)

  return (
    <>
      <button
        onClick={() => setShowConfirm(true)}
        className={`px-4 py-2 text-sm font-medium rounded-lg transition-all
          bg-red-500/10 border border-red-300/30 text-red-500
          hover:bg-red-500/20 active:scale-95 ${className}`}
      >
        {label}
      </button>
      <ConfirmDialog
        open={showConfirm}
        title={dialogTitle}
        message={dialogMessage}
        confirmText={confirmText}
        cancelText="取消"
        variant="danger"
        onConfirm={() => {
          setShowConfirm(false)
          onConfirm()
        }}
        onCancel={() => setShowConfirm(false)}
      />
    </>
  )
}

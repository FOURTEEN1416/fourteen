import Modal from './Modal'
import Button from '../common/Button'

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

/**
 * 通用二次确认框（NEW-31：两颗按钮收敛到 common/Button）。
 *
 * 危险确认**必须是红系实心主色块**——「删除/停用」这类不可逆动作不能弱化成描边钮。
 * Button 的 danger 变体本身是浅红描边（既有的次级危险语言，StorylineEditor 在用），
 * 这里不复制一份按钮实现，而是用 Tailwind 的 important 后缀（`bg-red-500!`）把底色
 * 强制提上来：同一 utilities layer 内两个 bg 工具类是按 Tailwind 内部顺序决胜负的，
 * 靠 className 书写顺序无法保证覆盖。
 *
 * props 契约与改版前完全一致（open/title/message/confirmText/cancelText/variant/
 * onConfirm/onCancel），既有 13 处调用点无需改动。
 */
export default function ConfirmDialog({
  open, title, message, confirmText = '确定', cancelText = '取消', variant = 'default',
  onConfirm, onCancel,
}: ConfirmDialogProps) {
  return (
    <Modal open={open} title={title} onClose={onCancel} size="sm">
      <p className="text-sm text-gray-500 mb-5">{message}</p>
      <div className="flex gap-2 justify-end">
        <Button
          variant="secondary"
          onClick={onCancel}
          className="min-h-[44px] px-4"
        >
          {cancelText}
        </Button>
        <Button
          variant={variant === 'danger' ? 'danger' : 'primary'}
          onClick={onConfirm}
          className={
            variant === 'danger'
              ? 'min-h-[44px] px-4 bg-red-500! text-white! border-red-500! hover:bg-red-600!'
              : 'min-h-[44px] px-4'
          }
        >
          {confirmText}
        </Button>
      </div>
    </Modal>
  )
}

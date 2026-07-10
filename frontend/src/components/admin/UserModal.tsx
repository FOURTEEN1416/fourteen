import { Modal } from '../shared'
import UserForm from './UserForm'
import type { AdminUser, AdminCreateUserRequest, AdminUpdateUserRequest } from '../../api/admin'

// ════════════════════════════════════════════════════
//  Props
// ════════════════════════════════════════════════════

export interface UserModalProps {
  open: boolean
  mode: 'create' | 'edit'
  initialData?: AdminUser | null
  formData: AdminCreateUserRequest | AdminUpdateUserRequest
  errors: Partial<Record<string, string>>
  saving: boolean
  onChange: (data: AdminCreateUserRequest | AdminUpdateUserRequest) => void
  onSubmit: () => void
  onClose: () => void
}

// ════════════════════════════════════════════════════
//  组件
// ════════════════════════════════════════════════════

export default function UserModal({
  open, mode, initialData, formData, errors, saving,
  onChange, onSubmit, onClose,
}: UserModalProps) {
  const title = mode === 'create'
    ? '创建用户'
    : `编辑用户 — ${initialData?.display_name || initialData?.username || ''}`

  const handleCancel = () => {
    onClose()
  }

  return (
    <Modal open={open} title={title} onClose={handleCancel} size="md">
      <UserForm
        mode={mode}
        formData={formData}
        errors={errors}
        saving={saving}
        onChange={onChange}
        onSubmit={onSubmit}
        onCancel={handleCancel}
      />
    </Modal>
  )
}

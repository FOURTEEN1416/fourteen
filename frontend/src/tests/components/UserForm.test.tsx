import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import UserForm, { type UserFormProps } from '../../components/admin/UserForm'

// ════════════════════════════════════════════════════════════════
//  NEW-20②：封禁（停用）是危险方向——true→false 才弹确认，
//  取消不得改写 formData；恢复活跃一键直生效。
//  确认框与 Modal 嵌套（编辑弹窗 → 确认框），ESC 栈语义见 Modal.test.tsx。
// ════════════════════════════════════════════════════════════════

function setup(overrides: Partial<UserFormProps> = {}) {
  const onChange = vi.fn()
  const props: UserFormProps = {
    mode: 'edit',
    formData: {
      email: 'user@test.local',
      username: 'user',
      display_name: 'User',
      role: 'viewer',
      is_active: true,
    },
    errors: {},
    saving: false,
    onChange,
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  }
  render(<UserForm {...props} />)
  return { onChange }
}

describe('UserForm — 停用需二次确认', () => {
  it('取消勾选「活跃」→ 出现危险确认框，且不立刻改写 is_active', () => {
    const { onChange } = setup()
    const checkbox = screen.getByRole('checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(true)

    fireEvent.click(checkbox)

    expect(screen.getByText('确认停用用户')).toBeDefined()
    expect(screen.getByText(/登录会话/)).toBeDefined()
    expect(screen.getByText(/旧 token 不会恢复/)).toBeDefined()
    // 未确认前不得写入 is_active=false
    expect(onChange).not.toHaveBeenCalled()
    expect(checkbox.checked).toBe(true)
  })

  it('点取消 → 确认框关闭、复选框仍勾选、formData 未被改写', () => {
    const { onChange } = setup()
    const checkbox = screen.getByRole('checkbox') as HTMLInputElement

    fireEvent.click(checkbox)
    fireEvent.click(screen.getByRole('button', { name: '保持活跃' }))

    expect(screen.queryByText('确认停用用户')).toBeNull()
    expect(checkbox.checked).toBe(true)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('点确认 → is_active 写成 false，其余字段保留', () => {
    const { onChange } = setup()
    const checkbox = screen.getByRole('checkbox') as HTMLInputElement

    fireEvent.click(checkbox)
    fireEvent.click(screen.getByRole('button', { name: '确认停用' }))

    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toMatchObject({
      is_active: false,
      email: 'user@test.local',
      username: 'user',
    })
    expect(screen.queryByText('确认停用用户')).toBeNull()
  })

  it('初始停用（is_active=false）勾选回「活跃」→ 不弹框，直接写 is_active=true', () => {
    const { onChange } = setup({
      formData: {
        email: 'user@test.local',
        username: 'user',
        display_name: 'User',
        role: 'viewer',
        is_active: false,
      },
    })
    const checkbox = screen.getByRole('checkbox') as HTMLInputElement
    expect(checkbox.checked).toBe(false)

    fireEvent.click(checkbox)

    expect(screen.queryByText('确认停用用户')).toBeNull()
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toMatchObject({ is_active: true })
  })

  it('创建模式不渲染状态复选框（无停用入口）', () => {
    setup({ mode: 'create', formData: { email: 'n@test.local', username: 'newbie', role: 'viewer' } })
    expect(screen.queryByRole('checkbox')).toBeNull()
  })
})

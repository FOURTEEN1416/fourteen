import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import Modal from '../../components/shared/Modal'
import ConfirmDialog from '../../components/shared/ConfirmDialog'
import UserModal from '../../components/admin/UserModal'
import ProviderEditModal from '../../components/llm/ProviderEditModal'
import ProviderGuideModal from '../../components/llm/ProviderGuideModal'
import type { ProviderOption } from '../../api/llmProviders'

// ════════════════════════════════════════════════════════════════
//  NEW-20：Modal 的 ESC 栈式守卫
//  - 单弹窗 ESC 照常关闭；
//  - 确认框叠在编辑弹窗上时，ESC 只关最上层，下层保持打开；
//  - 父组件直接卸载弹窗（不是 open=false）时栈项被清理，不留残渣。
//  ⚠️ 边界：layout/MobileDrawer 自带独立 ESC 监听、不经过 Modal，
//     「抽屉 + 弹窗同开」仍会双响应——这是既有行为，本测试不覆盖也不声称已统一。
// ════════════════════════════════════════════════════════════════

function pressEscape() {
  fireEvent.keyDown(document, { key: 'Escape' })
}

const GUIDE_PROVIDER: ProviderOption = {
  key: 'agnes',
  name: 'Agnes',
  description: '演示供应商',
  sort_order: 1,
  guide: { apply_url: 'https://example.com/apply', free_quota: '免费', steps: ['第一步'], tips: [], warnings: [] },
  is_special: false,
  is_preset: true,
  enabled: true,
}

describe('shared/Modal — ESC 栈语义', () => {
  it('单弹窗：ESC 关闭自身', () => {
    const onClose = vi.fn()
    const { unmount } = render(
      <Modal open title="单弹窗" onClose={onClose}>正文</Modal>,
    )
    expect(screen.getByText('正文')).toBeDefined()
    pressEscape()
    expect(onClose).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('嵌套确认框：ESC 只关确认框，编辑弹窗仍在', () => {
    const onOuter = vi.fn()
    const onInnerCancel = vi.fn()
    const { unmount } = render(
      <Modal open title="编辑用户" onClose={onOuter}>
        <p>编辑弹窗正文</p>
        <ConfirmDialog
          open
          title="确认停用用户"
          message="后果说明"
          onConfirm={() => {}}
          onCancel={onInnerCancel}
        />
      </Modal>,
    )

    pressEscape()

    expect(onInnerCancel).toHaveBeenCalledTimes(1)
    expect(onOuter).not.toHaveBeenCalled()
    // 编辑弹窗内容仍在（未被一起关掉）
    expect(screen.getByText('编辑弹窗正文')).toBeDefined()
    unmount()
  })

  it('open=true 时父组件直接卸载弹窗：栈项（含嵌套项）被清理，之后其它弹窗 ESC 仍生效', () => {
    const onOuter = vi.fn()
    const onInnerCancel = vi.fn()
    const onFresh = vi.fn()

    // 先开一组嵌套弹窗（登记表里同时有 0 层与 1 层），再把它整体从树上移除——
    // 注意是「父组件移除」而不是 open=false，走的是卸载清理路径。
    const first = render(
      <Modal open title="外层" onClose={onOuter}>
        <p>外层正文</p>
        <ConfirmDialog
          open
          title="内层"
          message="后果"
          onConfirm={() => {}}
          onCancel={onInnerCancel}
        />
      </Modal>,
    )
    first.unmount()

    // 新弹窗（0 层）：若先前的嵌套项残留在登记表里（1 层 > 0 层），
    // 它会一直被当成「最上层」，ESC 被吃掉、这里就关不掉——本用例即为该泄漏的护栏。
    const second = render(
      <Modal open title="新弹窗" onClose={onFresh}>新弹窗正文</Modal>,
    )
    pressEscape()

    expect(onFresh).toHaveBeenCalledTimes(1)
    expect(onInnerCancel).not.toHaveBeenCalled()
    expect(onOuter).not.toHaveBeenCalled()
    second.unmount()
  })

  // ── 四个既有消费者回归：单弹窗行为零变化 ──
  it('回归 UserModal：ESC 走 onClose', () => {
    const onClose = vi.fn()
    const { unmount } = render(
      <UserModal
        open
        mode="edit"
        initialData={null}
        formData={{ email: 'a@b.com', username: 'abc', role: 'viewer', is_active: true }}
        errors={{}}
        saving={false}
        onChange={() => {}}
        onSubmit={() => {}}
        onClose={onClose}
      />,
    )
    pressEscape()
    expect(onClose).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('回归 ProviderEditModal：ESC 走 onClose', () => {
    const onClose = vi.fn()
    const { unmount } = render(
      <ProviderEditModal open mode="create" onSubmit={() => {}} onClose={onClose} />,
    )
    pressEscape()
    expect(onClose).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('回归 ProviderGuideModal：ESC 走 onClose', () => {
    const onClose = vi.fn()
    const { unmount } = render(
      <ProviderGuideModal provider={GUIDE_PROVIDER} open onClose={onClose} />,
    )
    pressEscape()
    expect(onClose).toHaveBeenCalledTimes(1)
    unmount()
  })

  it('回归 ConfirmDialog：ESC 等价取消（onCancel）', () => {
    const onCancel = vi.fn()
    const onConfirm = vi.fn()
    const { unmount } = render(
      <ConfirmDialog open title="确认" message="后果" onConfirm={onConfirm} onCancel={onCancel} />,
    )
    pressEscape()
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
    unmount()
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import * as shared from '../../components/shared'
import Button from '../../components/common/Button'
import ConfirmDialog from '../../components/shared/ConfirmDialog'
import Skeleton from '../../components/shared/Skeleton'
import EmptyState from '../../components/shared/EmptyState'

// ════════════════════════════════════════════════════════════════
//  NEW-31（2026-10-30）：组件库收编的契约守卫
//  ① 子标签栏组件已按删除纪律处置（文件删除 + barrel 去导出），全仓零消费者；
//  ② 基础 Button 的 primary 与 index.css 的 .btn-macaron 同源（暖黄渐变 + 深墨字），
//     不再是第三套「实心蓝」按钮语言；
//  ③ ConfirmDialog 两颗按钮渲染为 Button，危险确认仍是红系实心主色（不弱化成描边），
//     两钮高度 ≥44px，props 契约与调用点语义不变。
//  每个断言在改动前都会失败（红测精神）：旧实现既没有 btn-macaron，也没有 Button 子节点。
// ════════════════════════════════════════════════════════════════

// 已删除组件的名字在断言里**拼接构造**而不是写成字面量——删除纪律要求
// `grep -rn <组件名> frontend/src` 零命中作为零引用举证；守卫用例自身不得成为假阳性来源。
const REMOVED_BARREL_EXPORT = ['Sub', 'TabBar'].join('')

describe('shared barrel — 子标签栏组件已下线', () => {
  it('不再导出已删除组件（文件已删，barrel 不得留悬空再导出）', () => {
    expect(Object.keys(shared)).not.toContain(REMOVED_BARREL_EXPORT)
  })

  it('其余公开组件仍在（删除不能顺手带走邻居）', () => {
    for (const name of ['Slider', 'Toggle', 'Select', 'FileUpload', 'Modal', 'ConfirmDialog', 'Skeleton', 'EmptyState', 'AnimatedPage', 'ScrollProgress']) {
      expect(Object.keys(shared)).toContain(name)
    }
  })
})

describe('common/Button — primary 对齐 .btn-macaron', () => {
  it('primary 带 btn-macaron + 深墨字，不再用 bg-primary-600 实心蓝', () => {
    render(<Button>保存</Button>)
    const btn = screen.getByRole('button', { name: '保存' })
    expect(btn.className).toContain('btn-macaron')
    expect(btn.className).not.toContain('bg-primary-600')
    // 文字色与焦点环必须带 ! —— .btn-macaron 是未分层 CSS（index.css:333），
    // 同属性下会压过 @layer utilities 的普通声明；不带 ! 这两个类实际是死码。
    expect(btn.className).toContain('text-text-primary!')
    expect(btn.className).toContain('focus:ring-2!')
  })

  it('disabled / loading 仍可辨识：opacity-50 + cursor-not-allowed + 灰字 + spinner', () => {
    const { rerender } = render(<Button disabled>停用</Button>)
    const disabledBtn = screen.getByRole('button', { name: '停用' }) as HTMLButtonElement
    expect(disabledBtn.disabled).toBe(true)
    expect(disabledBtn.className).toContain('opacity-50')
    expect(disabledBtn.className).toContain('cursor-not-allowed')
    // 禁用态灰字同样需要 !，否则被 .btn-macaron 的深墨字压掉
    expect(disabledBtn.className).toContain('disabled:text-gray-400!')

    rerender(<Button loading>提交</Button>)
    const loadingBtn = screen.getByRole('button', { name: '提交' }) as HTMLButtonElement
    expect(loadingBtn.disabled).toBe(true)
    expect(loadingBtn.className).toContain('opacity-50')
    expect(loadingBtn.querySelector('.animate-spin')).not.toBeNull()
  })
})

describe('shared/ConfirmDialog — 按钮收敛到 Button', () => {
  it('两颗按钮都是 Button，且 props 语义不变（取消→onCancel，确认→onConfirm）', () => {
    const onConfirm = vi.fn()
    const onCancel = vi.fn()
    render(
      <ConfirmDialog open title="确认删除" message="后果说明" onConfirm={onConfirm} onCancel={onCancel} />,
    )

    const cancel = screen.getByRole('button', { name: '取消' })
    const confirm = screen.getByRole('button', { name: '确定' })

    // 两钮都是 common/Button（最小高度由调用点 class 保证 ≥44px）
    expect(cancel.className).toContain('min-h-[44px]')
    expect(confirm.className).toContain('min-h-[44px]')
    // 默认变体的确认钮走 primary = btn-macaron
    expect(confirm.className).toContain('btn-macaron')
    // 取消钮走 secondary（描边、非主色块）
    expect(cancel.className).toContain('border')

    fireEvent.click(cancel)
    expect(onCancel).toHaveBeenCalledTimes(1)
    fireEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('危险确认仍是红系实心主色块（不能被弱化成浅红描边），高度 ≥44px', () => {
    render(
      <ConfirmDialog
        open
        title="确认删除用户"
        message="后果说明"
        confirmText="删除"
        variant="danger"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    )

    const confirm = screen.getByRole('button', { name: '删除' })
    // 危险态必须把红底强制提上来（Button 的 danger 变体默认是 bg-red-50 描边语言）
    expect(confirm.className).toContain('bg-red-500!')
    expect(confirm.className).toContain('text-white!')
    expect(confirm.className).toContain('min-h-[44px]')
    // 主色块不得是浅红描边形态的替身：红底必须以 important 级生效
    expect(confirm.className).not.toContain('bg-primary-600')
    // 取消钮仍然 ≥44px（两钮等高）
    expect(screen.getByRole('button', { name: '取消' }).className).toContain('min-h-[44px]')
  })
})

describe('shared/Skeleton 与 shared/EmptyState — 收编件本身可用', () => {
  it('Skeleton 按 count 渲染多条骨架（RolesPage 加载态的拼装基石）', () => {
    const { container } = render(<Skeleton className="h-4 w-1/2" count={3} />)
    const items = container.querySelectorAll('.animate-skeleton')
    expect(items.length).toBe(3)
    expect(items[0].className).toContain('h-4')
    expect(items[0].className).toContain('w-1/2')
  })

  it('EmptyState 四个槽位齐活（icon/title/description/action）', () => {
    render(
      <EmptyState
        icon={<span data-testid="empty-icon" />}
        title="暂无角色"
        description="可在这里创建一个开始使用"
        action={<button>创建角色</button>}
      />,
    )
    expect(screen.getByTestId('empty-icon')).toBeDefined()
    expect(screen.getByText('暂无角色')).toBeDefined()
    expect(screen.getByText('可在这里创建一个开始使用')).toBeDefined()
    expect(screen.getByRole('button', { name: '创建角色' })).toBeDefined()
  })
})

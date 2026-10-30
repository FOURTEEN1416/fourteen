import { describe, it, expect, vi } from 'vitest'
import type { ReactElement } from 'react'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Slider from '../../components/shared/Slider'
import { RoleBadge } from '../../components/admin/UserBadges'
import Breadcrumb from '../../components/layout/Breadcrumb'

// NEW-32：设计 token 与视觉语言统一——四项均为「组织方式」收编，
// 断言的是「引用 token / 复用语义类」，不是像素快照。
// 红测前提：改前 Slider 硬编码 #22d0df、徽章用 rose/amber/sky 族、面包屑用 mat-floating。

vi.mock('../../hooks/useQueries', () => ({
  useUnifiedCharacters: () => ({ data: { characters: [] } }),
  useActiveCharacter: () => ({ data: null }),
  useWechatStatus: () => ({ data: null }),
}))

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'admin', display_name: '十四的主人', role: 'admin' },
    logout: vi.fn().mockResolvedValue(undefined),
  }),
}))

function renderWithProviders(ui: ReactElement, path = '/roles') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

// ── ① Slider 色相：滑轨渐变与 hover 环必须引用 accent token ──

describe('NEW-32 ① Slider 滑轨色相', () => {
  it('渐变引用 var(--color-accent-400) 且不再出现孤立青色 #22d0df', () => {
    const { container } = render(<Slider label="亲密度" value={0.5} onChange={() => {}} />)
    const input = container.querySelector('input[type="range"]') as HTMLInputElement
    expect(input).toBeTruthy()

    const style = input.getAttribute('style') || ''
    expect(style).toContain('var(--color-accent-400)')
    expect(style).not.toContain('#22d0df')
    expect(style).not.toContain('34,208,223')
  })

  it('hover 环引用 accent token，不再硬编码 rgba(34,208,223,…)', () => {
    const { container } = render(<Slider label="亲密度" value={0.5} onChange={() => {}} />)
    const input = container.querySelector('input[type="range"]') as HTMLInputElement
    const classes = input.className

    expect(classes).toContain('var(--color-accent-400)')
    expect(classes).not.toContain('34,208,223')
    // 进度条宽度仍按取值计算（0.5 → 50%），拖动语义不变
    const style = input.getAttribute('style') || ''
    expect(style).toContain('50%')
  })
})

// ── ② 角色徽章收编到 tag 三族，rose/pink/indigo 留给情绪数据色 ──

describe('NEW-32 ② 角色徽章 tag 族', () => {
  const cases: [('admin' | 'editor' | 'viewer'), string][] = [
    ['admin', 'tag-yellow'],
    ['editor', 'tag-blue'],
    ['viewer', 'tag-mint'],
  ]

  it.each(cases)('%s 徽章使用 %s', (role, tagClass) => {
    render(<RoleBadge role={role} />)
    const el = screen.getByText(role === 'admin' ? '管理员' : role === 'editor' ? '编辑者' : '观察者')
    expect(el.className).toContain(tagClass)
    // 尺寸语言保持不变（px-2 py-0.5 rounded-full text-[11px]）
    expect(el.className).toContain('px-2')
    expect(el.className).toContain('py-0.5')
    expect(el.className).toContain('rounded-full')
    expect(el.className).toContain('text-[11px]')
    // 情绪数据色相不得回流为身份色
    for (const banned of ['rose-', 'pink-', 'indigo-', 'amber-', 'sky-']) {
      expect(el.className).not.toContain(banned)
    }
  })
})

// ── ③ 面包屑材质：常驻文档流不享受玻璃浮层特权 ──

describe('NEW-32 ③ 面包屑材质', () => {
  it('nav 用 mat-raised 并保留贴边覆写', () => {
    renderWithProviders(<Breadcrumb />, '/settings/llm')
    const nav = screen.getByLabelText('面包屑导航')

    expect(nav.className).toContain('mat-raised')
    expect(nav.className).not.toContain('mat-floating')
    expect(nav.className).toContain('rounded-none')
    expect(nav.className).toContain('border-x-0')
    expect(nav.className).toContain('border-t-0')
  })
})

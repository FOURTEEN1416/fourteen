import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import BindingDetailPage from '../../pages/BindingDetailPage'

// vi.mock factory 被 hoist，需要用 vi.hoisted() 创建共享 mock fns
const { mockListMyBindings, mockUpdateBinding, mockListPresets } = vi.hoisted(() => ({
  mockListMyBindings: vi.fn(),
  mockUpdateBinding: vi.fn(),
  mockListPresets: vi.fn(),
}))

vi.mock('../../api/wechat', () => ({
  listMyBindings: mockListMyBindings,
  updateBinding: mockUpdateBinding,
}))

vi.mock('../../api/characters', () => ({
  listPresets: mockListPresets,
}))

// ── fixture data ──
const FAKE_BINDINGS = [
  {
    id: 1,
    user_id: 16,
    wxid: 'wx_test_001',
    nickname: '测试微信',
    avatar: null,
    character_card_id: 'default',
    bound_at: '2026-06-04T00:00:00Z',
  },
]

const FAKE_PRESETS = [
  { id: 'gentle_teacher', name: '温柔老师', description: '一位耐心的语文老师', tags: ['温柔', '教学'] },
  { id: 'tsundere', name: '傲娇女友', description: '口是心非的可爱女友', tags: ['傲娇', '可爱'] },
  { id: 'mysterious', name: '神秘人', description: '身份不明的神秘角色', tags: ['神秘'] },
]

// ── 正确渲染：用 MemoryRouter + Route 让 useParams 能获取 :wxid ──
function renderComponent() {
  return render(
    <MemoryRouter initialEntries={['/bindings/wx_test_001']}>
      <Routes>
        <Route path="/bindings/:wxid" element={<BindingDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('BindingDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // default happy path
    mockListMyBindings.mockResolvedValue({ data: { bindings: FAKE_BINDINGS } })
    mockListPresets.mockResolvedValue({ presets: FAKE_PRESETS })
    mockUpdateBinding.mockResolvedValue({
      data: { binding: { ...FAKE_BINDINGS[0], character_card_id: 'gentle_teacher' } },
    })
  })

  it('shows loading skeleton initially', () => {
    // 不 resolve 让 loading 保持 true
    mockListMyBindings.mockReturnValue(new Promise(() => {}))
    mockListPresets.mockReturnValue(new Promise(() => {}))
    const { container } = renderComponent()
    // skeleton 有 animate-skeleton class
    const skeletonEls = container.querySelectorAll('.animate-skeleton')
    expect(skeletonEls.length).toBeGreaterThan(0)
  })

  it('shows "绑定不存在" when wxid not found', async () => {
    mockListMyBindings.mockResolvedValue({ data: { bindings: [] } })
    renderComponent()
    expect(await screen.findByText('绑定不存在', {}, { timeout: 3000 })).toBeDefined()
    expect(screen.getByText('未找到该微信绑定')).toBeDefined()
  })

  it('renders binding info and preset grid after loading', async () => {
    renderComponent()
    expect(await screen.findByText('测试微信', {}, { timeout: 3000 })).toBeDefined()
    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.getByText('未选择角色（默认）')).toBeDefined()
    expect(screen.getByText('选择一个角色')).toBeDefined()
    expect(screen.getByText('温柔老师')).toBeDefined()
    expect(screen.getByText('傲娇女友')).toBeDefined()
    expect(screen.getByText('神秘人')).toBeDefined()
  })

  it('shows selected role card', async () => {
    mockListMyBindings.mockResolvedValue({
      data: {
        bindings: [{ ...FAKE_BINDINGS[0], character_card_id: 'gentle_teacher' }],
      },
    })
    renderComponent()
    expect(await screen.findByText('gentle_teacher', {}, { timeout: 3000 })).toBeDefined()
    const btns = screen.getAllByRole('button')
    expect(btns.some((b) => b.textContent?.includes('温柔老师'))).toBe(true)
  })

  it('calls updateBinding when a preset is clicked', async () => {
    renderComponent()
    expect(await screen.findByText('温柔老师', {}, { timeout: 3000 })).toBeDefined()

    const btns = screen.getAllByRole('button')
    const teacherBtn = btns.find((b) => b.textContent?.includes('温柔老师'))
    expect(teacherBtn).toBeDefined()
    fireEvent.click(teacherBtn!)

    await waitFor(() => {
      expect(mockUpdateBinding).toHaveBeenCalledWith('wx_test_001', {
        character_card_id: 'gentle_teacher',
      })
    })
  })

  it('shows empty state when no presets', async () => {
    mockListPresets.mockResolvedValue({ presets: [] })
    renderComponent()
    expect(await screen.findByText('暂无角色预设', {}, { timeout: 3000 })).toBeDefined()
  })
})

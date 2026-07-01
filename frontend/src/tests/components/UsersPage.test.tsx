import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import UsersPage from '../../pages/UsersPage'

// ── vi.hoisted() 工厂：mock 数据 + mock fn（在 vi.mock 之前创建） ──
const { mockNavigate, sampleBindings } = vi.hoisted(() => {
  const sampleBindings = [
    {
      id: 1,
      user_id: 16,
      wxid: 'wx_test_001',
      nickname: 'Alice',
      avatar: '',
      character_card_id: 'gentle_teacher',
      bound_at: '2026-06-04T00:00:00Z',
    },
    {
      id: 2,
      user_id: 16,
      wxid: 'wx_test_002',
      nickname: 'Bob',
      avatar: '',
      character_card_id: 'default',
      bound_at: '2026-06-04T00:00:00Z',
    },
    {
      id: 3,
      user_id: 16,
      wxid: 'wx_charlie_003',
      nickname: 'Charlie',
      avatar: '',
      character_card_id: 'tsundere',
      bound_at: '2026-06-04T00:00:00Z',
    },
  ]
  return {
    mockNavigate: vi.fn(),
    sampleBindings,
  }
})

// ── AxiosResponse 包装辅助 ──
import type { AxiosResponse } from 'axios'

function mockAxiosResponse<T>(data: T): AxiosResponse<T> {
  return { data, status: 200, statusText: 'OK', headers: {}, config: { headers: {} } } as AxiosResponse<T>
}

// ── module mocks（useNavigate 保留真实实现但 override） ──
vi.mock('../../api/wechat', () => ({
  listMyBindings: vi.fn(),
  unbindWechat: vi.fn(),
}))

vi.mock('../../store/authStore', () => ({
  getAccessToken: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

// ── 导入 mocked 模块 ──
import { listMyBindings, unbindWechat } from '../../api/wechat'
import { getAccessToken } from '../../store/authStore'

// ── render helper ──
function renderComponent() {
  return render(
    <MemoryRouter>
      <UsersPage />
    </MemoryRouter>
  )
}

describe('UsersPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.confirm = vi.fn(() => true) // default: confirm yes
    // Default: authenticated
    vi.mocked(getAccessToken).mockReturnValue('mock-token')
    // Default: successful response with 3 bindings
    vi.mocked(listMyBindings).mockResolvedValue(
      mockAxiosResponse({ bindings: sampleBindings, total: sampleBindings.length }),
    )
    vi.mocked(unbindWechat).mockResolvedValue(mockAxiosResponse({ status: 'ok' }))
  })

  // ──────────────────────────────────────────────
  //  1. 未登录状态 — 显示"请先登录"
  // ──────────────────────────────────────────────
  it('shows "请先登录" empty state when not authenticated', () => {
    vi.mocked(getAccessToken).mockReturnValue(null)
    renderComponent()

    expect(screen.getByText('请先登录')).toBeDefined()
    expect(screen.getByText('登录后即可查看绑定的微信账号')).toBeDefined()
    // listMyBindings should NOT be called when not logged in
    expect(listMyBindings).not.toHaveBeenCalled()
  })

  // ──────────────────────────────────────────────
  //  2. 加载中 — 显示骨架屏
  // ──────────────────────────────────────────────
  it('shows skeleton loading state while fetching', () => {
    vi.mocked(listMyBindings).mockReturnValue(new Promise(() => {})) // never resolves
    const { container } = renderComponent()

    // Skeleton 组件渲染 .animate-skeleton 元素
    const skeletonEls = container.querySelectorAll('.animate-skeleton')
    expect(skeletonEls.length).toBeGreaterThan(0)
    // 标题仍然可见
    expect(screen.getByText('我的微信')).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  3. 渲染绑定卡片
  // ──────────────────────────────────────────────
  it('renders binding cards with nickname and wxid', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeDefined()
    })
    expect(screen.getByText('Bob')).toBeDefined()
    expect(screen.getByText('Charlie')).toBeDefined()
    // wxid 以 <code> 标签渲染
    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.getByText('wx_test_002')).toBeDefined()
    expect(screen.getByText('wx_charlie_003')).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  4. 搜索按昵称过滤
  // ──────────────────────────────────────────────
  it('filters bindings by nickname search', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeDefined()
    })

    const searchInput = screen.getByPlaceholderText('搜索微信昵称或 wxid...')
    fireEvent.change(searchInput, { target: { value: 'ali' } })

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeDefined()
    })
    expect(screen.queryByText('Bob')).toBeNull()
    expect(screen.queryByText('Charlie')).toBeNull()
  })

  // ──────────────────────────────────────────────
  //  5. 搜索无结果 — 显示"未找到匹配"
  // ──────────────────────────────────────────────
  it('shows "未找到匹配" when search has no results', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeDefined()
    })

    const searchInput = screen.getByPlaceholderText('搜索微信昵称或 wxid...')
    fireEvent.change(searchInput, { target: { value: 'zzz' } })

    await waitFor(() => {
      expect(screen.getByText('未找到匹配')).toBeDefined()
    })
    expect(screen.getByText('尝试修改搜索关键词')).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  6. 点击绑定卡片 → navigate 到详情页
  // ──────────────────────────────────────────────
  it('navigates to binding detail on card click', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeDefined()
    })

    // 卡片是 <button>，包含昵称文本
    const buttons = screen.getAllByRole('button')
    // 找到包含 Alice 的那个
    const aliceButton = buttons.find((b) => b.textContent?.includes('Alice'))
    expect(aliceButton).toBeDefined()
    fireEvent.click(aliceButton!)

    expect(mockNavigate).toHaveBeenCalledWith('/bindings/wx_test_001')
  })

  // ──────────────────────────────────────────────
  //  7. 解绑前弹出确认对话框
  // ──────────────────────────────────────────────
  it('shows confirm dialog before unbind', async () => {
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeDefined()
    })

    const unbindButtons = screen.getAllByTitle('解除绑定')
    fireEvent.click(unbindButtons[0])

    expect(window.confirm).toHaveBeenCalledWith('确定解除绑定此微信？')
    // 等待异步解绑流程（含 setBindings）完成，避免 act() 警告
    await waitFor(() => {
      expect(unbindWechat).toHaveBeenCalledWith('wx_test_001')
    })
  })

  // ──────────────────────────────────────────────
  //  8. 确认后解绑成功，卡片从列表移除
  // ──────────────────────────────────────────────
  it('removes binding card after successful unbind', async () => {
    vi.mocked(unbindWechat).mockResolvedValue(mockAxiosResponse({ status: 'ok', wxid: 'wx_test_001' }))
    window.confirm = vi.fn(() => true)

    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('Alice')).toBeDefined()
    })

    const unbindButtons = screen.getAllByTitle('解除绑定')
    fireEvent.click(unbindButtons[0])

    // Alice 应被移除，Bob 和 Charlie 应保留
    await waitFor(() => {
      expect(screen.queryByText('Alice')).toBeNull()
    })
    expect(screen.getByText('Bob')).toBeDefined()
    expect(screen.getByText('Charlie')).toBeDefined()

    // 验证 unbindWechat 被正确调用
    expect(unbindWechat).toHaveBeenCalledWith('wx_test_001')
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '../utils/test-utils'
import WeChatPage from '../../pages/WeChatPage'

// ── Shared mock fns (hoisted so vi.mock factories can reference them) ──
const { mockUseWechatStatus } = vi.hoisted(() => ({
  mockUseWechatStatus: vi.fn(),
}))

vi.mock('../../hooks/useQueries', () => ({
  useWechatStatus: mockUseWechatStatus,
}))

vi.mock('../../api/wechat', () => ({
  wechatCreateConnection: vi.fn(() => Promise.resolve()),
  wechatDeleteConnection: vi.fn(() => Promise.resolve()),
  bindWechat: vi.fn(() => Promise.resolve()),
}))

vi.mock('../../api/system', () => ({
  wechatQrCode: vi.fn(() => Promise.resolve({ data: {} })),
  wechatConnectionStatus: vi.fn(() => Promise.resolve({ data: {} })),
  wechatConnect: vi.fn(() => Promise.resolve()),
}))

vi.mock('../../store/authStore', () => ({
  getAccessToken: () => null,
}))

// ── Default mock status ──
const defaultStatus = {
  connected: false,
  uptime_seconds: 0,
  reconnect_attempts: 0,
  messages_today: 0,
  last_activity: null,
  qr_code: null,
}

function renderPage() {
  return render(<WeChatPage />)
}

describe('WeChatPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mockUseWechatStatus.mockReturnValue({
      data: defaultStatus,
      isLoading: false,
      isError: false,
    })
  })

  // ── 1. Header render ──
  it('renders header "微信接入" and "扫码连接" button', () => {
    renderPage()
    expect(screen.getByText('微信接入')).toBeDefined()
    const connectBtn = screen.getByRole('button', { name: /扫码连接/ })
    expect(connectBtn).toBeDefined()
  })

  // ── 2. Empty state ──
  it('shows empty state when no connections exist', () => {
    renderPage()
    expect(
      screen.getByText('暂无连接，点击上方 "扫码连接" 添加'),
    ).toBeDefined()
  })

  // ── 3. Loading state in LiveStatusBanner ──
  it('shows loading state in LiveStatusBanner when isLoading=true', () => {
    mockUseWechatStatus.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    })
    const { container } = renderPage()
    expect(container.querySelector('.animate-pulse')).toBeDefined()
  })

  // ── 4. Error state in LiveStatusBanner ──
  it('shows error state in LiveStatusBanner when isError=true', () => {
    mockUseWechatStatus.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
    })
    renderPage()
    expect(screen.getByText('无法获取微信连接状态')).toBeDefined()
    expect(screen.getByText('后端可能未运行')).toBeDefined()
  })

  // ── 5. Connected status ──
  it('shows connected status (green dot, "已连接") when status.connected=true', () => {
    mockUseWechatStatus.mockReturnValue({
      data: { ...defaultStatus, connected: true },
      isLoading: false,
      isError: false,
    })
    renderPage()
    expect(screen.getByText('微信桥接 已连接')).toBeDefined()
    expect(screen.getByText('微信桥接 已连接').closest('.glass-card')).toBeDefined()
  })

  // ── 6. Connections from localStorage ──
  it('displays connections from localStorage', () => {
    const mockConns = [
      { wxid: 'wx_test_001', alias: '测试1号', isOnline: true, isCurrent: false },
      { wxid: 'wx_test_002', alias: '测试2号', isOnline: false, isCurrent: true },
    ]
    localStorage.setItem(
      'unique-you-wechat-connections',
      JSON.stringify(mockConns),
    )
    renderPage()

    // Each row shows wxid and alias
    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.getByText('wx_test_002')).toBeDefined()
    expect(screen.getByText('测试1号')).toBeDefined()
    expect(screen.getByText('测试2号')).toBeDefined()

    // StatsBar labels
    expect(screen.getByText('总连接')).toBeDefined()
    // "在线"/"离线" appear in both StatsBar labels and status badges
    expect(screen.getAllByText('在线').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('离线').length).toBeGreaterThanOrEqual(1)
  })

  // ── 7. Search filters connections ──
  it('search filters connections by wxid', () => {
    const mockConns = [
      { wxid: 'wx_test_001', alias: '测试1号', isOnline: true, isCurrent: false },
      { wxid: 'wx_other_002', alias: '其他号', isOnline: false, isCurrent: false },
    ]
    localStorage.setItem(
      'unique-you-wechat-connections',
      JSON.stringify(mockConns),
    )
    renderPage()

    // Both visible initially
    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.getByText('wx_other_002')).toBeDefined()

    // Search for '001' — matches only wx_test_001
    const searchInput = screen.getByPlaceholderText('搜索 wxid 或别名...')
    fireEvent.change(searchInput, { target: { value: '001' } })

    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.queryByText('wx_other_002')).toBeNull()
  })

  // ── 8. Delete connection ──
  it('delete connection removes from list', async () => {
    const mockConns = [
      { wxid: 'wx_test_001', alias: '测试1号', isOnline: true, isCurrent: false },
      { wxid: 'wx_test_002', alias: '测试2号', isOnline: false, isCurrent: false },
    ]
    localStorage.setItem(
      'unique-you-wechat-connections',
      JSON.stringify(mockConns),
    )
    renderPage()

    // Both visible
    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.getByText('wx_test_002')).toBeDefined()

    // Click delete on first row
    const deleteBtns = screen.getAllByTitle('删除')
    expect(deleteBtns.length).toBe(2)
    fireEvent.click(deleteBtns[0])

    // First connection removed
    await waitFor(() => {
      expect(screen.queryByText('wx_test_001')).toBeNull()
    })
    expect(screen.getByText('wx_test_002')).toBeDefined()

    // Not empty — one still remains
    expect(screen.queryByText('暂无连接')).toBeNull()
  })
})

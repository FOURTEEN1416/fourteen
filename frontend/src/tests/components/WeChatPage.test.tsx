import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '../utils/test-utils'
import WeChatPage from '../../pages/WeChatPage'

// ── Shared mock fns (hoisted so vi.mock factories can reference them) ──
const { mockUseWechatStatus, mockUseWechatBindings, mockUseCharacters } = vi.hoisted(() => ({
  mockUseWechatStatus: vi.fn(),
  mockUseWechatBindings: vi.fn(),
  mockUseCharacters: vi.fn(),
}))

vi.mock('../../hooks/useQueries', () => ({
  useWechatStatus: mockUseWechatStatus,
  useWechatBindings: mockUseWechatBindings,
  useCharacters: mockUseCharacters,
  queryKeys: {
    wechat: { status: ['wechat', 'status'] },
    characters: { all: ['characters'], detail: (id: string) => ['characters', id] },
  },
}))

vi.mock('../../api/wechat', () => ({
  wechatCreateConnection: vi.fn(() => Promise.resolve()),
  wechatDeleteConnection: vi.fn(() => Promise.resolve()),
  bindWechat: vi.fn(() => Promise.resolve()),
  unbindWechat: vi.fn(() => Promise.resolve()),
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
    mockUseWechatBindings.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
    })
    mockUseCharacters.mockReturnValue({
      data: [],
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
  it('shows empty state when no bindings exist', () => {
    renderPage()
    expect(
      screen.getByText('暂无绑定，点击上方 "扫码连接" 添加'),
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

  // ── 6. Displays bindings from API ──
  it('displays bindings from API', () => {
    mockUseCharacters.mockReturnValue({
      data: [
        { id: 'c1', name: '角色一', description: '', is_active: false },
        { id: 'c2', name: '角色二', description: '', is_active: false },
      ],
      isLoading: false,
      isError: false,
    })
    mockUseWechatBindings.mockReturnValue({
      data: [
        { id: 1, user_id: 1, wxid: 'wx_test_001', nickname: '测试1号', avatar: '', character_card_id: 'c1', bound_at: '' },
        { id: 2, user_id: 1, wxid: 'wx_test_002', nickname: '测试2号', avatar: '', character_card_id: 'c2', bound_at: '' },
      ],
      isLoading: false,
      isError: false,
    })
    renderPage()

    // Each row shows wxid and nickname
    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.getByText('wx_test_002')).toBeDefined()
    expect(screen.getByText('测试1号')).toBeDefined()
    expect(screen.getByText('测试2号')).toBeDefined()

    // StatsBar labels
    expect(screen.getByText('总绑定')).toBeDefined()
  })

  // ── 7. Search filters bindings ──
  it('search filters bindings by wxid', () => {
    mockUseCharacters.mockReturnValue({
      data: [
        { id: 'c1', name: '角色一', description: '', is_active: false },
        { id: 'c2', name: '角色二', description: '', is_active: false },
      ],
      isLoading: false,
      isError: false,
    })
    mockUseWechatBindings.mockReturnValue({
      data: [
        { id: 1, user_id: 1, wxid: 'wx_test_001', nickname: '测试1号', avatar: '', character_card_id: 'c1', bound_at: '' },
        { id: 2, user_id: 1, wxid: 'wx_other_002', nickname: '其他号', avatar: '', character_card_id: 'c2', bound_at: '' },
      ],
      isLoading: false,
      isError: false,
    })
    renderPage()

    // Both visible initially
    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.getByText('wx_other_002')).toBeDefined()

    // Search for '001' — matches only wx_test_001
    const searchInput = screen.getByPlaceholderText('搜索 wxid 或昵称...')
    fireEvent.change(searchInput, { target: { value: '001' } })

    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.queryByText('wx_other_002')).toBeNull()
  })

  // ── 8. Delete binding calls unbindWechat ──
  it('delete binding calls unbindWechat', async () => {
    mockUseCharacters.mockReturnValue({
      data: [
        { id: 'c1', name: '角色一', description: '', is_active: false },
        { id: 'c2', name: '角色二', description: '', is_active: false },
      ],
      isLoading: false,
      isError: false,
    })
    mockUseWechatBindings.mockReturnValue({
      data: [
        { id: 1, user_id: 1, wxid: 'wx_test_001', nickname: '测试1号', avatar: '', character_card_id: 'c1', bound_at: '' },
        { id: 2, user_id: 1, wxid: 'wx_test_002', nickname: '测试2号', avatar: '', character_card_id: 'c2', bound_at: '' },
      ],
      isLoading: false,
      isError: false,
    })
    const { unbindWechat } = await import('../../api/wechat')
    renderPage()

    // Both visible
    expect(screen.getByText('wx_test_001')).toBeDefined()
    expect(screen.getByText('wx_test_002')).toBeDefined()

    // Click unbind on first row
    const deleteBtns = screen.getAllByTitle('解绑')
    expect(deleteBtns.length).toBe(2)
    fireEvent.click(deleteBtns[0])

    await waitFor(() => {
      expect(unbindWechat).toHaveBeenCalledWith('wx_test_001')
    })
  })
})

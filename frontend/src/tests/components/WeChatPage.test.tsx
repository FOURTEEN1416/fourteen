import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '../utils/test-utils'
import WeChatPage from '../../pages/WeChatPage'

// ── Shared mock fns (hoisted so vi.mock factories can reference them) ──
const { mockUseWechatStatus } = vi.hoisted(() => ({
  mockUseWechatStatus: vi.fn(),
}))

vi.mock('../../hooks/useQueries', () => ({
  useWechatStatus: mockUseWechatStatus,
  queryKeys: {
    wechat: { status: ['wechat', 'status'] },
    characters: { all: ['characters'], detail: (id: string) => ['characters', id] },
  },
}))

vi.mock('../../api/wechat', () => ({
  wechatCreateConnection: vi.fn(() => Promise.resolve()),
  wechatDeleteConnection: vi.fn(() => Promise.resolve()),
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
    const connectBtn = screen.getByRole('button', { name: '扫码连接' })
    expect(connectBtn).toBeDefined()
  })

  // ── 2. Loading state in LiveStatusBanner ──
  it('shows loading state in LiveStatusBanner when isLoading=true', () => {
    mockUseWechatStatus.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
    })
    const { container } = renderPage()
    expect(container.querySelector('.animate-pulse')).toBeDefined()
  })

  // ── 3. Error state in LiveStatusBanner ──
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

  // ── 4. Connected status ──
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
})

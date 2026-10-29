import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '../utils/test-utils'
import WeChatPage from '../../pages/WeChatPage'

// ── Shared mock fns (hoisted so vi.mock factories can reference them) ──
const { mockUseWechatStatus, mockUseUnifiedCharacters } = vi.hoisted(() => ({
  mockUseWechatStatus: vi.fn(),
  mockUseUnifiedCharacters: vi.fn(),
}))

vi.mock('../../hooks/useQueries', () => ({
  useWechatStatus: mockUseWechatStatus,
  useUnifiedCharacters: mockUseUnifiedCharacters,
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
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: [] },
      isLoading: false,
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
    expect(screen.getByText('我的微信 已连接')).toBeDefined()
    expect(screen.getByText('我的微信 已连接').closest('.glass-card')).toBeDefined()
  })

  // ── 5. V2-2（2026-10-29）：绑定账号 + 当前聊天角色可见 ──
  it('shows the bound bot account id', () => {
    mockUseWechatStatus.mockReturnValue({
      data: { ...defaultStatus, connected: true, bot_id: 'bot-abc123' },
      isLoading: false,
      isError: false,
    })
    renderPage()
    expect(screen.getByText(/绑定账号：bot-abc123/)).toBeDefined()
  })

  it('shows 未绑定 when the channel reports no bot id', () => {
    renderPage()
    expect(screen.getByText(/绑定账号：未绑定/)).toBeDefined()
  })

  it('shows the active character as the current chat companion', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: [{ id: 'c1', name: '林挽夏', is_active: true }] },
      isLoading: false,
    })
    renderPage()
    expect(screen.getByText('当前聊天角色：')).toBeDefined()
    expect(screen.getByText('林挽夏')).toBeDefined()
  })

  it('prompts to pick a role when none is active', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: [{ id: 'c2', name: '凌白', is_active: false }] },
      isLoading: false,
    })
    renderPage()
    expect(screen.getByText(/尚未选择，去选择角色/)).toBeDefined()
  })
})

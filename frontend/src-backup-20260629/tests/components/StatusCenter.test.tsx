import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import StatusCenter from '../../pages/StatusCenter'

// ── hoisted mock fns ──
const { mockUseUnifiedCharacters } = vi.hoisted(() => ({
  mockUseUnifiedCharacters: vi.fn(),
}))

const { mockDashboardStats } = vi.hoisted(() => ({
  mockDashboardStats: vi.fn(),
}))

vi.mock('../../hooks/useQueries', () => ({
  useUnifiedCharacters: () => mockUseUnifiedCharacters(),
}))

vi.mock('../../api/system', () => ({
  dashboardStats: () => mockDashboardStats(),
}))

// ── fixture characters ──
const FAKE_CHAR_1 = {
  id: 'char-001',
  name: '小雅',
  description: '温柔可爱的AI女友',
  personality: { warmth: 0.8 },
  speaking_style: { gentle: 0.7 },
  core_anchors: [],
  user_id: 'u1',
  is_active: true,
  created_at: '2026-01-15T00:00:00Z',
  updated_at: '2026-06-01T00:00:00Z',
  version: 3,
}

const FAKE_CHAR_2 = {
  id: 'char-002',
  name: '小雪',
  description: '高冷傲娇的AI女友',
  personality: { cool: 0.9 },
  speaking_style: { tsundere: 0.8 },
  core_anchors: [],
  user_id: 'u1',
  is_active: false,
  created_at: '2026-03-20T00:00:00Z',
  updated_at: '2026-05-15T00:00:00Z',
  version: 1,
}

const FAKE_STATS = {
  data: {
    today_chats: 42,
    recent_memories: 18,
    affinity: 85,
    energy: 70,
    current_emotion: 'happy',
    system_status: 'healthy',
    uptime_seconds: 7200,
    wechat_connected: true,
    wechat: {},
    training: { status: 'idle', progress: 0, loss: 0 },
  },
}

function renderWithQuery(ui: React.ReactNode) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('StatusCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseUnifiedCharacters.mockReturnValue({ data: { characters: [] as unknown[] } })
  })

  it('shows empty state when no characters', async () => {
    mockUseUnifiedCharacters.mockReturnValue({ data: { characters: [] as unknown[] } })
    renderWithQuery(<StatusCenter />)

    expect(screen.getByText('暂无角色数据，请先创建角色')).toBeDefined()
  })

  it('renders character name and stats when characters exist', async () => {
    mockUseUnifiedCharacters.mockReturnValue({ data: { characters: [FAKE_CHAR_1] as unknown[] } })
    mockDashboardStats.mockResolvedValue(FAKE_STATS)

    renderWithQuery(<StatusCenter />)

    // Character name
    await waitFor(() => {
      expect(screen.getByText('小雅')).toBeDefined()
    })

    // Character description
    expect(screen.getByText('温柔可爱的AI女友')).toBeDefined()

    // Version & created date shown
    expect(screen.getByText('3')).toBeDefined() // version

    // Badge shows 活跃中
    expect(screen.getByText('活跃中')).toBeDefined()

    // Stats visible after query resolves
    await waitFor(() => {
      expect(screen.getByText('42')).toBeDefined() // today_chats
    })
    expect(screen.getByText('18')).toBeDefined() // recent_memories
    expect(screen.getByText('85')).toBeDefined() // affinity
  })

  it('shows tabs when there are multiple characters', async () => {
    mockUseUnifiedCharacters.mockReturnValue({ data: { characters: [FAKE_CHAR_1, FAKE_CHAR_2] as unknown[] } })
    mockDashboardStats.mockResolvedValue(FAKE_STATS)

    renderWithQuery(<StatusCenter />)

    // 小雅 appears both as tab and heading, 小雪 only as tab
    await waitFor(() => {
      expect(screen.getAllByText('小雅').length).toBeGreaterThanOrEqual(1)
    })
    expect(screen.getByText('小雪')).toBeDefined()

    // Both tabs exist (2+ buttons rendered by SubTabBar)
    const tabButtons = screen.getAllByRole('button').filter(b =>
      b.textContent === '小雅' || b.textContent === '小雪',
    )
    expect(tabButtons.length).toBe(2)
  })

  it('renders dashboard stats (system status, emotion, uptime, wechat)', async () => {
    mockUseUnifiedCharacters.mockReturnValue({ data: { characters: [FAKE_CHAR_1] as unknown[] } })
    mockDashboardStats.mockResolvedValue(FAKE_STATS)

    renderWithQuery(<StatusCenter />)

    await waitFor(() => {
      expect(screen.getByText('正常')).toBeDefined() // system_status=healthy
    })
    expect(screen.getByText('已连接')).toBeDefined() // wechat_connected=true
    expect(screen.getByText('2h')).toBeDefined() // 7200s = 2h
    expect(screen.getByText('happy')).toBeDefined() // current_emotion
  })
})

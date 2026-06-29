import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import StatusCenter from '../../pages/StatusCenter'

// ── hoisted mock fns ──
const { mockUseActiveCharacter, mockUseDashboard, mockUseEmotionState, mockUseMemoryFacts } = vi.hoisted(
  () => ({
    mockUseActiveCharacter: vi.fn(),
    mockUseDashboard: vi.fn(),
    mockUseEmotionState: vi.fn(),
    mockUseMemoryFacts: vi.fn(),
  }),
)

vi.mock('../../hooks/useQueries', () => ({
  useActiveCharacter: () => mockUseActiveCharacter(),
  useDashboard: () => mockUseDashboard(),
  useEmotionState: () => mockUseEmotionState(),
  useMemoryFacts: () => mockUseMemoryFacts(),
}))

const FAKE_CHARACTER = {
  id: 'char-001',
  name: '小雅',
  description: '温柔可爱的AI女友',
  is_active: true,
}

const FAKE_STATS = {
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
    mockUseActiveCharacter.mockReturnValue({ activeCharacter: null })
    mockUseDashboard.mockReturnValue({ data: undefined, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: undefined })
  })

  it('shows empty state when no active character', () => {
    renderWithQuery(<StatusCenter />)
    expect(screen.getByText('暂无活跃角色，请先创建或激活角色')).toBeDefined()
  })

  it('renders emotion, affinity level and memory count', () => {
    mockUseActiveCharacter.mockReturnValue({ activeCharacter: FAKE_CHARACTER })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: undefined })

    renderWithQuery(<StatusCenter />)

    expect(screen.getByText('happy')).toBeDefined()
    expect(screen.getByText('亲密')).toBeDefined() // affinity 85
    expect(screen.getByText('18')).toBeDefined()
  })

  it('renders recent memories from facts', () => {
    mockUseActiveCharacter.mockReturnValue({ activeCharacter: FAKE_CHARACTER })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({
      data: [
        { id: 'f1', content: '记得喜欢喝咖啡', category: 'preference', importance: 0.8 },
        { id: 'f2', content: '周末常去公园散步', category: 'routine', importance: 0.6 },
      ],
    })

    renderWithQuery(<StatusCenter />)

    expect(screen.getByText('记得喜欢喝咖啡')).toBeDefined()
    expect(screen.getByText('周末常去公园散步')).toBeDefined()
  })

  it('falls back to placeholder memories when no facts', () => {
    mockUseActiveCharacter.mockReturnValue({ activeCharacter: FAKE_CHARACTER })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: [] })

    renderWithQuery(<StatusCenter />)

    expect(screen.getByText('用户说最近加班很多，需要安静陪伴。')).toBeDefined()
    expect(screen.getByText('用户不喜欢被主动追问过去。')).toBeDefined()
  })
})

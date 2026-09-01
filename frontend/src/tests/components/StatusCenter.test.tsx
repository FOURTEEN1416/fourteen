import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import StatusCenter from '../../pages/StatusCenter'

// ── hoisted mock fns ──
const { mockUseActiveCharacter, mockUseDashboard, mockUseEmotionState, mockUseMemoryFacts, mockUseAchievements, mockUseEmotionTrend, mockUseEmotionDistribution } = vi.hoisted(
  () => ({
    mockUseActiveCharacter: vi.fn(),
    mockUseDashboard: vi.fn(),
    mockUseEmotionState: vi.fn(),
    mockUseMemoryFacts: vi.fn(),
    mockUseAchievements: vi.fn(),
    mockUseEmotionTrend: vi.fn(),
    mockUseEmotionDistribution: vi.fn(),
  }),
)

vi.mock('../../hooks/useQueries', () => ({
  useActiveCharacter: () => mockUseActiveCharacter(),
  useDashboard: () => mockUseDashboard(),
  useEmotionState: () => mockUseEmotionState(),
  useMemoryFacts: () => mockUseMemoryFacts(),
  useAchievements: () => mockUseAchievements(),
  useEmotionTrend: () => mockUseEmotionTrend(),
  useEmotionDistribution: () => mockUseEmotionDistribution(),
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
    mockUseAchievements.mockReturnValue({ data: undefined })
    mockUseEmotionTrend.mockReturnValue({ data: undefined })
    mockUseEmotionDistribution.mockReturnValue({ data: undefined })
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

  it('renders achievements card with unlocked count', () => {
    mockUseActiveCharacter.mockReturnValue({ activeCharacter: FAKE_CHARACTER })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: [] })
    mockUseAchievements.mockReturnValue({
      data: {
        character_id: 'char-001',
        unlocked_count: 2,
        total: 10,
        achievements: [
          { achievement_id: 'companion_first', name: '初次相识', description: 'd', category: 'companion', target: 1, progress: 1, unlocked: true, unlocked_at: '2026-09-01T00:00:00' },
          { achievement_id: 'memory_10', name: '记忆初绽', description: 'd', category: 'memory', target: 10, progress: 3, unlocked: false, unlocked_at: null },
        ],
      },
    })

    renderWithQuery(<StatusCenter />)

    expect(screen.getByText('已解锁 2 / 10')).toBeDefined()
    expect(screen.getByText('初次相识')).toBeDefined() // 已解锁显示名字
    expect(screen.queryByText('记忆初绽')).toBeNull() // 未解锁且有解锁项时默认隐藏
  })

  it('shows empty state when no facts', () => {
    mockUseActiveCharacter.mockReturnValue({ activeCharacter: FAKE_CHARACTER })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: [] })

    renderWithQuery(<StatusCenter />)

    expect(screen.getByText('还没有沉淀下来的记忆')).toBeDefined()
  })
})

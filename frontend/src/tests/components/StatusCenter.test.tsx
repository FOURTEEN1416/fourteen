import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import StatusCenter from '../../pages/StatusCenter'
import { useAuthStore } from '../../store/authStore'
import { useGlobalViewStore } from '../../store/globalViewStore'

// ── hoisted mock fns ──
const { mockUseActiveCharacter, mockUseUnifiedCharacter, mockUseDashboard, mockUseEmotionState, mockUseMemoryFacts, mockUseAchievements, mockUseEmotionTrend, mockUseEmotionDistribution, mockClientGet } = vi.hoisted(
  () => ({
    mockUseActiveCharacter: vi.fn(),
    mockUseUnifiedCharacter: vi.fn(),
    mockUseDashboard: vi.fn(),
    mockUseEmotionState: vi.fn(),
    mockUseMemoryFacts: vi.fn(),
    mockUseAchievements: vi.fn(),
    mockUseEmotionTrend: vi.fn(),
    mockUseEmotionDistribution: vi.fn(),
    mockClientGet: vi.fn(),
  }),
)

vi.mock('../../hooks/useQueries', () => ({
  useActiveCharacter: () => mockUseActiveCharacter(),
  useUnifiedCharacter: (id?: string) => mockUseUnifiedCharacter(id),
  useDashboard: () => mockUseDashboard(),
  useEmotionState: () => mockUseEmotionState(),
  useMemoryFacts: () => mockUseMemoryFacts(),
  useAchievements: () => mockUseAchievements(),
  useEmotionTrend: () => mockUseEmotionTrend(),
  useEmotionDistribution: () => mockUseEmotionDistribution(),
}))

// StatusCenter 用 useQuery 拉 /shisi/emotion-stage/stages；测试固定返回与
// constants/persona.AFFINITY_STAGES 一致的阶段，避免 CI 无后端时落到空数组。
vi.mock('../../api/client', () => ({
  default: { get: (...args: unknown[]) => mockClientGet(...args) },
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

function renderWithQuery(ui: React.ReactNode, path = '/roles/char-001/status') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/roles/:roleId/status" element={ui} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('StatusCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // URL 分路 mock：MemorySystemCard 直连 /stats、/memory/diary、/characters/{id}/*
    mockClientGet.mockImplementation((url: string) => {
      if (url === '/shisi/emotion-stage/stages') {
        return Promise.resolve({
          data: {
            data: [
              { name: '陌生', min: 0, max: 25 },
              { name: '熟悉', min: 25, max: 50 },
              { name: '亲密', min: 50, max: 75 },
              { name: '羁绊', min: 75, max: 100 },
            ],
          },
        })
      }
      // V2-6③：后端已回纯日期（rsplit 剥键），前端按原样渲染
      if (url === '/memory/diary') {
        return Promise.resolve({
          data: { entries: [{ date: '2026-10-28', summary: '今天聊了咖啡' }] },
        })
      }
      if (url === '/stats') {
        return Promise.resolve({ data: { working_count: 5 } })
      }
      if (url.startsWith('/characters/char-001')) {
        return Promise.resolve({ data: { total: 3 } })
      }
      return Promise.resolve({ data: {} })
    })
    mockUseActiveCharacter.mockReturnValue({ activeCharacter: null })
    mockUseUnifiedCharacter.mockReturnValue({ data: undefined, isLoading: false })
    mockUseDashboard.mockReturnValue({ data: undefined, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: undefined })
    mockUseAchievements.mockReturnValue({ data: undefined })
    mockUseEmotionTrend.mockReturnValue({ data: undefined })
    mockUseEmotionDistribution.mockReturnValue({ data: undefined })
  })

  it('URL 角色不存在 → 显式「未找到角色」（NEW-8：不再静默回退别的角色）', () => {
    renderWithQuery(<StatusCenter />)
    expect(screen.getByText(/未找到角色/)).toBeDefined()
    expect(screen.getByText(/char-001/)).toBeDefined()
  })

  it('renders emotion, affinity level and memory count', async () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: undefined })

    renderWithQuery(<StatusCenter />)

    expect(screen.getByText('happy')).toBeDefined()
    // stages 经 client mock 异步返回：affinity 85 → 羁绊
    expect(await screen.findByText('羁绊')).toBeDefined()
    expect(screen.getByText('18')).toBeDefined()
  })

  it('renders recent memories from facts', () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false })
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
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false })
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
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: [] })

    renderWithQuery(<StatusCenter />)

    expect(screen.getByText('还没有沉淀下来的记忆')).toBeDefined()
  })

  // ── NEW-5①（2026-10-29）：affinity 是 0-8 档位序数，旧实现按 0-100 刻度表换算恒「陌生」 ──
  it('affinity_name 在契约内时直读档名，不再按 0-100 刻度表换算', async () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false })
    mockUseDashboard.mockReturnValue({
      data: { ...FAKE_STATS, affinity: 6, affinity_name: '恋人' },
      isLoading: false,
    })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: [] })

    renderWithQuery(<StatusCenter />)

    expect(await screen.findByText('恋人')).toBeDefined()
    // 6 若被 0-100 刻度表解读会落「陌生」——旧缺陷回归哨
    expect(screen.queryByText('陌生')).toBeNull()
  })

  // ── V2-6③：情景归档时间线渲染纯日期 ──
  it('情景归档时间线渲染纯日期，不泄漏 user_key|character_id 键', async () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({ data: [] })

    renderWithQuery(<StatusCenter />)

    fireEvent.click(await screen.findByText('时间线'))
    expect(await screen.findByText('2026-10-28')).toBeDefined()
    expect(screen.queryByText(/4:wxid/)).toBeNull()
  })
})

// ── V2-1①（2026-10-29）：admin 全局视图下「最近沉淀」按 user_key 标注归属 ──
describe('StatusCenter admin 全局视图归属标注', () => {
  it('最近沉淀按 user_key 前缀映射用户名徽章（仅全局视图）', async () => {
    useAuthStore.setState({
      user: {
        id: 1, email: 'a@t', username: 'admin', display_name: 'Admin',
        role: 'admin', is_active: true, is_verified: true,
      },
    } as never)
    useGlobalViewStore.setState({ enabled: true, loaded: true })

    // /admin/users 返回用户名映射（ownerNames 底座）
    mockClientGet.mockImplementation((url: string) => {
      if (url === '/admin/users') {
        return Promise.resolve({
          data: { users: [{ id: 2, display_name: '小小鱼', username: 'fish', email: 'f@t' }], total: 1, page: 1, page_size: 100 },
        })
      }
      if (url === '/shisi/emotion-stage/stages') {
        return Promise.resolve({ data: { data: [{ name: '陌生', min: 0, max: 25 }] } })
      }
      return Promise.resolve({ data: {} })
    })

    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false })
    mockUseDashboard.mockReturnValue({ data: FAKE_STATS, isLoading: false })
    mockUseEmotionState.mockReturnValue({ data: undefined })
    mockUseAchievements.mockReturnValue({ data: undefined })
    mockUseEmotionTrend.mockReturnValue({ data: undefined })
    mockUseEmotionDistribution.mockReturnValue({ data: undefined })
    mockUseMemoryFacts.mockReturnValue({
      data: [{ content: '记得喜欢喝咖啡', user_key: '2:peer@im.wechat' }],
    })

    renderWithQuery(<StatusCenter />)

    expect(await screen.findByText('小小鱼')).toBeDefined()
    expect(screen.getByText('记得喜欢喝咖啡')).toBeDefined()

    // 收尾：恢复非 admin，避免污染其他用例的全局 store
    useAuthStore.setState({ user: null } as never)
    useGlobalViewStore.setState({ enabled: false, loaded: true })
  })
})

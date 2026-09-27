import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RoleSettings from '../../pages/RoleSettings'
import { useAuthStore } from '../../store/authStore'

// ════════════════════════════════════════════════════════════════
//  vi.hoisted() — 所有 mock fn 和常量必须在 vi.mock 工厂之前定义
// ════════════════════════════════════════════════════════════════

const { mockUseUnifiedCharacter, mockUseVoiceStatus, mockUseDeleteCharacter, FAKE_CHARACTER } = vi.hoisted(() => {
  const FAKE_CHARACTER = {
    id: 'c1',
    name: '小雅',
    description: '温柔可爱的AI',
    personality: { warmth: 0.8, playfulness: 0.5 },
    speaking_style: { gentle: 0.7 },
    is_active: true,
    user_id: 'u1',
    version: 1,
    created_at: '2026-01-01',
    updated_at: '2026-06-01',
    core_anchors: ['善良', '温柔'],
    message: {
      proactive: true,
      dailyLimit: 20,
      minInterval: 15,
      cooldown: 30,
      urgency: 0.7,
    },
    stats: {
      messages: 128,
      memories: 45,
      avgResponse: '1.2s',
    },
    rag: {
      vectorDocs: 256,
      keywordIndex: 1024,
      hitRate: 87,
    },
    knowledgeDocs: ['角色设定.md', '对话风格.txt'],
    voice_config: null,
    catchphrases: ['好呀~', '没问题呢'],
  }

  return {
    mockUseUnifiedCharacter: vi.fn(),
    mockUseVoiceStatus: vi.fn(),
    mockUseDeleteCharacter: vi.fn(),
    FAKE_CHARACTER,
  }
})

const {
  mockProactiveGetConfig, mockProactiveHistory, mockProactiveSend,
  mockProactivePause, mockUpdateProactiveConfig,
  mockEnrichCharacter, mockKnowledgeCollectConfig, mockUpdateKnowledgeCollectConfig,
  mockAgentPlaneReplay, mockAgentPlaneCurate,
} = vi.hoisted(() => ({
  mockProactiveGetConfig: vi.fn(),
  mockProactiveHistory: vi.fn(),
  mockProactiveSend: vi.fn(),
  mockProactivePause: vi.fn(),
  mockUpdateProactiveConfig: vi.fn(),
  mockEnrichCharacter: vi.fn(),
  mockKnowledgeCollectConfig: vi.fn(),
  mockUpdateKnowledgeCollectConfig: vi.fn(),
  mockAgentPlaneReplay: vi.fn(),
  mockAgentPlaneCurate: vi.fn(),
}))

// ════════════════════════════════════════════════════════════════
//  Module mocks
// ════════════════════════════════════════════════════════════════

vi.mock('../../hooks/useQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hooks/useQueries')>()
  return {
    ...actual,
    useUnifiedCharacter: (...args: unknown[]) => mockUseUnifiedCharacter(...args),
    useVoiceStatus: (...args: unknown[]) => mockUseVoiceStatus(...args),
    useDeleteCharacter: (...args: unknown[]) => mockUseDeleteCharacter(...args),
  }
})

// 注意：必须 importOriginal 展开真实模块 —— api/client.ts 从 ./system 引入了
// health 等一大批函数，工厂式整体替换会让 client.ts 的具名导入解析失败
// （整份 suite 无法 collect）。
vi.mock('../../api/system', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/system')>()
  return {
    ...actual,
    enrichCharacter: (...args: unknown[]) => mockEnrichCharacter(...args),
    proactiveGetConfig: (...args: unknown[]) => mockProactiveGetConfig(...args),
    proactiveHistory: (...args: unknown[]) => mockProactiveHistory(...args),
    proactiveSend: (...args: unknown[]) => mockProactiveSend(...args),
    proactivePause: (...args: unknown[]) => mockProactivePause(...args),
    updateProactiveConfig: (...args: unknown[]) => mockUpdateProactiveConfig(...args),
    knowledgeCollectConfig: (...args: unknown[]) => mockKnowledgeCollectConfig(...args),
    updateKnowledgeCollectConfig: (...args: unknown[]) => mockUpdateKnowledgeCollectConfig(...args),
    agentPlaneReplay: (...args: unknown[]) => mockAgentPlaneReplay(...args),
    agentPlaneCurate: (...args: unknown[]) => mockAgentPlaneCurate(...args),
  }
})

// ════════════════════════════════════════════════════════════════
//  Render helpers
// ════════════════════════════════════════════════════════════════

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="location-probe">{loc.pathname}</div>
}

function renderComponent(initialEntries = ['/users/u1/roles/c1/settings']) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <LocationProbe />
        <Routes>
          <Route path="/users/:userId/roles/:roleId/settings" element={<RoleSettings />} />
          <Route path="/users/:userId/roles/:roleId/settings/:tab" element={<RoleSettings />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function makeUser(role: 'admin' | 'viewer' | null) {
  if (role === null) {
    useAuthStore.setState({ user: null, isAuthenticated: false })
    return
  }
  useAuthStore.setState({
    user: {
      id: role === 'admin' ? 1 : 2,
      email: `${role}@test.local`,
      username: role,
      display_name: role,
      avatar_url: '',
      role,
      is_active: true,
      is_verified: true,
      created_at: '2026-01-01T00:00:00Z',
      last_login_at: null,
    },
    isAuthenticated: true,
  })
}

const PROACTIVE_CONFIG = {
  threshold: 2.0,
  max_daily_messages: 8,
  min_interval_minutes: 30,
  cooldown_after_reply_minutes: 15,
  quiet_hours_start: 23,
  quiet_hours_end: 7,
  follow_up: { enabled: true, delay1_seconds: 45, delay2_seconds: 150, daily_max: 12 },
  reply_mode: 'immersive',
  paused: false,
  llm_proactive: { enabled: true, style_hint: '', intensity: 'normal', respect_quiet_hours: true, character_hint: '' },
}

// ════════════════════════════════════════════════════════════════
//  Test Suite
// ════════════════════════════════════════════════════════════════

describe('RoleSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    makeUser(null)
    mockUseVoiceStatus.mockReturnValue({ data: { enabled: true }, isLoading: false })
    mockUseDeleteCharacter.mockReturnValue({ mutate: vi.fn(), isPending: false })
    mockProactiveGetConfig.mockResolvedValue({ data: PROACTIVE_CONFIG })
    mockProactiveHistory.mockResolvedValue({ data: { history: [] } })
    mockKnowledgeCollectConfig.mockResolvedValue({ data: { enabled: false, interval_minutes: 60, available: true } })
  })

  // ────────────────────────────────────────────────
  //  1. 加载中状态
  // ────────────────────────────────────────────────
  it('shows loading state while character loads', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: undefined,
      isLoading: true,
      error: null,
    })

    renderComponent()

    expect(screen.getByText('加载角色中...')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  2. 角色不存在（error 或 null）
  // ────────────────────────────────────────────────
  it('shows "角色不存在" when character not found', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: null,
      isLoading: false,
      error: new Error('not found'),
    })

    renderComponent()

    expect(screen.getByText('角色不存在或加载失败')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  3. 渲染 5 个 tab
  // ────────────────────────────────────────────────
  it('renders tabs: 基础/语音/消息/数据/时间线', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    // 角色名称
    expect(screen.getByText('小雅')).toBeDefined()

    // 当前 SUB_TABS 为 5 项（表情包 tab 已下线，2026-09-19 对齐）
    expect(screen.getByText('基础')).toBeDefined()
    expect(screen.getByText('语音')).toBeDefined()
    expect(screen.getByText('消息')).toBeDefined()
    expect(screen.getByText('数据')).toBeDefined()
    expect(screen.getByText('时间线')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  4. 点击 tab 切换活动内容（普通用户：消息 tab 显示全局调度说明）
  // ────────────────────────────────────────────────
  it('click tab switches active tab content', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    // 默认「基础」tab：应该有角色名称输入
    expect(screen.getByDisplayValue('小雅')).toBeDefined()

    // 点击「消息」tab —— 普通用户看到全局调度说明（非 admin 不可调主动消息）
    fireEvent.click(screen.getByText('消息'))
    expect(screen.getByText(/全局引擎统一调度/)).toBeDefined()
    // 不可见任何 admin-only 表单
    expect(screen.queryByText('LLM 主动决策（人设·画像·控制台）')).toBeNull()
    expect(mockProactiveGetConfig).not.toHaveBeenCalled()

    // 点击「数据」tab
    fireEvent.click(screen.getByText('数据'))
    // 数据 tab 显示数据概览
    expect(screen.getByText('数据概览')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  5. 默认 tab 为「基础」
  // ────────────────────────────────────────────────
  it('defaults to "basic" tab', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    // 默认 tab 显示基础设置——角色名称输入框
    expect(screen.getByDisplayValue('小雅')).toBeDefined()
    // 性格特质区域可见
    expect(screen.getByText('性格特质')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  6. 深链：URL 带 tab 时落在对应 tab（刷新保留）【W11-D2 红测】
  // ────────────────────────────────────────────────
  it('deep link /settings/voice lands on voice tab, not basic', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent(['/users/u1/roles/c1/settings/voice'])

    // 语音 tab 内容可见
    expect(screen.getByText('MiMo Cloud TTS')).toBeDefined()
    // 基础 tab 内容不可见
    expect(screen.queryByDisplayValue('小雅')).toBeNull()
  })

  // ────────────────────────────────────────────────
  //  7. 深链：非法 tab 回退 basic【W11-D2】
  // ────────────────────────────────────────────────
  it('invalid deep-link tab falls back to basic', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent(['/users/u1/roles/c1/settings/hacker'])

    expect(screen.getByDisplayValue('小雅')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  8. 点击 tab 同步 URL（深链可分享）【W11-D2】
  // ────────────────────────────────────────────────
  it('clicking a tab writes it into the URL', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    fireEvent.click(screen.getByText('时间线'))

    expect(screen.getByTestId('location-probe').textContent).toBe(
      '/users/u1/roles/c1/settings/timeline',
    )
    // 基础内容随之消失
    expect(screen.queryByDisplayValue('小雅')).toBeNull()
  })

  // ────────────────────────────────────────────────
  //  9. 消息 tab（admin）：表单可见，但禁止全局广播按钮【W11-D4 红测】
  // ────────────────────────────────────────────────
  it('admin sees proactive forms but no global-broadcast send button', async () => {
    makeUser('admin')
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    fireEvent.click(screen.getByText('消息'))

    // admin 可见全局配置表单（等待配置拉取完成）
    expect(await screen.findByText('LLM 主动决策（人设·画像·控制台）')).toBeDefined()
    expect(screen.getByText('保存频率配置')).toBeDefined()

    // 🔴 全局广播发送按钮必须不存在（角色页禁止操作全局广播）
    expect(screen.queryByText(/立即发送一条主动消息/)).toBeNull()
    expect(mockProactiveSend).not.toHaveBeenCalled()

    // 作用域横幅：全局配置明示（非角色级）
    expect(screen.getByText(/全局/)).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  10. 消息 tab（admin）：引擎未初始化 503 显式报错，不渲染默认假表单【W11-D4】
  // ────────────────────────────────────────────────
  it('admin sees explicit error state when proactive engine unavailable (503)', async () => {
    makeUser('admin')
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })
    mockProactiveGetConfig.mockRejectedValue({ response: { status: 503 } })

    renderComponent()

    fireEvent.click(screen.getByText('消息'))

    // 显式错误态 + 重试，而不是拿默认值假装正常
    expect(await screen.findByText(/主动消息引擎未初始化/)).toBeDefined()
    expect(screen.getByText('重试')).toBeDefined()
    // 不出现默认假表单
    expect(screen.queryByText('保存频率配置')).toBeNull()
  })
})

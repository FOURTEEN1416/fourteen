import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RoleSettings from '../../pages/RoleSettings'
import { useAuthStore } from '../../store/authStore'
import { useErrorStore } from '../../store/errorStore'

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
  mockGetMyCharacterSettings, mockSaveMyCharacterSettings, mockResetMyCharacterSettings,
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
  // P5：角色级设置面板（/api/characters/{id}/my-settings）
  mockGetMyCharacterSettings: vi.fn(),
  mockSaveMyCharacterSettings: vi.fn(),
  mockResetMyCharacterSettings: vi.fn(),
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

vi.mock('../../api/characterSettings', () => ({
  getMyCharacterSettings: (...args: unknown[]) => mockGetMyCharacterSettings(...args),
  saveMyCharacterSettings: (...args: unknown[]) => mockSaveMyCharacterSettings(...args),
  resetMyCharacterSettings: (...args: unknown[]) => mockResetMyCharacterSettings(...args),
}))

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
  reply_mode: 'immersive',
  paused: false,
  llm_proactive: { enabled: true, style_hint: '', intensity: 'normal', respect_quiet_hours: true, character_hint: '' },
}

/** 读真实 store 的 toast 消息（toast 容器不在测试 DOM，直接查状态） */

// ════════════════════════════════════════════════════════════════
//  Test Suite
// ════════════════════════════════════════════════════════════════

describe('RoleSettings', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    makeUser(null)
    // toast 状态在模块级 zustand 单例里跨用例残留 → 每例清空再断言
    useErrorStore.setState({ toasts: [], lastError: null })
    mockUseVoiceStatus.mockReturnValue({ data: { enabled: true }, isLoading: false })
    mockUseDeleteCharacter.mockReturnValue({ mutate: vi.fn(), isPending: false })
    mockProactiveGetConfig.mockResolvedValue({ data: PROACTIVE_CONFIG })
    mockProactiveHistory.mockResolvedValue({ data: { history: [] } })
    // P5：角色级设置面板默认回执（服务端真值，非前端兜底）
    mockGetMyCharacterSettings.mockResolvedValue({
      status: 'ok',
      character_id: 'c1',
      scope: 'user_character',
      config: {
        delay_mode: 'dynamic', per_char_ms: 55, max_segments: 3,
        proactive_split: true, typing_enabled: false, typo_rate: 2,
        enabled: true, paused: true, threshold: 3.5, max_daily_messages: 12,
        llm_enabled: false, intensity: 'high', style_hint: '温柔',
        dice_enabled: true, dice_dry_run: false, peak_line: 30,
        topic_enabled: true, llm_judge_enabled: false,
      },
    })
    mockSaveMyCharacterSettings.mockResolvedValue({ status: 'ok', character_id: 'c1', scope: 'user_character', config: {} })
    mockResetMyCharacterSettings.mockResolvedValue({ status: 'ok', reset: true })
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
  it('renders tabs: 基础/语音/消息/数据（P9：时间线并入剧情线页）', () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    // 角色名称
    expect(screen.getByText('小雅')).toBeDefined()

    // P9（2026-11-07）：SUB_TABS 收敛为 4 项——时间线并入独立剧情线页
    expect(screen.getByText('基础')).toBeDefined()
    expect(screen.getByText('语音')).toBeDefined()
    expect(screen.getByText('消息')).toBeDefined()
    expect(screen.getByText('数据')).toBeDefined()
    expect(screen.queryByText('时间线')).toBeNull()
  })

  // ────────────────────────────────────────────────
  //  4. 点击 tab 切换活动内容（普通用户：消息 tab = 个人生效视图）【D21 契约变更】
  // ────────────────────────────────────────────────
  it('click tab switches active tab content', async () => {
    mockUseUnifiedCharacter.mockReturnValue({
      data: FAKE_CHARACTER,
      isLoading: false,
      error: null,
    })

    renderComponent()

    // 默认「基础」tab：应该有角色名称输入
    expect(screen.getByDisplayValue('小雅')).toBeDefined()

    // P5（2026-11-07）+ D-C（2026-11-08）：消息 tab = 「我 × 这个角色」设置面板
    // （六组：回复模式/说话节奏/主动消息/LLM 决策/骰子闸/话题引擎），
    // 无全局横幅、无管理员代设、无 admin 专属块——全局运维页已于 D-B 整机删除
    fireEvent.click(screen.getByText('消息'))
    expect(await screen.findByTestId('character-settings-panel')).toBeDefined()
    expect(screen.getByText('① 回复模式（这个角色怎么回你）')).toBeDefined()
    expect(screen.getByText('② 说话节奏')).toBeDefined()
    expect(screen.getByText('③ 主动消息（这个角色什么时候来找我）')).toBeDefined()
    expect(screen.getByText('④ LLM 决策（开口时机与风格）')).toBeDefined()
    expect(screen.getByText('⑤ 骰子闸（要不要开口的零 token 概率闸）')).toBeDefined()
    expect(screen.getByText('⑥ 话题引擎')).toBeDefined()
    expect(screen.getByText('保存本角色设置')).toBeDefined()
    // 取数走「我 × 角色」端点，不打 admin 全局端点
    // （旧 user 自助端点与其 client 函数已整体删除，静态守卫见 tests/api/system.test.ts）
    expect(mockGetMyCharacterSettings).toHaveBeenCalledWith('c1')
    expect(mockProactiveGetConfig).not.toHaveBeenCalled()
    // 不可见任何 admin-only 表单
    expect(screen.queryByText('LLM 主动决策（人设·画像·控制台）')).toBeNull()
    expect(screen.queryByText('拟人度')).toBeNull()

    // 点击「数据」tab
    fireEvent.click(screen.getByText('数据'))
    // 2026-10-29 NEW-19：死数据三卡已删（后端从不返回 stats），改为「数据导出」区
    expect(screen.getByText('数据导出')).toBeDefined()
    expect(screen.queryByText('数据概览')).toBeNull()
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

    fireEvent.click(screen.getByText('数据'))

    expect(screen.getByTestId('location-probe').textContent).toBe(
      '/users/u1/roles/c1/settings/data',
    )
    // 基础内容随之消失
    expect(screen.queryByDisplayValue('小雅')).toBeNull()
  })

  // ────────────────────────────────────────────────
  //  9. 消息 tab（admin）：表单可见，但禁止全局广播按钮【W11-D4 红测】
  // ────────────────────────────────────────────────
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RoleSettings from '../../pages/RoleSettings'
import { queryKeys } from '../../hooks/useQueries'
import { useAuthStore } from '../../store/authStore'
import { useErrorStore } from '../../store/errorStore'
import { DEFAULT_HUMANIZE_CONFIG, useHumanizeStore } from '../../store/humanizeStore'

// ════════════════════════════════════════════════════════════════
//  vi.hoisted() — 所有 mock fn 和常量必须在 vi.mock 工厂之前定义
// ════════════════════════════════════════════════════════════════

const { mockUseUnifiedCharacter, mockUseVoiceStatus, mockUseDeleteCharacter, mockUseUserProactiveConfig, FAKE_CHARACTER } = vi.hoisted(() => {
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
    mockUseUserProactiveConfig: vi.fn(),
    FAKE_CHARACTER,
  }
})

const {
  mockProactiveGetConfig, mockProactiveHistory, mockProactiveSend,
  mockProactivePause, mockUpdateProactiveConfig,
  mockUpdateUserProactiveConfig, mockClearUserProactiveConfig, mockUserProactivePause,
  mockEnrichCharacter, mockKnowledgeCollectConfig, mockUpdateKnowledgeCollectConfig,
  mockAgentPlaneReplay, mockAgentPlaneCurate,
  mockHumanizeGetConfig, mockHumanizeUpdate,
} = vi.hoisted(() => ({
  mockProactiveGetConfig: vi.fn(),
  mockProactiveHistory: vi.fn(),
  mockProactiveSend: vi.fn(),
  mockProactivePause: vi.fn(),
  mockUpdateProactiveConfig: vi.fn(),
  // D21：用户自助主动消息（/api/user/proactive-config）三件
  mockUpdateUserProactiveConfig: vi.fn(),
  mockClearUserProactiveConfig: vi.fn(),
  mockUserProactivePause: vi.fn(),
  mockEnrichCharacter: vi.fn(),
  mockKnowledgeCollectConfig: vi.fn(),
  mockUpdateKnowledgeCollectConfig: vi.fn(),
  mockAgentPlaneReplay: vi.fn(),
  mockAgentPlaneCurate: vi.fn(),
  // 拟人度区块（2026-10-16 新增）已挂进 MessageTab：不 mock 会让真实 axios 客户端
  // 在 jsdom 里发请求（测试不再 hermetic）。
  mockHumanizeGetConfig: vi.fn(),
  mockHumanizeUpdate: vi.fn(),
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
    // D21：默认返回「继承全局」视图；admin 用例断言它收到 enabled=false
    useUserProactiveConfig: (...args: unknown[]) => mockUseUserProactiveConfig(...args),
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
    updateUserProactiveConfig: (...args: unknown[]) => mockUpdateUserProactiveConfig(...args),
    clearUserProactiveConfig: (...args: unknown[]) => mockClearUserProactiveConfig(...args),
    userProactivePause: (...args: unknown[]) => mockUserProactivePause(...args),
    knowledgeCollectConfig: (...args: unknown[]) => mockKnowledgeCollectConfig(...args),
    updateKnowledgeCollectConfig: (...args: unknown[]) => mockUpdateKnowledgeCollectConfig(...args),
    agentPlaneReplay: (...args: unknown[]) => mockAgentPlaneReplay(...args),
    agentPlaneCurate: (...args: unknown[]) => mockAgentPlaneCurate(...args),
    humanizeGetConfig: (...args: unknown[]) => mockHumanizeGetConfig(...args),
    updateHumanizeConfig: (...args: unknown[]) => mockHumanizeUpdate(...args),
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
  reply_mode: 'immersive',
  paused: false,
  llm_proactive: { enabled: true, style_hint: '', intensity: 'normal', respect_quiet_hours: true, character_hint: '' },
}

// D21：用户自助端点回执 = **交付树真实形状**（api/routers/training_routes.py::
// _proactive_config_payload）：effective / global_default / overridden_keys / scope /
// user_key_present。GET 的暂停态在 `effective.paused`（无顶层 paused；仅 pause 端点回顶层 paused）。
const USER_EFFECTIVE = {
  enabled: true,
  paused: false,
  quiet_hours_start: 23,
  quiet_hours_end: 7,
  threshold: 4.0,
  max_daily_messages: 8,
  min_interval_minutes: 30,
  cooldown_after_reply_minutes: 10,
}

const USER_GLOBAL_DEFAULT = { ...USER_EFFECTIVE }

const USER_PROACTIVE_GLOBAL = {
  status: 'ok',
  effective: { ...USER_EFFECTIVE },
  global_default: { ...USER_GLOBAL_DEFAULT },
  overridden_keys: [] as string[],
  scope: 'global',
  user_key_present: true,
}

const USER_PROACTIVE_OVERRIDDEN = {
  status: 'ok',
  effective: { ...USER_EFFECTIVE, threshold: 6, max_daily_messages: 20, paused: true },
  global_default: { ...USER_GLOBAL_DEFAULT },
  overridden_keys: ['threshold', 'max_daily_messages', 'paused'],
  scope: 'user',
  user_key_present: true,
}

/** 读真实 store 的 toast 消息（toast 容器不在测试 DOM，直接查状态） */
function toastMessages(): string[] {
  return useErrorStore.getState().toasts.map(t => t.message)
}

function userProactiveQueryResult(data: unknown) {
  return { data, isLoading: false, isError: false, refetch: vi.fn() }
}

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
    // D21：个人生效视图默认「继承全局」
    mockUseUserProactiveConfig.mockReturnValue(userProactiveQueryResult(USER_PROACTIVE_GLOBAL))
    mockKnowledgeCollectConfig.mockResolvedValue({ data: { enabled: false, interval_minutes: 60, available: true } })
    // 拟人度区块：稳定回执 + 复位 store（store 是模块级单例，跨用例会残留 dirty 态）
    mockHumanizeGetConfig.mockResolvedValue({
      data: { status: 'ok', config: { ...DEFAULT_HUMANIZE_CONFIG, delay_mode_label: '逐字(按字数)' } },
    })
    mockHumanizeUpdate.mockResolvedValue({
      data: { status: 'ok', config: { ...DEFAULT_HUMANIZE_CONFIG, delay_mode_label: '逐字(按字数)' } },
    })
    useHumanizeStore.setState({
      config: { ...DEFAULT_HUMANIZE_CONFIG },
      snapshot: JSON.stringify(DEFAULT_HUMANIZE_CONFIG),
      status: 'ready',
      error: '',
      saving: false,
      savedAt: '',
    })
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
  //  4. 点击 tab 切换活动内容（普通用户：消息 tab = 个人生效视图）【D21 契约变更】
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

    // 点击「消息」tab —— 普通用户看到的是**自己的**主动消息表单 + 作用域徽章
    // （旧断言钉的是「请用管理员账号」挡板，属契约变更而非修 bug）
    fireEvent.click(screen.getByText('消息'))
    expect(screen.queryByText(/请用管理员账号/)).toBeNull()
    expect(screen.getByTestId('user-proactive-scope').textContent).toBe('继承全局')
    expect(screen.getByText('保存我的节奏')).toBeDefined()
    // 取数走 user 端点（enabled=true），**不**打 admin 全局端点
    expect(mockUseUserProactiveConfig).toHaveBeenCalledWith(true)
    expect(mockProactiveGetConfig).not.toHaveBeenCalled()
    // 不可见任何 admin-only 表单
    expect(screen.queryByText('LLM 主动决策（人设·画像·控制台）')).toBeNull()
    // 拟人度区块同为 admin-only：非 admin 既不渲染也不打接口
    expect(screen.queryByText('拟人度')).toBeNull()
    expect(mockHumanizeGetConfig).not.toHaveBeenCalled()

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

    // 作用域横幅：全局配置明示（非角色级）；拟人度区块也会出现「全局」字样 → 取全部
    expect(screen.getAllByText(/全局/).length).toBeGreaterThan(0)

    // 拟人度区块（2026-10-16 新增）：挂在消息 tab 内、回复模式段之后，走独立端点
    expect(await screen.findByText('拟人度')).toBeDefined()
    expect(mockHumanizeGetConfig).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '3 条' }).getAttribute('aria-pressed')).toBe('true')
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

  // ────────────────────────────────────────────────
  //  11.【D21】普通用户改一项并保存：只打 user 端点，回执 ok 才给成功 toast
  // ────────────────────────────────────────────────
  it('普通用户保存个人节奏：只调 /user/proactive-config，且只提交改动键', async () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    mockUpdateUserProactiveConfig.mockResolvedValue({
      data: {
        status: 'ok',
        effective: { ...USER_EFFECTIVE, max_daily_messages: 20 },
        global_default: { ...USER_GLOBAL_DEFAULT },
        overridden_keys: ['max_daily_messages'],
        scope: 'user',
        user_key_present: true,
        user_key: 'user_id:2',
      },
    })

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    const row = screen.getByTestId('user-freq-max_daily_messages')
    fireEvent.change(row.querySelector('input[type="range"]')!, { target: { value: '20' } })
    fireEvent.click(screen.getByText('保存我的节奏'))

    await waitFor(() => expect(mockUpdateUserProactiveConfig).toHaveBeenCalledTimes(1))
    // 未改动的键不提交（未覆盖=继续继承），只有每日上限一项
    expect(mockUpdateUserProactiveConfig.mock.calls[0][0]).toEqual({ max_daily_messages: 20 })
    // 绝不落 admin 全局端点
    expect(mockUpdateProactiveConfig).not.toHaveBeenCalled()
    expect(mockProactiveGetConfig).not.toHaveBeenCalled()
    // 成功回执 → 成功 toast（HTTP 200 本身不算）
    expect(toastMessages()).toContain('已保存（仅作用于你自己的主动消息）')
  })

  // ────────────────────────────────────────────────
  //  12.【D21】回执 scope=user → 徽章「个人覆盖」+「恢复继承全局」，逐字段标记照抄 overridden_keys
  // ────────────────────────────────────────────────
  it('回执 scope=user：徽章翻「个人覆盖」并出现「恢复继承全局」', () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    mockUseUserProactiveConfig.mockReturnValue(userProactiveQueryResult(USER_PROACTIVE_OVERRIDDEN))

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    expect(screen.getByTestId('user-proactive-scope').textContent).toBe('个人覆盖')
    expect(screen.getByText('恢复继承全局')).toBeDefined()
    // 作用域只看服务端 overridden_keys：6 个节奏行里 2 项「个人」+ 4 项「继承」
    // （overridden_keys 里还有 paused，但暂停不是节奏行，由上方开关承载）
    expect(screen.getAllByText('个人').length).toBe(2)
    expect(screen.getAllByText('继承').length).toBe(4)
  })

  // ────────────────────────────────────────────────
  //  12b.【D21】表单必须渲染服务端 `effective`（读错字段/落前端兜底值即红）
  // ────────────────────────────────────────────────
  it('表单渲染服务端 effective 值，而不是前端兜底默认', () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    mockUseUserProactiveConfig.mockReturnValue(userProactiveQueryResult(USER_PROACTIVE_OVERRIDDEN))

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    const thresholdInput = screen.getByTestId('user-freq-threshold').querySelector('input[type="range"]') as HTMLInputElement
    const dailyInput = screen.getByTestId('user-freq-max_daily_messages').querySelector('input[type="range"]') as HTMLInputElement
    expect(thresholdInput.value).toBe('6')
    expect(dailyInput.value).toBe('20')
    // 区间必须与后端 ge/le 同界，否则超区间值会被滑块静默钳回
    expect(thresholdInput.max).toBe('20')
    expect(dailyInput.max).toBe('200')
    // 暂停态来自 effective.paused（GET 无顶层 paused）
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('false')
    expect(screen.getByText('我的主动消息已暂停')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  12b-2.【D21】effective 缺键 → 按 global_default 补齐（前端不得持有默认值副本）
  // ────────────────────────────────────────────────
  it('effective 缺键时用服务端 global_default 补齐，不用前端硬编码默认', () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    mockUseUserProactiveConfig.mockReturnValue(
      userProactiveQueryResult({
        status: 'ok',
        // threshold 不在 effective 里 → 只能从 global_default 取（服务端下发的 7）
        effective: {
          enabled: true,
          paused: false,
          quiet_hours_start: 23,
          quiet_hours_end: 7,
          max_daily_messages: 8,
          min_interval_minutes: 30,
          cooldown_after_reply_minutes: 10,
        },
        global_default: { ...USER_GLOBAL_DEFAULT, threshold: 7 },
        overridden_keys: [],
        scope: 'global',
        user_key_present: true,
      }),
    )

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    const thresholdInput = screen.getByTestId('user-freq-threshold').querySelector('input[type="range"]') as HTMLInputElement
    // 7 只可能来自服务端 global_default；前端写死 2.0 / 4.0 都会让本断言变红
    expect(thresholdInput.value).toBe('7')
  })

  // ────────────────────────────────────────────────
  //  12c.【D21】加载中/失败不渲染暂停开关：不给「运行中」错误基线
  // ────────────────────────────────────────────────
  it('个人视图loading 时不渲染暂停开关与运行态文案', () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    mockUseUserProactiveConfig.mockReturnValue({ data: undefined, isLoading: true, isError: false, refetch: vi.fn() })

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    expect(screen.queryByRole('switch')).toBeNull()
    expect(screen.queryByText('我的主动消息运行中')).toBeNull()
    expect(screen.queryByText('我的主动消息已暂停')).toBeNull()
  })

  // ────────────────────────────────────────────────
  //  12d.【D21】未绑定微信（user_key_present=false）：写面禁用 + 显式提示
  // ────────────────────────────────────────────────
  it('未绑定微信：保存/恢复/暂停均禁用并给出提示', () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    // scope=user → 「恢复继承全局」也会渲染，三个写控件都要断言禁用；
    // paused 置 false 让开关初始为「开」，才能证明禁用不是状态巧合。
    mockUseUserProactiveConfig.mockReturnValue(
      userProactiveQueryResult({
        ...USER_PROACTIVE_OVERRIDDEN,
        effective: { ...USER_PROACTIVE_OVERRIDDEN.effective, paused: false },
        user_key_present: false,
      }),
    )

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    expect(screen.getByText(/当前账号未绑定微信通道/)).toBeDefined()
    expect((screen.getByText('保存我的节奏') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByText('恢复继承全局') as HTMLButtonElement).disabled).toBe(true)
    const sw = screen.getByRole('switch')
    expect(sw.getAttribute('aria-checked')).toBe('true')
    expect(sw.hasAttribute('disabled')).toBe(true)
  })

  // ────────────────────────────────────────────────
  //  12e.【R2-3】enabled=false：文案「已关闭」时 Toggle 必须显示关闭且禁用
  //  （旧实现 checked 只看 !paused → 显示「开」，点它还会误发 pause）
  // ────────────────────────────────────────────────
  it('enabled=false：显示「已关闭」，暂停开关为关且禁用，不误发 pause', () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    mockUseUserProactiveConfig.mockReturnValue(
      userProactiveQueryResult({
        status: 'ok',
        effective: { ...USER_EFFECTIVE, enabled: false, paused: false },
        global_default: { ...USER_GLOBAL_DEFAULT },
        overridden_keys: [],
        scope: 'global',
        user_key_present: true,
      }),
    )

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    expect(screen.getByText('我的主动消息已关闭')).toBeDefined()
    expect(screen.getByText(/主动消息总开关已关闭/)).toBeDefined()
    const sw = screen.getByRole('switch')
    expect(sw.getAttribute('aria-checked')).toBe('false')
    expect(sw.hasAttribute('disabled')).toBe(true)
    fireEvent.click(sw)
    expect(mockUserProactivePause).not.toHaveBeenCalled()
  })

  // ────────────────────────────────────────────────
  //  13.【D21】点击「恢复继承全局」→ DELETE 被调用，徽章回「继承全局」
  //  【R2-1】前端防御：DELETE 回执的 user_key_present 一律不可信。历史实现
  //  曾以 user_key=None 计算（恒 false）；R1-5 已修为实际 target_key，此用例
  //  仍用 false 形状验证防御路径——无论后端回退到哪种形状，前端都不得据此
  //  误判「未绑定微信」。
  // ────────────────────────────────────────────────
  it('恢复继承全局：DELETE 被调用且回执 user_key_present=false 不触发假「未绑定」横幅', async () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    mockUseUserProactiveConfig.mockReturnValue(userProactiveQueryResult(USER_PROACTIVE_OVERRIDDEN))
    mockClearUserProactiveConfig.mockResolvedValue({
      data: {
        status: 'ok',
        effective: { ...USER_EFFECTIVE },
        global_default: { ...USER_GLOBAL_DEFAULT },
        overridden_keys: [],
        scope: 'global',
        // 防御性回退形状：R1-5 前的 DELETE 回执（`_proactive_config_payload(None)`）；
        // R1-5 后真实回执返回实际 target_key 的 user_key_present（见后端用例）。
        user_key_present: false,
        user_key: '',
      },
    })

    renderComponent()
    fireEvent.click(screen.getByText('消息'))
    expect(screen.getByTestId('user-proactive-scope').textContent).toBe('个人覆盖')

    fireEvent.click(screen.getByText('恢复继承全局'))

    await waitFor(() => expect(mockClearUserProactiveConfig).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId('user-proactive-scope').textContent).toBe('继承全局')
    expect(screen.queryByText('恢复继承全局')).toBeNull()
    expect(toastMessages()).toContain('已恢复继承全局')
    // R2-1 回归：DELETE 回执的 user_key_present=false 不得翻转绑定态
    expect(screen.queryByText(/当前账号未绑定微信通道/)).toBeNull()
    expect((screen.getByText('保存我的节奏') as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByRole('switch').hasAttribute('disabled')).toBe(false)
  })

  // ────────────────────────────────────────────────
  //  13b.【D21】DELETE 空回执不给绿色回执（W11-D5：HTTP 200/无 body 不算生效）
  // ────────────────────────────────────────────────
  it('恢复继承全局：空回执只给「未生效」warning，不谎报成功', async () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    mockUseUserProactiveConfig.mockReturnValue(userProactiveQueryResult(USER_PROACTIVE_OVERRIDDEN))
    mockClearUserProactiveConfig.mockResolvedValue({ data: undefined })

    renderComponent()
    fireEvent.click(screen.getByText('消息'))
    fireEvent.click(screen.getByText('恢复继承全局'))

    await waitFor(() => expect(toastMessages()).toContain('恢复未生效（后端未确认），请重试'))
    expect(toastMessages()).not.toContain('已恢复继承全局')
    // 徽章保持「个人覆盖」——没有生效就不改口径
    expect(screen.getByTestId('user-proactive-scope').textContent).toBe('个人覆盖')
  })

  // ────────────────────────────────────────────────
  //  14.【D21】非 admin 页面 DOM 内不存在骰子闸/账本回放/消息统计（防「先渲染再 403」回潮）
  // ────────────────────────────────────────────────
  it('非 admin 不渲染骰子闸/账本回放/消息统计，也不请求 admin 历史', () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    expect(screen.queryByText(/零 token 骰子闸/)).toBeNull()
    expect(screen.queryByText(/状态账本 · 因果回放/)).toBeNull()
    expect(screen.queryByText('消息统计')).toBeNull()
    expect(screen.queryByText('保存频率配置')).toBeNull()
    expect(mockProactiveHistory).not.toHaveBeenCalled()
  })

  // ────────────────────────────────────────────────
  //  15.【D21】普通用户暂停走 /user/proactive/pause，不落全局暂停端点
  //  【R2-4】暂停成功后必须 invalidate 个人配置查询（否则 30s 缓存导致状态回滚）
  // ────────────────────────────────────────────────
  it('普通用户暂停：调 /user/proactive/pause，不调 /proactive/pause，并 invalidate 个人配置', async () => {
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })
    // pause 端点回执：顶层 paused + 全量生效视图（写覆盖行后 scope 翻 user）
    mockUserProactivePause.mockResolvedValue({
      data: {
        status: 'ok',
        paused: true,
        effective: { ...USER_EFFECTIVE, paused: true },
        global_default: { ...USER_GLOBAL_DEFAULT },
        overridden_keys: ['paused'],
        scope: 'user',
        user_key_present: true,
        user_key: 'user_id:2',
      },
    })
    const invalidateSpy = vi.spyOn(QueryClient.prototype, 'invalidateQueries')

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    fireEvent.click(screen.getByRole('switch'))

    await waitFor(() => expect(mockUserProactivePause).toHaveBeenCalledWith(true))
    expect(mockProactivePause).not.toHaveBeenCalled()
    // 暂停即写本人覆盖行 → 徽章翻「个人覆盖」
    await waitFor(() => expect(screen.getByTestId('user-proactive-scope').textContent).toBe('个人覆盖'))
    // R2-4：暂停成功后 invalidate 个人配置查询键（与 handleSaveMine 一致）
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: queryKeys.userProactive.config })
    invalidateSpy.mockRestore()
  })

  // ────────────────────────────────────────────────
  //  16.【D21】admin 视图回归：全局横幅与骰子闸仍在，且不走个人端点
  // ────────────────────────────────────────────────
  it('admin 视图回归：全局横幅 + 骰子闸 + 消息统计仍在', async () => {
    makeUser('admin')
    mockUseUnifiedCharacter.mockReturnValue({ data: FAKE_CHARACTER, isLoading: false, error: null })

    renderComponent()
    fireEvent.click(screen.getByText('消息'))

    expect(await screen.findByText(/全局配置：以下参数作用于全部角色的主动消息调度/)).toBeDefined()
    expect(screen.getByText('零 token 骰子闸（dsh-speak-up）')).toBeDefined()
    expect(screen.getByText('消息统计')).toBeDefined()
    expect(screen.getByText('保存频率配置')).toBeDefined()
    // admin 走全局控制面：个人端点 enabled=false，个人表单不渲染
    expect(mockUseUserProactiveConfig).toHaveBeenCalledWith(false)
    expect(screen.queryByText('保存我的节奏')).toBeNull()
  })
})

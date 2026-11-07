/**
 * ProactiveOpsTab（管理员运维页内容）回归（2026-11-07 P5）。
 *
 * P5 重构后，原角色设置页「消息」tab 里的 admin 全局块整体迁到
 * /settings/proactive（ProactiveOpsTab）。本文件承接原 RoleSettings.test.tsx
 * 中钉这些块的用例：全局表单可见、503 显式错误态、全局横幅/骰子闸/统计仍在。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ProactiveOpsTab } from '../../components/admin/RoleSettingsTabs'
import { useAuthStore } from '../../store/authStore'
import { useErrorStore } from '../../store/errorStore'
import { DEFAULT_HUMANIZE_CONFIG, useHumanizeStore } from '../../store/humanizeStore'

const {
  mockProactiveGetConfig, mockProactiveHistory, mockProactiveSend,
  mockHumanizeGetConfig, mockHumanizeUpdate, mockAgentPlaneReplay, mockAgentPlaneCurate,
  mockKnowledgeCollectConfig, mockUpdateKnowledgeCollectConfig,
} = vi.hoisted(() => ({
  mockProactiveGetConfig: vi.fn(),
  mockProactiveHistory: vi.fn(),
  mockProactiveSend: vi.fn(),
  mockHumanizeGetConfig: vi.fn(),
  mockHumanizeUpdate: vi.fn(),
  mockAgentPlaneReplay: vi.fn(),
  mockAgentPlaneCurate: vi.fn(),
  mockKnowledgeCollectConfig: vi.fn(),
  mockUpdateKnowledgeCollectConfig: vi.fn(),
}))

vi.mock('../../api/system', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/system')>()
  return {
    ...actual,
    proactiveGetConfig: (...args: unknown[]) => mockProactiveGetConfig(...args),
    proactiveHistory: (...args: unknown[]) => mockProactiveHistory(...args),
    proactiveSend: (...args: unknown[]) => mockProactiveSend(...args),
    knowledgeCollectConfig: (...args: unknown[]) => mockKnowledgeCollectConfig(...args),
    updateKnowledgeCollectConfig: (...args: unknown[]) => mockUpdateKnowledgeCollectConfig(...args),
    agentPlaneReplay: (...args: unknown[]) => mockAgentPlaneReplay(...args),
    agentPlaneCurate: (...args: unknown[]) => mockAgentPlaneCurate(...args),
    humanizeGetConfig: (...args: unknown[]) => mockHumanizeGetConfig(...args),
    updateHumanizeConfig: (...args: unknown[]) => mockHumanizeUpdate(...args),
  }
})

function makeUser(role: 'admin' | 'viewer') {
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
  llm_proactive: {
    enabled: true,
    intensity: 'normal',
    style_hint: '',
    respect_quiet_hours: true,
    character_hint: '',
    jitter_seconds: 120,
  },
  dice_gate: {
    enabled: false,
    dry_run: true,
    peak_line: 20,
    offpeak_line: 60,
    miss_bonus: 10.0,
    miss_bonus_cap: 40.0,
    idle_short_minutes: 20,
    idle_long_minutes: 360,
    idle_short_factor: 0.15,
    idle_long_factor: 1.5,
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  makeUser('admin')
  useErrorStore.setState({ toasts: [], lastError: null })
  mockProactiveGetConfig.mockResolvedValue({ data: PROACTIVE_CONFIG })
  mockProactiveHistory.mockResolvedValue({ data: { history: [] } })
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

function renderOps() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <ProactiveOpsTab />
    </QueryClientProvider>,
  )
}

describe('ProactiveOpsTab（管理员运维页内容）', () => {
  it('admin 可见全局配置表单与拟人度区块，且无全局广播发送按钮', async () => {
    renderOps()

    expect(await screen.findByText('LLM 主动决策（人设·画像·控制台）')).toBeDefined()
    expect(screen.getByText('保存频率配置')).toBeDefined()
    expect(screen.queryByText(/立即发送一条主动消息/)).toBeNull()
    expect(mockProactiveSend).not.toHaveBeenCalled()
    expect(await screen.findByText('拟人度')).toBeDefined()
  })

  it('引擎未初始化（503）→ 显式错误态 + 重试，不渲染默认假表单', async () => {
    mockProactiveGetConfig.mockRejectedValue({ response: { status: 503 } })
    renderOps()

    expect(await screen.findByText(/主动消息引擎未初始化/)).toBeDefined()
    expect(screen.getByText('重试')).toBeDefined()
    expect(screen.queryByText('保存频率配置')).toBeNull()
  })

  it('全局横幅 + 骰子闸 + 消息统计仍在（运维面完整性）', async () => {
    renderOps()

    expect(await screen.findByText(/全局配置：以下参数作用于全部角色的主动消息调度/)).toBeDefined()
    expect(screen.getByText('零 token 骰子闸（dsh-speak-up）')).toBeDefined()
    expect(screen.getByText('消息统计')).toBeDefined()
    expect(screen.getByText('保存频率配置')).toBeDefined()
  })

  it('非 admin 不渲染骰子闸/账本回放，也不请求 admin 历史', async () => {
    makeUser('viewer')
    renderOps()

    expect(await screen.findByText("我的主动消息")).toBeDefined()
    expect(screen.queryByText('零 token 骰子闸（dsh-speak-up）')).toBeNull()
    expect(mockProactiveHistory).not.toHaveBeenCalled()
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// ════════════════════════════════════════════════════════════════
//  HumanizeSection —— 控制台「拟人度」面板
//
//  契约（设计契约 ui_spec）：
//   • 门禁同 MessageTab：非 admin 不渲染表单、不请求 admin-only 接口
//   • 读失败 → 显式错误态 + 重试（绝不渲染默认假表单）
//   • 档位四选一；dynamic 显示 每字/下钳/上钳，fixed 显示固定延时，random 显示区间
//   • max_segments 用 2/3/4 选择器呈现（默认 3 选中）
//   • 保存为**显式按钮**：POST 增量 → 必须回执 status==='ok'；dirty 时标「未保存」
// ════════════════════════════════════════════════════════════════

const { mockGet, mockPost, authState } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  authState: { user: null as unknown },
}))


vi.mock('../../api/admin', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/admin')>()),
  adminListUsers: vi.fn(async () => ({ users: [], total: 0, page: 1, page_size: 100 })),
}))

vi.mock('../../api/system', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/system')>()),
  humanizeGetConfig: (...args: unknown[]) => mockGet(...args),
  updateHumanizeConfig: (...args: unknown[]) => mockPost(...args),
}))

vi.mock('../../store/authStore', () => ({
  useAuthStore: vi.fn((selector?: (s: unknown) => unknown) => (selector ? selector(authState) : authState)),
}))

import HumanizeSection from '../../components/admin/HumanizeSection'
import {
  DEFAULT_HUMANIZE_CONFIG,
  humanizeIsDirty,
  useHumanizeStore,
} from '../../store/humanizeStore'
import { useErrorStore } from '../../store/errorStore'

const SERVER_CONFIG = {
  delay_mode: 'dynamic',
  per_char_ms: 45,
  fixed_ms: 600,
  random_min_ms: 400,
  random_max_ms: 1600,
  delay_floor_ms: 400,
  delay_ceil_ms: 1600,
  bracket_delay_ms: 0,
  proactive_split: false,
  max_segments: 3,
  delay_mode_label: '逐字(按字数)',
}

function setRole(role: 'admin' | 'viewer' | null) {
  authState.user = role === null ? null : { id: 1, role }
}

function setServerConfig(overrides: Record<string, unknown> = {}) {
  mockGet.mockResolvedValue({ data: { status: 'ok', config: { ...SERVER_CONFIG, ...overrides } } })
}

function resetStore() {
  useHumanizeStore.setState({
    config: { ...DEFAULT_HUMANIZE_CONFIG },
    snapshot: JSON.stringify(DEFAULT_HUMANIZE_CONFIG),
    status: 'ready',
    error: '',
    saving: false,
    savedAt: '',
  })
}

/** 渲染并等待面板进入 ready */
async function renderReady() {
  setRole('admin')
  setServerConfig()
  render(<HumanizeSection />)
  await screen.findByRole('button', { name: '逐字(按字数)' })
}

beforeEach(() => {
  vi.clearAllMocks()
  resetStore()
  useErrorStore.setState({ toasts: [], lastError: null })
  setRole('admin')
  setServerConfig()
})

describe('门禁与加载态', () => {
  it('非 admin：只给说明文案，不请求 admin-only 接口', () => {
    setRole('viewer')

    render(<HumanizeSection />)

    expect(screen.getByText(/仅管理员可查看与调整/)).toBeDefined()
    expect(mockGet).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '不延时' })).toBeNull()
  })

  it('admin：挂载即热读 GET /api/humanize/config（无 orchestrator 依赖，无 503 文案）', async () => {
    await renderReady()

    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(screen.getByText('拟人度')).toBeDefined()
  })

  it('读失败 → 显式错误态 + 重试按钮；重试再次打接口并进入 ready', async () => {
    setRole('admin')
    mockGet.mockRejectedValueOnce(new Error('Network Error'))
    render(<HumanizeSection />)

    expect(await screen.findByText('Network Error')).toBeDefined()
    // 绝不渲染默认假表单
    expect(screen.queryByRole('button', { name: '保存拟人度' })).toBeNull()

    setServerConfig()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByRole('button', { name: '保存拟人度' })).toBeDefined()
    expect(mockGet).toHaveBeenCalledTimes(2)
  })
})

describe('控件呈现', () => {
  it('四个延时档位 + 只读拆条上限 + 主动拆条开关', async () => {
    await renderReady()

    for (const label of ['逐字(按字数)', '固定延时', '随机区间', '不延时']) {
      expect(screen.getByRole('button', { name: label })).toBeDefined()
    }
    expect(screen.getByRole('button', { name: '3 条' }).getAttribute('aria-pressed')).toBe('true')
    // Toggle 组件以 role=switch 暴露（主动拆条 / 阅读延迟 / typing / 按用户覆盖 共 ≥1）
    expect(screen.getAllByRole('switch').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('主动消息也拆句发送（默认：整段发）')).toBeDefined()
  })

  it('dynamic 显示 每字毫秒/延时下钳/延时上钳；切到 fixed 只留每段固定延时', async () => {
    await renderReady()

    expect(screen.getByLabelText('每字毫秒')).toBeDefined()
    expect(screen.getByLabelText('延时下钳')).toBeDefined()
    expect(screen.getByLabelText('延时上钳')).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: '固定延时' }))

    expect(screen.getByLabelText('每段固定延时')).toBeDefined()
    expect(screen.queryByLabelText('每字毫秒')).toBeNull()
  })

  it('random 档位显示上下界；off 档位显示连发说明且无档位参数', async () => {
    await renderReady()

    fireEvent.click(screen.getByRole('button', { name: '随机区间' }))
    expect(screen.getByLabelText('随机下界')).toBeDefined()
    expect(screen.getByLabelText('随机上界')).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: '不延时' }))
    expect(screen.getByTestId('humanize-off-hint')).toBeDefined()
    expect(screen.queryByTestId('humanize-random-params')).toBeNull()
    expect(screen.queryByLabelText('每字毫秒')).toBeNull()
  })

  it('括号段延时滑条常驻（四档位下均生效，默认 0 = 不启用）', async () => {
    await renderReady()

    const slider = screen.getByLabelText('括号段延时') as HTMLInputElement
    expect(slider.value).toBe('0')

    fireEvent.click(screen.getByRole('button', { name: '不延时' }))
    expect(screen.getByLabelText('括号段延时')).toBeDefined()
  })

  it('纯指令隐身：只作事实陈述，不摆放写了不生效的开关（后端无此字段）', async () => {
    await renderReady()

    expect(screen.getByTestId('humanize-pure-instruction-note')).toBeDefined()
    // 面板里唯一的开关是「主动消息拆句」
    expect(screen.getAllByRole('switch').length).toBeGreaterThanOrEqual(4)
  })

  it('区间互钳：下界推过上界时上界跟随，不产生倒置载荷（避免必然的 400）', async () => {
    await renderReady()
    fireEvent.click(screen.getByRole('button', { name: '随机区间' }))

    fireEvent.change(screen.getByLabelText('随机下界'), { target: { value: '5000' } })

    const s = useHumanizeStore.getState()
    expect(s.config.random_min_ms).toBe(5000)
    expect(s.config.random_max_ms).toBeGreaterThanOrEqual(5000)
  })
})

describe('保存（partial merge + 回执纪律）', () => {
  it('改动后显「未保存」，点保存只 POST 增量；回执 ok → 显示已保存且 dirty 归零', async () => {
    await renderReady()
    mockPost.mockResolvedValue({
      data: { status: 'ok', config: { ...SERVER_CONFIG, per_char_ms: 60 } },
    })

    fireEvent.change(screen.getByLabelText('每字毫秒'), { target: { value: '60' } })
    expect(screen.getByText('未保存')).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: '保存拟人度' }))

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1))
    expect(mockPost).toHaveBeenCalledWith({ per_char_ms: 60 })
    await waitFor(() => expect(screen.queryByText('未保存')).toBeNull())
    const s = useHumanizeStore.getState()
    expect(s.savedAt).not.toBe('')
    expect(humanizeIsDirty(s.config, s.snapshot)).toBe(false)
  })

  it('回执 status!==ok：不给绿色回执，仍留「未保存」并给 warning', async () => {
    await renderReady()
    mockPost.mockResolvedValue({ data: { status: 'degraded' } })

    fireEvent.click(screen.getByRole('button', { name: '不延时' }))
    fireEvent.click(screen.getByRole('button', { name: '保存拟人度' }))

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1))
    expect(screen.getByText('未保存')).toBeDefined()
    expect(useHumanizeStore.getState().savedAt).toBe('')
    expect(useErrorStore.getState().toasts.some((t) => t.type === 'warning')).toBe(true)
  })

  it('主动拆条开关提交 proactive_split：true', async () => {
    await renderReady()
    mockPost.mockResolvedValue({
      data: { status: 'ok', config: { ...SERVER_CONFIG, proactive_split: true } },
    })

    fireEvent.click(screen.getAllByRole('switch')[0])
    expect(useHumanizeStore.getState().config.proactive_split).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '保存拟人度' }))

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith({ proactive_split: true }))
  })

  it('无改动时保存按钮禁用（不发无谓写请求）', async () => {
    await renderReady()

    const btn = screen.getByRole('button', { name: '保存拟人度' }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
    expect(screen.queryByText('未保存')).toBeNull()
  })

  it('撤销修改回到服务端值', async () => {
    await renderReady()

    fireEvent.change(screen.getByLabelText('每字毫秒'), { target: { value: '90' } })
    fireEvent.click(screen.getByRole('button', { name: '撤销修改' }))

    expect((screen.getByLabelText('每字毫秒') as HTMLInputElement).value).toBe('45')
    expect(screen.queryByText('未保存')).toBeNull()
  })
})

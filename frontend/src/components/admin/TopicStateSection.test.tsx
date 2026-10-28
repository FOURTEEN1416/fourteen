import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

// ════════════════════════════════════════════════════════════════
//  TopicStateSection —— 控制台「话题状态」面板
//
//  契约：
//   • 非 admin 不渲染表单、不请求 admin-only 接口
//   • 11 个配置键全部可见；Q1/Q4/Q5/Q6 固定项只读展示
//   • 保存为显式按钮：POST 增量 → 回执 status==='ok'；dirty 标「未保存」
//   • 会话键 + 角色 id 查询只读快照；空键不请求（作用域显式）
//   • 读失败 → 错误态 + 重试
// ════════════════════════════════════════════════════════════════

const { mockGet, mockPost, mockSnapshot, authState } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
  mockSnapshot: vi.fn(),
  authState: { user: null as unknown },
}))

vi.mock('../../api/system', () => ({
  topicStateGetConfig: (...args: unknown[]) => mockGet(...args),
  updateTopicStateConfig: (...args: unknown[]) => mockPost(...args),
  topicStateSnapshot: (...args: unknown[]) => mockSnapshot(...args),
}))

vi.mock('../../store/authStore', () => ({
  useAuthStore: vi.fn((selector?: (s: unknown) => unknown) => (selector ? selector(authState) : authState)),
}))

import TopicStateSection from './TopicStateSection'
import {
  DEFAULT_TOPIC_STATE_CONFIG,
  topicStateIsDirty,
  useTopicStateStore,
} from '../../store/topicStateStore'
import { useErrorStore } from '../../store/errorStore'

const SERVER_CONFIG = {
  ...DEFAULT_TOPIC_STATE_CONFIG,
}

const SNAPSHOT = {
  status: 'ok',
  enabled: true,
  active: [
    {
      title: '面试',
      summary: '明天二面',
      status: 'active',
      salience: 0.8,
      mention_count: 2,
      turn_count: 3,
      last_active_at: '2026-10-27 10:00:00',
    },
  ],
  parked: [
    {
      title: '搬家',
      summary: '',
      status: 'parked',
      salience: 0.4,
      mention_count: 1,
      turn_count: 1,
      last_active_at: '2026-10-26 09:00:00',
    },
  ],
  closed: [
    {
      title: '爬山',
      summary: '',
      status: 'closed',
      salience: 0.2,
      mention_count: 1,
      turn_count: 1,
      last_active_at: '2026-10-25 08:00:00',
    },
  ],
  next_plan: {
    topic: '面试',
    intent: '问问结果',
    planned_at: '2026-10-27 10:00:00',
    next_at: '2026-10-28 09:00:00',
    expires_at: '2026-10-29 09:00:00',
    consumed_at: null,
  },
}

function setRole(role: 'admin' | 'viewer' | null) {
  authState.user = role === null ? null : { id: 1, role }
}

function resetStore() {
  useTopicStateStore.setState({
    config: { ...DEFAULT_TOPIC_STATE_CONFIG },
    snapshot: JSON.stringify(DEFAULT_TOPIC_STATE_CONFIG),
    status: 'ready',
    error: '',
    saving: false,
    savedAt: '',
    scopeKey: '',
    scopeCharacterId: 'default',
    snapshotStatus: 'idle',
    snapshotError: '',
    topicSnapshot: null,
  })
}

async function renderReady() {
  setRole('admin')
  mockGet.mockResolvedValue({ data: { status: 'ok', config: SERVER_CONFIG } })
  render(<TopicStateSection />)
  await screen.findByRole('button', { name: '保存话题状态配置' })
}

beforeEach(() => {
  vi.clearAllMocks()
  resetStore()
  useErrorStore.setState({ toasts: [], lastError: null })
  setRole('admin')
  mockGet.mockResolvedValue({ data: { status: 'ok', config: SERVER_CONFIG } })
})

describe('门禁与加载态', () => {
  it('非 admin：只给说明文案，不请求 admin-only 接口', () => {
    setRole('viewer')

    render(<TopicStateSection />)

    expect(screen.getByText(/仅管理员可查看与调整/)).toBeDefined()
    expect(mockGet).not.toHaveBeenCalled()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('admin：挂载即热读 GET，11 个配置键全部可见', async () => {
    await renderReady()

    expect(mockGet).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('heading', { name: '话题状态' })).toBeDefined()
    for (const label of ['并行上限', '复提间隔', '复提条数', 'θ1', 'θ2']) {
      expect(screen.getByLabelText(label)).toBeDefined()
    }
    for (const text of ['启用话题状态机', '稀疏 LLM 判定', 'LLM 判定频率', '每日 LLM 预算', '状态保留期', '空闲转「没聊完」']) {
      expect(screen.getByText(text)).toBeDefined()
    }
    expect(screen.getAllByRole('switch')).toHaveLength(2)
  })

  it('读失败 → 显式错误态 + 重试；重试进入 ready', async () => {
    mockGet.mockRejectedValueOnce(new Error('Network Error'))
    render(<TopicStateSection />)

    expect(await screen.findByText('Network Error')).toBeDefined()
    expect(screen.queryByRole('button', { name: '保存话题状态配置' })).toBeNull()

    mockGet.mockResolvedValue({ data: { status: 'ok', config: SERVER_CONFIG } })
    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByRole('button', { name: '保存话题状态配置' })).toBeDefined()
    expect(mockGet).toHaveBeenCalledTimes(2)
  })
})

describe('配置渲染与保存', () => {
  it('固定项只读展示，不以可编辑控件出现', async () => {
    await renderReady()

    expect(screen.getAllByText('固定')).toHaveLength(4)
    // 固定项没有滑条 label
    expect(screen.queryByLabelText('LLM 判定频率')).toBeNull()
    expect(screen.queryByLabelText('状态保留期')).toBeNull()
  })

  it('改动后标「未保存」，保存只 POST 增量；回执 ok → 已保存', async () => {
    await renderReady()
    mockPost.mockResolvedValue({
      data: { status: 'ok', config: { ...SERVER_CONFIG, active_cap: 2 } },
    })

    fireEvent.change(screen.getByLabelText('并行上限'), { target: { value: '2' } })
    expect(screen.getByText('未保存')).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: '保存话题状态配置' }))

    await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1))
    expect(mockPost).toHaveBeenCalledWith({ active_cap: 2 })
    await waitFor(() => expect(screen.queryByText('未保存')).toBeNull())
    expect(useTopicStateStore.getState().savedAt).not.toBe('')
    expect(topicStateIsDirty(
      useTopicStateStore.getState().config,
      useTopicStateStore.getState().snapshot,
    )).toBe(false)
  })

  it('开关 enabled 提交 true；保存失败回执保留「未保存」并给 warning', async () => {
    setRole('admin')
    mockGet.mockResolvedValue({
      data: {
        status: 'ok',
        config: { ...SERVER_CONFIG, enabled: false, llm_judge_enabled: false },
      },
    })
    render(<TopicStateSection />)
    await screen.findByRole('button', { name: '保存话题状态配置' })
    mockPost.mockResolvedValue({ data: { status: 'degraded' } })

    fireEvent.click(screen.getAllByRole('switch')[0])
    expect(useTopicStateStore.getState().config.enabled).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: '保存话题状态配置' }))

    await waitFor(() => expect(mockPost).toHaveBeenCalledWith({ enabled: true }))
    expect(screen.getByText('未保存')).toBeDefined()
    expect(useErrorStore.getState().toasts.some((t) => t.type === 'warning')).toBe(true)
  })

  it('撤销修改回到服务端值', async () => {
    await renderReady()

    fireEvent.change(screen.getByLabelText('并行上限'), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: '撤销修改' }))

    const slider = screen.getByLabelText('并行上限') as HTMLInputElement
    expect(slider.value).toBe('3')
    expect(screen.queryByText('未保存')).toBeNull()
  })
})

describe('会话快照作用域', () => {
  it('会话键 + 角色 id 透传，展示正在聊/没聊完/已结束/下一次对话计划', async () => {
    await renderReady()
    mockSnapshot.mockResolvedValue({ data: SNAPSHOT })

    fireEvent.change(screen.getByLabelText('会话键'), { target: { value: '2:wxid@im.wechat' } })
    fireEvent.change(screen.getByLabelText('角色 ID'), { target: { value: 'role-a' } })
    fireEvent.click(screen.getByRole('button', { name: '查看该会话' }))

    await waitFor(() => expect(mockSnapshot).toHaveBeenCalledWith('2:wxid@im.wechat', 'role-a'))
    expect(await screen.findByTestId('topic-state-snapshot')).toBeDefined()
    expect(screen.getByText(/正在聊（1）/)).toBeDefined()
    expect(screen.getByText(/没聊完（1）/)).toBeDefined()
    expect(screen.getByText(/已结束（1）/)).toBeDefined()
    expect(screen.getByText('下一次对话计划')).toBeDefined()
    expect(screen.getAllByText('面试').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/问问结果/)).toBeDefined()
    expect(screen.getByText(/当前作用域：会话 2:wxid@im.wechat \/ 角色 role-a/)).toBeDefined()
  })

  it('空会话键不发请求，显示作用域错误', async () => {
    await renderReady()

    fireEvent.click(screen.getByRole('button', { name: '查看该会话' }))

    expect(await screen.findByText(/请输入会话键/)).toBeDefined()
    expect(mockSnapshot).not.toHaveBeenCalled()
  })

  it('快照读失败 → 错误态 + 重试；重试成功展示记录', async () => {
    await renderReady()
    mockSnapshot.mockRejectedValueOnce(new Error('403 Forbidden'))
    mockSnapshot.mockResolvedValueOnce({ data: SNAPSHOT })

    fireEvent.change(screen.getByLabelText('会话键'), { target: { value: '2:wxid@im.wechat' } })
    fireEvent.click(screen.getByRole('button', { name: '查看该会话' }))

    expect(await screen.findByText('403 Forbidden')).toBeDefined()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByTestId('topic-state-snapshot')).toBeDefined()
    expect(mockSnapshot).toHaveBeenCalledTimes(2)
  })

  it('关闭态快照：显示未启用提示 + 空记录', async () => {
    await renderReady()
    mockSnapshot.mockResolvedValue({
      data: { status: 'ok', enabled: false, active: [], parked: [], closed: [], next_plan: null },
    })

    fireEvent.change(screen.getByLabelText('会话键'), { target: { value: '2:wxid@im.wechat' } })
    fireEvent.click(screen.getByRole('button', { name: '查看该会话' }))

    expect(await screen.findByText(/话题状态机当前未启用/)).toBeDefined()
    expect(screen.getByText('当前没有记录。')).toBeDefined()
  })
})

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ════════════════════════════════════════════════════════════════
//  topicStateStore —— 配置编辑态 / partial 保存 / 会话快照作用域
//
//  契约：
//   • load：回执 status==='ok' 才 ready；否则 error（不渲染默认假表单）
//   • save：只 POST 相对 snapshot 变化的键；回执非 ok 不落 savedAt、不动 snapshot
//   • 快照：会话键为空不发请求（作用域显式，不猜键）；成功归一 active/parked/closed/next_plan
//   • 裁决固化项（Q1/Q4/Q5/Q6）在归一化时收敛，不把非法值带进编辑态
// ════════════════════════════════════════════════════════════════

import {
  DEFAULT_TOPIC_STATE_CONFIG,
  normalizeTopicStateConfig,
  normalizeTopicStateSnapshot,
  topicStateDiff,
  topicStateIsDirty,
  useTopicStateStore,
} from '../../store/topicStateStore'
import { useErrorStore } from '../../store/errorStore'

const SERVER_CONFIG = {
  enabled: false,
  llm_judge_enabled: false,
  judge_every_turns: 4,
  daily_llm_budget: 0,
  active_cap: 3,
  retention_days: 7,
  recall_after_hours: 6,
  recall_max: 2,
  park_idle_hours: 6,
  switch_theta1: 0.2,
  switch_theta2: 0.35,
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

beforeEach(() => {
  resetStore()
  useErrorStore.setState({ toasts: [], lastError: null })
})

afterEach(() => {
  useErrorStore.setState({ toasts: [], lastError: null })
})

describe('normalizeTopicStateConfig（脏回执逐字段回落）', () => {
  it('缺键 / 非数字 / 越界 → 逐字段回落默认，不抛异常', () => {
    const cfg = normalizeTopicStateConfig({
      enabled: 'yes',
      active_cap: 9,
      recall_after_hours: 2,
      recall_max: 5,
      switch_theta1: 2,
    })

    expect(cfg.enabled).toBe(DEFAULT_TOPIC_STATE_CONFIG.enabled)
    expect(cfg.active_cap).toBe(DEFAULT_TOPIC_STATE_CONFIG.active_cap)
    expect(cfg.recall_after_hours).toBe(DEFAULT_TOPIC_STATE_CONFIG.recall_after_hours)
    expect(cfg.recall_max).toBe(DEFAULT_TOPIC_STATE_CONFIG.recall_max)
    expect(cfg.switch_theta1).toBe(DEFAULT_TOPIC_STATE_CONFIG.switch_theta1)
  })

  it('裁决固化项只接受固定值：judge_every_turns/retention_days/daily_llm_budget/park_idle_hours', () => {
    const cfg = normalizeTopicStateConfig({
      ...SERVER_CONFIG,
      judge_every_turns: 1,
      retention_days: 30,
      daily_llm_budget: 10,
      park_idle_hours: 2,
    })

    expect(cfg.judge_every_turns).toBe(4)
    expect(cfg.retention_days).toBe(7)
    expect(cfg.daily_llm_budget).toBe(0)
    expect(cfg.park_idle_hours).toBe(6)
  })

  it('θ2 < θ1 → 两个阈值同时回落默认（读侧愈合）', () => {
    const cfg = normalizeTopicStateConfig({ switch_theta1: 0.8, switch_theta2: 0.2 })
    expect(cfg.switch_theta1).toBe(DEFAULT_TOPIC_STATE_CONFIG.switch_theta1)
    expect(cfg.switch_theta2).toBe(DEFAULT_TOPIC_STATE_CONFIG.switch_theta2)
  })
})

describe('topicStateDiff / topicStateIsDirty', () => {
  it('只带变化键，且不包含未变化的固定项', () => {
    const next = { ...DEFAULT_TOPIC_STATE_CONFIG, active_cap: 2, enabled: false }
    const diff = topicStateDiff(next, JSON.stringify(DEFAULT_TOPIC_STATE_CONFIG))

    expect(diff).toEqual({ active_cap: 2, enabled: false })
    expect('judge_every_turns' in diff).toBe(false)
    expect(topicStateIsDirty(next, JSON.stringify(DEFAULT_TOPIC_STATE_CONFIG))).toBe(true)
  })

  it('快照损坏不炸：按无基线处理，增量含全部键', () => {
    const diff = topicStateDiff(DEFAULT_TOPIC_STATE_CONFIG, '{不是 JSON')
    expect(Object.keys(diff)).toHaveLength(11)
  })
})

describe('load', () => {
  it('回执 ok → ready，snapshot 落定为归一后的 JSON', async () => {
    const getConfig = vi.fn().mockResolvedValue({
      data: { status: 'ok', config: { ...SERVER_CONFIG, active_cap: 2 } },
    })

    await useTopicStateStore.getState().load(getConfig)

    const s = useTopicStateStore.getState()
    expect(getConfig).toHaveBeenCalledTimes(1)
    expect(s.status).toBe('ready')
    expect(s.config.active_cap).toBe(2)
    expect(s.snapshot).toBe(JSON.stringify(s.config))
    expect(topicStateIsDirty(s.config, s.snapshot)).toBe(false)
  })

  it('回执 status!==ok → error 态 + 错误文案（不伪造已加载）', async () => {
    const getConfig = vi.fn().mockResolvedValue({ data: { status: 'degraded' } })

    await useTopicStateStore.getState().load(getConfig)

    const s = useTopicStateStore.getState()
    expect(s.status).toBe('error')
    expect(s.error).toMatch(/未确认/)
  })

  it('请求抛错 → error 态，错误信息透传', async () => {
    const getConfig = vi.fn().mockRejectedValue(new Error('Network Error'))

    await useTopicStateStore.getState().load(getConfig)

    expect(useTopicStateStore.getState().status).toBe('error')
    expect(useTopicStateStore.getState().error).toBe('Network Error')
  })
})

describe('save（回执纪律）', () => {
  it('只 POST 增量；回执 ok → savedAt 落定、snapshot 更新、dirty 归零', async () => {
    useTopicStateStore.getState().patch({ active_cap: 2 })
    const updateConfig = vi.fn().mockResolvedValue({
      data: { status: 'ok', config: { ...SERVER_CONFIG, active_cap: 2 } },
    })

    const ok = await useTopicStateStore.getState().save(updateConfig)

    expect(ok).toBe(true)
    expect(updateConfig).toHaveBeenCalledWith({ active_cap: 2 })
    const s = useTopicStateStore.getState()
    expect(s.savedAt).not.toBe('')
    expect(s.config.active_cap).toBe(2)
    expect(topicStateIsDirty(s.config, s.snapshot)).toBe(false)
    expect(s.saving).toBe(false)
  })

  it('回执 status!==ok → 判失败：不写 savedAt、snapshot 不动（保持未保存）、给 warning', async () => {
    useTopicStateStore.getState().patch({ active_cap: 2 })
    const updateConfig = vi.fn().mockResolvedValue({ data: { status: 'degraded' } })

    const ok = await useTopicStateStore.getState().save(updateConfig)

    expect(ok).toBe(false)
    const s = useTopicStateStore.getState()
    expect(s.savedAt).toBe('')
    expect(topicStateIsDirty(s.config, s.snapshot)).toBe(true)
    expect(useErrorStore.getState().toasts.some((t) => t.type === 'warning')).toBe(true)
  })

  it('请求抛错 → false + error toast，编辑值保留', async () => {
    useTopicStateStore.getState().patch({ recall_max: 1 })
    const updateConfig = vi.fn().mockRejectedValue(new Error('422 越界'))

    const ok = await useTopicStateStore.getState().save(updateConfig)

    expect(ok).toBe(false)
    expect(useTopicStateStore.getState().config.llm_judge_enabled).toBe(true)
    expect(useTopicStateStore.getState().savedAt).toBe('')
    expect(useErrorStore.getState().toasts.some((t) => t.type === 'error')).toBe(true)
  })

  it('无改动不打写请求（不制造无谓落盘），但仍给成功回执', async () => {
    const updateConfig = vi.fn()

    const ok = await useTopicStateStore.getState().save(updateConfig)

    expect(ok).toBe(true)
    expect(updateConfig).not.toHaveBeenCalled()
  })
})

describe('patch / reset', () => {
  it('patch 阈值互钳：θ1 推高时 θ2 跟随，θ2 压低时 θ1 跟随', () => {
    useTopicStateStore.getState().patch({ switch_theta1: 0.6 })
    expect(useTopicStateStore.getState().config.switch_theta2).toBeGreaterThanOrEqual(0.6)

    useTopicStateStore.getState().patch({ switch_theta2: 0.1 })
    expect(useTopicStateStore.getState().config.switch_theta1).toBeLessThanOrEqual(0.1)
  })

  it('reset 回到上次落定的 snapshot（丢弃未保存编辑）', async () => {
    const getConfig = vi.fn().mockResolvedValue({
      data: { status: 'ok', config: { ...SERVER_CONFIG, recall_max: 1 } },
    })
    await useTopicStateStore.getState().load(getConfig)
    useTopicStateStore.getState().patch({ recall_max: 2, enabled: true })

    useTopicStateStore.getState().reset()

    const s = useTopicStateStore.getState()
    expect(s.config.recall_max).toBe(1)
    expect(s.config.enabled).toBe(false)
    expect(topicStateIsDirty(s.config, s.snapshot)).toBe(false)
  })
})

describe('loadSnapshot（会话作用域）', () => {
  it('成功：完整键 + 角色 id 透传，active/parked/closed/next_plan 归一', async () => {
    const getSnapshot = vi.fn().mockResolvedValue({
      data: {
        status: 'ok',
        enabled: true,
        active: [{ title: '面试', turn_count: 3, mention_count: 2, salience: 0.8, last_active_at: '2026-10-27 10:00:00' }],
        parked: [{ title: '搬家', salience: 0.4, last_active_at: '2026-10-26 09:00:00' }],
        closed: [{ title: '爬山', salience: 0.2, last_active_at: '2026-10-25 08:00:00' }],
        next_plan: { topic: '面试', intent: '问问结果', next_at: '2026-10-28 09:00:00' },
      },
    })

    await useTopicStateStore.getState().loadSnapshot(getSnapshot, ' 2:wxid@im.wechat ', 'role-a')

    expect(getSnapshot).toHaveBeenCalledWith('2:wxid@im.wechat', 'role-a')
    const s = useTopicStateStore.getState()
    expect(s.scopeKey).toBe('2:wxid@im.wechat')
    expect(s.scopeCharacterId).toBe('role-a')
    expect(s.snapshotStatus).toBe('ready')
    expect(s.topicSnapshot?.active[0].title).toBe('面试')
    expect(s.topicSnapshot?.parked[0].title).toBe('搬家')
    expect(s.topicSnapshot?.closed[0].title).toBe('爬山')
    expect(s.topicSnapshot?.next_plan?.intent).toBe('问问结果')
  })

  it('空会话键不发请求，直接 error（作用域必须显式，不猜键）', async () => {
    const getSnapshot = vi.fn()

    await useTopicStateStore.getState().loadSnapshot(getSnapshot, '   ', 'default')

    expect(getSnapshot).not.toHaveBeenCalled()
    expect(useTopicStateStore.getState().snapshotStatus).toBe('error')
    expect(useTopicStateStore.getState().snapshotError).toMatch(/请输入会话键/)
  })

  it('回执非 ok → snapshotStatus=error，不伪造快照', async () => {
    const getSnapshot = vi.fn().mockResolvedValue({ data: { status: 'error' } })

    await useTopicStateStore.getState().loadSnapshot(getSnapshot, '2:wxid@im.wechat')

    expect(useTopicStateStore.getState().snapshotStatus).toBe('error')
    expect(useTopicStateStore.getState().topicSnapshot).toBeNull()
  })

  it('请求抛错 → error 态，错误信息透传', async () => {
    const getSnapshot = vi.fn().mockRejectedValue(new Error('403 Forbidden'))

    await useTopicStateStore.getState().loadSnapshot(getSnapshot, '2:wxid@im.wechat')

    expect(useTopicStateStore.getState().snapshotStatus).toBe('error')
    expect(useTopicStateStore.getState().snapshotError).toBe('403 Forbidden')
  })
})

describe('normalizeTopicStateSnapshot', () => {
  it('缺字段 / 非数组 → 空态，不抛异常', () => {
    const snap = normalizeTopicStateSnapshot({ enabled: true, active: 'oops', parked: null })
    expect(snap.active).toEqual([])
    expect(snap.parked).toEqual([])
    expect(snap.closed).toEqual([])
    expect(snap.next_plan).toBeNull()
  })
})

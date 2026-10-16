import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ════════════════════════════════════════════════════════════════
//  humanizeStore —— 控制台「拟人度」的编辑态 / dirty 比对 / 保存回执纪律
//
//  契约（设计契约 ui_spec + 后端 api_spec）：
//   • load：GET 回执 status==='ok' 才算成功；否则 error 态（不渲染默认假表单）
//   • save：POST **增量**（只带相对 snapshot 变化的可写键）→ partial merge；
//     回执 status!=='ok' 一律判失败（不落 savedAt、不动 snapshot）
//   • max_segments 只读：无论服务端回什么，编辑态恒为 2（用户裁决）
//   • 跨字段互钳由 UI 侧保证；store 只做存取，不发明校验真源
// ════════════════════════════════════════════════════════════════

const { mockGet, mockPost } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockPost: vi.fn(),
}))

vi.mock('../../api/system', () => ({
  humanizeGetConfig: (...args: unknown[]) => mockGet(...args),
  updateHumanizeConfig: (...args: unknown[]) => mockPost(...args),
}))

import {
  DEFAULT_HUMANIZE_CONFIG,
  HUMANIZE_MAX_SEGMENTS,
  humanizeDiff,
  humanizeIsDirty,
  normalizeHumanizeConfig,
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
  max_segments: 2,
  delay_mode_label: '逐字(按字数)',
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

beforeEach(() => {
  vi.clearAllMocks()
  resetStore()
  useErrorStore.setState({ toasts: [], lastError: null })
})

afterEach(() => {
  // errorStore 的 addToast 会挂 4s 定时器；清理避免跨测试串台
  useErrorStore.setState({ toasts: [], lastError: null })
})

describe('normalizeHumanizeConfig（脏回执逐字段回落）', () => {
  it('缺键 / 非数字 / 枚举越界 → 逐字段回落默认，不抛异常', () => {
    const cfg = normalizeHumanizeConfig({
      delay_mode: '慢速模式',
      per_char_ms: '不是数字',
      fixed_ms: null,
      random_min_ms: 12345, // 越界（>10000）→ 回落默认，绝不放大进编辑态
      proactive_split: 'yes',
      max_segments: 5,
    })

    expect(cfg.delay_mode).toBe(DEFAULT_HUMANIZE_CONFIG.delay_mode)
    expect(cfg.per_char_ms).toBe(DEFAULT_HUMANIZE_CONFIG.per_char_ms)
    expect(cfg.fixed_ms).toBe(DEFAULT_HUMANIZE_CONFIG.fixed_ms)
    expect(cfg.random_min_ms).toBe(DEFAULT_HUMANIZE_CONFIG.random_min_ms)
    expect(cfg.proactive_split).toBe(DEFAULT_HUMANIZE_CONFIG.proactive_split)
    // max_segments 只读真值来自用户裁决，不信回执
    expect(cfg.max_segments).toBe(HUMANIZE_MAX_SEGMENTS)
  })

  it('布尔/空串不得被 Number() 伪装成 0 或 1（回落默认而非 0）', () => {
    const cfg = normalizeHumanizeConfig({ fixed_ms: false, per_char_ms: '' })
    expect(cfg.fixed_ms).toBe(DEFAULT_HUMANIZE_CONFIG.fixed_ms)
    expect(cfg.per_char_ms).toBe(DEFAULT_HUMANIZE_CONFIG.per_char_ms)
  })

  it('跨字段倒置在编辑态也愈合（两侧一并回落默认）', () => {
    const cfg = normalizeHumanizeConfig({
      ...SERVER_CONFIG,
      random_min_ms: 900,
      random_max_ms: 300,
      delay_floor_ms: 5000,
      delay_ceil_ms: 100,
    })

    expect(cfg.random_min_ms).toBe(DEFAULT_HUMANIZE_CONFIG.random_min_ms)
    expect(cfg.random_max_ms).toBe(DEFAULT_HUMANIZE_CONFIG.random_max_ms)
    expect(cfg.delay_floor_ms).toBe(DEFAULT_HUMANIZE_CONFIG.delay_floor_ms)
    expect(cfg.delay_ceil_ms).toBe(DEFAULT_HUMANIZE_CONFIG.delay_ceil_ms)
  })

  it('非对象（null / 字符串 / 数组外）→ 全默认', () => {
    for (const bad of [null, undefined, 'oops', 42]) {
      expect(normalizeHumanizeConfig(bad)).toEqual(DEFAULT_HUMANIZE_CONFIG)
    }
  })

  it('数字字符串被归一为数字（Pydantic 宽松模式的服务端行为镜像）', () => {
    const cfg = normalizeHumanizeConfig({ ...SERVER_CONFIG, per_char_ms: '60' })
    expect(cfg.per_char_ms).toBe(60)
  })
})

describe('humanizeDiff / humanizeIsDirty', () => {
  it('只带变化键，且绝不含只读 max_segments', () => {
    const next = { ...DEFAULT_HUMANIZE_CONFIG, per_char_ms: 60, proactive_split: true }
    const diff = humanizeDiff(next, JSON.stringify(DEFAULT_HUMANIZE_CONFIG))

    expect(diff).toEqual({ per_char_ms: 60, proactive_split: true })
    expect('max_segments' in diff).toBe(false)
    expect(humanizeIsDirty(next, JSON.stringify(DEFAULT_HUMANIZE_CONFIG))).toBe(true)
  })

  it('快照损坏不炸：按无基线处理，增量含全部可写键', () => {
    const diff = humanizeDiff(DEFAULT_HUMANIZE_CONFIG, '{不是 JSON')
    expect(Object.keys(diff).sort()).toEqual(
      ['bracket_delay_ms', 'delay_floor_ms', 'delay_ceil_ms', 'delay_mode', 'fixed_ms', 'per_char_ms', 'proactive_split', 'random_max_ms', 'random_min_ms'].sort(),
    )
  })
})

describe('load', () => {
  it('回执 ok → ready，snapshot 落定为回执归一后的 JSON', async () => {
    mockGet.mockResolvedValue({ data: { status: 'ok', config: { ...SERVER_CONFIG, per_char_ms: 50 } } })

    await useHumanizeStore.getState().load(mockGet)

    const s = useHumanizeStore.getState()
    expect(mockGet).toHaveBeenCalledWith()
    expect(s.status).toBe('ready')
    expect(s.config.per_char_ms).toBe(50)
    expect(s.snapshot).toBe(JSON.stringify(s.config))
    expect(humanizeIsDirty(s.config, s.snapshot)).toBe(false)
  })

  it('回执 status!==ok → error 态 + 错误文案（不伪造已加载）', async () => {
    mockGet.mockResolvedValue({ data: { status: 'degraded' } })

    await useHumanizeStore.getState().load(mockGet)

    const s = useHumanizeStore.getState()
    expect(s.status).toBe('error')
    expect(s.error).toMatch(/未确认/)
  })

  it('请求抛错 → error 态，错误信息透传', async () => {
    mockGet.mockRejectedValue(new Error('Network Error'))

    await useHumanizeStore.getState().load(mockGet)

    const s = useHumanizeStore.getState()
    expect(s.status).toBe('error')
    expect(s.error).toBe('Network Error')
  })
})

describe('save（W11-D5 回执纪律）', () => {
  it('只 POST 增量；回执 ok → savedAt 落定、snapshot 更新、dirty 归零', async () => {
    useHumanizeStore.getState().patch({ per_char_ms: 60 })
    mockPost.mockResolvedValue({
      data: { status: 'ok', config: { ...SERVER_CONFIG, per_char_ms: 60 } },
    })

    const ok = await useHumanizeStore.getState().save(mockPost)

    expect(ok).toBe(true)
    expect(mockPost).toHaveBeenCalledWith({ per_char_ms: 60 })
    const s = useHumanizeStore.getState()
    expect(s.savedAt).not.toBe('')
    expect(s.config.per_char_ms).toBe(60)
    expect(humanizeIsDirty(s.config, s.snapshot)).toBe(false)
    expect(s.saving).toBe(false)
  })

  it('回执 status!==ok → 判失败：不写 savedAt、snapshot 不动（保持未保存）、给 warning', async () => {
    useHumanizeStore.getState().patch({ fixed_ms: 900 })
    mockPost.mockResolvedValue({ data: { status: 'degraded' } })

    const ok = await useHumanizeStore.getState().save(mockPost)

    expect(ok).toBe(false)
    const s = useHumanizeStore.getState()
    expect(s.savedAt).toBe('')
    expect(humanizeIsDirty(s.config, s.snapshot)).toBe(true)
    const toasts = useErrorStore.getState().toasts
    expect(toasts.some((t) => t.type === 'warning')).toBe(true)
  })

  it('请求抛错 → 返回 false + error toast，编辑值保留（不静默丢弃用户改动）', async () => {
    useHumanizeStore.getState().patch({ proactive_split: true })
    mockPost.mockRejectedValue(new Error('422 越界'))

    const ok = await useHumanizeStore.getState().save(mockPost)

    expect(ok).toBe(false)
    expect(useHumanizeStore.getState().config.proactive_split).toBe(true)
    expect(useHumanizeStore.getState().savedAt).toBe('')
    expect(useErrorStore.getState().toasts.some((t) => t.type === 'error')).toBe(true)
  })

  it('无改动不打写请求（不制造无谓落盘），但仍给成功回执', async () => {
    const ok = await useHumanizeStore.getState().save(mockPost)

    expect(ok).toBe(true)
    expect(mockPost).not.toHaveBeenCalled()
  })

  it('保存中 saving=true，结束后复位', async () => {
    useHumanizeStore.getState().patch({ delay_mode: 'off' })
    let sawSaving = false
    mockPost.mockImplementation(async () => {
      sawSaving = useHumanizeStore.getState().saving
      return { data: { status: 'ok', config: { ...SERVER_CONFIG, delay_mode: 'off' } } }
    })

    await useHumanizeStore.getState().save(mockPost)

    expect(sawSaving).toBe(true)
    expect(useHumanizeStore.getState().saving).toBe(false)
  })
})

describe('patch / reset', () => {
  it('patch 只改传入键，且 max_segments 恒被钉为 2', () => {
    useHumanizeStore.getState().patch({ per_char_ms: 88, max_segments: 99 })

    const s = useHumanizeStore.getState()
    expect(s.config.per_char_ms).toBe(88)
    expect(s.config.max_segments).toBe(2)
    expect(s.config.fixed_ms).toBe(DEFAULT_HUMANIZE_CONFIG.fixed_ms)
  })

  it('reset 回到上次落定的 snapshot（丢弃未保存编辑）', async () => {
    mockGet.mockResolvedValue({ data: { status: 'ok', config: { ...SERVER_CONFIG, fixed_ms: 800 } } })
    await useHumanizeStore.getState().load(mockGet)
    useHumanizeStore.getState().patch({ fixed_ms: 1234, proactive_split: true })

    useHumanizeStore.getState().reset()

    const s = useHumanizeStore.getState()
    expect(s.config.fixed_ms).toBe(800)
    expect(s.config.proactive_split).toBe(false)
    expect(humanizeIsDirty(s.config, s.snapshot)).toBe(false)
  })

  it('无 snapshot 时 reset 不清空成假默认（保持现状）', () => {
    useHumanizeStore.setState({ snapshot: '' })
    useHumanizeStore.getState().patch({ per_char_ms: 77 })

    useHumanizeStore.getState().reset()

    expect(useHumanizeStore.getState().config.per_char_ms).toBe(77)
  })
})

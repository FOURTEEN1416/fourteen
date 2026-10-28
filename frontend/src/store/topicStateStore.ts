/**
 * 话题状态机配置/快照状态 —— 控制台「消息」tab 的 TopicStateSection 专用。
 *
 * 契约（2026-10-27 实施计划 §9.2）：
 *  - 配置真源 = `data/scheduler_config.json` 的 `topic_state` 块（跨 worker 热读）；
 *    store 只做局部编辑态、dirty 比对与一次 partial POST，不复制校验真源。
 *  - 保存语义同 humanizeStore：HTTP 200 不算成功，回执必须 `status === 'ok'`；
 *    成功后才落 `savedAt`，失败保留编辑值并给 toast（不伪造已保存）。
 *  - 快照查询是独立状态：会话键 + 角色 id → active/parked/closed/next_plan；
 *    空键不发请求（作用域必须显式，不猜键）。
 *
 * FF-0007（ADR-0006）：Zustand store 禁止 import API 层；load/save/loadSnapshot
 * 接受 API 函数作为参数，由组件层注入真实实现、测试注入 mock。
 */
import { create } from 'zustand'
import { useErrorStore } from './errorStore'

export type TopicStateGetConfigFn = () => Promise<{
  data?: { status?: string; config?: unknown }
}>

export type TopicStateUpdateConfigFn = (
  cfg: TopicStateConfigPayload,
) => Promise<{ data?: { status?: string; config?: unknown } }>

export type TopicStateGetSnapshotFn = (
  sessionKey: string,
  characterId?: string,
) => Promise<{ data?: { status?: string } & Record<string, unknown> }>

export interface TopicStateConfig {
  enabled: boolean
  llm_judge_enabled: boolean
  judge_every_turns: number
  daily_llm_budget: number
  active_cap: number
  retention_days: number
  recall_after_hours: number
  recall_max: number
  park_idle_hours: number
  switch_theta1: number
  switch_theta2: number
}

export type TopicStateConfigPayload = Partial<TopicStateConfig>

/** 后端正源默认值（utils/topic_state.py::DEFAULTS 的只读镜像，仅作编辑态占位） */
export const DEFAULT_TOPIC_STATE_CONFIG: TopicStateConfig = {
  enabled: true,
  llm_judge_enabled: true,
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

/** 全部配置键（顺序与后端 DEFAULTS / topic_state 块一致） */
export const TOPIC_STATE_KEYS = [
  'enabled',
  'llm_judge_enabled',
  'judge_every_turns',
  'daily_llm_budget',
  'active_cap',
  'retention_days',
  'recall_after_hours',
  'recall_max',
  'park_idle_hours',
  'switch_theta1',
  'switch_theta2',
] as const

export type TopicStateKey = (typeof TOPIC_STATE_KEYS)[number]

/**
 * 裁决固化项（Q1/Q4/Q5/Q6）：不是“可调建议”，管理端只读展示。
 * 前端仍读后端回执，但在这里做一次越界回落，避免坏回执把滑条撑出 DOM。
 */
export const TOPIC_STATE_FIXED = {
  judge_every_turns: 4,
  daily_llm_budget: 0,
  retention_days: 7,
  park_idle_hours: 6,
} as const

/** 可调整字段的闭区间（与后端 `_BOUNDS` 同源；固定项由 TOPIC_STATE_FIXED 约束） */
const TOPIC_STATE_BOUNDS = {
  judge_every_turns: [TOPIC_STATE_FIXED.judge_every_turns, TOPIC_STATE_FIXED.judge_every_turns],
  daily_llm_budget: [TOPIC_STATE_FIXED.daily_llm_budget, TOPIC_STATE_FIXED.daily_llm_budget],
  active_cap: [1, 3],
  retention_days: [TOPIC_STATE_FIXED.retention_days, TOPIC_STATE_FIXED.retention_days],
  recall_after_hours: [6, 168],
  recall_max: [1, 2],
  park_idle_hours: [TOPIC_STATE_FIXED.park_idle_hours, TOPIC_STATE_FIXED.park_idle_hours],
  switch_theta1: [0, 1],
  switch_theta2: [0, 1],
} as const satisfies Partial<Record<TopicStateKey, readonly [number, number]>>

function toNumber(value: unknown, fallback: number): number {
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') {
    return fallback
  }
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? n : fallback
}

function readInt(key: keyof typeof TOPIC_STATE_BOUNDS, raw: unknown): number {
  const fallback = DEFAULT_TOPIC_STATE_CONFIG[key]
  const [lo, hi] = TOPIC_STATE_BOUNDS[key]
  const n = Math.trunc(toNumber(raw, fallback))
  return n < lo || n > hi ? fallback : n
}

function readFloat(key: keyof typeof TOPIC_STATE_BOUNDS, raw: unknown): number {
  const fallback = DEFAULT_TOPIC_STATE_CONFIG[key]
  const [lo, hi] = TOPIC_STATE_BOUNDS[key]
  const n = toNumber(raw, fallback)
  return n < lo || n > hi ? fallback : Math.round(n * 100) / 100
}

/** 坏回执逐字段回落默认；固定项按裁决值收敛，不把非法值带进编辑态。 */
export function normalizeTopicStateConfig(raw: unknown): TopicStateConfig {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const d = DEFAULT_TOPIC_STATE_CONFIG
  let theta1 = readFloat('switch_theta1', r.switch_theta1)
  let theta2 = readFloat('switch_theta2', r.switch_theta2)
  if (theta2 < theta1) {
    theta1 = d.switch_theta1
    theta2 = d.switch_theta2
  }
  return {
    enabled: typeof r.enabled === 'boolean' ? r.enabled : d.enabled,
    llm_judge_enabled: typeof r.llm_judge_enabled === 'boolean'
      ? r.llm_judge_enabled
      : d.llm_judge_enabled,
    judge_every_turns: readInt('judge_every_turns', r.judge_every_turns),
    daily_llm_budget: readInt('daily_llm_budget', r.daily_llm_budget),
    active_cap: readInt('active_cap', r.active_cap),
    retention_days: readInt('retention_days', r.retention_days),
    recall_after_hours: readInt('recall_after_hours', r.recall_after_hours),
    recall_max: readInt('recall_max', r.recall_max),
    park_idle_hours: readInt('park_idle_hours', r.park_idle_hours),
    switch_theta1: theta1,
    switch_theta2: theta2,
  }
}

/** dirty 判定：编辑态 JSON 与上次落定的 snapshot 逐字比较 */
export function topicStateIsDirty(config: TopicStateConfig, snapshot: string): boolean {
  return JSON.stringify(config) !== snapshot
}

/** 只提交相对 snapshot 变化的键（partial merge，避免把陈旧整份配置写回） */
export function topicStateDiff(
  config: TopicStateConfig,
  snapshot: string,
): TopicStateConfigPayload {
  let base: Record<string, unknown> = {}
  try {
    const parsed: unknown = JSON.parse(snapshot || '{}')
    if (parsed && typeof parsed === 'object') base = parsed as Record<string, unknown>
  } catch {
    base = {} // 快照损坏：按无基线处理，后端仍会做边界校验
  }
  const diff: Record<string, unknown> = {}
  for (const key of TOPIC_STATE_KEYS) {
    if (base[key] === undefined || JSON.stringify(base[key]) !== JSON.stringify(config[key])) {
      diff[key] = config[key]
    }
  }
  return diff as TopicStateConfigPayload
}

export interface TopicStateItem {
  title: string
  summary: string
  status: string
  salience: number
  mention_count: number
  turn_count: number
  last_active_at: string
}

export interface TopicStatePlan {
  topic: string
  intent: string
  planned_at: string
  next_at: string
  expires_at: string
  consumed_at: string | null
}

export interface TopicStateSnapshot {
  enabled: boolean
  active: TopicStateItem[]
  parked: TopicStateItem[]
  closed: TopicStateItem[]
  next_plan: TopicStatePlan | null
  degraded?: boolean
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value : value === null || value === undefined ? '' : String(value)
}

function readCount(value: unknown): number {
  const n = toNumber(value, 0)
  return Number.isFinite(n) ? n : 0
}

function normalizeTopicItem(raw: unknown): TopicStateItem | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  return {
    title: readString(r.title),
    summary: readString(r.summary),
    status: readString(r.status),
    salience: readCount(r.salience),
    mention_count: readCount(r.mention_count),
    turn_count: readCount(r.turn_count),
    last_active_at: readString(r.last_active_at),
  }
}

function normalizeTopicItems(raw: unknown): TopicStateItem[] {
  if (!Array.isArray(raw)) return []
  return raw.map(normalizeTopicItem).filter((item): item is TopicStateItem => item !== null)
}

function normalizeTopicPlan(raw: unknown): TopicStatePlan | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  return {
    topic: readString(r.topic),
    intent: readString(r.intent),
    planned_at: readString(r.planned_at),
    next_at: readString(r.next_at),
    expires_at: readString(r.expires_at),
    consumed_at: r.consumed_at === null || r.consumed_at === undefined
      ? null
      : readString(r.consumed_at),
  }
}

/** 快照回执归一：后端 snake_case 直接消费，缺字段按空态兜底。 */
export function normalizeTopicStateSnapshot(raw: unknown): TopicStateSnapshot {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  return {
    enabled: r.enabled === true,
    active: normalizeTopicItems(r.active),
    parked: normalizeTopicItems(r.parked),
    closed: normalizeTopicItems(r.closed),
    next_plan: normalizeTopicPlan(r.next_plan),
    ...(r.degraded === true ? { degraded: true } : {}),
  }
}

export interface TopicStateStore {
  config: TopicStateConfig
  snapshot: string
  status: 'idle' | 'loading' | 'ready' | 'error'
  error: string
  saving: boolean
  savedAt: string
  scopeKey: string
  scopeCharacterId: string
  snapshotStatus: 'idle' | 'loading' | 'ready' | 'error'
  snapshotError: string
  topicSnapshot: TopicStateSnapshot | null
  load: (getConfig: TopicStateGetConfigFn) => Promise<void>
  patch: (partial: TopicStateConfigPayload) => void
  save: (updateConfig: TopicStateUpdateConfigFn) => Promise<boolean>
  reset: () => void
  loadSnapshot: (
    getSnapshot: TopicStateGetSnapshotFn,
    sessionKey: string,
    characterId?: string,
  ) => Promise<void>
}

function nowLabel(): string {
  return new Date().toLocaleTimeString('zh-CN')
}

export const useTopicStateStore = create<TopicStateStore>()((set, get) => ({
  config: { ...DEFAULT_TOPIC_STATE_CONFIG },
  snapshot: '',
  status: 'idle',
  error: '',
  saving: false,
  savedAt: '',
  scopeKey: '',
  scopeCharacterId: 'default',
  snapshotStatus: 'idle',
  snapshotError: '',
  topicSnapshot: null,

  async load(getConfig) {
    set({ status: 'loading', error: '' })
    try {
      const res = await getConfig()
      const receipt = res?.data as { status?: string; config?: unknown } | undefined
      if (receipt?.status !== 'ok' || !receipt.config) {
        throw new Error('后端未确认话题状态配置（status≠ok）')
      }
      const config = normalizeTopicStateConfig(receipt.config)
      set({ config, snapshot: JSON.stringify(config), status: 'ready', error: '' })
    } catch (e) {
      set({
        status: 'error',
        error: e instanceof Error ? e.message : '话题状态配置加载失败',
      })
    }
  },

  patch(partial) {
    set((state) => {
      const next = { ...state.config, ...partial }
      // 阈值互钳：θ2 不得小于 θ1（后端合并后仍会二次校验，前端先保证不发必然 400 的载荷）
      if (partial.switch_theta1 !== undefined) {
        next.switch_theta1 = partial.switch_theta1
        next.switch_theta2 = Math.max(next.switch_theta2, partial.switch_theta1)
      }
      if (partial.switch_theta2 !== undefined) {
        next.switch_theta2 = partial.switch_theta2
        next.switch_theta1 = Math.min(next.switch_theta1, partial.switch_theta2)
      }
      return { config: next }
    })
  },

  async save(updateConfig) {
    const { config, snapshot } = get()
    const diff = topicStateDiff(config, snapshot)
    if (Object.keys(diff).length === 0) {
      set({ savedAt: nowLabel() })
      return true
    }
    set({ saving: true })
    try {
      const res = await updateConfig(diff)
      const receipt = res?.data as { status?: string; config?: unknown } | undefined
      if (receipt?.status !== 'ok') {
        useErrorStore.getState().addToast({
          type: 'warning',
          message: '话题状态配置未生效（后端未确认），请重试',
        })
        return false
      }
      const next = receipt.config ? normalizeTopicStateConfig(receipt.config) : config
      set({ config: next, snapshot: JSON.stringify(next), savedAt: nowLabel(), error: '' })
      return true
    } catch (e) {
      useErrorStore.getState().addToast({
        type: 'error',
        message: e instanceof Error ? e.message : '话题状态配置保存失败',
      })
      return false
    } finally {
      set({ saving: false })
    }
  },

  reset() {
    const { snapshot } = get()
    if (!snapshot) return
    try {
      const parsed: unknown = JSON.parse(snapshot)
      set({ config: normalizeTopicStateConfig(parsed) })
    } catch {
      // 快照不可解析：保留当前编辑值，绝不清成假默认
    }
  },

  async loadSnapshot(getSnapshot, sessionKey, characterId = 'default') {
    const key = sessionKey.trim()
    const character = characterId.trim() || 'default'
    if (!key) {
      set({
        scopeKey: '',
        scopeCharacterId: character,
        snapshotStatus: 'error',
        snapshotError: '请输入会话键（如 2:wxid@im.wechat 或 2:web:8hex）',
        topicSnapshot: null,
      })
      return
    }
    set({
      scopeKey: key,
      scopeCharacterId: character,
      snapshotStatus: 'loading',
      snapshotError: '',
    })
    try {
      const res = await getSnapshot(key, character)
      const receipt = res?.data as ({ status?: string } & Record<string, unknown>) | undefined
      if (receipt?.status !== 'ok') {
        throw new Error('后端未确认话题快照（status≠ok）')
      }
      set({
        topicSnapshot: normalizeTopicStateSnapshot(receipt),
        snapshotStatus: 'ready',
        snapshotError: '',
      })
    } catch (e) {
      set({
        snapshotStatus: 'error',
        snapshotError: e instanceof Error ? e.message : '话题快照读取失败',
        topicSnapshot: null,
      })
    }
  },
}))

/**
 * 拟人度（humanize）配置状态 —— 控制台「消息」tab 的 HumanizeSection 专用。
 *
 * 设计契约 `humanize_schema` / `ui_spec`：
 *  - 契约真源 = `data/scheduler_config.json` 的 `humanize` 块（跨 4 worker 热读）；
 *    本 store 只做**局部编辑态 + dirty 比对 + 一次 POST**，不复制任何校验真源。
 *  - 保存语义（W11-D5）：HTTP 200 不算成功，必须回执 `status === 'ok'`；
 *    成功后才落 `savedAt`，失败走 useErrorStore 提示且**不改动 snapshot**
 *    （否则本地乐观值会把「没生效」伪装成「已保存」）。
 *  - `max_segments`：2026-10-27 Q6 起默认 **3**、可配 2/3/4（只读展示，
 *    管理员经 API 调整；前端不把它当可提交键，避免误写覆盖）。
 *  - `bracket_delay_ms` 默认 0 = 括号分支惰性（零行为变更）；>0 才生效。
 *
 * 局部编辑由 UI 侧完成「区间互钳」（min≤max / floor≤ceil），故正常操作下不会触发
 * 后端 400；后端仍保留合并后二次校验（契约 `write_validation`）。
 */
import { create } from 'zustand'
import { useErrorStore } from './errorStore'

// FF-0007（ADR-0006）：Zustand store 禁止 import API 层。
// 故 API 函数不进 store —— load/save 接受 API 函数作为参数（依赖注入），
// 由组件层（HumanizeSection）从 `../../api/system` 传入真实实现，
// 测试传入 mock。store 因此保持零 API 依赖，只做状态存取。

/** GET 配置的 API 函数签名（组件层注入真实实现，测试注入 mock） */
export type HumanizeGetConfigFn = () => Promise<{ data?: { status?: string; config?: unknown } }>
/** POST 配置的 API 函数签名 */
export type HumanizeUpdateConfigFn = (
  cfg: HumanizeConfigPayload,
) => Promise<{ data?: { status?: string; config?: unknown } }>

/** 单次回复拆条上限默认值（Q6：可配 2/3/4；前端只读展示，管理员经 API 调整） */
export const HUMANIZE_MAX_SEGMENTS = 3

/** 合法档位（与后端 SEGMENT_CAP_CHOICES 同集） */
export const HUMANIZE_SEGMENT_CAP_CHOICES = [2, 3, 4] as const

/** 后端回执的 max_segments 归一：仅接受 2/3/4，其余回落默认 3 */
function readSegmentCap(raw: unknown): number {
  return typeof raw === 'number' && (HUMANIZE_SEGMENT_CAP_CHOICES as readonly number[]).includes(raw)
    ? raw
    : HUMANIZE_MAX_SEGMENTS
}

// ── 类型定义（本文件为唯一真源，api/system.ts 反向 import）──
/** 段间延时档位（与后端 HumanizeConfigRequest 的 pattern 同集） */
export type HumanizeDelayMode = 'dynamic' | 'fixed' | 'random' | 'off'

export interface HumanizeConfigPayload {
  /** dynamic=逐字按字数 / fixed=固定 / random=区间随机 / off=不延时 */
  delay_mode?: HumanizeDelayMode
  per_char_ms?: number
  fixed_ms?: number
  random_min_ms?: number
  random_max_ms?: number
  delay_floor_ms?: number
  delay_ceil_ms?: number
  bracket_delay_ms?: number
  /** 主动消息是否也拆条发送；false（默认）=整段直发 */
  proactive_split?: boolean
  /** **只读**：恒为 2（用户裁决）；写入非 2 → 400 */
  max_segments?: number
  /** **只读**展示标签，GET 回执附带，不落真源文件 */
  delay_mode_label?: string
}

export type HumanizeConfig = Required<Omit<HumanizeConfigPayload, 'delay_mode_label'>>

/** 后端正源默认值（utils/humanize_config.py DEFAULTS 的只读镜像，仅作编辑态占位） */
export const DEFAULT_HUMANIZE_CONFIG: HumanizeConfig = {
  delay_mode: 'dynamic',
  per_char_ms: 45,
  fixed_ms: 600,
  random_min_ms: 400,
  random_max_ms: 1600,
  delay_floor_ms: 400,
  delay_ceil_ms: 1600,
  bracket_delay_ms: 0,
  proactive_split: false,
  max_segments: HUMANIZE_MAX_SEGMENTS,
}

/** 可写字段（顺序与后端 WRITABLE_FIELDS 一致）；只读的 max_segments 不在其中 */
export const HUMANIZE_WRITABLE_KEYS = [
  'delay_mode',
  'per_char_ms',
  'fixed_ms',
  'random_min_ms',
  'random_max_ms',
  'delay_floor_ms',
  'delay_ceil_ms',
  'bracket_delay_ms',
  'proactive_split',
] as const

export type HumanizeWritableKey = (typeof HUMANIZE_WRITABLE_KEYS)[number]

const DELAY_MODES: readonly HumanizeDelayMode[] = ['dynamic', 'fixed', 'random', 'off']

/**
 * 整型字段闭区间 —— `utils/humanize_config.py::INT_BOUNDS` 的只读镜像。
 *
 * 为什么前端也要有：读侧的边界容错若只在后端，「越界值」会以滑条
 * `<input type=range max=200 value=9999>` 的形态进 DOM —— 浏览器自行钳到 max 后
 * 显示值与状态值不再一致，用户一拖一存就写下一个自己没选过的数。故此处照后端
 * read_humanize 同口径（越界 → 回落默认），**不**自造新边界。
 * 写入侧真源仍是后端（Pydantic + write_humanize），前端从不放大任何值。
 */
const HUMANIZE_INT_BOUNDS = {
  per_char_ms: [5, 200],
  fixed_ms: [0, 5000],
  random_min_ms: [0, 10000],
  random_max_ms: [0, 10000],
  delay_floor_ms: [0, 10000],
  delay_ceil_ms: [0, 60000],
  bracket_delay_ms: [0, 5000],
} as const satisfies Partial<Record<keyof HumanizeConfig, readonly [number, number]>>

type HumanizeIntKey = keyof typeof HUMANIZE_INT_BOUNDS

function toInt(value: unknown, fallback: number): number {
  // null / undefined / '' / 布尔一律视同「未提供」回落默认 —— 注意 Number(null) === 0
  // 与 Number(true) === 1 会把「脏值」伪装成合法整数（读侧容错不得放过它们）。
  if (value === null || value === undefined || value === '' || typeof value === 'boolean') return fallback
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? Math.trunc(n) : fallback
}

/** 读侧容错取整：类型不符 / 越界 → 回落默认（同后端 read_humanize 的逐字段口径） */
function readInt(key: HumanizeIntKey, raw: unknown): number {
  const fallback = DEFAULT_HUMANIZE_CONFIG[key]
  const n = toInt(raw, fallback)
  const [lo, hi] = HUMANIZE_INT_BOUNDS[key]
  return n < lo || n > hi ? fallback : n
}

/**
 * 把后端回执归一为**完整**编辑态配置（缺键/类型不符/越界逐字段回落默认）。
 *
 * 与后端 read_humanize 的容错读同哲学：一个手改坏的真源文件不该让控制台白屏。
 * 跨字段倒置（min>max / floor>ceil）两侧一并回落默认 —— 同样照后端读侧愈合口径
 * （后端若已愈合则此处是恒真式，留此只为「回执直接来自别处」时不自相矛盾）。
 * `max_segments` 一律强制常量——只读真值由用户裁决给出，不信回执。
 */
export function normalizeHumanizeConfig(raw: unknown): HumanizeConfig {
  const r = (raw && typeof raw === 'object' ? raw : {}) as HumanizeConfigPayload
  const d = DEFAULT_HUMANIZE_CONFIG
  const mode = r.delay_mode
  let randomMin = readInt('random_min_ms', r.random_min_ms)
  let randomMax = readInt('random_max_ms', r.random_max_ms)
  if (randomMin > randomMax) {
    randomMin = d.random_min_ms
    randomMax = d.random_max_ms
  }
  let floor = readInt('delay_floor_ms', r.delay_floor_ms)
  let ceil = readInt('delay_ceil_ms', r.delay_ceil_ms)
  if (floor > ceil) {
    floor = d.delay_floor_ms
    ceil = d.delay_ceil_ms
  }
  return {
    delay_mode: typeof mode === 'string' && (DELAY_MODES as readonly string[]).includes(mode)
      ? (mode as HumanizeDelayMode)
      : d.delay_mode,
    per_char_ms: readInt('per_char_ms', r.per_char_ms),
    fixed_ms: readInt('fixed_ms', r.fixed_ms),
    random_min_ms: randomMin,
    random_max_ms: randomMax,
    delay_floor_ms: floor,
    delay_ceil_ms: ceil,
    bracket_delay_ms: readInt('bracket_delay_ms', r.bracket_delay_ms),
    proactive_split: typeof r.proactive_split === 'boolean' ? r.proactive_split : d.proactive_split,
    max_segments: readSegmentCap(r.max_segments),
  }
}

/** dirty 判定真源：编辑态 JSON 与上次落定的 snapshot 是否逐字相同 */
export function humanizeIsDirty(config: HumanizeConfig, snapshot: string): boolean {
  return JSON.stringify(config) !== snapshot
}

/**
 * 计算 partial merge 的**增量**（只带相对 snapshot 变化的可写键）。
 *
 * 只发增量而非整份配置：后端 write_humanize 在 json_state 锁内与磁盘现值 merge，
 * 发送全量会把本 worker 的陈旧快照写回 = utils/json_state.py 记录的 W3 缺陷 D 类丢更新。
 */
export function humanizeDiff(config: HumanizeConfig, snapshot: string): HumanizeConfigPayload {
  let base: Partial<Record<string, unknown>> = {}
  try {
    const parsed: unknown = JSON.parse(snapshot || '{}')
    if (parsed && typeof parsed === 'object') base = parsed as Partial<Record<string, unknown>>
  } catch {
    base = {} // 快照不可解析：按「无基线」处理，增量含全部可写键（后端仍会校验）
  }
  const diff: Record<string, unknown> = {}
  for (const key of HUMANIZE_WRITABLE_KEYS) {
    if (base[key] === undefined || JSON.stringify(base[key]) !== JSON.stringify(config[key])) {
      diff[key] = config[key]
    }
  }
  return diff as HumanizeConfigPayload
}

export interface HumanizeState {
  config: HumanizeConfig
  /** 上次 load/save 落定配置的 JSON（dirty 比对基线；绝不存服务端以外的猜测值） */
  snapshot: string
  status: 'idle' | 'loading' | 'ready' | 'error'
  error: string
  saving: boolean
  savedAt: string
  load: (getConfig: HumanizeGetConfigFn) => Promise<void>
  patch: (partial: Partial<HumanizeConfig>) => void
  save: (updateConfig: HumanizeUpdateConfigFn) => Promise<boolean>
  reset: () => void
}

function nowLabel(): string {
  return new Date().toLocaleTimeString('zh-CN')
}

export const useHumanizeStore = create<HumanizeState>()((set, get) => ({
  config: { ...DEFAULT_HUMANIZE_CONFIG },
  snapshot: '',
  status: 'idle',
  error: '',
  saving: false,
  savedAt: '',

  /** 热读真源；后端未回 `status:'ok'` 一律判失败（不渲染默认假表单） */
  async load(getConfig) {
    set({ status: 'loading', error: '' })
    try {
      const res = await getConfig()
      const receipt = res?.data as { status?: string; config?: unknown } | undefined
      if (receipt?.status !== 'ok' || !receipt.config) {
        throw new Error('后端未确认拟人度配置（status≠ok）')
      }
      const config = normalizeHumanizeConfig(receipt.config)
      set({ config, snapshot: JSON.stringify(config), status: 'ready', error: '' })
    } catch (e) {
      set({
        status: 'error',
        error: e instanceof Error ? e.message : '拟人度配置加载失败',
      })
    }
  },

  patch(partial) {
    set((state) => ({
      // max_segments 不由前端编辑态改写（只读展示，管理员经 API 调）
      config: { ...state.config, ...partial, max_segments: state.config.max_segments },
    }))
  },

  /** 保存增量；返回是否**确实**生效（回执 ok） */
  async save(updateConfig) {
    const { config, snapshot } = get()
    const diff = humanizeDiff(config, snapshot)
    if (Object.keys(diff).length === 0) {
      // 无改动就不打无谓的写请求（后端空 body 会回 ok，但没必要多一次落盘）
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
          message: '拟人度配置未生效（后端未确认），请重试',
        })
        return false
      }
      // 以服务端回执为权威（partial merge 后的全量生效值），避免本地乐观值漂移
      const next = receipt.config ? normalizeHumanizeConfig(receipt.config) : config
      set({ config: next, snapshot: JSON.stringify(next), savedAt: nowLabel(), error: '' })
      return true
    } catch (e) {
      useErrorStore.getState().addToast({
        type: 'error',
        message: e instanceof Error ? e.message : '拟人度配置保存失败',
      })
      return false
    } finally {
      set({ saving: false })
    }
  },

  /** 丢弃本地编辑，回到上次落定的 snapshot */
  reset() {
    const { snapshot } = get()
    if (!snapshot) return
    try {
      const parsed: unknown = JSON.parse(snapshot)
      set({ config: normalizeHumanizeConfig(parsed) })
    } catch {
      // 快照不可解析：保留当前编辑值，绝不清成假默认
    }
  },
}))

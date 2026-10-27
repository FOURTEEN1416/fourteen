/**
 * 控制台「拟人度」配置面板（消息 tab 子区块）。
 *
 * 设计契约 `ui_spec`：挂在 `RoleSettingsTabs.tsx` 的 MessageTab（回复模式段之后），
 * 状态在 `store/humanizeStore.ts`，写入只走 `POST /api/humanize/config`
 * （partial merge，锁内 merge，跨 4 worker 即时一致）。
 *
 * 四条纪律：
 *  ① 门禁沿用 MessageTab：非 admin 不渲染表单、不请求 admin-only 接口；
 *  ② 读失败不渲染默认假表单 —— 显式错误态 + 重试（照 MessageTab 的 W11-D5 写法）；
 *  ③ 保存以回执 `status === 'ok'` 判成功，成功才落 `savedAt`；
 *     dirty 时按钮显式标注「未保存」；
 *  ④ `max_segments` 只读（用户裁决锁 2）—— 呈现为只读文本，绝不作为可提交键。
 *
 * 关于「不延时」档位：`off` 即段间连发（无停顿），等价于把「段间停顿」这一拟人化
 * 特征关掉；`bracket_delay_ms = 0`（默认）让括号段覆盖分支保持惰性（零行为变更）。
 */
import { useEffect } from 'react'
import Section from './RoleSettingsSection'
import Slider from '../shared/Slider'
import Toggle from '../shared/Toggle'
import { useAuthStore } from '../../store/authStore'
import { humanizeIsDirty, useHumanizeStore } from '../../store/humanizeStore'
import type { HumanizeConfig } from '../../store/humanizeStore'
import { humanizeGetConfig, updateHumanizeConfig } from '../../api/system'
import type { HumanizeDelayMode } from '../../api/system'

//: 四档延时模式：标签与后端 utils/humanize_config.py::DELAY_MODE_LABELS 逐字对齐
//: （dynamic 一项由契约钉死为「逐字(按字数)」）。
const DELAY_MODE_OPTIONS: ReadonlyArray<{ key: HumanizeDelayMode; label: string; hint: string }> = [
  { key: 'dynamic', label: '逐字(按字数)', hint: '按上一段字数估停顿' },
  { key: 'fixed', label: '固定延时', hint: '每段固定等待' },
  { key: 'random', label: '随机区间', hint: '区间内随机停顿' },
  { key: 'off', label: '不延时', hint: '段间连发' },
]

interface NumberRowProps {
  label: string
  value: number
  min: number
  max: number
  step: number
  unit?: string
  tooltip?: string
  onChange: (value: number) => void
}

/** 滑条 + 数值：标签走 Slider 自身的 <label htmlFor>（可被辅助技术/测试定位） */
function NumberRow({ label, value, min, max, step, unit, tooltip, onChange }: NumberRowProps) {
  return (
    <div className="flex items-center gap-4">
      <div className="flex-1">
        <Slider
          label={label}
          tooltip={tooltip}
          value={value}
          min={min}
          max={max}
          step={step}
          showValue={false}
          onChange={onChange}
        />
      </div>
      <span className="w-20 shrink-0 text-right text-xs font-mono text-gray-400">
        {value}{unit ? ` ${unit}` : ''}
      </span>
    </div>
  )
}

export default function HumanizeSection() {
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const config = useHumanizeStore((s) => s.config)
  const snapshot = useHumanizeStore((s) => s.snapshot)
  const status = useHumanizeStore((s) => s.status)
  const errorMessage = useHumanizeStore((s) => s.error)
  const saving = useHumanizeStore((s) => s.saving)
  const savedAt = useHumanizeStore((s) => s.savedAt)
  const load = useHumanizeStore((s) => s.load)
  const patch = useHumanizeStore((s) => s.patch)
  const save = useHumanizeStore((s) => s.save)
  const reset = useHumanizeStore((s) => s.reset)

  useEffect(() => {
    // 非 admin 不打 admin-only 接口（后端 /api/humanize/config 一律 require_role admin）
    if (!isAdmin) return
    void load(humanizeGetConfig)
  }, [isAdmin, load])

  // ── 门禁（照 MessageTab）────────────────────────────────
  if (!isAdmin) {
    return (
      <Section title="拟人度">
        <p className="text-xs text-gray-500 leading-relaxed">
          段间延时与主动拆条作用于全局投递链路（全部会话、全部角色），仅管理员可查看与调整。
        </p>
      </Section>
    )
  }

  if (status === 'loading' || status === 'idle') {
    return (
      <Section title="拟人度">
        <div className="flex items-center justify-center py-8">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-500 border-t-transparent" />
        </div>
      </Section>
    )
  }

  // 读失败：显式错误态 + 重试，绝不渲染默认假表单（用户改不到真值还以为是现状）
  if (status === 'error') {
    return (
      <Section title="拟人度">
        <div className="rounded-xl border border-red-200/40 bg-red-50/40 p-5 text-center space-y-3">
          <p className="text-sm text-red-600">{errorMessage || '拟人度配置加载失败'}</p>
          <button
            type="button"
            onClick={() => { void load(humanizeGetConfig) }}
            className="rounded-lg border border-red-200 bg-white/80 px-4 py-1.5 text-xs text-red-600 hover:bg-white transition-colors"
          >
            重试
          </button>
        </div>
      </Section>
    )
  }

  const dirty = humanizeIsDirty(config, snapshot)
  const setMode = (mode: HumanizeDelayMode) => patch({ delay_mode: mode })

  // 区间互钳：本地即保 min≤max / floor≤ceil，避免把必然 400 的载荷发出去
  const setRandomMin = (v: number) =>
    patch({ random_min_ms: v, random_max_ms: Math.max(config.random_max_ms, v) })
  const setRandomMax = (v: number) =>
    patch({ random_max_ms: v, random_min_ms: Math.min(config.random_min_ms, v) })
  const setFloor = (v: number) =>
    patch({ delay_floor_ms: v, delay_ceil_ms: Math.max(config.delay_ceil_ms, v) })
  const setCeil = (v: number) =>
    patch({ delay_ceil_ms: v, delay_floor_ms: Math.min(config.delay_floor_ms, v) })

  const modeHint: Record<HumanizeDelayMode, string> = {
    dynamic: `按上一段字数逐字估停顿：每字 ${config.per_char_ms} ms，并钳在 ${config.delay_floor_ms}–${config.delay_ceil_ms} ms 之间（默认组合与现行行为逐位等价）。`,
    fixed: `每两段之间固定等待 ${config.fixed_ms} ms（不受字数影响）。`,
    random: `每两段之间在 ${config.random_min_ms}–${config.random_max_ms} ms 内随机，节奏更像真人。`,
    off: '段间连发，两条消息一口气发出去（读起来像连续打字，不再有停顿）。',
  }

  return (
    <Section title="拟人度">
      <p className="text-xs text-gray-500 mb-3 leading-relaxed">
        控制「拆条后的段间停顿」与主动消息是否也拆条发送。默认值＝现行行为（零变更），
        改动即时对全部 worker 生效。
      </p>

      <div className="space-y-4">
        {/* 延时档位 */}
        <div>
          <div className="text-xs text-gray-600 mb-1">延时档位</div>
          <div className="text-[11px] text-gray-400 mb-2">{modeHint[config.delay_mode]}</div>
          <div className="flex gap-1 p-1 bg-gray-100/60 rounded-xl">
            {DELAY_MODE_OPTIONS.map((m) => (
              <button
                key={m.key}
                type="button"
                aria-label={m.label}
                aria-pressed={config.delay_mode === m.key}
                onClick={() => setMode(m.key)}
                className={`flex-1 rounded-lg px-2 py-1.5 text-xs transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/50 ${
                  config.delay_mode === m.key
                    ? 'bg-white text-gray-800 font-medium shadow-sm ring-1 ring-black/5'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                <div>{m.label}</div>
                <div className="text-[10px] opacity-70">{m.hint}</div>
              </button>
            ))}
          </div>
        </div>

        {/* 档位参数 */}
        {config.delay_mode === 'dynamic' && (
          <div className="space-y-2" data-testid="humanize-dynamic-params">
            <NumberRow
              label="每字毫秒"
              tooltip="dynamic 档位下每个字估算的停顿毫秒（默认 45 = 现行 0.045s/字）"
              value={config.per_char_ms}
              min={5}
              max={200}
              step={1}
              unit="ms"
              onChange={(v) => patch({ per_char_ms: v })}
            />
            <NumberRow
              label="延时下钳"
              value={config.delay_floor_ms}
              min={0}
              max={10000}
              step={50}
              unit="ms"
              tooltip="短句的最低停顿（默认 400 = 现行 0.4s 下限）"
              onChange={setFloor}
            />
            <NumberRow
              label="延时上钳"
              value={config.delay_ceil_ms}
              min={0}
              max={60000}
              step={50}
              unit="ms"
              tooltip="长句的停顿上限（默认 1600 = 现行 1.6s 上限）"
              onChange={setCeil}
            />
          </div>
        )}

        {config.delay_mode === 'fixed' && (
          <div className="space-y-2" data-testid="humanize-fixed-params">
            <NumberRow
              label="每段固定延时"
              value={config.fixed_ms}
              min={0}
              max={5000}
              step={50}
              unit="ms"
              onChange={(v) => patch({ fixed_ms: v })}
            />
          </div>
        )}

        {config.delay_mode === 'random' && (
          <div className="space-y-2" data-testid="humanize-random-params">
            <NumberRow
              label="随机下界"
              value={config.random_min_ms}
              min={0}
              max={10000}
              step={50}
              unit="ms"
              onChange={setRandomMin}
            />
            <NumberRow
              label="随机上界"
              value={config.random_max_ms}
              min={0}
              max={10000}
              step={50}
              unit="ms"
              onChange={setRandomMax}
            />
          </div>
        )}

        {config.delay_mode === 'off' && (
          <p className="text-[11px] text-gray-400" data-testid="humanize-off-hint">
            不延时：两段之间不等待，直接连发。
          </p>
        )}

        {/* 括号段覆盖延时（四种档位下均生效：>0 时括号段覆盖为该值） */}
        <NumberRow
          label="括号段延时"
          tooltip="0＝不启用（默认，零行为变更）；>0 时以 […] 包裹的段覆盖为该毫秒值"
          value={config.bracket_delay_ms}
          min={0}
          max={5000}
          step={50}
          unit="ms"
          onChange={(v) => patch({ bracket_delay_ms: v })}
        />

        {/* 主动消息拆条 */}
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-gray-700">主动消息也拆句发送（默认：整段发）</p>
            <p className="text-[11px] text-gray-400 mt-0.5">
              关闭时主动消息整段直发（现行为）；开启后与主链一样按上限拆条投递
            </p>
          </div>
          <Toggle
            checked={config.proactive_split}
            onChange={(v) => patch({ proactive_split: v })}
          />
        </div>

        {/* 拆条上限（只读） */}
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-gray-700">单次回复拆条上限</p>
            <p className="text-[11px] text-gray-400 mt-0.5">可配 2/3/4（默认 3），经 API 调整</p>
          </div>
          <span
            className="rounded-lg bg-gray-100 px-3 py-1 text-xs font-mono text-gray-500"
            data-testid="humanize-max-segments"
          >
            {config.max_segments} 条（可配 2/3/4）
          </span>
        </div>

        {/* 只读说明（无开关）：ask 里点名的「纯指令隐身」目前在链路上只有**一半**
            实现——纯指令段（仅含系统标记，如 `[主动]`）段间已是零延时
            （wechat_connector.py `_segment_delay` 首行的 `is_pure_instruction` 短路）；
            而「整轮不发送」的隐身语义**没有后端字段**（utils/humanize_config.py
            WRITABLE_FIELDS 无此键，POST 未知键 → 400）。
            故此处只作事实陈述，**不**摆放一个写了不生效的开关（W11-D4 假表单纪律）。 */}
        <p className="text-[11px] text-gray-400 leading-relaxed" data-testid="humanize-pure-instruction-note">
          纯指令段（只含「[主动]」等系统标记、无正文的段）段间已自动零延时；
          「整轮不发送（隐身）」的语义开关尚未落地，暂不可在此开启。
        </p>

        {/* 保存 / 撤销 */}
        <div className="flex items-center justify-end gap-2">
          {dirty && (
            <span className="rounded-lg bg-amber-50 border border-amber-100 px-2 py-1 text-[11px] text-amber-700">
              未保存
            </span>
          )}
          <button
            type="button"
            onClick={reset}
            disabled={!dirty || saving}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-40"
          >
            撤销修改
          </button>
          <button
            type="button"
            onClick={() => { void save(updateHumanizeConfig) }}
            disabled={saving || !dirty}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors shadow-sm disabled:opacity-50"
          >
            {saving ? '保存中…' : savedAt && !dirty ? `已保存（${savedAt}）` : '保存拟人度'}
          </button>
        </div>
      </div>
    </Section>
  )
}

/** 供测试/调用方复用的只读导出（避免测试里硬编码长度契约） */
export type { HumanizeConfig }

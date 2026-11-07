/**
 * 「我 × 这个角色」设置面板（2026-11-07 P5/P1）。
 *
 * 用户裁决：先选角色，对这个角色进行设置；所有与体验/个性化强相关的参数
 * （说话节奏 / 主动消息调度 / LLM 决策 / 骰子闸 / 话题引擎）都归用户自己，
 * 按角色各存一份（`utils/character_settings.py` 唯一真源）。没有「全局配置」
 * 横幅、没有管理员代设、没有跨角色共享层。
 *
 * 数据：GET/PUT/DELETE /api/characters/{id}/my-settings（服务端按 token+绑定
 * 派生会话键，绝不接受客户端传键）。
 */
import { useCallback, useEffect, useState } from 'react'
import { RotateCcw, Loader2 } from 'lucide-react'
import { getMyCharacterSettings, saveMyCharacterSettings, resetMyCharacterSettings } from '../../api/characterSettings'
import Section from '../admin/RoleSettingsSection'
import Slider from '../shared/Slider'
import Toggle from '../shared/Toggle'
import { useErrorStore } from '../../store/errorStore'

type SettingsMap = Record<string, unknown>

const DELAY_MODES: Array<{ key: string; label: string; hint: string }> = [
  { key: 'dynamic', label: '逐字', hint: '按字数估停顿' },
  { key: 'fixed', label: '固定', hint: '每段固定等待' },
  { key: 'random', label: '随机', hint: '区间内随机' },
  { key: 'off', label: '不延时', hint: '段间连发' },
]

/** 数字行控件（min/max 校验在本地即做，避免把必然 400 的载荷发出去） */
function NumberRow({
  label, value, min, max, step = 1, unit = '', onChange, hint,
}: {
  label: string
  value: number
  min: number
  max: number
  step?: number
  unit?: string
  onChange: (v: number) => void
  hint?: string
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="w-28 shrink-0">
        <span className="text-xs text-gray-600">{label}</span>
        {hint && <p className="text-[10px] text-gray-400 leading-tight">{hint}</p>}
      </div>
      <div className="flex-1">
        <Slider
          value={Math.min(Math.max(value, min), max)}
          min={min}
          max={max}
          step={step}
          label={label}
          showLabel={false}
          showValue={false}
          onChange={onChange}
        />
      </div>
      <span className="w-20 text-right text-xs font-mono text-gray-400">
        {value}
        {unit ? ` ${unit}` : ''}
      </span>
    </div>
  )
}

function ToggleRow({ label, desc, checked, onChange, disabled }: {
  label: string
  desc?: string
  checked: boolean
  onChange: (v: boolean) => void
  disabled?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="min-w-0">
        <p className="text-sm text-gray-700">{label}</p>
        {desc && <p className="text-[11px] text-gray-400 mt-0.5">{desc}</p>}
      </div>
      <Toggle checked={checked} onChange={onChange} disabled={disabled} />
    </div>
  )
}

export default function CharacterSettingsPanel({ characterId, characterName }: {
  characterId: string
  characterName?: string
}) {
  const addToast = useErrorStore((s) => s.addToast)
  const [cfg, setCfg] = useState<SettingsMap>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState('')
  const [reloadToken, setReloadToken] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    getMyCharacterSettings(characterId)
      .then((res) => {
        if (!cancelled) setCfg((res.config ?? {}) as SettingsMap)
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(
            (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
            || '角色设置加载失败，当前显示的不是你的真实设置',
          )
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [characterId, reloadToken])

  const patch = useCallback((k: string, v: unknown) => {
    setCfg((prev) => ({ ...prev, [k]: v }))
  }, [])

  const num = (k: string, fallback: number) => (typeof cfg[k] === 'number' ? (cfg[k] as number) : fallback)
  const bool = (k: string, fallback = false) => (typeof cfg[k] === 'boolean' ? (cfg[k] as boolean) : fallback)
  const str = (k: string, fallback = '') => (typeof cfg[k] === 'string' ? (cfg[k] as string) : fallback)

  const handleSave = async () => {
    setSaving(true)
    try {
      const res = await saveMyCharacterSettings(characterId, cfg)
      setCfg((res.config ?? {}) as SettingsMap)
      setSavedAt(new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
      addToast({ type: 'success', message: '角色设置已保存（只影响你对这个角色的体验）' })
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        || '保存失败，请重试'
      addToast({ type: 'error', message: msg })
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    setSaving(true)
    try {
      await resetMyCharacterSettings(characterId)
      const res = await getMyCharacterSettings(characterId)
      setCfg((res.config ?? {}) as SettingsMap)
      setSavedAt('')
      addToast({ type: 'success', message: '已恢复出厂默认' })
    } catch {
      addToast({ type: 'error', message: '恢复默认失败，请重试' })
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <Section title="本角色设置">
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-4 w-4 animate-spin text-primary-500" />
        </div>
      </Section>
    )
  }

  if (error) {
    return (
      <Section title="本角色设置">
        <div className="rounded-xl border border-red-200/40 bg-red-50/40 p-5 text-center space-y-3">
          <p className="text-sm text-red-600">{error}</p>
          <button
            type="button"
            onClick={() => setReloadToken((t) => t + 1)}
            className="rounded-lg border border-red-200 bg-white/80 px-4 py-1.5 text-xs text-red-600 hover:bg-white transition-colors"
          >
            重试
          </button>
        </div>
      </Section>
    )
  }

  return (
    <div className="space-y-5" data-testid="character-settings-panel">
      <p className="text-[11px] text-gray-400 leading-relaxed">
        这里是对{characterName ? `「${characterName}」` : '这个角色'}的**全部设置**——只影响你对这个角色的体验；
        其他角色各自一份，互不影响。改动后点「保存」生效。
      </p>

      {/* ── ① 说话节奏 ── */}
      <Section title="① 说话节奏">
        <div className="space-y-3">
          <div>
            <div className="text-xs text-gray-600 mb-1">段间延时档位</div>
            <div className="flex gap-1 p-1 bg-gray-100/60 rounded-xl">
              {DELAY_MODES.map((m) => (
                <button
                  key={m.key}
                  type="button"
                  aria-pressed={str('delay_mode') === m.key}
                  onClick={() => patch('delay_mode', m.key)}
                  className={`flex-1 rounded-lg px-2 py-1.5 text-xs transition-all ${
                    str('delay_mode') === m.key
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
          {str('delay_mode', 'dynamic') === 'dynamic' && (
            <>
              <NumberRow label="每字毫秒" value={num('per_char_ms', 45)} min={5} max={200} unit="ms" onChange={(v) => patch('per_char_ms', v)} />
              <NumberRow label="延时下钳" value={num('delay_floor_ms', 400)} min={0} max={10000} step={50} unit="ms" onChange={(v) => patch('delay_floor_ms', Math.min(v, num('delay_ceil_ms', 1600)))} />
              <NumberRow label="延时上钳" value={num('delay_ceil_ms', 1600)} min={0} max={60000} step={50} unit="ms" onChange={(v) => patch('delay_ceil_ms', Math.max(v, num('delay_floor_ms', 400)))} />
            </>
          )}
          {str('delay_mode', 'dynamic') === 'fixed' && (
            <NumberRow label="每段固定延时" value={num('fixed_ms', 600)} min={0} max={5000} step={50} unit="ms" onChange={(v) => patch('fixed_ms', v)} />
          )}
          {str('delay_mode', 'dynamic') === 'random' && (
            <>
              <NumberRow label="随机下界" value={num('random_min_ms', 400)} min={0} max={10000} step={50} unit="ms" onChange={(v) => patch('random_min_ms', Math.min(v, num('random_max_ms', 1600)))} />
              <NumberRow label="随机上界" value={num('random_max_ms', 1600)} min={0} max={10000} step={50} unit="ms" onChange={(v) => patch('random_max_ms', Math.max(v, num('random_min_ms', 400)))} />
            </>
          )}
          <NumberRow label="括号段延时" value={num('bracket_delay_ms', 0)} min={0} max={5000} step={50} unit="ms" onChange={(v) => patch('bracket_delay_ms', v)} hint="0=不启用" />
          <ToggleRow label="主动消息也拆句发送" desc="默认整段发；开启后按拆条上限分段投递" checked={bool('proactive_split')} onChange={(v) => patch('proactive_split', v)} />
          <div className="flex items-center justify-between gap-4">
            <p className="text-sm text-gray-700">单次回复拆条上限</p>
            <div className="flex gap-1 p-1 bg-gray-100/60 rounded-xl">
              {[2, 3, 4].map((cap) => (
                <button
                  key={cap}
                  type="button"
                  aria-pressed={num('max_segments', 3) === cap}
                  onClick={() => patch('max_segments', cap)}
                  className={`rounded-lg px-3 py-1.5 text-xs transition-all ${
                    num('max_segments', 3) === cap
                      ? 'bg-white text-gray-800 font-medium shadow-sm ring-1 ring-black/5'
                      : 'text-gray-500 hover:text-gray-700'
                  }`}
                >
                  {cap} 条
                </button>
              ))}
            </div>
          </div>
          <div className="pt-3 border-t border-gray-100 space-y-3">
            <ToggleRow label="生成前阅读延迟" desc="先「读一下」再回" checked={bool('read_delay_enabled', true)} onChange={(v) => patch('read_delay_enabled', v)} />
            {bool('read_delay_enabled', true) && (
              <>
                <NumberRow label="延迟下界" value={num('read_delay_min_ms', 1000)} min={0} max={30000} step={100} unit="ms" onChange={(v) => patch('read_delay_min_ms', Math.min(v, num('read_delay_max_ms', 6000)))} />
                <NumberRow label="延迟上界" value={num('read_delay_max_ms', 6000)} min={0} max={60000} step={100} unit="ms" onChange={(v) => patch('read_delay_max_ms', Math.max(v, num('read_delay_min_ms', 1000)))} />
                <NumberRow label="阅读速度" value={num('read_delay_chars_per_second', 0)} min={0} max={100} unit="字/秒" onChange={(v) => patch('read_delay_chars_per_second', v)} hint="0=关闭" />
                <NumberRow label="总上限" value={num('read_delay_max_total_ms', 30000)} min={0} max={120000} step={1000} unit="ms" onChange={(v) => patch('read_delay_max_total_ms', v)} />
                <NumberRow label="无输入状态封顶" value={num('read_delay_channel_cap_ms', 5000)} min={0} max={60000} step={500} unit="ms" onChange={(v) => patch('read_delay_channel_cap_ms', v)} />
              </>
            )}
            <ToggleRow label="输入状态（typing）" desc="生成期间显示「正在输入」" checked={bool('typing_enabled', true)} onChange={(v) => patch('typing_enabled', v)} />
            <NumberRow label="口误概率（仅沉浸式）" value={num('typo_rate', 0)} min={0} max={100} unit="%" onChange={(v) => patch('typo_rate', v)} hint="0=关闭" />
          </div>
        </div>
      </Section>

      {/* ── ② 主动消息 ── */}
      <Section title="② 主动消息（这个角色什么时候来找我）">
        <div className="space-y-3">
          <ToggleRow label="主动消息总开关" desc="关闭后这个角色不会主动发消息给你" checked={bool('enabled', true)} onChange={(v) => patch('enabled', v)} />
          <ToggleRow label="暂停" desc="暂停期间不自动发，不影响其他设置" checked={bool('paused')} onChange={(v) => patch('paused', v)} />
          <NumberRow label="紧迫阈值" value={num('threshold', 2)} min={0} max={20} step={0.5} onChange={(v) => patch('threshold', v)} hint="越高越不容易开口" />
          <NumberRow label="每日上限" value={num('max_daily_messages', 8)} min={1} max={200} unit="条/天" onChange={(v) => patch('max_daily_messages', v)} />
          <NumberRow label="最小间隔" value={num('min_interval_minutes', 30)} min={0} max={1440} unit="分钟" onChange={(v) => patch('min_interval_minutes', v)} />
          <NumberRow label="回复后冷却" value={num('cooldown_after_reply_minutes', 15)} min={0} max={1440} unit="分钟" onChange={(v) => patch('cooldown_after_reply_minutes', v)} />
          <NumberRow label="免打扰开始" value={num('quiet_hours_start', 23)} min={0} max={23} unit="点" onChange={(v) => patch('quiet_hours_start', v)} />
          <NumberRow label="免打扰结束" value={num('quiet_hours_end', 7)} min={0} max={23} unit="点" onChange={(v) => patch('quiet_hours_end', v)} />
        </div>
      </Section>

      {/* ── ③ LLM 决策 ── */}
      <Section title="③ LLM 决策（开口时机与风格）">
        <div className="space-y-3">
          <ToggleRow label="启用 LLM 主动决策" checked={bool('llm_enabled', true)} onChange={(v) => patch('llm_enabled', v)} />
          <ToggleRow label="免打扰时段写入提示词" desc="由 LLM 遵守你的免打扰设置" checked={bool('respect_quiet_hours', true)} onChange={(v) => patch('respect_quiet_hours', v)} />
          <div className="flex items-center gap-3">
            <span className="w-28 shrink-0 text-xs text-gray-600">力度</span>
            <select
              value={str('intensity', 'normal')}
              onChange={(e) => patch('intensity', e.target.value)}
              className="flex-1 rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-sm"
            >
              <option value="low">低（克制）</option>
              <option value="normal">中（自然）</option>
              <option value="high">高（更主动）</option>
            </select>
          </div>
          <div>
            <p className="text-xs text-gray-600 mb-1">风格提示（进 LLM）</p>
            <input
              value={str('style_hint')}
              maxLength={200}
              onChange={(e) => patch('style_hint', e.target.value)}
              placeholder="例如：温柔、少用感叹号"
              className="w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm"
            />
          </div>
          <div>
            <p className="text-xs text-gray-600 mb-1">角色补充提示</p>
            <input
              value={str('character_hint')}
              maxLength={200}
              onChange={(e) => patch('character_hint', e.target.value)}
              placeholder="可选，覆盖/补充角色人设口吻"
              className="w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm"
            />
          </div>
          <NumberRow label="等待窗抖动" value={num('jitter_seconds', 120)} min={0} max={900} unit="秒" onChange={(v) => patch('jitter_seconds', v)} hint="0=关闭；避免精确卡点" />
        </div>
      </Section>

      {/* ── ④ 骰子闸 ── */}
      <Section title="④ 骰子闸（要不要开口的零 token 概率闸）">
        <div className="space-y-3">
          <ToggleRow label="启用骰子闸" checked={bool('dice_enabled')} onChange={(v) => patch('dice_enabled', v)} />
          <ToggleRow label="干跑（只记账不拦截）" checked={bool('dice_dry_run', true)} onChange={(v) => patch('dice_dry_run', v)} />
          <NumberRow label="峰时命中线" value={num('peak_line', 20)} min={0} max={100} unit="%" onChange={(v) => patch('peak_line', v)} />
          <NumberRow label="谷时命中线" value={num('offpeak_line', 60)} min={0} max={100} unit="%" onChange={(v) => patch('offpeak_line', v)} />
          <NumberRow label="连没中加成" value={num('miss_bonus', 10)} min={0} max={40} step={0.5} unit="%" onChange={(v) => patch('miss_bonus', v)} />
          <NumberRow label="加成上限" value={num('miss_bonus_cap', 40)} min={0} max={40} step={1} unit="%" onChange={(v) => patch('miss_bonus_cap', v)} />
          <NumberRow label="短空闲阈值" value={num('idle_short_minutes', 20)} min={0} max={1440} unit="分钟" onChange={(v) => patch('idle_short_minutes', v)} />
          <NumberRow label="长空闲阈值" value={num('idle_long_minutes', 360)} min={1} max={10080} unit="分钟" onChange={(v) => patch('idle_long_minutes', v)} />
          <NumberRow label="短空闲因子" value={num('idle_short_factor', 0.15)} min={0} max={1} step={0.05} onChange={(v) => patch('idle_short_factor', v)} />
          <NumberRow label="长空闲因子" value={num('idle_long_factor', 1.5)} min={1} max={10} step={0.1} onChange={(v) => patch('idle_long_factor', v)} />
          <p className="text-[11px] text-gray-400">峰时窗口：周一至周五 09:00–12:00 / 14:00–18:00（其余为谷时）。</p>
        </div>
      </Section>

      {/* ── ⑤ 话题与记忆 ── */}
      <Section title="⑤ 话题引擎">
        <div className="space-y-3">
          <ToggleRow label="话题状态机" desc="让对话有话题延续与翻篇意识" checked={bool('topic_enabled', true)} onChange={(v) => patch('topic_enabled', v)} />
          <ToggleRow label="稀疏 LLM 判定" desc="每 4 轮用一次 LLM 校准话题状态" checked={bool('llm_judge_enabled', true)} onChange={(v) => patch('llm_judge_enabled', v)} />
        </div>
      </Section>

      {/* 保存 / 复位 */}
      <div className="flex flex-wrap items-center justify-end gap-2 pb-2">
        <button
          type="button"
          onClick={() => void handleReset()}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-50"
        >
          <RotateCcw className="h-3.5 w-3.5" />
          恢复出厂默认
        </button>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-primary-500 px-4 py-2 text-xs font-medium text-white hover:bg-primary-400 transition-colors shadow-sm disabled:opacity-50"
        >
          {saving ? '保存中…' : savedAt ? `已保存（${savedAt}）` : '保存本角色设置'}
        </button>
      </div>
    </div>
  )
}

import { useState, useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import client from '../../api/client'
import { queryKeys } from '../../hooks/useQueries'
import { updateCharacter } from '../../api/characters'
import { useErrorStore } from '../../store/errorStore'
import { sanitizeCharacterName } from '../../utils/character'
import Slider from '../shared/Slider'
import TagInput from '../shared/TagInput'
import Toggle from '../shared/Toggle'
import ConfirmDialog from '../shared/ConfirmDialog'
import StorylineEditor from '../storyline/StorylineEditor'
import type { RoleSettingsTab, RoleSettingsCharacter } from '../../types/framework'
import type { UnifiedCharacterUpdate } from '../../types/api'
import { Save, Trash2, Copy, Smile } from 'lucide-react'
import { ENGINE_OPTIONS, MIMO_MODELS } from './RoleSettingsConstants'
import { enrichCharacter, proactiveGetConfig, proactiveHistory, proactiveSend, proactivePause, updateProactiveConfig } from '../../api/system'
import Section from './RoleSettingsSection'

// ═══ Tab: Basic ═══

function BasicTab({ character }: { character: RoleSettingsCharacter }) {
  const qc = useQueryClient()
  const [name, setName] = useState(character.name)
  const [description, setDescription] = useState(character.description ?? '')
  const [personality, setPersonality] = useState<Record<string, number>>(character.personality || {})
  const [anchors, setAnchors] = useState<string[]>(character.core_anchors || [])
  const [speaking, setSpeaking] = useState<Record<string, number>>(character.speaking_style || {})
  const [catchphrases, setCatchphrases] = useState<string[]>(character.catchphrases || [])
  const [saving, setSaving] = useState(false)

  // 当 character 变化时（角色切换），同步重置本地表单 state
  // 触发条件：character.id 变化而非整个对象引用变化
  const characterId = character.id
  /* eslint-disable react-hooks/set-state-in-effect -- props-to-form-state sync（标准模式）*/
  useEffect(() => {
    setName(character.name)
    setDescription(character.description ?? '')
    setPersonality(character.personality || {})
    setAnchors(character.core_anchors || [])
    setSpeaking(character.speaking_style || {})
    setCatchphrases(character.catchphrases || [])
  }, [characterId]) // eslint-disable-line react-hooks/exhaustive-deps
  /* eslint-enable react-hooks/set-state-in-effect */

  const personaJson = { name, description, personality, core_anchors: anchors, speaking_style: { ...speaking, catchphrases } }

  const handleSave = async () => {
    setSaving(true)
    try {
      const payload: UnifiedCharacterUpdate = {
        name,
        description,
        personality,
        speaking_style: speaking,
        core_anchors: anchors,
        catchphrases,
      }
      await updateCharacter(character.id, payload)
      await qc.invalidateQueries({ queryKey: queryKeys.characters.all })
      await qc.invalidateQueries({ queryKey: queryKeys.characters.detail(character.id) })
      useErrorStore.getState().addToast({ type: 'success', message: '角色基础设置已保存' })
    } catch {
      useErrorStore.getState().addToast({ type: 'error', message: '保存失败，请重试' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Identity */}
      <Section title="角色身份">
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1.5 block">角色名称</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-800 placeholder-gray-300 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20 transition-all"
              placeholder="给你的角色取个名字"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-gray-500 mb-1.5 block">一句话描述</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={2}
              className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-800 placeholder-gray-300 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20 transition-all resize-none"
              placeholder="描述角色的身份、性格、背景..."
            />
          </div>
        </div>
      </Section>

      {/* Personality */}
      <Section title="性格特质">
        <div className="space-y-4">
          {(Object.entries(personality) as [string, number][]).map(([key, val]) => {
            const labels: Record<string, { zh: string; emoji: string }> = {
              warmth: { zh: '温暖', emoji: '☀️' },
              playfulness: { zh: '俏皮', emoji: '🎭' },
              independence: { zh: '独立', emoji: '🦅' },
              jealousy: { zh: '吃醋', emoji: '💢' },
              stubbornness: { zh: '固执', emoji: '🧱' },
            }
            const info = labels[key] || { zh: key, emoji: '' }
            return (
              <div key={key} className="flex items-center gap-4">
                <div className="w-20 shrink-0">
                  <span className="text-xs text-gray-600">{info.emoji} {info.zh}</span>
                </div>
                <div className="flex-1">
                  <Slider value={val} min={0} max={1} step={0.01} label={labels[key]?.zh || key} onChange={v => setPersonality((prev: Record<string, number>) => ({ ...prev, [key]: v }))} />
                </div>
                <span className="w-10 text-right text-xs font-mono text-gray-400">{(val * 100).toFixed(0)}</span>
              </div>
            )
          })}
        </div>
        <div className="mt-4 pt-4 border-t border-gray-100">
          <label className="text-xs font-medium text-gray-500 mb-1.5 block">核心锚点</label>
          <TagInput tags={anchors} placeholder="输入后按回车添加..." onChange={setAnchors} />
        </div>
      </Section>

      {/* Speaking Style */}
      <Section title="说话风格">
        <div className="space-y-4">
          {(Object.entries(speaking) as [string, number][]).map(([key, val]) => {
            const labels: Record<string, string> = { formality: '正式度', humor: '幽默感', liveliness: '活泼度', gentleness: '温柔度' }
            return (
              <div key={key} className="flex items-center gap-4">
                <div className="w-20 shrink-0"><span className="text-xs text-gray-600">{labels[key] || key}</span></div>
                <div className="flex-1">
                  <Slider value={val} min={0} max={1} step={0.01} label={labels[key] || key} onChange={v => setSpeaking((prev: Record<string, number>) => ({ ...prev, [key]: v }))} />
                </div>
                <span className="w-10 text-right text-xs font-mono text-gray-400">{(val * 100).toFixed(0)}</span>
              </div>
            )
          })}
        </div>
        <div className="mt-4 pt-4 border-t border-gray-100">
          <label className="text-xs font-medium text-gray-500 mb-1.5 block">口头禅</label>
          <TagInput tags={catchphrases} placeholder="输入后按回车添加..." onChange={setCatchphrases} />
        </div>
      </Section>

      {/* Preview */}
      <Section title="配置预览">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-gray-400">JSON 预览 · 可复制导出</span>
          <button onClick={() => navigator.clipboard.writeText(JSON.stringify(personaJson, null, 2))} className="flex items-center gap-1 text-xs text-primary-500 hover:text-primary-600 transition-colors">
            <Copy className="w-3 h-3" /> 复制
          </button>
        </div>
        <pre className="bg-gray-50 rounded-xl p-3 text-xs text-gray-600 font-mono overflow-auto max-h-48 border border-gray-100">
          {JSON.stringify(personaJson, null, 2)}
        </pre>
      </Section>

      <button
        onClick={handleSave}
        disabled={saving}
        className="w-full py-2.5 rounded-xl bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 active:scale-[0.98] transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <Save className="w-4 h-4" />
        {saving ? '保存中…' : '保存设置'}
      </button>

      <ImportantDatesSection characterId={character.id} />
    </div>
  )
}

// ═══ 重要日期（候选 D：生日/纪念日/自定义，ASE 每日维护自动检查） ═══

interface DateItem { name: string; date: string; kind: string }

function ImportantDatesSection({ characterId }: { characterId: string }) {
  const [dates, setDates] = useState<DateItem[]>([])
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    client.get(`/characters/${characterId}/important-dates`)
      .then((r: { data?: { dates?: DateItem[] } }) => { if (alive) setDates(r.data?.dates ?? []) })
      .catch(() => {})
    return () => { alive = false }
  }, [characterId])

  function update(i: number, patch: Partial<DateItem>) {
    setDates(prev => prev.map((d, j) => (j === i ? { ...d, ...patch } : d)))
    setDirty(true)
  }

  async function handleSave() {
    setSaving(true)
    try {
      await client.put(`/characters/${characterId}/important-dates`, { dates })
      setDirty(false)
      useErrorStore.getState().addToast({ type: 'success', message: '重要日期已保存' })
    } catch {
      useErrorStore.getState().addToast({ type: 'error', message: '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  return (
    <Section title="重要日期">
      <p className="text-xs text-gray-400 mb-3">生日 / 纪念日命中当天时，角色会主动发来祝福（格式 MM-DD 或 YYYY-MM-DD）</p>
      <div className="space-y-2">
        {dates.map((d, i) => (
          <div key={i} className="flex items-center gap-2">
            <input
              value={d.name}
              onChange={e => update(i, { name: e.target.value })}
              placeholder="名称（如：我的生日）"
              className="flex-1 rounded-lg bg-white/60 border border-gray-200 px-2.5 py-1.5 text-xs outline-none focus:border-macaron-blue/50"
            />
            <input
              value={d.date}
              onChange={e => update(i, { date: e.target.value })}
              placeholder="MM-DD"
              className="w-24 rounded-lg bg-white/60 border border-gray-200 px-2.5 py-1.5 text-xs font-mono outline-none focus:border-macaron-blue/50"
            />
            <select
              value={d.kind}
              onChange={e => update(i, { kind: e.target.value })}
              className="rounded-lg bg-white/60 border border-gray-200 px-1.5 py-1.5 text-xs outline-none"
            >
              <option value="birthday">生日</option>
              <option value="anniversary">纪念日</option>
              <option value="custom">自定义</option>
            </select>
            <button
              onClick={() => { setDates(prev => prev.filter((_, j) => j !== i)); setDirty(true) }}
              className="text-gray-300 hover:text-red-400 text-xs px-1"
              title="删除"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button
          onClick={() => { setDates(prev => [...prev, { name: '', date: '', kind: 'custom' }]); setDirty(true) }}
          className="text-xs text-macaron-blue-deep hover:underline"
        >
          + 添加日期
        </button>
        {dirty && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="ml-auto px-3 py-1.5 rounded-lg bg-primary-500 text-white text-xs font-medium hover:bg-primary-600 disabled:opacity-50"
          >
            {saving ? '保存中…' : '保存日期'}
          </button>
        )}
      </div>
    </Section>
  )
}

// ═══ Tab: Voice ═══

function VoiceTab({ character }: { character: RoleSettingsCharacter }) {
  const [engine] = useState(character.voice_config?.engine || 'mimo-tts')
  // voice_config 是 VoiceConfig | 自定义对象 联合类型；mimo_model 是自定义字段，需要运行时安全访问
  const mimoModelInitial = (character.voice_config as { mimo_model?: string } | null | undefined)?.mimo_model
  const [mimoModel, setMimoModel] = useState(mimoModelInitial || 'mimo-v2.5-tts')
  const [status] = useState('就绪')

  return (
    <div className="space-y-4">
      {/* Engine Picker */}
      <Section title="语音引擎">
        <div className="grid grid-cols-2 gap-2">
          {ENGINE_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => {}}
              className={`text-left p-3 rounded-xl transition-all glass-pink ring-1 ring-primary-400/30`}
            >
              <p className="text-sm font-medium text-primary-700">{opt.label}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">{opt.desc}</p>
            </button>
          ))}
        </div>
      </Section>

      {/* MiMo Cloud */}
      {engine === 'mimo-tts' && (
        <Section title="MiMo Cloud TTS">
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-2">
              {MIMO_MODELS.map(m => (
                <button
                  key={m.value}
                  onClick={() => setMimoModel(m.value)}
                  className={`text-left p-3 rounded-xl border transition-all ${
                    mimoModel === m.value ? 'border-primary-400 bg-primary-50/50 ring-1 ring-primary-400/30' : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  <p className="text-xs font-medium text-gray-700">{m.label}</p>
                  <p className="text-[10px] text-gray-400 mt-0.5">{m.desc}</p>
                </button>
              ))}
            </div>

            {(mimoModel === 'mimo-v2.5-tts-voiceclone' || mimoModel === 'mimo-v2.5-tts-voicedesign') && (
              <div className="rounded-xl bg-amber-50/50 border border-amber-100 p-4 text-center">
                <p className="text-sm text-amber-700">
                  {mimoModel === 'mimo-v2.5-tts-voiceclone' ? '语音克隆' : '音色设计'}功能开发中
                </p>
                <p className="text-xs text-amber-500 mt-1">当前请先使用基础合成</p>
              </div>
            )}

            <div className="text-xs text-gray-400">状态: {status}</div>
          </div>
        </Section>
      )}

      <div className="rounded-xl bg-gray-50 border border-gray-100 p-3 text-center">
        <p className="text-xs text-gray-400">语音设置保存接口开发中，当前仅支持预览配置</p>
      </div>
    </div>
  )
}

// ═══ Tab: Message ═══

function MessageTab({ character }: { character: RoleSettingsCharacter }) {
  const [threshold, setThreshold] = useState(2.0)
  const [dailyLimit, setDailyLimit] = useState(8)
  const [minInterval, setMinInterval] = useState(30)
  const [cooldown, setCooldown] = useState(15)
  const [paused, setPaused] = useState(false)
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState('')
  const [sending, setSending] = useState(false)
  const [sendResult, setSendResult] = useState<string | null>(null)
  const [history, setHistory] = useState<Array<{ type: string; message: string; at: string }>>([])
  const qc = useQueryClient()

  useEffect(() => {
    let alive = true
    ;(async () => {
      try {
        const [cfgRes, histRes] = await Promise.all([
          proactiveGetConfig().catch(() => null),
          proactiveHistory(10).catch(() => null),
        ])
        if (!alive) return
        if (cfgRes?.data) {
          setThreshold(cfgRes.data.threshold ?? 2.0)
          setDailyLimit(cfgRes.data.max_daily_messages ?? 8)
          setMinInterval(cfgRes.data.min_interval_minutes ?? 30)
          setCooldown(cfgRes.data.cooldown_after_reply_minutes ?? 15)
          setPaused(!!cfgRes.data.paused)
        }
        if (histRes?.data?.history) setHistory(histRes.data.history)
      } catch { /* 静默：未初始化引擎时展示占位 */ }
    })()
    return () => { alive = false }
  }, [])

  async function handleSave() {
    setSaving(true)
    try {
      await updateProactiveConfig({
        threshold, max_daily: dailyLimit,
        min_interval_minutes: minInterval, cooldown_after_reply_minutes: cooldown,
      })
      setSavedAt(new Date().toLocaleTimeString('zh-CN'))
    } catch (e) {
      useErrorStore.getState().addToast({ type: 'error', message: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setSaving(false)
    }
  }

  async function handlePause(next: boolean) {
    try {
      await proactivePause(next)
      setPaused(next)
    } catch (e) {
      useErrorStore.getState().addToast({ type: 'error', message: e instanceof Error ? e.message : '操作失败' })
    }
  }

  async function handleSendNow() {
    setSending(true)
    setSendResult(null)
    try {
      const res = await proactiveSend()
      const msg = res.data?.message ?? ''
      setSendResult(msg)
      const histRes = await proactiveHistory(10).catch(() => null)
      if (histRes?.data?.history) setHistory(histRes.data.history)
      qc.invalidateQueries({ queryKey: queryKeys.proactive.state })
    } catch (e) {
      useErrorStore.getState().addToast({ type: 'error', message: e instanceof Error ? e.message : '发送失败（引擎未初始化？）' })
    } finally {
      setSending(false)
    }
  }

  const todayCount = history.filter(h => (h.at || '').startsWith(new Date().toISOString().slice(0, 10))).length
  const lastAt = history[0]?.at ? new Date(history[0].at).toLocaleString('zh-CN') : '—'

  return (
    <div className="space-y-4">
      {/* Stats（真数据：/api/proactive/history + config） */}
      <Section title="消息统计">
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: '消息总数', value: (character.stats?.messages ?? 0).toLocaleString(), color: 'text-blue-600' },
            { label: '今日主动', value: String(todayCount), color: 'text-green-600' },
            { label: '最后发送', value: lastAt, color: 'text-gray-600', small: true },
          ].map(s => (
            <div key={s.label} className="text-center p-3 rounded-xl bg-gray-50">
              <p className={`font-bold ${s.small ? 'text-xs mt-1' : 'text-lg'} ${s.color}`}>{s.value}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">{s.label}</p>
            </div>
          ))}
        </div>
      </Section>

      {/* Toggle（真控制：暂停/恢复调度） */}
      <Section title="主动对话">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-gray-700">{paused ? '主动消息已暂停' : '主动消息运行中'}</p>
            <p className="text-xs text-gray-400 mt-0.5">暂停后引擎跳过自动触发；下方手动发送不受影响</p>
          </div>
          <Toggle checked={!paused} onChange={v => handlePause(!v)} />
        </div>
      </Section>

      {/* Frequency（真生效：写入运行时控制器） */}
      <Section title="频率控制">
        <div className={`space-y-4 ${paused ? 'opacity-40 pointer-events-none' : ''}`}>
          <div className="flex items-center gap-4">
            <div className="w-24 shrink-0"><span className="text-xs text-gray-600">紧迫阈值</span></div>
            <div className="flex-1"><Slider value={threshold} min={0} max={10} step={0.5} label="紧迫阈值" onChange={setThreshold} /></div>
            <span className="w-16 text-right text-xs font-mono text-gray-400">{threshold.toFixed(1)}</span>
          </div>
          {[
            { label: '每日上限', value: dailyLimit, min: 1, max: 50, unit: '条/天', onChange: setDailyLimit },
            { label: '最小间隔', value: minInterval, min: 5, max: 120, unit: '分钟', onChange: setMinInterval },
            { label: '冷却时间', value: cooldown, min: 5, max: 240, unit: '分钟', onChange: setCooldown },
          ].map(s => (
            <div key={s.label} className="flex items-center gap-4">
              <div className="w-24 shrink-0"><span className="text-xs text-gray-600">{s.label}</span></div>
              <div className="flex-1"><Slider value={s.value} min={s.min} max={s.max} step={1} label={s.label} onChange={s.onChange} /></div>
              <span className="w-16 text-right text-xs font-mono text-gray-400">{s.value} {s.unit}</span>
            </div>
          ))}
          <button
            onClick={handleSave}
            disabled={saving}
            className="w-full py-2 rounded-xl btn-macaron text-white text-xs font-medium disabled:opacity-50"
          >
            {saving ? '保存中…' : savedAt ? `已保存（${savedAt}）` : '保存频率配置'}
          </button>
        </div>
      </Section>

      {/* Manual send（08-28 新增：手动控制） */}
      <Section title="手动控制">
        <button
          onClick={handleSendNow}
          disabled={sending}
          className="w-full py-2 rounded-xl bg-macaron-blue text-white text-xs font-medium hover:bg-macaron-blue-deep transition-colors disabled:opacity-50"
        >
          {sending ? '生成发送中…' : '立即发送一条主动消息'}
        </button>
        {sendResult && (
          <div className="mt-2 rounded-xl bg-white/70 border border-macaron-blue/30 px-3 py-2">
            <p className="text-[10px] text-gray-400 mb-0.5">已发送</p>
            <p className="text-xs text-gray-700">{sendResult}</p>
          </div>
        )}
        {history.length > 0 && (
          <div className="mt-3 space-y-1.5 max-h-40 overflow-y-auto">
            {history.slice(0, 5).map((h, i) => (
              <div key={i} className="rounded-lg bg-gray-50 px-3 py-1.5 flex items-start justify-between gap-2">
                <span className="text-[10px] text-gray-400 shrink-0">{h.type}</span>
                <span className="text-[11px] text-gray-600 text-right flex-1 line-clamp-1">{h.message}</span>
                <span className="text-[10px] text-gray-300 shrink-0">{new Date(h.at).toLocaleTimeString('zh-CN')}</span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}

// ═══ Tab: Data ═══

function DataTab({ character }: { character: RoleSettingsCharacter }) {
  const [showDelete, setShowDelete] = useState(false)
  const [enriching, setEnriching] = useState(false)
  const [enrichResult, setEnrichResult] = useState<{
    status: string
    documents_found: number
    chunks_added: number
    sources_used: string[]
    duration_seconds: number
    errors: string[]
  } | null>(null)
  const [enrichError, setEnrichError] = useState<string | null>(null)

  const handleEnrich = async () => {
    if (!character.name?.trim()) {
      useErrorStore.getState().addToast({ type: 'warning', message: '请先设置角色名称' })
      return
    }
    setEnriching(true)
    setEnrichError(null)
    setEnrichResult(null)
    try {
      const res = await enrichCharacter(character.id, character.name)
      setEnrichResult(res.data)
      useErrorStore.getState().addToast({
        type: res.data?.status === 'enriched' ? 'success' : 'info',
        message: res.data?.status === 'enriched'
          ? `人设增强完成：写入 ${res.data.chunks_added} 块知识`
          : '人设增强未新增知识块',
      })
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : '人设增强失败'
      setEnrichError(msg)
      useErrorStore.getState().addToast({ type: 'error', message: msg })
    } finally {
      setEnriching(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Stats */}
      <Section title="数据概览">
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: '消息总数', value: (character.stats?.messages ?? 0).toLocaleString(), icon: '💬' },
            { label: '记忆条数', value: (character.stats?.memories ?? 0).toLocaleString(), icon: '🧠' },
            { label: '平均响应', value: character.stats?.avgResponse ?? '—', icon: '⚡' },
          ].map(s => (
            <div key={s.label} className="text-center p-3 rounded-xl bg-gray-50">
              <p className="text-lg mb-0.5">{s.icon}</p>
              <p className="text-lg font-bold text-gray-800">{s.value}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">{s.label}</p>
            </div>
          ))}
        </div>
        <p className="text-xs text-gray-400 mt-3">导出功能开发中</p>
      </Section>

      {/* 火爬虫 + AgentReach 人设增强 */}
      <Section title="网络人设增强（火爬虫 + AgentReach）">
        <div className="space-y-3">
          <div className="rounded-xl bg-amber-50/50 border border-amber-100 p-3">
            <p className="text-xs text-gray-600 leading-relaxed">
              抓取 B站、小红书、Firecrawl、Jina Reader、Exa 等多源网络素材，
              处理为知识块后写入角色知识库，供对话时 RAG 检索使用。
              <span className="text-gray-400">（与对话内 search 工具不同：search 是实时搜索不写入知识库，此处是离线批量写入）</span>
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={handleEnrich}
              disabled={enriching || !character.name?.trim()}
              className="px-4 py-2 text-sm font-medium text-white rounded-lg bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all shadow-sm flex items-center gap-2"
            >
              {enriching ? (
                <>
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" />
                  增强中...
                </>
              ) : (
                <>🔥 开始网络增强</>
              )}
            </button>
            <span className="text-xs text-gray-400">
              以角色名「{character.name || '未命名'}」为关键词搜索
            </span>
          </div>
          {enrichError && (
            <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-xs">
              {enrichError}
            </div>
          )}
          {enrichResult && (
            <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-3 py-2 text-xs space-y-1">
              <div className="font-medium">
                {enrichResult.status === 'enriched' ? '✅ 增强成功' : '⚠️ 未写入新知识块'}
              </div>
              <div>找到文档：{enrichResult.documents_found} 篇</div>
              <div>生成知识块：{enrichResult.chunks_added} 块</div>
              {enrichResult.sources_used?.length > 0 && (
                <div>数据源：{enrichResult.sources_used.join('、')}</div>
              )}
              <div className="text-gray-500">耗时：{enrichResult.duration_seconds}s</div>
              {enrichResult.errors?.length > 0 && (
                <div className="text-red-600">错误：{enrichResult.errors.join('；')}</div>
              )}
            </div>
          )}
        </div>
      </Section>

      {/* RAG */}
      <Section title="知识库引擎 (RAG)">
        <div className="flex items-center gap-2 mb-4">
          <span className="flex items-center gap-1 text-[11px] text-green-600 font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" /> 运行正常
          </span>
        </div>
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: '向量文档', value: (character.rag?.vectorDocs ?? 0).toLocaleString() },
            { label: '关键词索引', value: (character.rag?.keywordIndex ?? 0).toLocaleString() },
            { label: '检索命中率', value: `${character.rag?.hitRate ?? 0}%` },
          ].map(s => (
            <div key={s.label} className="text-center p-2.5 rounded-xl bg-purple-50/50 border border-purple-100/50">
              <p className="text-lg font-bold text-purple-600">{s.value}</p>
              <p className="text-[10px] text-gray-400 mt-0.5">{s.label}</p>
            </div>
          ))}
        </div>
        <div className="mt-3 rounded-xl bg-gray-50 border border-gray-100 p-3 text-center">
          <p className="text-xs text-gray-400">知识库搜索与文档管理接口开发中</p>
        </div>
      </Section>

      {/* Timestamps */}
      <Section title="时间信息">
        <div className="flex justify-between text-sm">
          <div><span className="text-gray-400 text-xs">创建时间</span><p className="text-gray-700 font-medium">{character.created_at ? new Date(character.created_at).toLocaleDateString() : '—'}</p></div>
          <div><span className="text-gray-400 text-xs">最后更新</span><p className="text-gray-700 font-medium">{character.updated_at ? new Date(character.updated_at).toLocaleDateString() : '—'}</p></div>
        </div>
      </Section>

      {/* Danger */}
      <Section title="危险区域" className="border-red-200/40 bg-red-50/20">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-medium text-red-600">删除角色</p>
            <p className="text-xs text-gray-400 mt-0.5">所有对话记录、记忆、知识文档将被永久清除</p>
          </div>
          <button onClick={() => setShowDelete(true)} className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-red-500 text-white text-xs font-medium hover:bg-red-600 transition-colors">
            <Trash2 className="w-3.5 h-3.5" /> 删除
          </button>
        </div>
      </Section>

      <ConfirmDialog open={showDelete} title="确认删除角色" message={`确定要删除「${sanitizeCharacterName(character.name)}」吗？此操作不可恢复。`} confirmText="确认删除" cancelText="取消" variant="danger" onConfirm={() => setShowDelete(false)} onCancel={() => setShowDelete(false)} />
    </div>
  )
}

// ═══ Tab: Stickers ═══

function StickersTab() {
  const EMOJIS = ['😊', '😘', '🥰', '😭', '😤', '🤔', '💕', '✨', '🎉', '😅', '😂', '🥺', '😍', '🙈', '💪', '🔥', '👍', '👋']

  return (
    <div className="space-y-4">
      <Section title="常用表情">
        <div className="grid grid-cols-6 gap-2">
          {EMOJIS.map(emoji => (
            <button key={emoji} className="aspect-square rounded-xl bg-white border border-gray-100 text-2xl flex items-center justify-center hover:bg-primary-50 hover:border-primary-200 hover:scale-110 transition-all active:scale-95">
              {emoji}
            </button>
          ))}
        </div>
      </Section>
      <Section title="自定义贴图">
        <div className="border-2 border-dashed border-gray-200 rounded-xl p-6 text-center hover:border-primary-300 transition-colors cursor-pointer">
          <Smile className="w-8 h-8 text-gray-300 mx-auto" />
          <p className="text-xs text-gray-400 mt-2">上传自定义贴图</p>
          <p className="text-[10px] text-gray-300 mt-0.5">PNG / GIF / JPEG · 每张 ≤ 5MB</p>
        </div>
      </Section>
      <div className="rounded-xl bg-gray-50 border border-gray-100 p-3 text-center">
        <p className="text-xs text-gray-400">自定义贴图保存接口开发中</p>
      </div>
    </div>
  )
}

// ═══ Tab: Timeline ═══

function TimelineTab({ character }: { character: RoleSettingsCharacter }) {
  return (
    <div className="space-y-4">
      <StorylineEditor characterId={character.id} />
      <div className="rounded-xl bg-gray-50 border border-gray-100 p-3 text-center">
        <p className="text-xs text-gray-400">时间线由剧情编辑器自动保存</p>
      </div>
    </div>
  )
}

// ═══ Tab Router ═══

function ActiveTab({ tab, character }: { tab: RoleSettingsTab; character: RoleSettingsCharacter }) {
  switch (tab) {
    case 'basic': return <BasicTab character={character} />
    case 'voice': return <VoiceTab character={character} />
    case 'message': return <MessageTab character={character} />
    case 'data': return <DataTab character={character} />
    case 'stickers': return <StickersTab />
    case 'timeline': return <TimelineTab character={character} />
  }
}

export default ActiveTab

import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import client from '../../api/client'
import { queryKeys, useVoiceStatus, useDeleteCharacter } from '../../hooks/useQueries'
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
import { Save, Trash2, Copy } from 'lucide-react'
import { ENGINE_OPTIONS, MIMO_MODELS } from './RoleSettingsConstants'
import { PERSONALITY_LABELS, SPEAKING_STYLE_LABELS, normalizePersonality, normalizeSpeakingStyle } from '../../constants/persona'
import { enrichCharacter, proactiveGetConfig, proactiveHistory, proactiveSend, proactivePause, updateProactiveConfig, knowledgeCollectConfig, updateKnowledgeCollectConfig } from '../../api/system'
import Section from './RoleSettingsSection'
import KnowledgePreview from '../storyline/KnowledgePreview'

// ═══ Tab: Basic ═══

function BasicTab({ character }: { character: RoleSettingsCharacter }) {
  const qc = useQueryClient()
  const [name, setName] = useState(character.name)
  const [description, setDescription] = useState(character.description ?? '')
  const [personality, setPersonality] = useState<Record<string, number>>(() => normalizePersonality(character.personality))
  const [anchors, setAnchors] = useState<string[]>(character.core_anchors || [])
  const [speaking, setSpeaking] = useState<Record<string, number>>(() => normalizeSpeakingStyle(character.speaking_style))
  const [catchphrases, setCatchphrases] = useState<string[]>(character.catchphrases || [])
  const [saving, setSaving] = useState(false)

  // 当 character 变化时（角色切换），同步重置本地表单 state
  // 触发条件：character.id 变化而非整个对象引用变化
  const characterId = character.id
  /* eslint-disable react-hooks/set-state-in-effect -- props-to-form-state sync（标准模式）*/
  useEffect(() => {
    setName(character.name)
    setDescription(character.description ?? '')
    setPersonality(normalizePersonality(character.personality))
    setAnchors(character.core_anchors || [])
    setSpeaking(normalizeSpeakingStyle(character.speaking_style))
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
              rows={3}
              className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-800 placeholder-gray-300 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20 transition-all resize-y min-h-[4.5rem]"
              placeholder="描述角色的身份、性格、背景..."
            />
          </div>
        </div>
      </Section>

      {/* Personality */}
      <Section title="性格特质">
        <div className="space-y-4">
          {(Object.entries(personality) as [string, number][]).map(([key, val]) => {
            const emoji: Record<string, string> = {
              warmth: '☀️',
              playfulness: '🎭',
              independence: '🦅',
              jealousy: '💢',
              stubbornness: '🧱',
            }
            const info = { zh: PERSONALITY_LABELS[key] || key, emoji: emoji[key] || '' }
            return (
              <div key={key} className="flex items-center gap-4">
                <div className="w-20 shrink-0">
                  <span className="text-xs text-gray-600">{info.emoji} {info.zh}</span>
                </div>
                <div className="flex-1">
                  <Slider value={val} min={0} max={1} step={0.01} label={info.zh} onChange={v => setPersonality((prev: Record<string, number>) => ({ ...prev, [key]: v }))} />
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
            const labels = SPEAKING_STYLE_LABELS
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
              className="text-gray-400 hover:text-red-400 text-xs px-1"
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

// ═══ Data: Export（GAP-4 收尾，2026-09-01）——角色卡/聊天记录真实下载 ═══

function ExportRow({ characterId, characterName }: { characterId: string; characterName?: string }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')

  async function download(kind: 'card' | 'chat', format: string) {
    const key = `${kind}-${format}`
    setBusy(key)
    setError('')
    try {
      const url = kind === 'card'
        ? `/characters/${characterId}/export?format=${format}`
        : `/characters/${characterId}/chat/export?format=${format}`
      const res = await client.get(url, { responseType: 'blob' })
      const safeName = (characterName || characterId).replace(/[^\w\u4e00-\u9fff]/g, '_').slice(0, 50)
      const filename =
        kind === 'card'
          ? `${safeName}.${format === 'png' ? 'png' : 'json'}`
          : `chat-${characterId}.${format}`
      const blobUrl = URL.createObjectURL(res.data as Blob)
      const a = document.createElement('a')
      a.href = blobUrl
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(blobUrl)
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || '导出失败，请稍后重试')
    } finally {
      setBusy(null)
    }
  }

  const btn = 'px-3 py-1.5 rounded-lg border text-xs font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed'
  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-gray-500 mr-1">导出</span>
        <button onClick={() => download('card', 'png')} disabled={busy !== null}
          className={`${btn} border-macaron-blue text-macaron-blue-deep hover:bg-macaron-blue/20`}>
          {busy === 'card-png' ? '导出中…' : '角色卡 PNG'}
        </button>
        <button onClick={() => download('card', 'json')} disabled={busy !== null}
          className={`${btn} border-macaron-blue text-macaron-blue-deep hover:bg-macaron-blue/20`}>
          {busy === 'card-json' ? '导出中…' : '角色卡 JSON'}
        </button>
        <button onClick={() => download('chat', 'json')} disabled={busy !== null}
          className={`${btn} border-macaron-mint text-macaron-mint-deep hover:bg-macaron-mint/20`}>
          {busy === 'chat-json' ? '导出中…' : '聊天记录 JSON'}
        </button>
        <button onClick={() => download('chat', 'csv')} disabled={busy !== null}
          className={`${btn} border-macaron-mint text-macaron-mint-deep hover:bg-macaron-mint/20`}>
          {busy === 'chat-csv' ? '导出中…' : '聊天记录 CSV'}
        </button>
      </div>
      {error && <p className="text-xs text-red-500">{error}</p>}
    </div>
  )
}

// ═══ Tab: Voice ═══

function VoiceTab({ character }: { character: RoleSettingsCharacter }) {
  const qc = useQueryClient()
  const [engine] = useState(character.voice_config?.engine || 'mimo-tts')
  // voice_config 是 VoiceConfig | 自定义对象 联合类型；mimo_model 是自定义字段，需要运行时安全访问
  const mimoModelInitial = (character.voice_config as { mimo_model?: string } | null | undefined)?.mimo_model
  const [mimoModel, setMimoModel] = useState(mimoModelInitial || 'mimo-v2.5-tts')
  // 状态来自 GET /voice/status 真实探测（曾写死 useState('就绪')）
  const { data: voiceStatus, isLoading: voiceStatusLoading } = useVoiceStatus()
  const status = voiceStatusLoading
    ? '检测中…'
    : voiceStatus?.last_error
      ? `异常：${voiceStatus.last_error}`
      : voiceStatus?.enabled
        ? '就绪'
        : '语音引擎未启用'
  // 保存接线（GAP-4 修复，2026-09-01）：POST /characters/{id}/voice 落盘
  const [saving, setSaving] = useState(false)
  const [savedTick, setSavedTick] = useState(false)
  const [saveError, setSaveError] = useState('')
  const dirty = mimoModel !== (mimoModelInitial || 'mimo-v2.5-tts')

  async function handleSaveVoice() {
    setSaving(true)
    setSaveError('')
    try {
      await client.post(`/characters/${character.id}/voice`, {
        engine: 'mimo-tts',
        speaker_name: (character.voice_config as { speaker_name?: string } | null)?.speaker_name ?? '',
        extra_params: { mimo_model: mimoModel },
      })
      setSavedTick(true)
      setTimeout(() => setSavedTick(false), 2000)
      qc.invalidateQueries({ queryKey: queryKeys.characters.detail(character.id) })
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setSaveError(detail || '保存失败，请稍后重试')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-4">
      {/* Engine Picker */}
      <Section title="语音引擎">
        <div className="grid grid-cols-1 gap-2">
          {ENGINE_OPTIONS.map(opt => (
            <button
              key={opt.value}
              onClick={() => {}}
              className={`text-left p-3 rounded-xl transition-all glass-yellow ring-1 ring-primary-400/30`}
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
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
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
                  {mimoModel === 'mimo-v2.5-tts-voiceclone' ? '克隆音色' : '设计音色'}的创建在「系统设置 → 语音工作台」
                </p>
                <p className="text-xs text-amber-500 mt-1">此处选择要应用于该角色的模型形态</p>
              </div>
            )}

            <div className="text-xs text-gray-400">状态: {status}</div>

            <div className="flex items-center justify-end gap-3">
              {saveError && <span className="text-xs text-red-500">{saveError}</span>}
              {savedTick && <span className="text-xs text-green-600">已保存</span>}
              <button
                onClick={handleSaveVoice}
                disabled={saving || !dirty}
                className="px-4 py-2 rounded-lg bg-primary-500 text-white text-xs font-medium hover:bg-primary-600 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                {saving ? '保存中…' : '保存语音设置'}
              </button>
            </div>
          </div>
        </Section>
      )}

      {!dirty && !savedTick && !saveError && (
        <p className="text-center text-xs text-gray-400">模型选择实时生效于预览，点击「保存语音设置」落盘</p>
      )}
    </div>
  )
}

// ═══ Tab: Message ═══

function MessageTab({ character }: { character: RoleSettingsCharacter }) {
  const [threshold, setThreshold] = useState(2.0)
  const [dailyLimit, setDailyLimit] = useState(8)
  const [minInterval, setMinInterval] = useState(30)
  const [cooldown, setCooldown] = useState(15)
  const [quietStart, setQuietStart] = useState(23)
  const [quietEnd, setQuietEnd] = useState(7)
  // 对话内追问：回复后对方没接话时自动再补一句（2026-09-19 新增，可调）
  const [fuEnabled, setFuEnabled] = useState(true)
  const [fuDelay1, setFuDelay1] = useState(45)
  const [fuDelay2, setFuDelay2] = useState(150)
  const [fuDailyMax, setFuDailyMax] = useState(12)
  // 回复模式：沉浸式真人聊天 / 小说式（带动作神态）
  const [replyMode, setReplyMode] = useState<'immersive' | 'novel'>('immersive')
  // LLM 主动决策（web 可调：人设/画像/风格注入 LLM，非硬编码日程）
  const [llmEnabled, setLlmEnabled] = useState(true)
  const [llmStyle, setLlmStyle] = useState('')
  const [llmIntensity, setLlmIntensity] = useState<'low' | 'normal' | 'high'>('normal')
  const [llmRespectQuiet, setLlmRespectQuiet] = useState(true)
  const [llmCharHint, setLlmCharHint] = useState('')
  // Agent Plane 回放
  const [replayKey, setReplayKey] = useState('')
  const [replayTurn, setReplayTurn] = useState('')
  const [replayOut, setReplayOut] = useState<string>('')
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
          setQuietStart(cfgRes.data.quiet_hours_start ?? 23)
          setQuietEnd(cfgRes.data.quiet_hours_end ?? 7)
          setFuEnabled(cfgRes.data.follow_up?.enabled ?? true)
          setFuDelay1(cfgRes.data.follow_up?.delay1_seconds ?? 45)
          setFuDelay2(cfgRes.data.follow_up?.delay2_seconds ?? 150)
          setFuDailyMax(cfgRes.data.follow_up?.daily_max ?? 12)
          setReplyMode(cfgRes.data.reply_mode ?? 'immersive')
          setPaused(!!cfgRes.data.paused)
          const lp = cfgRes.data.llm_proactive || {}
          setLlmEnabled(lp.enabled !== false)
          setLlmStyle(lp.style_hint || '')
          setLlmIntensity((lp.intensity as 'low' | 'normal' | 'high') || 'normal')
          setLlmRespectQuiet(lp.respect_quiet_hours !== false)
          setLlmCharHint(lp.character_hint || '')
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
        quiet_hours_start: quietStart, quiet_hours_end: quietEnd,
        follow_up_enabled: fuEnabled,
        follow_up_delay1_seconds: fuDelay1,
        follow_up_delay2_seconds: fuDelay2,
        follow_up_daily_max: fuDailyMax,
        reply_mode: replyMode,
        llm_proactive_enabled: llmEnabled,
        llm_proactive_style_hint: llmStyle,
        llm_proactive_intensity: llmIntensity,
        llm_proactive_respect_quiet: llmRespectQuiet,
        llm_proactive_character_hint: llmCharHint,
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

  async function handleReplay() {
    try {
      const { agentPlaneReplay } = await import('../../api/system')
      const res = await agentPlaneReplay(replayKey.trim(), replayTurn.trim())
      setReplayOut(JSON.stringify(res.data, null, 2).slice(0, 4000))
    } catch (e) {
      setReplayOut(e instanceof Error ? e.message : '回放失败')
    }
  }

  async function handleCurate() {
    try {
      const { agentPlaneCurate } = await import('../../api/system')
      const res = await agentPlaneCurate(replayKey.trim() || undefined)
      setReplayOut(JSON.stringify(res.data, null, 2).slice(0, 4000))
    } catch (e) {
      setReplayOut(e instanceof Error ? e.message : '整理失败')
    }
  }

  return (
    <div className="space-y-4">
      {/* LLM 主动决策 web 可调 */}
      <Section title="LLM 主动决策（人设·画像·控制台）">
        <p className="text-xs text-gray-500 mb-2">时机与文案由 LLM 综合判断；此处参数注入决策提示词，可动态调整。</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={llmEnabled} onChange={e => setLlmEnabled(e.target.checked)} />
            启用 LLM 主动决策
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={llmRespectQuiet} onChange={e => setLlmRespectQuiet(e.target.checked)} />
            免打扰时段写入 prompt（由 LLM 遵守）
          </label>
          <div>
            <p className="text-xs text-gray-500 mb-1">力度提示</p>
            <select className="w-full border rounded-lg px-2 py-1.5 text-sm" value={llmIntensity} onChange={e => setLlmIntensity(e.target.value as 'low' | 'normal' | 'high')}>
              <option value="low">低（克制）</option>
              <option value="normal">中（自然）</option>
              <option value="high">高（更主动）</option>
            </select>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">风格提示（进 LLM）</p>
            <input className="w-full border rounded-lg px-2 py-1.5 text-sm" value={llmStyle} onChange={e => setLlmStyle(e.target.value)} placeholder="例如：温柔、少用感叹号" maxLength={200} />
          </div>
          <div className="sm:col-span-2">
            <p className="text-xs text-gray-500 mb-1">角色补充提示</p>
            <input className="w-full border rounded-lg px-2 py-1.5 text-sm" value={llmCharHint} onChange={e => setLlmCharHint(e.target.value)} placeholder="可选，覆盖/补充角色人设口吻" maxLength={200} />
          </div>
        </div>
      </Section>

      {/* 因果回放 + curator */}
      <Section title="状态账本 · 因果回放 / 记忆整理">
        <div className="grid gap-2 sm:grid-cols-3">
          <input className="border rounded-lg px-2 py-1.5 text-sm" placeholder="session_key 如 N:wxid" value={replayKey} onChange={e => setReplayKey(e.target.value)} />
          <input className="border rounded-lg px-2 py-1.5 text-sm" placeholder="turn_id（回放用）" value={replayTurn} onChange={e => setReplayTurn(e.target.value)} />
          <div className="flex gap-2">
            <button className="flex-1 rounded-lg bg-sky-500 text-white text-sm py-1.5" onClick={handleReplay}>回放</button>
            <button className="flex-1 rounded-lg bg-emerald-500 text-white text-sm py-1.5" onClick={handleCurate}>整理记忆</button>
          </div>
        </div>
        {replayOut ? <pre className="mt-2 text-xs bg-gray-50 rounded-lg p-2 overflow-auto max-h-64 whitespace-pre-wrap">{replayOut}</pre> : null}
      </Section>
      {/* Stats（真数据：/api/proactive/history + config） */}
      <Section title="消息统计">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
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
          {/* 回复模式：两种完全不同的回复逻辑 */}
          <div>
            <div className="text-xs text-gray-600 mb-1">回复模式</div>
            <div className="text-[11px] text-gray-400 mb-2">
              沉浸式＝像真人发微信（不写动作神态）；小说式＝带动作、神态、环境描写
            </div>
            <div className="flex gap-1 p-1 bg-gray-100/60 rounded-xl">
              {([
                { key: 'immersive', label: '沉浸式聊天', hint: '像真人发微信' },
                { key: 'novel', label: '小说式', hint: '带动作神态' },
              ] as const).map(m => (
                <button
                  key={m.key}
                  type="button"
                  onClick={() => setReplyMode(m.key)}
                  className={`flex-1 rounded-lg px-3 py-1.5 text-xs transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/50 ${
                    replyMode === m.key
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
          <div className="flex items-center gap-4">
            <div className="w-24 shrink-0"><span className="text-xs text-gray-600">紧迫阈值</span></div>
            <div className="flex-1"><Slider value={threshold} min={0} max={10} step={0.5} label="紧迫阈值" showLabel={false} showValue={false} onChange={setThreshold} /></div>
            <span className="w-16 text-right text-xs font-mono text-gray-400">{threshold.toFixed(1)}</span>
          </div>
          {[
            { label: '每日上限', value: dailyLimit, min: 1, max: 50, unit: '条/天', onChange: setDailyLimit },
            { label: '最小间隔', value: minInterval, min: 5, max: 120, unit: '分钟', onChange: setMinInterval },
            { label: '冷却时间', value: cooldown, min: 5, max: 240, unit: '分钟', onChange: setCooldown },
            { label: '免打扰开始', value: quietStart, min: 0, max: 23, unit: '点', onChange: setQuietStart },
            { label: '免打扰结束', value: quietEnd, min: 0, max: 23, unit: '点', onChange: setQuietEnd },
          ].map(s => (
            <div key={s.label} className="flex items-center gap-4">
              <div className="w-24 shrink-0"><span className="text-xs text-gray-600">{s.label}</span></div>
              <div className="flex-1"><Slider value={s.value} min={s.min} max={s.max} step={1} label={s.label} showLabel={false} showValue={false} onChange={s.onChange} /></div>
              <span className="w-16 text-right text-xs font-mono text-gray-400">{s.value} {s.unit}</span>
            </div>
          ))}
          {/* 对话内追问：回复后对方没接话，自动再补一句（最多 2 次） */}
          <div className="pt-3 mt-1 border-t border-gray-100">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs text-gray-600">对话内追问</div>
                <div className="text-[11px] text-gray-400">回复后对方没接话时自动再补一句（最多 2 次）</div>
              </div>
              <Toggle checked={fuEnabled} onChange={setFuEnabled} />
            </div>
          </div>
          <div className={`space-y-4 ${fuEnabled ? '' : 'opacity-40 pointer-events-none'}`}>
            {[
              { label: '首次延迟', value: fuDelay1, min: 10, max: 600, unit: '秒', onChange: setFuDelay1 },
              { label: '二次延迟', value: fuDelay2, min: 10, max: 1200, unit: '秒', onChange: setFuDelay2 },
              { label: '追问上限', value: fuDailyMax, min: 0, max: 50, unit: '条/天', onChange: setFuDailyMax },
            ].map(s => (
              <div key={s.label} className="flex items-center gap-4">
                <div className="w-24 shrink-0"><span className="text-xs text-gray-600">{s.label}</span></div>
                <div className="flex-1"><Slider value={s.value} min={s.min} max={s.max} step={1} label={s.label} showLabel={false} showValue={false} onChange={s.onChange} /></div>
                <span className="w-16 text-right text-xs font-mono text-gray-400">{s.value} {s.unit}</span>
              </div>
            ))}
          </div>
          <div className="flex justify-end">
            <button
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors shadow-sm disabled:opacity-50"
            >
              {saving ? '保存中…' : savedAt ? `已保存（${savedAt}）` : '保存频率配置'}
            </button>
          </div>
        </div>
      </Section>

      {/* Manual send（08-28 新增：手动控制） */}
      <Section title="手动控制">
        <div className="flex justify-end">
          <button
            onClick={handleSendNow}
            disabled={sending}
            className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-medium text-white bg-primary-500 hover:bg-primary-400 rounded-lg transition-colors shadow-sm disabled:opacity-50"
          >
            {sending ? '生成发送中…' : '立即发送一条主动消息'}
          </button>
        </div>
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
                <span className="text-[10px] text-gray-400 shrink-0">{new Date(h.at).toLocaleTimeString('zh-CN')}</span>
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
  const navigate = useNavigate()
  const deleteMutation = useDeleteCharacter()
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
  // 知识库定期采集（Vault collect）开关 — 2026-09-17 web 控制端接入
  const [collectEnabled, setCollectEnabled] = useState(false)
  const [collectInterval, setCollectInterval] = useState(60)
  const [collectSaving, setCollectSaving] = useState(false)
  const [collectAvailable, setCollectAvailable] = useState(true)

  useEffect(() => {
    let alive = true
    knowledgeCollectConfig().then((res) => {
      if (!alive) return
      setCollectEnabled(!!res.data?.enabled)
      setCollectInterval(res.data?.interval_minutes ?? 60)
      setCollectAvailable(res.data?.available !== false)
    }).catch(() => setCollectAvailable(false))
    return () => { alive = false }
  }, [])

  const handleCollectToggle = async (next: boolean) => {
    setCollectSaving(true)
    try {
      const res = await updateKnowledgeCollectConfig({ enabled: next, interval_minutes: collectInterval })
      setCollectEnabled(res.data?.config?.enabled ?? next)
      useErrorStore.getState().addToast({ type: 'success', message: next ? '知识库定期采集已开启' : '知识库定期采集已关闭' })
    } catch (e) {
      useErrorStore.getState().addToast({ type: 'error', message: e instanceof Error ? e.message : '操作失败' })
    } finally {
      setCollectSaving(false)
    }
  }

  const handleCollectIntervalSave = async () => {
    setCollectSaving(true)
    try {
      await updateKnowledgeCollectConfig({ enabled: collectEnabled, interval_minutes: collectInterval })
      useErrorStore.getState().addToast({ type: 'success', message: `采集间隔已保存：${collectInterval} 分钟` })
    } catch (e) {
      useErrorStore.getState().addToast({ type: 'error', message: e instanceof Error ? e.message : '保存失败' })
    } finally {
      setCollectSaving(false)
    }
  }

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
        <ExportRow characterId={character.id} characterName={character.name} />
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
              className="px-4 py-2 text-sm font-semibold text-text-primary rounded-lg btn-macaron disabled:opacity-50 disabled:cursor-not-allowed transition-all flex items-center gap-2"
            >
              {enriching ? (
                <>
                  <span className="h-3 w-3 animate-spin rounded-full border-2 border-text-primary border-t-transparent" />
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

      {/* RAG（SP-4 + GAP-4 修复，2026-09-01）：真实知识库统计 + 检索测试，替换假数据占位 */}
      <Section title="知识库引擎 (RAG)">
        <p className="text-xs text-gray-400 mb-1">
          对话时按相关度检索角色知识库（BM25）；来源含角色卡字段、文档导入、vault 与网络爬取。
        </p>
        <KnowledgePreview characterId={character.id} />
        {collectAvailable && (
          <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-white/40 bg-white/30 px-3 py-2">
            <div className="min-w-0">
              <p className="text-xs font-medium text-gray-700">定期采集（Vault）</p>
              <p className="text-[11px] text-gray-400">周期性对角色库全部角色重建知识索引，防卡更新后索引陈旧</p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <input
                type="number"
                min={10}
                max={1440}
                value={collectInterval}
                onChange={(e) => setCollectInterval(Number(e.target.value) || 60)}
                onBlur={handleCollectIntervalSave}
                disabled={collectSaving}
                className="w-16 rounded-lg border border-gray-200 px-2 py-1 text-xs"
                aria-label="采集间隔（分钟）"
              />
              <span className="text-[11px] text-gray-400">分钟</span>
              <Toggle checked={collectEnabled} onChange={handleCollectToggle} />
            </div>
          </div>
        )}
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

      <ConfirmDialog
        open={showDelete}
        title="确认删除角色"
        message={`确定要删除「${sanitizeCharacterName(character.name)}」吗？此操作不可恢复。`}
        confirmText={deleteMutation.isPending ? '删除中…' : '确认删除'}
        cancelText="取消"
        variant="danger"
        onConfirm={() => {
          deleteMutation.mutate(character.id, {
            onSuccess: () => {
              useErrorStore.getState().addToast({ type: 'success', message: `角色「${sanitizeCharacterName(character.name)}」已删除` })
              navigate('/roles', { replace: true })
            },
            onError: (err: unknown) => {
              setShowDelete(false)
              const msg = err instanceof Error ? err.message : '删除失败，请重试'
              useErrorStore.getState().addToast({ type: 'error', message: msg })
            },
          })
        }}
        onCancel={() => setShowDelete(false)}
      />
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
    case 'timeline': return <TimelineTab character={character} />
  }
}

export default ActiveTab

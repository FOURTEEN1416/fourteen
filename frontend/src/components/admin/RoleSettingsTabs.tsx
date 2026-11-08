import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import client from '../../api/client'
import { queryKeys, useVoiceStatus, useDeleteCharacter } from '../../hooks/useQueries'
import { updateCharacter } from '../../api/characters'
import { playAudioBlob } from '../../api/mimo'
import { useErrorStore } from '../../store/errorStore'
import { sanitizeCharacterName } from '../../utils/character'
import Slider from '../shared/Slider'
import { Select } from '../shared'
import TagInput from '../shared/TagInput'
import Toggle from '../shared/Toggle'
import ConfirmDialog from '../shared/ConfirmDialog'
import type { RoleSettingsTab, RoleSettingsCharacter } from '../../types/framework'
import type { UnifiedCharacterUpdate } from '../../types/api'
import { Save, Trash2, Copy, Flame, CircleCheck, CircleAlert, Sun, Theater, Bird, HeartCrack, BrickWall } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { ENGINE_OPTIONS, MIMO_MODELS } from './RoleSettingsConstants'
import { PERSONALITY_LABELS, SPEAKING_STYLE_LABELS, normalizePersonality, normalizeSpeakingStyle } from '../../constants/persona'
import { enrichCharacter, knowledgeCollectConfig, updateKnowledgeCollectConfig } from '../../api/system'
import { useAuthStore } from '../../store/authStore'
import Section from './RoleSettingsSection'
import KnowledgePreview from '../storyline/KnowledgePreview'
import CharacterSettingsPanel from '../roles/CharacterSettingsPanel'

// ═══ 性格特质图标（NEW-32：全站图标统一 lucide，不再混用 emoji） ═══

const PERSONALITY_ICONS: Record<string, LucideIcon> = {
  warmth: Sun,
  playfulness: Theater,
  independence: Bird,
  jealousy: HeartCrack,
  stubbornness: BrickWall,
}

// ═══ 统计小卡（NEW-32）已随 2026-11-08 D-B 整机删除的运维面一并移除：
// 其唯一消费者是「消息统计」卡（今日主动/最后发送），该卡所在页面已不存在。

// ═══ Tab: Basic ═══

function BasicTab({ character }: { character: RoleSettingsCharacter }) {
  const qc = useQueryClient()
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
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
      const res = await updateCharacter(character.id, payload)
      // W11-D5：以回执判业务成功（契约 {"status":"updated"}），HTTP 200 不足为凭
      if (res?.status !== 'updated') {
        useErrorStore.getState().addToast({ type: 'warning', message: '保存未生效（后端未确认），请重试' })
        return
      }
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
            const Icon = PERSONALITY_ICONS[key]
            return (
              <div key={key} className="flex items-center gap-4">
                <div className="w-20 shrink-0">
                  <span className="text-xs text-gray-600 inline-flex items-center gap-1">
                    {Icon ? <Icon className="w-3 h-3 shrink-0" aria-hidden /> : null}
                    {PERSONALITY_LABELS[key] || key}
                  </span>
                </div>
                <div className="flex-1">
                  <Slider value={val} min={0} max={1} step={0.01} label={PERSONALITY_LABELS[key] || key} onChange={v => setPersonality((prev: Record<string, number>) => ({ ...prev, [key]: v }))} />
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

      {/* W11-D2：重要日期写入端点 PUT /characters/{id}/important-dates 是
          admin-only（misc_routes require_role("admin")）——普通用户不渲染入口，
          避免点了必 403 的假按钮。 */}
      {isAdmin && <ImportantDatesSection characterId={character.id} />}
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
      const res = await client.put(`/characters/${characterId}/important-dates`, { dates })
      // W11-D5：契约 {"status":"saved","count":N} —— 非 saved 不谎报成功
      const receipt = res.data as { status?: string } | undefined
      if (receipt?.status !== 'saved') {
        useErrorStore.getState().addToast({ type: 'warning', message: '重要日期未保存（后端未确认）' })
        return
      }
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

interface SpeakerOption {
  name: string
  display_name?: string
  kind?: string
  description?: string
}

function VoiceTab({ character }: { character: RoleSettingsCharacter }) {
  const qc = useQueryClient()
  const [engine] = useState(character.voice_config?.engine || 'mimo-tts')
  // voice_config 是 VoiceConfig | 自定义对象 联合类型；W7 显式契约字段需运行时安全访问
  const cfg = (character.voice_config ?? {}) as Record<string, unknown>
  const mimoModelInitial = (cfg.mimo_model as string | undefined) || 'mimo-v2.5-tts'
  const [mimoModel, setMimoModel] = useState(mimoModelInitial)
  // W7：voice_id（克隆/设计音色 ID）显式契约字段；回退旧 speaker_name（预设音色）
  const voiceIdInitial = (cfg.voice_id as string | undefined) || (cfg.speaker_name as string | undefined) || ''
  const [voiceId, setVoiceId] = useState(voiceIdInitial)
  const speedInitial = Number(cfg.speed as number | undefined ?? 1.0)
  const pitchInitial = Number(cfg.pitch as number | undefined ?? 1.0)
  const [speed, setSpeed] = useState(speedInitial)
  const [pitch, setPitch] = useState(pitchInitial)
  // 音色列表：/voice/speakers = 预设 + catalog 自定义（重启/换 worker 均可找回）
  const [speakers, setSpeakers] = useState<SpeakerOption[]>([])
  // 状态来自 GET /voice/status 真实探测（曾写死 useState('就绪')）
  const { data: voiceStatus, isLoading: voiceStatusLoading } = useVoiceStatus()
  const status = voiceStatusLoading
    ? '检测中…'
    : voiceStatus?.last_error
      ? `异常：${voiceStatus.last_error}`
      : voiceStatus?.enabled
        ? '就绪'
        : '语音引擎未启用'
  // 保存接线（GAP-4 修复，2026-09-01；W7 显式契约字段扩展）
  const [saving, setSaving] = useState(false)
  const [savedTick, setSavedTick] = useState(false)
  const [saveError, setSaveError] = useState('')
  // 试听（W7：真实播放，失败可见）
  const [testing, setTesting] = useState(false)
  const [testError, setTestError] = useState('')

  /* eslint-disable react-hooks/set-state-in-effect -- data fetching */
  useEffect(() => {
    let alive = true
    client.get('/voice/speakers').then((res) => {
      if (!alive) return
      setSpeakers((res.data as { speakers?: SpeakerOption[] })?.speakers ?? [])
    }).catch(() => {
      if (alive) setSpeakers([])
    })
    return () => {
      alive = false
    }
  }, [])
  /* eslint-enable react-hooks/set-state-in-effect */

  const dirty = mimoModel !== mimoModelInitial || voiceId !== voiceIdInitial || speed !== speedInitial || pitch !== pitchInitial

  async function handleSaveVoice() {
    setSaving(true)
    setSaveError('')
    try {
      const res = await client.post(`/characters/${character.id}/voice`, {
        engine: 'mimo-tts',
        speaker_name: (cfg.speaker_name as string | undefined) ?? '',
        mimo_model: mimoModel,
        voice_id: voiceId,
        speed,
        pitch,
      })
      // W11-D5：契约 {"status":"bound"}；非 bound 不显示「已保存」
      const receipt = res.data as { status?: string } | undefined
      if (receipt?.status !== 'bound') {
        setSaveError('音色未生效（后端未确认），请重试')
        return
      }
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

  async function handleTestVoice() {
    setTesting(true)
    setTestError('')
    try {
      // 试听按「已保存」的角色契约合成；响应为音频 Blob，按实际 MIME 播放
      const res = await client.post(
        `/characters/${character.id}/voice/test`,
        { text: '嗨，这是我现在的声音，听得到吗？' },
        { responseType: 'blob' },
      )
      await playAudioBlob(res.data as Blob)
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setTestError(detail || '试听失败：请确认语音服务已启用且角色已保存音色设置')
    } finally {
      setTesting(false)
    }
  }

  const speakerOptions = speakers.map(s => ({
    value: s.name,
    label: `${s.kind === 'clone' ? '[克隆] ' : s.kind === 'design' ? '[设计] ' : ''}${s.display_name || s.name}`,
  }))

  return (
    <div className="space-y-4">
      {/* Engine Picker */}
      <Section title="语音引擎">
        <div className="grid grid-cols-1 gap-2">
          {ENGINE_OPTIONS.map(opt => (
            <div
              key={opt.value}
              className={`text-left p-3 rounded-xl transition-all glass-yellow ring-1 ring-primary-400/30`}
            >
              <p className="text-sm font-medium text-primary-700">{opt.label}</p>
              <p className="text-[11px] text-gray-400 mt-0.5">{opt.desc}</p>
            </div>
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

            {/* W7：音色绑定（预设 + 克隆/设计 catalog；保存后真实对话按此合成） */}
            <div>
              <label className="mb-1 block text-xs text-gray-500">音色（预设或克隆/设计音色）</label>
              <Select
                options={speakerOptions}
                value={voiceId}
                onChange={setVoiceId}
                placeholder={speakers.length ? '选择音色' : '音色列表加载中…'}
              />
              <p className="text-[10px] text-gray-400 mt-1">
                新克隆/设计音色在「系统设置 → 语音工作台」创建后即可在此选择
              </p>
            </div>

            {(mimoModel === 'mimo-v2.5-tts-voiceclone' || mimoModel === 'mimo-v2.5-tts-voicedesign') && (
              <div className="rounded-xl bg-amber-50/50 border border-amber-100 p-4 text-center">
                <p className="text-sm text-amber-700">
                  {mimoModel === 'mimo-v2.5-tts-voiceclone' ? '克隆音色' : '设计音色'}的创建在「系统设置 → 语音工作台」
                </p>
                <p className="text-xs text-amber-500 mt-1">此处选择要应用于该角色的模型形态与音色</p>
              </div>
            )}

            {/* W7：语速/音高数值契约（随合成请求逐次传递，不影响其他角色） */}
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Slider label="语速" value={speed} min={0.5} max={2.0} step={0.05} onChange={setSpeed} />
              <Slider label="音高" value={pitch} min={0.5} max={2.0} step={0.05} onChange={setPitch} />
            </div>

            <div className="text-xs text-gray-400">状态: {status}</div>

            {testError && <p className="text-xs text-red-500">{testError}</p>}

            <div className="flex items-center justify-end gap-3">
              {saveError && <span className="text-xs text-red-500">{saveError}</span>}
              {savedTick && <span className="text-xs text-green-600">已保存</span>}
              <button
                onClick={handleTestVoice}
                disabled={testing}
                className="px-4 py-2 rounded-lg border border-primary-300 text-primary-600 text-xs font-medium hover:bg-primary-50 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                {testing ? '合成中…' : '试听当前设置'}
              </button>
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
        <p className="text-center text-xs text-gray-400">试听按「已保存」的设置合成；改动后需点击「保存语音设置」落盘</p>
      )}
    </div>
  )
}

// ═══ Tab: Message / 运维面 ═══
//
// 2026-11-08 D-B 用户裁决：/settings/proactive 整机删除（页面 + 路由 + 侧栏入口 +
// RoleGuard 条目 + 本文件内原管理员运维组件全部内容）。全局主动急停与出厂默认值
// 不再有任何 UI，只保留既有后端 API 与代码常量。角色设置页「消息」tab 只渲染
// 「我 × 角色」设置面板（CharacterSettingsPanel），不承载任何全局运维内容。

// ═══ Tab: Message（P5 重构：用户×角色面板） ═══
function MessageTab({ character }: { character: RoleSettingsCharacter }) {
  return <CharacterSettingsPanel characterId={character.id} characterName={character.name} />
}

function DataTab({ character }: { character: RoleSettingsCharacter }) {
  const navigate = useNavigate()
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
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
      // W11-D5：契约 {"status":"ok","config":{…}} —— 非 ok 不谎报开关已生效
      const receipt = res.data as { status?: string; config?: { enabled?: boolean } } | undefined
      if (receipt?.status !== 'ok') {
        useErrorStore.getState().addToast({ type: 'warning', message: '采集开关未生效（后端未确认）' })
        return
      }
      setCollectEnabled(receipt.config?.enabled ?? next)
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
      const res = await updateKnowledgeCollectConfig({ enabled: collectEnabled, interval_minutes: collectInterval })
      const receipt = res.data as { status?: string } | undefined
      if (receipt?.status !== 'ok') {
        useErrorStore.getState().addToast({ type: 'warning', message: '采集间隔未生效（后端未确认）' })
        return
      }
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
      {/* 数据导出（2026-10-29 NEW-19：原「数据概览」三卡读 character.stats，
          后端从不返回 → 恒显 0/0/—，按裁决删卡止血；真实统计另立功能批） */}
      <Section title="数据导出">
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
                <>
                  <Flame className="w-3.5 h-3.5" aria-hidden />
                  开始网络增强
                </>
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
              <div className="font-medium inline-flex items-center gap-1">
                {enrichResult.status === 'enriched' ? (
                  <>
                    <CircleCheck className="w-3.5 h-3.5 shrink-0" aria-hidden />
                    增强成功
                  </>
                ) : (
                  <>
                    <CircleAlert className="w-3.5 h-3.5 shrink-0" aria-hidden />
                    未写入新知识块
                  </>
                )}
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
        {collectAvailable && isAdmin && (
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
            onSuccess: (res: { status?: string } | undefined) => {
              // W11-D5：契约 {"status":"deleted"}；非 deleted 不得谎报「已删除」
              if (res?.status !== 'deleted') {
                setShowDelete(false)
                useErrorStore.getState().addToast({ type: 'warning', message: '删除未生效（后端未确认），请重试' })
                return
              }
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

// ═══ Tab Router ═══
// P9（2026-11-07）：TimelineTab 删除——它本就只是 StorylineEditor 的外壳，
// 时间线已并入独立剧情线页 /roles/:id/storyline（旧链接重定向）。

function ActiveTab({ tab, character }: { tab: RoleSettingsTab; character: RoleSettingsCharacter }) {
  switch (tab) {
    case 'basic': return <BasicTab character={character} />
    case 'voice': return <VoiceTab character={character} />
    case 'message': return <MessageTab character={character} />
    case 'data': return <DataTab character={character} />
  }
}

export default ActiveTab

import { useState, useEffect, useMemo, useRef } from 'react'
import { api } from '../api/client'
import SensitiveInput from '../components/common/SensitiveInput'
import Button from '../components/common/Button'
import { isSensitiveField } from '../types/api'

interface Setting {
  id: string; label: string
  type: 'toggle' | 'select' | 'range'
  value: unknown
  options?: { value: string; label: string }[]
  min?: number; max?: number; step?: number
}

const MODEL_OPTIONS: Record<string, { value: string; label: string }[]> = {
  deepseek: [
    { value: 'deepseek-chat', label: 'DeepSeek Chat' },
    { value: 'deepseek-reasoner', label: 'DeepSeek Reasoner' },
  ],
  opencode_zen: [
    { value: 'big-pickle', label: 'Big Pickle' },
    { value: 'nemotron-3-super-free', label: 'Nemotron 3 Super Free' },
    { value: 'qwen3.6-plus-free', label: 'Qwen 3.6 Plus Free' },
    { value: 'deepseek-v4-flash-free', label: 'DeepSeek V4 Flash Free' },
    { value: 'minimax-m2.5-free', label: 'MiniMax M2.5 Free' },
  ],
}

const PROVIDER_OPTIONS = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'opencode_zen', label: 'OpenCode Zen（免费）' },
]

const sections: { id: string; label: string; items: Setting[] }[] = [
  { id: 'chat', label: '聊天', items: [
    { id: 'auto_reply', label: '自动回复', type: 'toggle', value: true },
    { id: 'reply_delay', label: '回复延迟(秒)', type: 'range', value: 1.5, min: 0, max: 5, step: 0.5 },
    { id: 'max_context', label: '记忆上下文条数', type: 'range', value: 20, min: 5, max: 50, step: 5 },
  ]},
  { id: 'voice', label: '语音', items: [
    { id: 'voice_input', label: '语音输入', type: 'toggle', value: false },
    { id: 'voice_output', label: '语音输出', type: 'toggle', value: false },
    { id: 'speaker', label: '音色', type: 'select', value: 'friendly',
      options: [{ value: 'friendly', label: '亲切女声' }, { value: 'gentle', label: '温柔女声' }, { value: 'cute', label: '甜美少女' }] },
  ]},
  { id: 'personality', label: '人设', items: [
    { id: 'top_p', label: '采样阈值', type: 'range', value: 0.9, min: 0.1, max: 1.0, step: 0.05 },
    { id: 'style', label: '回复风格', type: 'select', value: 'warm',
      options: [{ value: 'warm', label: '温暖贴心' }, { value: 'playful', label: '活泼调皮' }, { value: 'intellectual', label: '知性优雅' }] },
  ]},
  { id: 'model', label: '模型', items: [
    { id: 'provider', label: 'LLM 提供商', type: 'select', value: 'deepseek',
      options: PROVIDER_OPTIONS },
    { id: 'model_name', label: '主模型', type: 'select', value: 'deepseek-chat', options: MODEL_OPTIONS.deepseek },
    { id: 'fallback_model', label: '备用模型', type: 'select', value: 'deepseek-reasoner', options: MODEL_OPTIONS.deepseek },
    { id: 'temperature', label: '温度', type: 'range', value: 0.85, min: 0.0, max: 2.0, step: 0.05 },
    { id: 'max_tokens', label: '最大回复长度', type: 'range', value: 2048, min: 64, max: 4096, step: 64 },
    { id: 'api_base', label: 'API 地址', type: 'select', value: 'https://api.deepseek.com/v1',
      options: [
        { value: 'https://api.deepseek.com/v1', label: 'DeepSeek 官方' },
        { value: 'https://api.opencode.ai/zen/v1', label: 'OpenCode Zen' },
      ] },
    { id: 'api_key', label: 'API Key', type: 'select', value: '' },
  ]},
  { id: 'notifications', label: '通知', items: [
    { id: 'sound', label: '消息提示音', type: 'toggle', value: true },
    { id: 'desktop_notify', label: '桌面通知', type: 'toggle', value: true },
  ]},
  { id: 'privacy', label: '隐私', items: [
    { id: 'store_history', label: '保存聊天记录', type: 'toggle', value: true },
  ]},
]

export default function SettingsPage() {
  const [tab, setTab] = useState(sections[0].id)
  const [vals, setVals] = useState<Record<string, unknown>>(() => {
    const r: Record<string, unknown> = {}
    sections.forEach(s => s.items.forEach(i => { r[i.id] = i.value }))
    return r
  })
  const [saved, setSaved] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [configDiff, setConfigDiff] = useState<string[]>([])
  const configBackupRef = useRef<Record<string, unknown> | null>(null)

  const section = sections.find(s => s.id === tab)!

  // Compute model options based on current provider
  const currentProvider = (vals.provider as string) || 'deepseek'
  const modelOptions = useMemo(() => MODEL_OPTIONS[currentProvider] || MODEL_OPTIONS.deepseek, [currentProvider])

  // Build the items for the current section with dynamic options
  const sectionItems = useMemo(() => {
    if (section.id !== 'model') return section.items
    return section.items.map(item => {
      if (item.id === 'model_name' || item.id === 'fallback_model') {
        return { ...item, options: modelOptions }
      }
      if (item.id === 'api_base') {
        return {
          ...item,
          options: currentProvider === 'opencode_zen'
            ? [{ value: 'https://api.opencode.ai/zen/v1', label: 'OpenCode Zen' }]
            : [
                { value: 'https://api.deepseek.com/v1', label: 'DeepSeek 官方' },
                { value: 'https://api.opencode.ai/zen/v1', label: 'OpenCode Zen' },
              ],
        }
      }
      return item
    })
  }, [section, modelOptions, currentProvider])

  // Load config from backend on mount
  useEffect(() => {
    api.config().then(({ data }) => {
      const config = data as Record<string, unknown>
      if (config && Object.keys(config).length > 0) {
        setVals(prev => ({ ...prev, ...mapConfigToSettings(config) }))
      }
    }).catch(() => {})
  }, [])

  const set = (id: string, v: unknown) => {
    setVals(p => ({ ...p, [id]: v }))
    setSaved(false)
  }

  const handleSave = async () => {
    configBackupRef.current = structuredClone(vals)
    const diff: string[] = []
    sections.forEach(s => s.items.forEach(item => {
      const oldVal = (configBackupRef.current as Record<string, unknown> | undefined)?.[item.id]
      if (oldVal !== vals[item.id]) {
        diff.push(`${item.label}: ${String(oldVal)} → ${String(vals[item.id])}`)
      }
    }))
    setConfigDiff(diff)
    setShowConfirm(true)
  }

  const handleConfirmSave = async () => {
    setShowConfirm(false)
    try {
      const configPayload = buildConfigPayload(vals)
      await api.saveConfig(configPayload)
      setSaved(true)
      configBackupRef.current = null
      setTimeout(() => setSaved(false), 2000)
    } catch {
      if (configBackupRef.current) {
        setVals(configBackupRef.current)
        configBackupRef.current = null
      }
    }
  }

  const handleCancelSave = () => {
    setShowConfirm(false)
    configBackupRef.current = null
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-gray-800 mb-6">设置</h1>

      <div className="flex gap-6">
        <nav className="hidden lg:flex flex-col gap-0.5 w-28 shrink-0">
          {sections.map(s => (
            <button key={s.id} onClick={() => setTab(s.id)}
              className={`text-xs px-3 py-1.5 rounded-lg text-left transition-colors ${
                tab === s.id ? 'bg-primary-600/15 text-primary-200' : 'text-gray-400 hover:text-gray-700'
              }`}>
              {s.label}
            </button>
          ))}
        </nav>

        <div className="flex-1 max-w-lg">
          <select value={tab} onChange={e => setTab(e.target.value)}
            className="lg:hidden w-full mb-4 bg-gray-200 border border-gray-300 text-gray-800 rounded-lg px-3 py-2 text-sm outline-none">
            {sections.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>

          <div className="space-y-1">
            {sectionItems.map(item => (
              <div key={item.id} className="flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-gray-50">
                <span className="text-sm text-gray-700">{item.label}</span>
                {isSensitiveField(item.id) ? (
                  <div className="w-48">
                    <SensitiveInput
                      value={(vals[item.id] as string) ?? ''}
                      onChange={(v) => set(item.id, v)}
                      placeholder="输入..."
                    />
                  </div>
                ) : item.type === 'toggle' ? (
                  <button onClick={() => set(item.id, !vals[item.id])}
                    className={`w-9 h-5 rounded-full transition-colors ${
                      vals[item.id] ? 'bg-primary-500' : 'bg-gray-200'
                    }`}>
                    <div className={`w-4 h-4 bg-white rounded-full transition-transform ${
                      vals[item.id] ? 'translate-x-4.5' : 'translate-x-0.5'
                    }`} />
                  </button>
                ) : item.type === 'select' ? (
                  <select value={vals[item.id] as string} onChange={e => set(item.id, e.target.value)}
                    className="bg-gray-200 border border-gray-300 text-gray-800 rounded-lg px-2 py-1 text-xs outline-none max-w-48">
                    {item.options?.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                ) : item.type === 'range' ? (
                  <div className="flex items-center gap-2">
                    <input type="range" min={item.min} max={item.max} step={item.step}
                      value={vals[item.id] as number} onChange={e => set(item.id, parseFloat(e.target.value))}
                      className="w-20 h-1 bg-gray-200 rounded-full appearance-none cursor-pointer" />
                    <span className="text-xs text-gray-400 w-8 text-right">{vals[item.id] as number}</span>
                  </div>
                ) : null}
              </div>
            ))}
          </div>

          <div className="flex items-center gap-3 mt-4">
            <button onClick={handleSave}
              className="px-5 py-2 bg-primary-600 hover:bg-primary-500 text-white rounded-lg text-sm transition-colors">
              保存
            </button>
            {saved && (
              <span className="text-xs text-green-400">已保存</span>
            )}
          </div>

          {showConfirm && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={handleCancelSave}>
              <div className="bg-white rounded-xl shadow-xl p-5 max-w-md w-full mx-4" onClick={e => e.stopPropagation()}>
                <h3 className="text-sm font-semibold text-gray-800 mb-3">确认保存配置</h3>
                {configDiff.length > 0 && (
                  <div className="mb-4 max-h-48 overflow-y-auto space-y-1">
                    {configDiff.map((d, i) => (
                      <div key={i} className="text-xs text-gray-600 bg-gray-50 rounded px-2 py-1">{d}</div>
                    ))}
                  </div>
                )}
                <div className="flex gap-2 justify-end">
                  <Button variant="secondary" size="sm" onClick={handleCancelSave}>取消</Button>
                  <Button size="sm" onClick={handleConfirmSave}>确认保存</Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// Map nested backend SystemConfig to flat setting IDs
function mapConfigToSettings(config: Record<string, unknown>): Record<string, unknown> {
  const mapped: Record<string, unknown> = {}
  if (config.llm) {
    const llm = config.llm as Record<string, unknown>
    if (llm.provider) mapped.provider = llm.provider
    if (llm.primary_model) mapped.model_name = llm.primary_model
    if (llm.fallback_model) mapped.fallback_model = llm.fallback_model
    if (llm.temperature != null) mapped.temperature = llm.temperature
    if (llm.max_tokens != null) mapped.max_tokens = llm.max_tokens
    if (llm.top_p != null) mapped.top_p = llm.top_p
    if (llm.api_base != null) mapped.api_base = llm.api_base
    if (llm.api_key != null) mapped.api_key = llm.api_key
  }
  if (config.memory) {
    const memory = config.memory as Record<string, unknown>
    if (memory.working_memory_limit != null) mapped.max_context = memory.working_memory_limit
  }
  if (config.proactive) {
    const proactive = config.proactive as Record<string, unknown>
    if (proactive.max_daily_messages != null) mapped.auto_reply = (proactive.max_daily_messages as number) > 0
    if (proactive.min_interval_minutes != null) mapped.reply_delay = proactive.min_interval_minutes
  }
  if (config.voice) {
    const voice = config.voice as Record<string, unknown>
    if (voice.voice_input != null) mapped.voice_input = voice.voice_input
    if (voice.voice_output != null) mapped.voice_output = voice.voice_output
    if (voice.speaker != null) mapped.speaker = voice.speaker
  }
  if (config.personality) {
    const personality = config.personality as Record<string, unknown>
    if (personality.style != null) mapped.style = personality.style
  }
  if (config.notifications) {
    const notifications = config.notifications as Record<string, unknown>
    if (notifications.sound != null) mapped.sound = notifications.sound
    if (notifications.desktop_notify != null) mapped.desktop_notify = notifications.desktop_notify
  }
  return mapped
}

// Convert flat setting IDs to nested backend config payload
function buildConfigPayload(vals: Record<string, unknown>): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    llm: {},
    memory: {},
    proactive: {},
    voice: {},
    personality: {},
    notifications: {},
  }
  if (vals.provider) (payload.llm as Record<string, unknown>).provider = vals.provider
  if (vals.model_name) (payload.llm as Record<string, unknown>).primary_model = vals.model_name
  if (vals.fallback_model) (payload.llm as Record<string, unknown>).fallback_model = vals.fallback_model
  if (vals.temperature != null) (payload.llm as Record<string, unknown>).temperature = vals.temperature
  if (vals.max_tokens != null) (payload.llm as Record<string, unknown>).max_tokens = vals.max_tokens
  if (vals.top_p != null) (payload.llm as Record<string, unknown>).top_p = vals.top_p
  if (vals.api_base != null) (payload.llm as Record<string, unknown>).api_base = vals.api_base
  if (vals.api_key != null) (payload.llm as Record<string, unknown>).api_key = vals.api_key
  if (vals.max_context != null) (payload.memory as Record<string, unknown>).working_memory_limit = vals.max_context
  if (vals.auto_reply != null) (payload.proactive as Record<string, unknown>).max_daily_messages = vals.auto_reply ? 8 : 0
  if (vals.reply_delay != null) (payload.proactive as Record<string, unknown>).min_interval_minutes = Math.round(vals.reply_delay as number)
  if (vals.voice_input != null) (payload.voice as Record<string, unknown>).voice_input = vals.voice_input
  if (vals.voice_output != null) (payload.voice as Record<string, unknown>).voice_output = vals.voice_output
  if (vals.speaker != null) (payload.voice as Record<string, unknown>).speaker = vals.speaker
  if (vals.style != null) (payload.personality as Record<string, unknown>).style = vals.style
  if (vals.sound != null) (payload.notifications as Record<string, unknown>).sound = vals.sound
  if (vals.desktop_notify != null) (payload.notifications as Record<string, unknown>).desktop_notify = vals.desktop_notify
  return payload
}

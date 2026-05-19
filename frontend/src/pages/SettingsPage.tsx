import { useState, useEffect } from 'react'
import { api } from '../api/client'
import SensitiveInput from '../components/common/SensitiveInput'
import { isSensitiveField } from '../types/api'

interface Setting {
  id: string; label: string
  type: 'toggle' | 'select' | 'range'
  value: any
  options?: { value: string; label: string }[]
  min?: number; max?: number; step?: number
}

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
    { id: 'temperature', label: '创意温度', type: 'range', value: 0.8, min: 0.1, max: 1.5, step: 0.1 },
    { id: 'top_p', label: '采样阈值', type: 'range', value: 0.9, min: 0.1, max: 1.0, step: 0.05 },
    { id: 'style', label: '回复风格', type: 'select', value: 'warm',
      options: [{ value: 'warm', label: '温暖贴心' }, { value: 'playful', label: '活泼调皮' }, { value: 'intellectual', label: '知性优雅' }] },
  ]},
  { id: 'model', label: '模型', items: [
    { id: 'model_name', label: '模型选择', type: 'select', value: 'qwen2.5-7b',
      options: [{ value: 'qwen2.5-0.5b', label: 'Qwen2.5 0.5B' }, { value: 'qwen2.5-1.5b', label: 'Qwen2.5 1.5B' },
        { value: 'qwen2.5-3b', label: 'Qwen2.5 3B' }, { value: 'qwen2.5-7b', label: 'Qwen2.5 7B' }] },
    { id: 'max_tokens', label: '最大回复长度', type: 'range', value: 512, min: 64, max: 2048, step: 64 },
    { id: 'api_key', label: 'API Key', type: 'toggle' as any, value: '' },
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
  const [vals, setVals] = useState<Record<string, any>>(() => {
    const r: Record<string, any> = {}
    sections.forEach(s => s.items.forEach(i => { r[i.id] = i.value }))
    return r
  })
  const [saved, setSaved] = useState(false)

  // Load config from backend on mount
  useEffect(() => {
    api.config().then(({ data }) => {
      const config = data as Record<string, any>
      if (config && Object.keys(config).length > 0) {
        setVals(prev => ({ ...prev, ...mapConfigToSettings(config) }))
      }
    }).catch(() => {})
  }, [])

  const set = (id: string, v: any) => {
    setVals(p => ({ ...p, [id]: v }))
    setSaved(false)
  }

  const section = sections.find(s => s.id === tab)!

  const handleSave = async () => {
    try {
      const configPayload = buildConfigPayload(vals)
      await api.saveConfig(configPayload)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch {
      // error toast handled by interceptor
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <h1 className="text-base font-semibold text-slate-200 mb-6">设置</h1>

      <div className="flex gap-6">
        <nav className="hidden lg:flex flex-col gap-0.5 w-28 shrink-0">
          {sections.map(s => (
            <button key={s.id} onClick={() => setTab(s.id)}
              className={`text-xs px-3 py-1.5 rounded-lg text-left transition-colors ${
                tab === s.id ? 'bg-primary-600/15 text-primary-200' : 'text-slate-500 hover:text-slate-300'
              }`}>
              {s.label}
            </button>
          ))}
        </nav>

        <div className="flex-1 max-w-lg">
          <select value={tab} onChange={e => setTab(e.target.value)}
            className="lg:hidden w-full mb-4 bg-slate-800 border border-slate-700 text-slate-200 rounded-lg px-3 py-2 text-sm outline-none">
            {sections.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>

          <div className="space-y-1">
            {section.items.map(item => (
              <div key={item.id} className="flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-slate-800/30">
                <span className="text-sm text-slate-300">{item.label}</span>
                {isSensitiveField(item.id) ? (
                  <div className="w-48">
                    <SensitiveInput
                      value={vals[item.id] ?? ''}
                      onChange={(v) => set(item.id, v)}
                      placeholder="输入..."
                    />
                  </div>
                ) : item.type === 'toggle' ? (
                  <button onClick={() => set(item.id, !vals[item.id])}
                    className={`w-9 h-5 rounded-full transition-colors ${
                      vals[item.id] ? 'bg-primary-500' : 'bg-slate-700'
                    }`}>
                    <div className={`w-4 h-4 bg-white rounded-full transition-transform ${
                      vals[item.id] ? 'translate-x-4.5' : 'translate-x-0.5'
                    }`} />
                  </button>
                ) : item.type === 'select' ? (
                  <select value={vals[item.id]} onChange={e => set(item.id, e.target.value)}
                    className="bg-slate-800 border border-slate-700 text-slate-200 rounded-lg px-2 py-1 text-xs outline-none">
                    {item.options?.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                ) : item.type === 'range' ? (
                  <div className="flex items-center gap-2">
                    <input type="range" min={item.min} max={item.max} step={item.step}
                      value={vals[item.id]} onChange={e => set(item.id, parseFloat(e.target.value))}
                      className="w-20 h-1 bg-slate-700 rounded-full appearance-none cursor-pointer" />
                    <span className="text-xs text-slate-500 w-8 text-right">{vals[item.id]}</span>
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
        </div>
      </div>
    </div>
  )
}

// Map nested backend SystemConfig to flat setting IDs
function mapConfigToSettings(config: Record<string, any>): Record<string, any> {
  const mapped: Record<string, any> = {}
  if (config.llm) {
    if (config.llm.primary_model) mapped.model_name = config.llm.primary_model
    if (config.llm.temperature != null) mapped.temperature = config.llm.temperature
    if (config.llm.max_tokens != null) mapped.max_tokens = config.llm.max_tokens
    if (config.llm.top_p != null) mapped.top_p = config.llm.top_p
    if (config.llm.api_key != null) mapped.api_key = config.llm.api_key
  }
  if (config.memory) {
    if (config.memory.working_memory_limit != null) mapped.max_context = config.memory.working_memory_limit
  }
  if (config.proactive) {
    if (config.proactive.max_daily_messages != null) mapped.auto_reply = config.proactive.max_daily_messages > 0
    if (config.proactive.min_interval_minutes != null) mapped.reply_delay = config.proactive.min_interval_minutes
  }
  if (config.safety) {
    if (config.safety.input_filter_enabled != null) mapped.store_history = config.safety.input_filter_enabled
  }
  if (config.voice) {
    if (config.voice.voice_input != null) mapped.voice_input = config.voice.voice_input
    if (config.voice.voice_output != null) mapped.voice_output = config.voice.voice_output
    if (config.voice.speaker != null) mapped.speaker = config.voice.speaker
  }
  if (config.personality) {
    if (config.personality.style != null) mapped.style = config.personality.style
  }
  if (config.notifications) {
    if (config.notifications.sound != null) mapped.sound = config.notifications.sound
    if (config.notifications.desktop_notify != null) mapped.desktop_notify = config.notifications.desktop_notify
  }
  return mapped
}

// Convert flat setting IDs to nested backend config payload
function buildConfigPayload(vals: Record<string, any>): Record<string, any> {
  const payload: Record<string, any> = {
    llm: {},
    memory: {},
    proactive: {},
    voice: {},
    personality: {},
    notifications: {},
  }
  if (vals.model_name) payload.llm.primary_model = vals.model_name
  if (vals.temperature != null) payload.llm.temperature = vals.temperature
  if (vals.max_tokens != null) payload.llm.max_tokens = vals.max_tokens
  if (vals.top_p != null) payload.llm.top_p = vals.top_p
  if (vals.api_key != null) payload.llm.api_key = vals.api_key
  if (vals.max_context != null) payload.memory.working_memory_limit = vals.max_context
  if (vals.auto_reply != null) payload.proactive.max_daily_messages = vals.auto_reply ? 8 : 0
  if (vals.reply_delay != null) payload.proactive.min_interval_minutes = Math.round(vals.reply_delay)
  if (vals.voice_input != null) payload.voice.voice_input = vals.voice_input
  if (vals.voice_output != null) payload.voice.voice_output = vals.voice_output
  if (vals.speaker != null) payload.voice.speaker = vals.speaker
  if (vals.style != null) payload.personality.style = vals.style
  if (vals.sound != null) payload.notifications.sound = vals.sound
  if (vals.desktop_notify != null) payload.notifications.desktop_notify = vals.desktop_notify
  return payload
}

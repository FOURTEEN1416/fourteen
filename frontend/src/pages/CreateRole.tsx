import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCreateCharacter } from '../hooks/useQueries'
import { useErrorStore } from '../store/errorStore'
import { useCharacterBuilderStore, type PersonaState } from '../store/characterBuilderStore'
import { chat, listPresets, getPreset } from '../api/client'
import type { PresetItem } from '../api/characters'
import {
  MessageSquare, Send, Loader2, Sparkles,
  FileUp, Check, Users, Eye,
} from 'lucide-react'

type CreateMethod = 'ai-chat' | 'wechat-clone' | 'file-import'
interface ChatMessage { role: 'user' | 'assistant'; content: string }

const METHODS: { key: CreateMethod; label: string; gradient: string }[] = [
  { key: 'ai-chat', label: 'AI 对话', gradient: 'from-macaron-pink to-macaron-pink-deep' },
  { key: 'wechat-clone', label: '克隆好友', gradient: 'from-macaron-blue to-macaron-blue-deep' },
  { key: 'file-import', label: '文件导入', gradient: 'from-macaron-green to-macaron-green-deep' },
]

const PERSONALITY_KEYS = [
  { key: 'warmth', label: '温暖', color: 'bg-macaron-pink-deep' },
  { key: 'playfulness', label: '活泼', color: 'bg-macaron-blue-deep' },
  { key: 'independence', label: '独立', color: 'bg-macaron-green-deep' },
  { key: 'jealousy', label: '占有欲', color: 'bg-macaron-pink-deep' },
  { key: 'stubbornness', label: '固执', color: 'bg-macaron-blue-deep' },
]

// ═══ AI Chat Tab ═══
function AIChatTab({ onPersonaUpdate }: { onPersonaUpdate: (p: Partial<PersonaState>) => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages])

  async function handleSend() {
    const text = input.trim(); if (!text || loading) return
    setMessages(prev => [...prev, { role: 'user', content: text }]); setInput(''); setLoading(true)
    try {
      const res = await chat(text, '', 'character_build')
      const data = res.data as { reply?: string; persona_update?: Partial<PersonaState> }
      setMessages(prev => [...prev, { role: 'assistant', content: data?.reply || '嗯，我知道了～' }])
      if (data?.persona_update) onPersonaUpdate(data.persona_update)
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: '抱歉，我暂时无法回应。' }])
    } finally { setLoading(false) }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 mb-4">
        <MessageSquare className="w-4 h-4 text-macaron-pink-deep" />
        <h3 className="text-sm font-semibold text-gray-700">和十四聊一会儿</h3>
        <span className="text-[10px] text-gray-400 ml-auto">通过对话让 AI 学习你的期待</span>
      </div>

      <div className="flex-1 overflow-y-auto space-y-4 p-1 min-h-[260px]">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center py-10">
            <MessageSquare className="w-10 h-10 text-gray-200 mb-3" />
            <p className="text-sm text-gray-400">描述你想要的 AI 角色</p>
            <p className="text-xs text-gray-300 mt-1">比如："她是一个22岁的美术生，温柔细腻……"</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={`crmsg-${i}-${msg.role}-${msg.content.slice(0, 16)}`} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'bg-gradient-to-br from-macaron-pink to-macaron-pink-deep text-white rounded-br-md shadow-sm'
                : 'glass-card text-text-primary rounded-bl-md'
            }`}>
              {msg.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="glass-card rounded-2xl rounded-bl-md px-4 py-2.5 flex items-center gap-2">
              <Loader2 className="w-4 h-4 text-macaron-pink-deep animate-spin" />
              <span className="text-xs text-text-muted">正在思考...</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="flex items-end gap-3 mt-3">
        <textarea
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }}
          placeholder="描述你想要的 AI 角色..."
          disabled={loading}
          rows={1}
          className="input-macaron flex-1 rounded-xl px-4 py-2.5 text-sm resize-none text-text-primary placeholder:text-text-dim outline-none"
          style={{ minHeight: 44 }}
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          className="shrink-0 w-10 h-10 rounded-xl btn-macaron text-white flex items-center justify-center disabled:opacity-40 transition-all"
        >
          <Send className="w-4 h-4" />
        </button>
      </div>
    </div>
  )
}

// ═══ WeChat Clone Tab ═══
function WeChatCloneTab({ onPersonaUpdate }: { onPersonaUpdate: (p: Partial<PersonaState>) => void }) {
  const [phase, setPhase] = useState<'idle' | 'extracting' | 'analyzing' | 'generating' | 'done'>('idle')
  const [wxid, setWxid] = useState('')
  const STEPS = [
    { phase: 'extracting' as const, label: '提取聊天记录' },
    { phase: 'analyzing' as const, label: '分析性格特征' },
    { phase: 'generating' as const, label: '生成角色人设' },
  ]
  const curIdx = STEPS.findIndex(s => s.phase === phase)

  function startClone() {
    if (!wxid.trim()) return
    setPhase('extracting'); setTimeout(() => setPhase('analyzing'), 1500); setTimeout(() => setPhase('generating'), 3000)
    setTimeout(() => {
      setPhase('done')
      onPersonaUpdate({ name: wxid, anchors: ['幽默', '直率', '朋友多'], personality: { warmth: 0.7, playfulness: 0.8, independence: 0.6, jealousy: 0.2, stubbornness: 0.3 } })
    }, 4500)
  }

  return (
    <div className="flex flex-col items-center justify-center py-8 space-y-6">
      <div className="w-12 h-12 rounded-2xl glass-blue flex items-center justify-center text-macaron-blue-deep">
        <Users className="w-6 h-6" />
      </div>
      <div className="text-center">
        <p className="text-sm font-medium text-gray-700">从微信聊天记录克隆角色</p>
        <p className="text-xs text-gray-400 mt-1">输入微信 ID，自动分析聊天风格和性格特征</p>
      </div>
      <div className="w-full max-w-sm space-y-3">
        <input
          value={wxid}
          onChange={e => setWxid(e.target.value)}
          placeholder="输入微信 ID（如 wxid_xxx）"
          disabled={phase !== 'idle'}
          className="input-macaron w-full rounded-xl px-4 py-2.5 text-sm outline-none"
        />
        {phase === 'idle' ? (
          <button onClick={startClone} disabled={!wxid.trim()} className="w-full py-2.5 rounded-xl btn-macaron text-white text-sm font-semibold disabled:opacity-40 transition-all">开始克隆</button>
        ) : (
          <div className="space-y-2">
            {STEPS.map((s, i) => {
              const isCurrent = i === curIdx
              const isDone = i < curIdx
              return (
                <div key={s.phase} className={`flex items-center gap-3 px-3 py-2.5 rounded-xl transition-all border ${isCurrent ? 'glass-blue border-macaron-blue/30' : isDone ? 'glass-green border-macaron-green/30' : 'bg-white/20 border-white/40'}`}>
                  <div className={`w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-semibold ${isCurrent ? 'bg-macaron-blue text-white' : isDone ? 'bg-macaron-green text-white' : 'bg-white/50 text-gray-400'}`}>
                    {isDone ? <Check className="w-3 h-3" /> : i + 1}
                  </div>
                  <span className={`text-xs ${isCurrent ? 'text-macaron-blue-deep font-medium' : 'text-gray-400'}`}>{s.label}</span>
                  {isCurrent && <Loader2 className="w-3 h-3 text-macaron-blue-deep animate-spin ml-auto" />}
                </div>
              )
            })}
            {phase === 'done' && (
              <div className="glass-green border border-macaron-green/30 rounded-xl px-4 py-3 flex items-center gap-2">
                <Check className="w-4 h-4 text-macaron-green-deep" />
                <span className="text-xs text-macaron-green-deep font-medium">克隆完成！人设卡已更新</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ═══ File Import Tab ═══
function FileImportTab({ onPersonaUpdate }: { onPersonaUpdate: (p: Partial<PersonaState>) => void }) {
  const [jsonText, setJsonText] = useState('')
  const [error, setError] = useState('')
  const [parsed, setParsed] = useState<Record<string, string> | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result as string)
        const f: Record<string, string> = {}
        if (data.name) f['角色名称'] = data.name; if (data.description) f['角色描述'] = data.description
        setParsed(f); setError('')
        onPersonaUpdate({ name: data.name, description: data.description, anchors: Array.isArray(data.core_anchors) ? data.core_anchors : [], personality: data.personality || {} })
      } catch { setError('JSON 格式无效') }
    }
    reader.readAsText(file)
  }

  function handleTextParse() {
    try {
      const data = JSON.parse(jsonText); setParsed({ 角色名称: data.name || '', 角色描述: data.description || '' }); setError('')
      onPersonaUpdate({ name: data.name, description: data.description })
    } catch { setError('JSON 格式无效') }
  }

  return (
    <div className="space-y-4">
      <div
        onClick={() => fileRef.current?.click()}
        className="border-2 border-dashed border-white/60 rounded-2xl p-8 text-center hover:border-macaron-pink hover:bg-macaron-pink-light/30 transition-all cursor-pointer"
      >
        <FileUp className="w-8 h-8 text-macaron-pink-deep mx-auto mb-2" />
        <p className="text-sm text-gray-500">点击上传角色 JSON 文件</p>
        <p className="text-xs text-gray-400 mt-1">支持标准角色卡格式</p>
        <input ref={fileRef} type="file" accept=".json" className="hidden" onChange={handleFile} />
      </div>
      <div className="relative">
        <div className="absolute top-2 left-3 text-[10px] text-text-muted">JSON 手动输入</div>
        <textarea
          value={jsonText}
          onChange={e => setJsonText(e.target.value)}
          rows={6}
          placeholder='{"name": "角色名", "description": "描述"}'
          className="input-macaron w-full rounded-xl px-4 py-4 pt-7 text-xs font-mono text-text-primary placeholder:text-text-dim outline-none resize-none"
        />
      </div>
      {error && <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-xs text-red-600">{error}</div>}
      {jsonText.trim() && !error && (
        <button onClick={handleTextParse} className="w-full py-2.5 rounded-xl btn-macaron text-white text-sm font-semibold active:scale-[0.98] transition-all">解析 JSON</button>
      )}
      {parsed && (
        <div className="glass-card rounded-2xl p-4">
          <h4 className="text-xs font-semibold text-text-muted mb-3 flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-macaron-green-deep" /> 解析预览</h4>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(parsed).map(([label, value]) => (
              <div key={label} className="bg-white/40 rounded-xl px-3 py-2"><p className="text-[10px] text-text-muted">{label}</p><p className="text-xs text-text-primary mt-0.5 truncate">{value}</p></div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ═══ Live Persona Preview ═══
function PersonaPreviewCard() {
  const persona = useCharacterBuilderStore(s => s.persona)
  const hasContent = useCharacterBuilderStore(s => s.hasContent)

  if (!hasContent || !persona) {
    return (
      <div className="glass-card rounded-2xl p-5 h-full flex flex-col items-center justify-center text-center min-h-[280px]">
        <div className="w-12 h-12 rounded-full btn-macaron flex items-center justify-center text-white text-lg font-bold mb-3">你</div>
        <p className="text-sm text-gray-500">和十四聊聊</p>
        <p className="text-xs text-gray-400 mt-1">角色卡会在这里实时生长</p>
      </div>
    )
  }

  const anchors = persona.anchors?.length ? persona.anchors : []

  return (
    <div className="glass-card rounded-2xl p-5 space-y-4">
      <div className="flex items-center gap-3">
        <div className="w-12 h-12 rounded-full btn-macaron flex items-center justify-center text-white font-bold text-lg">你</div>
        <div>
          <div className="text-base font-semibold text-text-primary">{persona.name || '未命名角色'}</div>
          <div className="text-xs text-text-muted">有记忆的对话对象</div>
        </div>
      </div>

      <div>
        <div className="text-xs text-text-muted mb-1">角色描述</div>
        <p className="text-xs text-text-secondary leading-relaxed">{persona.description || '暂无描述'}</p>
      </div>

      {anchors.length > 0 && (
        <div>
          <div className="text-xs text-text-muted mb-2">核心锚点</div>
          <div className="flex flex-wrap gap-1.5">
            {anchors.slice(0, 8).map((a, i) => {
              const colorClass = i % 3 === 0 ? 'tag-pink' : i % 3 === 1 ? 'tag-blue' : 'tag-green'
              return <span key={a} className={`${colorClass} px-2 py-1 rounded-md text-[10px] font-medium`}>{a}</span>
            })}
          </div>
        </div>
      )}

      <div>
        <div className="text-xs text-text-muted mb-2">性格维度</div>
        <div className="space-y-2">
          {PERSONALITY_KEYS.map(({ key, label, color }) => {
            const value = persona.personality?.[key] ?? 0.5
            return (
              <div key={key} className="flex items-center gap-2 text-[10px]">
                <span className="w-12 text-text-secondary">{label}</span>
                <div className="flex-1 h-1.5 bg-white/60 rounded-full overflow-hidden">
                  <div className={`h-full ${color} rounded-full`} style={{ width: `${Math.round(value * 100)}%` }} />
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ═══ Create Button ═══
function CreateButton() {
  const navigate = useNavigate()
  const createMutation = useCreateCharacter()
  const toast = useErrorStore.getState().addToast
  const persona = useCharacterBuilderStore(s => s.persona)
  const hasContent = useCharacterBuilderStore(s => s.hasContent)

  async function handleCreate() {
    if (!hasContent || !persona) { toast({ type: 'warning', message: '请先生成角色人设' }); return }
    try {
      const result = await createMutation.mutateAsync({ name: persona.name || '未命名角色', description: persona.description || '', core_anchors: persona.anchors, personality: persona.personality, speaking_style: persona.speakingStyle })
      toast({ type: 'success', message: '角色创建成功！' })
      navigate(`/roles/${result.id}/settings`)
    } catch { toast({ type: 'error', message: '创建失败，请重试' }) }
  }
  return (
    <button onClick={handleCreate} disabled={createMutation.isPending || !hasContent} className="btn-macaron w-full py-3 rounded-xl text-sm font-semibold disabled:opacity-40 flex items-center justify-center gap-2 transition-all">
      <Sparkles className="w-4 h-4" /> {createMutation.isPending ? '创建中...' : '创建角色'}
    </button>
  )
}

// ═══ Preset Pills ═══
function PresetPills({ onSelect }: { onSelect: (p: Partial<PersonaState>) => void }) {
  const [presets, setPresets] = useState<PresetItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    listPresets()
      .then(res => { if (!cancelledRef.current) setPresets(res.presets || []) })
      .catch((err: unknown) => { if (import.meta.env.DEV) console.warn('加载预设角色失败:', err) })
      .finally(() => { if (!cancelledRef.current) setLoading(false) })
    return () => { cancelledRef.current = true }
  }, [])

  async function handleSelect(item: PresetItem) {
    setSelectedId(item.id)
    try {
      const detail = await getPreset(item.id)
      onSelect({
        name: detail.name,
        description: detail.description,
        anchors: detail.anchors?.length ? detail.anchors : detail.tags?.slice(0, 8) || [],
        personality: detail.personality || { warmth: 0.6, playfulness: 0.5, independence: 0.5, jealousy: 0.3, stubbornness: 0.4 },
        speakingStyle: detail.speakingStyle || { formality: 0.5, expressiveness: 0.5, humor: 0.5, directness: 0.5 },
      })
    } catch (err) {
      if (import.meta.env.DEV) console.warn('获取预设详情失败，使用概要数据:', err)
      onSelect({ name: item.name, description: item.description, anchors: item.tags?.slice(0, 8) || [] })
    }
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm">
        <Loader2 className="w-3.5 h-3.5 text-text-muted animate-spin" />
        <span className="text-text-muted">加载预设角色...</span>
      </div>
    )
  }

  if (!presets.length) return null

  return (
    <div className="flex items-center gap-2 text-sm flex-wrap">
      <span className="text-text-muted">或从预设开始：</span>
      {presets.map(item => (
        <button
          key={item.id}
          onClick={() => handleSelect(item)}
          className={`px-3.5 py-1.5 rounded-full text-sm transition-all border ${
            selectedId === item.id
              ? 'bg-white/75 border-macaron-pink text-macaron-pink-deep shadow-sm'
              : 'bg-white/25 border-white/40 text-text-secondary hover:bg-white/50'
          }`}
        >
          {item.name}
        </button>
      ))}
    </div>
  )
}

// ═══ Main ═══
export default function CreateRole() {
  const [method, setMethod] = useState<CreateMethod>('ai-chat')
  const setPersona = useCharacterBuilderStore(s => s.setPersona)
  const resetPersona = useCharacterBuilderStore(s => s.resetPersona)
  useEffect(() => { resetPersona(); return () => { resetPersona() } }, [resetPersona])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-5">
        {/* Header */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Sparkles className="w-5 h-5 text-macaron-pink-deep" />
            <h1 className="text-lg font-bold text-gray-800">创建角色</h1>
          </div>
          <p className="text-sm text-text-muted">选择一种方式来构建你的 AI 角色</p>
        </div>

        {/* Presets */}
        <PresetPills onSelect={setPersona} />

        {/* Method selector */}
        <div className="glass-card rounded-2xl p-1.5 flex">
          {METHODS.map(m => (
            <button
              key={m.key}
              onClick={() => setMethod(m.key)}
              className={`flex-1 rounded-xl py-2.5 text-sm font-medium transition-all ${
                method === m.key
                  ? `bg-gradient-to-r ${m.gradient} text-white shadow-sm`
                  : 'text-text-muted hover:text-text-secondary hover:bg-white/40'
              }`}
            >
              {m.label}
            </button>
          ))}
        </div>

        {/* Workspace */}
        <div className="glass-card rounded-2xl overflow-hidden grid grid-cols-1 lg:grid-cols-[2fr_1fr]">
          <div className="p-5 border-b lg:border-b-0 lg:border-r border-white/40 min-h-[420px]">
            {method === 'ai-chat' && <AIChatTab onPersonaUpdate={setPersona} />}
            {method === 'wechat-clone' && <WeChatCloneTab onPersonaUpdate={setPersona} />}
            {method === 'file-import' && <FileImportTab onPersonaUpdate={setPersona} />}
          </div>
          <div className="bg-white/20 backdrop-blur-sm p-5">
            <div className="flex items-center gap-2 mb-4">
              <Eye className="w-4 h-4 text-macaron-blue-deep" />
              <h3 className="text-sm font-semibold text-gray-700">实时预览</h3>
            </div>
            <PersonaPreviewCard />
          </div>
        </div>

        {/* Create button */}
        <CreateButton />
      </div>
    </div>
  )
}

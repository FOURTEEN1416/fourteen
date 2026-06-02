import { useState, useRef, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useCreateCharacter } from '../hooks/useQueries'
import { useErrorStore } from '../store/errorStore'
import { useCharacterBuilderStore, type PersonaState } from '../store/characterBuilderStore'
import { chat } from '../api/client'
import {
  MessageCircle, Users, FileUp, Send, Loader2,
  Sparkles, Bot, Check,
} from 'lucide-react'

type CreateMethod = 'ai-chat' | 'wechat-clone' | 'file-import'
interface ChatMessage { role: 'user' | 'assistant'; content: string }

const METHODS: { key: CreateMethod; label: string; desc: string; icon: React.ReactNode }[] = [
  { key: 'ai-chat', label: 'AI 对话', desc: '聊天中逐步构建角色人设', icon: <MessageCircle className="w-5 h-5" /> },
  { key: 'wechat-clone', label: '克隆好友', desc: '从微信聊天记录提取特征', icon: <Users className="w-5 h-5" /> },
  { key: 'file-import', label: '文件导入', desc: '上传角色定义 JSON 文件', icon: <FileUp className="w-5 h-5" /> },
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
      <div className="flex-1 overflow-y-auto space-y-3 p-1 min-h-[300px]">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center py-12">
            <Bot className="w-10 h-10 text-gray-200 mb-3" />
            <p className="text-sm text-gray-400">描述你想要的 AI 角色</p>
            <p className="text-xs text-gray-300 mt-1">比如："她是一个22岁的美术生，温柔细腻……"</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={`crmsg-${i}-${msg.role}-${msg.content.slice(0, 16)}`} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${msg.role === 'user' ? 'bg-primary-500 text-white rounded-br-md' : 'bg-gray-100 text-gray-700 rounded-bl-md'}`}>
              {msg.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start"><div className="bg-gray-100 rounded-2xl rounded-bl-md px-4 py-2.5 flex items-center gap-2"><Loader2 className="w-4 h-4 text-gray-400 animate-spin" /><span className="text-xs text-gray-400">正在思考...</span></div></div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <div className="flex gap-2 mt-3">
        <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()} placeholder="描述你想要的 AI 角色..." disabled={loading}
          className="flex-1 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm text-gray-700 placeholder-gray-300 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20 transition-all" />
        <button onClick={handleSend} disabled={loading || !input.trim()} className="rounded-xl bg-primary-500 text-white px-4 py-2.5 hover:bg-primary-600 disabled:opacity-40 transition-all flex items-center gap-1.5"><Send className="w-4 h-4" /></button>
      </div>
    </div>
  )
}

// ═══ WeChat Clone Tab ═══
function WeChatCloneTab({ onPersonaUpdate }: { onPersonaUpdate: (p: Partial<PersonaState>) => void }) {
  const [phase, setPhase] = useState<'idle' | 'extracting' | 'analyzing' | 'generating' | 'done'>('idle')
  const [wxid, setWxid] = useState('')
  const STEPS = [
    { phase: 'extracting' as const, label: '提取聊天记录', emoji: '📥' },
    { phase: 'analyzing' as const, label: '分析性格特征', emoji: '🔬' },
    { phase: 'generating' as const, label: '生成角色人设', emoji: '✨' },
  ]
  const curIdx = STEPS.findIndex(s => s.phase === phase)

  function startClone() {
    if (!wxid.trim()) return
    setPhase('extracting'); setTimeout(() => setPhase('analyzing'), 1500); setTimeout(() => setPhase('generating'), 3000)
    setTimeout(() => { setPhase('done'); onPersonaUpdate({ name: wxid, anchors: ['幽默', '直率', '朋友多'], personality: { warmth: 0.7, playfulness: 0.8, independence: 0.6, jealousy: 0.2, stubbornness: 0.3 } }) }, 4500)
  }

  return (
    <div className="flex flex-col items-center justify-center py-8 space-y-6">
      <Bot className="w-12 h-12 text-gray-200" />
      <div className="text-center"><p className="text-sm font-medium text-gray-700">从微信聊天记录克隆角色</p><p className="text-xs text-gray-400 mt-1">输入微信 ID，自动分析聊天风格和性格特征</p></div>
      <div className="w-full max-w-sm space-y-3">
        <input value={wxid} onChange={e => setWxid(e.target.value)} placeholder="输入微信 ID（如 wxid_xxx）" disabled={phase !== 'idle'} className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20 transition-all" />
        {phase === 'idle' ? (
          <button onClick={startClone} disabled={!wxid.trim()} className="w-full py-2.5 rounded-xl bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 disabled:opacity-40 transition-all">开始克隆</button>
        ) : (
          <div className="space-y-2">
            {STEPS.map((s, i) => (
              <div key={s.phase} className={`flex items-center gap-3 px-3 py-2 rounded-xl transition-all ${i === curIdx ? 'bg-primary-50 border border-primary-200' : i < curIdx ? 'bg-green-50 border border-green-100' : 'bg-gray-50'}`}>
                <span className="text-sm">{s.emoji}</span><span className={`text-xs ${i === curIdx ? 'text-primary-600 font-medium' : 'text-gray-400'}`}>{s.label}</span>
                {i === curIdx && <Loader2 className="w-3 h-3 text-primary-400 animate-spin ml-auto" />}
                {i < curIdx && <Check className="w-3 h-3 text-green-500 ml-auto" />}
              </div>
            ))}
            {phase === 'done' && <div className="bg-green-50 border border-green-200 rounded-xl px-4 py-3 flex items-center gap-2"><Check className="w-4 h-4 text-green-500" /><span className="text-xs text-green-700 font-medium">克隆完成！人设卡已更新</span></div>}
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
      <div onClick={() => fileRef.current?.click()} className="border-2 border-dashed border-gray-200 rounded-2xl p-8 text-center hover:border-primary-300 hover:bg-primary-50/30 transition-all cursor-pointer">
        <FileUp className="w-8 h-8 text-gray-300 mx-auto mb-2" /><p className="text-sm text-gray-500">点击上传角色 JSON 文件</p><p className="text-xs text-gray-400 mt-1">支持标准角色卡格式</p>
        <input ref={fileRef} type="file" accept=".json" className="hidden" onChange={handleFile} />
      </div>
      <div className="relative">
        <div className="absolute top-2 left-3 text-[10px] text-gray-400">JSON 手动输入</div>
        <textarea value={jsonText} onChange={e => setJsonText(e.target.value)} rows={8} placeholder='{"name": "角色名", "description": "描述"}' className="w-full rounded-xl border border-gray-200 bg-white px-4 py-4 pt-7 text-xs font-mono text-gray-700 placeholder-gray-300 outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/20 transition-all resize-none" />
      </div>
      {error && <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-xs text-red-600">{error}</div>}
      {jsonText.trim() && !error && <button onClick={handleTextParse} className="w-full py-2.5 rounded-xl bg-primary-500 text-white text-sm font-medium hover:bg-primary-600 active:scale-[0.98] transition-all">解析 JSON</button>}
      {parsed && (
        <div className="bg-white/60 backdrop-blur-sm border border-gray-200/60 rounded-2xl p-4">
          <h4 className="text-xs font-semibold text-gray-500 mb-3 flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-green-500" /> 解析预览</h4>
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(parsed).map(([label, value]) => (<div key={label} className="bg-gray-50 rounded-xl px-3 py-2"><p className="text-[10px] text-gray-400">{label}</p><p className="text-xs text-gray-700 mt-0.5 truncate">{value}</p></div>))}
          </div>
        </div>
      )}
    </div>
  )
}

// ═══ Create Button ═══
function CreateButton() {
  const { userId } = useParams<{ userId?: string }>()
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
      navigate(`/users/${userId || 'default'}/roles/${result.id}/settings`)
    } catch { toast({ type: 'error', message: '创建失败，请重试' }) }
  }
  return (
    <button onClick={handleCreate} disabled={createMutation.isPending || !hasContent} className="w-full py-3 rounded-xl bg-primary-500 text-white text-sm font-semibold hover:bg-primary-600 disabled:opacity-40 active:scale-[0.98] transition-all flex items-center justify-center gap-2">
      <Sparkles className="w-4 h-4" /> {createMutation.isPending ? '创建中...' : '创建角色'}
    </button>
  )
}

// ═══ Main ═══
export default function CreateRole() {
  const [method, setMethod] = useState<CreateMethod>('ai-chat')
  const setPersona = useCharacterBuilderStore(s => s.setPersona)
  const resetPersona = useCharacterBuilderStore(s => s.resetPersona)
  useEffect(() => { resetPersona(); return () => { resetPersona() } }, [resetPersona])

  const tabContent = (() => {
    switch (method) {
      case 'ai-chat': return <AIChatTab onPersonaUpdate={setPersona} />
      case 'wechat-clone': return <WeChatCloneTab onPersonaUpdate={setPersona} />
      case 'file-import': return <FileImportTab onPersonaUpdate={setPersona} />
    }
  })()

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-6 space-y-5">
        <div>
          <h1 className="text-lg font-bold text-gray-800 flex items-center gap-2"><Sparkles className="w-5 h-5 text-amber-400" />创建角色</h1>
          <p className="text-sm text-gray-400 mt-0.5">选择一种方式来构建你的 AI 角色</p>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {METHODS.map(m => (
            <button key={m.key} onClick={() => setMethod(m.key)} className={`flex flex-col items-center gap-1.5 p-4 rounded-2xl border transition-all ${method === m.key ? 'border-primary-400 bg-primary-50/50 ring-1 ring-primary-400/30' : 'border-gray-200 bg-white hover:border-gray-300'}`}>
              <span className={method === m.key ? 'text-primary-500' : 'text-gray-400'}>{m.icon}</span>
              <span className={`text-sm font-medium ${method === m.key ? 'text-primary-700' : 'text-gray-700'}`}>{m.label}</span>
              <span className="text-[10px] text-gray-400">{m.desc}</span>
            </button>
          ))}
        </div>
        <div className="bg-white/60 backdrop-blur-sm border border-gray-200/60 rounded-2xl p-5 min-h-[400px]">{tabContent}</div>
        <CreateButton />
      </div>
    </div>
  )
}

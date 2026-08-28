import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useCreateCharacter } from '../hooks/useQueries'
import { useErrorStore } from '../store/errorStore'
import { useCharacterBuilderStore, type PersonaState } from '../store/characterBuilderStore'
import {
  cloneUpload,
  getPreset,
  importCharacter,
  listPresets,
  previewCharacterFromDescription,
} from '../api/client'
import type { PresetItem } from '../api/characters'
import {
  MessageSquare, Send, Loader2, Sparkles,
  FileUp, Check, Users, Eye, Copy, Bot,
} from 'lucide-react'
import { CLONE_AGENT_GUIDE } from '../constants/cloneAgentGuide'
import { anchorTone } from '../utils/character'

type CreateMethod = 'ai-chat' | 'wechat-clone' | 'file-import'
interface ChatMessage { role: 'user' | 'assistant'; content: string }

interface PersonaPayload {
  name?: string
  description?: string
  core_anchors?: string[]
  personality?: Record<string, number>
  speaking_style?: Record<string, number>
}

function toPersonaState(persona: PersonaPayload): Partial<PersonaState> {
  return {
    name: persona.name || '',
    description: persona.description || '',
    anchors: Array.isArray(persona.core_anchors) ? persona.core_anchors : [],
    personality: persona.personality || {},
    speakingStyle: persona.speaking_style || {},
  }
}

function getCardPreview(raw: Record<string, unknown>): PersonaPayload {
  const nested = raw.data
  const source = nested && typeof nested === 'object' ? nested as Record<string, unknown> : raw
  return {
    name: String(source.name || raw.name || ''),
    description: String(source.description || raw.description || ''),
    core_anchors: Array.isArray(source.core_anchors)
      ? source.core_anchors.map(String)
      : Array.isArray(source.tags) ? source.tags.map(String).slice(0, 8) : [],
    personality: source.personality && typeof source.personality === 'object'
      ? source.personality as Record<string, number> : {},
    speaking_style: source.speaking_style && typeof source.speaking_style === 'object'
      ? source.speaking_style as Record<string, number> : {},
  }
}

const METHODS: { key: CreateMethod; label: string; gradient: string }[] = [
  { key: 'ai-chat', label: 'AI 对话', gradient: 'from-macaron-yellow to-macaron-yellow-deep' },
  { key: 'wechat-clone', label: '克隆好友', gradient: 'from-macaron-blue to-macaron-blue-deep' },
  { key: 'file-import', label: '文件导入', gradient: 'from-macaron-mint to-macaron-mint-deep' },
]

const PERSONALITY_KEYS = [
  { key: 'warmth', label: '温暖', color: 'bg-macaron-yellow-deep' },
  { key: 'playfulness', label: '活泼', color: 'bg-macaron-blue-deep' },
  { key: 'independence', label: '独立', color: 'bg-macaron-mint-deep' },
  { key: 'jealousy', label: '占有欲', color: 'bg-macaron-yellow-deep' },
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
      const data = await previewCharacterFromDescription(text)
      setMessages(prev => [...prev, { role: 'assistant', content: data.reply }])
      onPersonaUpdate(toPersonaState(data.persona))
    } catch {
      setMessages(prev => [...prev, { role: 'assistant', content: '抱歉，我暂时无法回应。' }])
    } finally { setLoading(false) }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 mb-4">
        <MessageSquare className="w-4 h-4 text-macaron-yellow-deep" />
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
                ? 'bg-gradient-to-br from-macaron-yellow to-macaron-yellow-deep text-white rounded-br-md shadow-sm'
                : 'glass-card text-text-primary rounded-bl-md'
            }`}>
              {msg.content}
            </div>
          </div>
        ))}
        {loading && (
          <div className="flex justify-start">
            <div className="glass-card rounded-2xl rounded-bl-md px-4 py-2.5 flex items-center gap-2">
              <Loader2 className="w-4 h-4 text-macaron-yellow-deep animate-spin" />
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
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'analyzing' | 'done' | 'error'>('idle')
  const [targetName, setTargetName] = useState('')
  const [error, setError] = useState('')
  const [sampleCount, setSampleCount] = useState(0)
  const [preview, setPreview] = useState<Array<{ user: string; reply: string }>>([])
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [guideCopied, setGuideCopied] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  async function copyGuide() {
    try {
      await navigator.clipboard.writeText(CLONE_AGENT_GUIDE)
      setGuideCopied(true)
      setTimeout(() => setGuideCopied(false), 2000)
    } catch {
      setError('复制失败，请手动打开 docs/guides/微信克隆-智能体任务书.md 复制')
      setPhase('error')
    }
  }

  async function startClone() {
    if (!targetName.trim()) {
      setError('请输入被克隆者名称')
      setPhase('error')
      return
    }
    if (!selectedFile) {
      setError('请选择本地提取的 JSON 数据文件')
      setPhase('error')
      return
    }
    setError('')
    setPhase('uploading')
    setUploadProgress(10)
    try {
      // 模拟上传进度
      const progressInterval = setInterval(() => {
        setUploadProgress(p => Math.min(p + 15, 80))
      }, 300)
      const data = await cloneUpload(targetName.trim(), selectedFile)
      clearInterval(progressInterval)
      setUploadProgress(100)
      setPhase('analyzing')
      onPersonaUpdate(toPersonaState(data.persona))
      setSampleCount(data.sample_count)
      setPreview(data.preview || [])
      setPhase('done')
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传分析失败，请检查文件格式')
      setPhase('error')
      setUploadProgress(0)
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    setSelectedFile(f ?? null)
    setPhase('idle')
    setError('')
    setPreview([])
  }

  function resetAll() {
    setPhase('idle')
    setError('')
    setPreview([])
    setSelectedFile(null)
    setUploadProgress(0)
    setSampleCount(0)
  }

  return (
    <div className="flex flex-col items-center justify-center py-4 space-y-4">
      <div className="w-12 h-12 rounded-2xl glass-blue flex items-center justify-center text-macaron-blue-deep">
        <Users className="w-6 h-6" />
      </div>
      <div className="text-center">
        <p className="text-sm font-medium text-gray-700">从微信聊天记录克隆角色</p>
        <p className="text-xs text-gray-400 mt-1">下载工具 → 本地提取 → 上传分析 → 生成人设</p>
      </div>

      
      {/* ═══ 步骤 1：准备 AI 智能体 ═══ */}
      <div className="w-full max-w-md rounded-xl bg-blue-50/60 border border-blue-100 p-4 space-y-3">
        <div className="flex items-center gap-2">
          <span className="w-5 h-5 rounded-full bg-macaron-blue text-white text-[10px] font-bold flex items-center justify-center">1</span>
          <p className="text-xs font-semibold text-blue-700">准备一个 AI 智能体，让它替你跑导出</p>
        </div>
        <div className="space-y-2 pl-7">
          <p className="text-[11px] text-gray-600 leading-relaxed">
            聊天记录解密只能在<strong>你登录微信的这台电脑</strong>上做，操作门槛高——所以交给智能体代跑：
            装一个智能体 → 把我们的任务书喂给它 → 它替你完成提取。
          </p>
          <div className="bg-white/70 rounded-lg p-2.5 space-y-1.5 border border-blue-100">
            <div className="flex items-center justify-between">
              <p className="text-[11px] font-semibold text-gray-800">
                A. OpenCode <span className="text-[9px] text-blue-600 bg-blue-100 px-1.5 py-0.5 rounded">推荐 · 免费模型充足</span>
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              <a href="https://opencode.ai" target="_blank" rel="noopener noreferrer"
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md bg-blue-600 text-white text-[10px] font-medium hover:bg-blue-700 transition-colors">
                <Bot className="w-3 h-3" /> opencode.ai
              </a>
            </div>
          </div>
          <div className="bg-white/70 rounded-lg p-2.5 space-y-1 border border-gray-200">
            <p className="text-[10px] text-gray-600 leading-relaxed">
              B. Claude Code / Cline / Cursor 等任意能执行终端命令的编码智能体均可。
            </p>
          </div>
        </div>
      </div>

      {/* ═══ 步骤 2：导出工具 + 任务书喂给智能体 ═══ */}
      <div className="w-full max-w-md rounded-xl bg-amber-50/60 border border-amber-100 p-4 space-y-3">
        <div className="flex items-center gap-2">
          <span className="w-5 h-5 rounded-full bg-amber-500 text-white text-[10px] font-bold flex items-center justify-center">2</span>
          <p className="text-xs font-semibold text-amber-700">把任务书喂给智能体，让它在本机执行</p>
        </div>
        <div className="space-y-2 pl-7">
          <p className="text-[11px] text-gray-600 leading-relaxed">
            导出工具：<a href="https://github.com/FOURTEEN1416/wechat-decrypt" target="_blank" rel="noopener noreferrer"
              className="text-blue-600 font-medium hover:underline">github.com/FOURTEEN1416/wechat-decrypt</a>（微信 4.x 解密 + JSON 导出一体化）。
            点下面按钮复制任务书，粘贴给智能体并告诉它<strong>好友的备注名</strong>，剩余步骤它自己会跑；遇到问题直接问它。
          </p>
          <button onClick={copyGuide}
            className="w-full py-2 rounded-xl bg-gray-900 text-white text-xs font-medium hover:bg-gray-800 transition-colors flex items-center justify-center gap-1.5">
            {guideCopied ? (<><Check className="w-3.5 h-3.5" /> 已复制，去粘贴给智能体</>) : (<><Copy className="w-3.5 h-3.5" /> 一键复制智能体任务书</>)}
          </button>
          <div className="text-[10px] text-amber-600 bg-amber-100/50 rounded-lg p-2">
            <p>• 智能体会检查管理员权限、微信运行状态，缺什么它会告诉你</p>
            <p>• 导出的 JSON 请留在本机，上传前确认文件就是这位好友的会话</p>
          </div>
        </div>
      </div>

      {/* ═══ 步骤 3：上传数据到服务器 ═══ */}
      <div className="w-full max-w-md rounded-xl bg-green-50/60 border border-green-100 p-4 space-y-3">
        <div className="flex items-center gap-2">
          <span className="w-5 h-5 rounded-full bg-macaron-mint text-white text-[10px] font-bold flex items-center justify-center">3</span>
          <p className="text-xs font-semibold text-green-700">上传智能体导出的 JSON 到服务器分析</p>
        </div>

        <div className="pl-7 space-y-3">
          <input
            value={targetName}
            onChange={e => setTargetName(e.target.value)}
            placeholder="被克隆者名称（如：小明）"
            disabled={!['idle', 'error'].includes(phase)}
            className="input-macaron w-full rounded-xl px-4 py-2.5 text-sm outline-none"
          />
          <div className="flex items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              onChange={handleFileChange}
              disabled={!['idle', 'error'].includes(phase)}
              className="hidden"
            />
            <button
              onClick={() => fileRef.current?.click()}
              disabled={!['idle', 'error'].includes(phase)}
              className="flex-1 px-3 py-2 rounded-xl bg-white/60 border border-gray-200 text-xs text-gray-700 hover:bg-white/80 disabled:opacity-50 truncate text-left"
            >
              {selectedFile ? `📄 ${selectedFile.name} (${(selectedFile.size / 1024).toFixed(1)}KB)` : '选择 JSON 数据文件'}
            </button>
          </div>

          {/* 上传进度 */}
          {phase === 'uploading' && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-[10px] text-gray-500">
                <span>{uploadProgress < 100 ? '上传中...' : '上传完成，分析中...'}</span>
                <span>{uploadProgress}%</span>
              </div>
              <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-macaron-blue to-macaron-blue-deep transition-all duration-300"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            </div>
          )}

          {/* 分析中 */}
          {phase === 'analyzing' && (
            <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl glass-blue border border-macaron-blue/30">
              <Loader2 className="w-4 h-4 text-macaron-blue-deep animate-spin" />
              <span className="text-xs text-macaron-blue-deep font-medium">服务器正在分析性格特征...</span>
            </div>
          )}

          {/* 完成 + 预览 */}
          {phase === 'done' && (
            <div className="space-y-2">
              <div className="glass-green border border-macaron-mint/30 rounded-xl px-4 py-3 flex items-center gap-2">
                <Check className="w-4 h-4 text-macaron-mint-deep" />
                <span className="text-xs text-macaron-mint-deep font-medium">已分析 {sampleCount} 轮对话，人设预览已更新</span>
              </div>
              {preview.length > 0 && (
                <div className="rounded-xl border border-gray-200 bg-white/60 p-3 space-y-1.5">
                  <p className="text-[10px] font-medium text-gray-500">对话预览（前 {Math.min(preview.length, 5)} 条）：</p>
                  {preview.slice(0, 5).map((msg, i) => (
                    <div key={i} className="text-[10px] text-gray-600 space-y-0.5 border-l-2 border-gray-200 pl-2">
                      <p><span className="text-blue-500">用户：</span>{(msg.user || '').slice(0, 50)}</p>
                      <p><span className="text-green-500">回复：</span>{(msg.reply || '').slice(0, 50)}</p>
                    </div>
                  ))}
                </div>
              )}
              <button
                onClick={resetAll}
                className="w-full py-2 rounded-xl bg-gray-100 text-gray-600 text-xs font-medium hover:bg-gray-200 transition-colors"
              >
                重新上传
              </button>
            </div>
          )}

          {/* 操作按钮 */}
          {(phase === 'idle' || phase === 'error') && (
            <button
              onClick={startClone}
              disabled={!targetName.trim() || !selectedFile}
              className="w-full py-2.5 rounded-xl btn-macaron text-white text-sm font-semibold disabled:opacity-40 transition-all"
            >
              上传并分析
            </button>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-xs text-red-600 space-y-1">
              <p className="font-medium">❌ 错误：</p>
              <p>{error}</p>
              <p className="text-[10px] text-gray-500 mt-1">请检查文件是否为 wechat-decrypt 导出的 JSON 格式</p>
            </div>
          )}
        </div>
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
  const setImportFile = useCharacterBuilderStore(s => s.setImportFile)

  function applyRawCard(raw: Record<string, unknown>, file: File) {
    const preview = getCardPreview(raw)
    if (!preview.name) throw new Error('角色卡缺少 name 字段')
    setImportFile(file)
    setParsed({ 角色名称: preview.name, 角色描述: preview.description || '' })
    setError('')
    onPersonaUpdate(toPersonaState(preview))
  }

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]; if (!file) return
    setImportFile(null)
    const reader = new FileReader()
    reader.onload = () => {
      try {
        applyRawCard(JSON.parse(reader.result as string) as Record<string, unknown>, file)
      } catch (err) { setError(err instanceof Error ? err.message : 'JSON 格式无效') }
    }
    reader.readAsText(file)
  }

  function handleTextParse() {
    try {
      const raw = JSON.parse(jsonText) as Record<string, unknown>
      applyRawCard(raw, new File([jsonText], 'character.json', { type: 'application/json' }))
    } catch (err) { setError(err instanceof Error ? err.message : 'JSON 格式无效') }
  }

  return (
    <div className="space-y-4">
      <div
        onClick={() => fileRef.current?.click()}
        className="border-2 border-dashed border-white/60 rounded-2xl p-8 text-center hover:border-macaron-yellow hover:bg-macaron-yellow-light/30 transition-all cursor-pointer"
      >
        <FileUp className="w-8 h-8 text-macaron-yellow-deep mx-auto mb-2" />
        <p className="text-sm text-gray-500">点击上传角色 JSON 文件</p>
        <p className="text-xs text-gray-400 mt-1">支持标准角色卡格式</p>
        <input ref={fileRef} type="file" accept=".json" className="hidden" onChange={handleFile} />
      </div>
      <div className="relative">
        <div className="absolute top-2 left-3 text-[10px] text-text-muted">JSON 手动输入</div>
        <textarea
          value={jsonText}
          onChange={e => {
            setJsonText(e.target.value)
            setImportFile(null)
            setParsed(null)
          }}
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
          <h4 className="text-xs font-semibold text-text-muted mb-3 flex items-center gap-1.5"><Check className="w-3.5 h-3.5 text-macaron-mint-deep" /> 解析预览</h4>
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
            {anchors.slice(0, 8).map((a) => (
              <span key={a} className={`tag-${anchorTone(a)} px-2 py-1 rounded-md text-[10px] font-medium`}>{a}</span>
            ))}
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
  const importFile = useCharacterBuilderStore(s => s.importFile)
  const hasContent = useCharacterBuilderStore(s => s.hasContent)
  const [isImporting, setIsImporting] = useState(false)

  async function handleCreate() {
    if (!hasContent || !persona) { toast({ type: 'warning', message: '请先生成角色人设' }); return }
    try {
      setIsImporting(Boolean(importFile))
      const result = importFile
        ? await importCharacter(importFile)
        : await createMutation.mutateAsync({ name: persona.name || '未命名角色', description: persona.description || '', core_anchors: persona.anchors, personality: persona.personality, speaking_style: persona.speakingStyle })
      toast({ type: 'success', message: '角色创建成功！' })
      navigate(`/roles/${result.id}/settings`)
    } catch { toast({ type: 'error', message: '创建失败，请重试' }) }
    finally { setIsImporting(false) }
  }
  return (
    <button onClick={handleCreate} disabled={createMutation.isPending || isImporting || !hasContent} className="btn-macaron w-full py-3 rounded-xl text-sm font-semibold disabled:opacity-40 flex items-center justify-center gap-2 transition-all">
      <Sparkles className="w-4 h-4" /> {createMutation.isPending || isImporting ? '创建中...' : '创建角色'}
    </button>
  )
}

// ═══ Preset Pills ═══
function PresetPills({ onSelect }: { onSelect: (p: Partial<PersonaState>) => void }) {
  const [presets, setPresets] = useState<PresetItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const cancelledRef = useRef(false)
  const setImportFile = useCharacterBuilderStore(s => s.setImportFile)

  useEffect(() => {
    listPresets()
      .then(res => { if (!cancelledRef.current) setPresets(res.presets || []) })
      .catch((err: unknown) => { if (import.meta.env.DEV) console.warn('加载预设角色失败:', err) })
      .finally(() => { if (!cancelledRef.current) setLoading(false) })
    return () => { cancelledRef.current = true }
  }, [])

  async function handleSelect(item: PresetItem) {
    setImportFile(null)
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
              ? 'bg-white/75 border-macaron-yellow text-macaron-yellow-deep shadow-sm'
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
  const setImportFile = useCharacterBuilderStore(s => s.setImportFile)
  const resetPersona = useCharacterBuilderStore(s => s.resetPersona)
  useEffect(() => { resetPersona(); return () => { resetPersona() } }, [resetPersona])

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-5">
        {/* Header */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Sparkles className="w-5 h-5 text-macaron-yellow-deep" />
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
              onClick={() => {
                setMethod(m.key)
                if (m.key !== 'file-import') setImportFile(null)
              }}
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

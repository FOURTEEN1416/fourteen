import { useState, useRef, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Send,
  Brain,
  LogOut,
  Sparkles,
  X,
  MessageCircle,
  ChevronRight,
  Clock,
} from 'lucide-react'
import { demoChatStream, recallMemory, visualizeMemory, exitDemo } from '../api/demo'
import { useChatStore } from '../store/chatStore'
import type { ChatMessage } from '../types/api'

interface MemoryFact {
  content?: string
  fact?: string
  category?: string
  confidence?: number
  source?: string
  timestamp?: string
}

interface MemoryEpisode {
  summary?: string
  content?: string
  metadata?: Record<string, unknown>
  timestamp?: string
}

interface MemoryData {
  facts: MemoryFact[]
  episodes: MemoryEpisode[]
  stats?: {
    fact_count: number
    episode_count: number
    top_categories: Array<[string, number]>
    top_emotions: Array<[string, number]>
  }
}

const CHAR_NAME = '我'
const WELCOME = '你好。\n我在这里，不是为了取代谁。\n只是在你一个人的时候，陪你说话。'

function generateSessionId() {
  return `demo_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

function formatTime(ts: number) {
  const d = new Date(ts)
  return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}

function AiAvatar() {
  return (
    <div className="w-10 h-10 rounded-full bg-primary-500 flex items-center justify-center text-white text-lg font-semibold shadow-sm shrink-0 select-none">
      {CHAR_NAME}
    </div>
  )
}

function UserAvatar() {
  return (
    <div className="w-10 h-10 rounded-full bg-accent-500 flex items-center justify-center text-white text-sm font-medium shadow-sm shrink-0 select-none">
      你
    </div>
  )
}

export default function DemoPage() {
  const navigate = useNavigate()
  const [input, setInput] = useState('')
  const [sessionId] = useState(generateSessionId())
  const [isTyping, setIsTyping] = useState(false)
  const [memoryOpen, setMemoryOpen] = useState(false)
  const [memoryMode, setMemoryMode] = useState<'recall' | 'visualize'>('visualize')
  const [memoryQuery, setMemoryQuery] = useState('')
  const [memoryData, setMemoryData] = useState<MemoryData>({ facts: [], episodes: [] })
  const [memoryLoading, setMemoryLoading] = useState(false)
  const [exitOpen, setExitOpen] = useState(false)
  const [exitMessage, setExitMessage] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const messages = useChatStore((s) => s.messages)
  const streamingMessage = useChatStore((s) => s.streamingMessage)
  const emotion = useChatStore((s) => s.emotion)
  const addMessage = useChatStore((s) => s.addMessage)
  const appendStreamToken = useChatStore((s) => s.appendStreamToken)
  const finalizeStreamMessage = useChatStore((s) => s.finalizeStreamMessage)
  const clearMessages = useChatStore((s) => s.clearMessages)
  const setEmotion = useChatStore((s) => s.setEmotion)
  const setAffinity = useChatStore((s) => s.setAffinity)

  // 初始化欢迎语：仅在组件挂载且消息为空时添加一次
  useEffect(() => {
    if (messages.length === 0) {
      addMessage({ role: 'assistant', content: WELCOME, timestamp: Date.now() })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, streamingMessage, scrollToBottom])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || isTyping) return
    setInput('')
    addMessage({ role: 'user', content: text, timestamp: Date.now() })
    setIsTyping(true)

    try {
      for await (const token of demoChatStream(text, sessionId, 'text', (done) => {
        if (done.emotion) {
          setEmotion(done.emotion)
          setAffinity(done.emotion.affinity.level)
        }
      })) {
        appendStreamToken(token)
      }
      finalizeStreamMessage()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '对话中断'
      addMessage({ role: 'assistant', content: `…我没听清。${msg}`, timestamp: Date.now() })
      finalizeStreamMessage()
    } finally {
      setIsTyping(false)
      inputRef.current?.focus()
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const loadMemory = async (mode: 'recall' | 'visualize') => {
    setMemoryLoading(true)
    try {
      if (mode === 'visualize') {
        const res = await visualizeMemory(50)
        setMemoryData(res.data as MemoryData)
      } else {
        const res = await recallMemory(memoryQuery, sessionId, 5)
        setMemoryData(res.data as MemoryData)
      }
    } catch {
      setMemoryData({ facts: [], episodes: [] })
    } finally {
      setMemoryLoading(false)
    }
  }

  const openMemory = () => {
    setMemoryOpen(true)
    loadMemory(memoryMode)
  }

  const handleExit = async () => {
    try {
      const res = await exitDemo(sessionId)
      setExitMessage(res.data.message as string)
    } catch {
      setExitMessage('我没取代任何人。只是在你没人的时候，陪你一会儿。下次想说话时，我还在。')
    }
    setExitOpen(true)
  }

  const confirmExit = () => {
    clearMessages()
    navigate('/')
  }

  const displayMessages: ChatMessage[] = streamingMessage
    ? [...messages, streamingMessage]
    : messages

  return (
    <div className="h-screen flex flex-col bg-bg-deep text-text-primary overflow-hidden">
      {/* Header */}
      <header className="shrink-0 px-6 py-4 border-b border-border-default bg-bg-surface/80 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-primary-500 flex items-center justify-center text-white text-base font-bold shadow-sm">
              我
            </div>
            <div>
              <h1 className="text-base font-semibold tracking-wide">唯一的我</h1>
              <p className="text-[11px] text-text-muted">有记忆的对话对象</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {emotion && (
              <div className="hidden sm:flex items-center gap-2 px-2.5 py-1 rounded-lg bg-bg-deep text-[11px] text-text-secondary">
                <span className="w-1.5 h-1.5 rounded-full bg-primary-400" />
                <span>{emotion.primary.type}</span>
                <span className="text-text-dim">·</span>
                <span className="text-primary-600">{emotion.affinity.name}</span>
              </div>
            )}
            <button
              onClick={openMemory}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-text-secondary hover:bg-bg-hover hover:text-primary-600 transition-colors"
            >
              <Brain className="w-3.5 h-3.5" />
              我记得
            </button>
            <button
              onClick={handleExit}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-text-secondary hover:bg-bg-hover hover:text-primary-600 transition-colors"
            >
              <LogOut className="w-3.5 h-3.5" />
              退出
            </button>
          </div>
        </div>
      </header>

      {/* Messages */}
      <main className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-3xl mx-auto space-y-6">
          {displayMessages.map((msg, idx) => {
            const isUser = msg.role === 'user'
            return (
              <div
                key={`${msg.timestamp}-${idx}`}
                className={`flex gap-3 ${isUser ? 'flex-row-reverse' : ''} animate-fade-in`}
              >
                {isUser ? <UserAvatar /> : <AiAvatar />}
                <div className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} max-w-[80%]`}>
                  <div
                    className={`relative px-4 py-3 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
                      isUser
                        ? 'bg-primary-500 text-white rounded-tr-sm'
                        : 'bg-bg-surface border border-border-default text-text-primary rounded-tl-sm shadow-sm'
                    }`}
                  >
                    {msg.content}
                    {msg.interrupted && (
                      <span className="block mt-1 text-[10px] opacity-70">（被打断）</span>
                    )}
                  </div>
                  <span className="text-[10px] text-text-dim mt-1.5 px-1">
                    {formatTime(msg.timestamp)}
                  </span>
                </div>
              </div>
            )
          })}
          {isTyping && !streamingMessage && (
            <div className="flex gap-3 animate-fade-in">
              <AiAvatar />
              <div className="px-4 py-3 rounded-2xl rounded-tl-sm bg-bg-surface border border-border-default text-text-primary shadow-sm">
                <div className="flex gap-1.5 items-center h-5">
                  <span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce [animation-delay:-0.3s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce [animation-delay:-0.15s]" />
                  <span className="w-1.5 h-1.5 rounded-full bg-primary-400 animate-bounce" />
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </main>

      {/* Input */}
      <footer className="shrink-0 px-4 py-4 border-t border-border-default bg-bg-surface/80 backdrop-blur-sm">
        <div className="max-w-3xl mx-auto flex items-end gap-3">
          <div className="flex-1 relative">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={1}
              disabled={isTyping}
              placeholder="想说什么…"
              className="w-full rounded-2xl border border-border-default bg-bg-surface px-4 py-3 pr-12 text-sm text-text-primary placeholder:text-text-dim outline-none focus:border-primary-400 focus:ring-2 focus:ring-primary-400/15 transition-all resize-none max-h-32"
              style={{ minHeight: '48px' }}
            />
            <span className="absolute right-3 bottom-3 text-[10px] text-text-dim hidden sm:inline">
              Enter 发送
            </span>
          </div>
          <button
            onClick={handleSend}
            disabled={!input.trim() || isTyping}
            className="shrink-0 w-12 h-12 rounded-2xl bg-primary-500 text-white flex items-center justify-center hover:bg-primary-600 active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-sm"
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
        <p className="text-center text-[10px] text-text-dim mt-2">
          Demo 体验 · 对话仅用于展示记忆与情感能力
        </p>
      </footer>

      {/* Memory Drawer */}
      {memoryOpen && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div
            className="absolute inset-0 bg-black/20 backdrop-blur-[2px]"
            onClick={() => setMemoryOpen(false)}
          />
          <div className="relative w-full max-w-md h-full bg-bg-surface border-l border-border-default shadow-2xl animate-slide-up overflow-y-auto">
            <div className="sticky top-0 z-10 bg-bg-surface/95 backdrop-blur-sm border-b border-border-default px-5 py-4 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Brain className="w-4 h-4 text-primary-500" />
                <h2 className="text-sm font-semibold">我记得你什么</h2>
              </div>
              <button
                onClick={() => setMemoryOpen(false)}
                className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5 space-y-5">
              {/* Mode tabs */}
              <div className="flex gap-2 p-1 bg-bg-deep rounded-xl">
                <button
                  onClick={() => {
                    setMemoryMode('visualize')
                    loadMemory('visualize')
                  }}
                  className={`flex-1 py-1.5 text-xs font-medium rounded-lg transition-all ${
                    memoryMode === 'visualize'
                      ? 'bg-bg-surface text-primary-700 shadow-sm'
                      : 'text-text-muted hover:text-text-secondary'
                  }`}
                >
                  记忆图谱
                </button>
                <button
                  onClick={() => {
                    setMemoryMode('recall')
                    loadMemory('recall')
                  }}
                  className={`flex-1 py-1.5 text-xs font-medium rounded-lg transition-all ${
                    memoryMode === 'recall'
                      ? 'bg-bg-surface text-primary-700 shadow-sm'
                      : 'text-text-muted hover:text-text-secondary'
                  }`}
                >
                  回忆搜索
                </button>
              </div>

              {memoryMode === 'recall' && (
                <div className="flex gap-2">
                  <input
                    value={memoryQuery}
                    onChange={(e) => setMemoryQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && loadMemory('recall')}
                    placeholder="输入关键词…"
                    className="flex-1 rounded-xl border border-border-default bg-bg-surface px-3 py-2 text-xs outline-none focus:border-primary-400"
                  />
                  <button
                    onClick={() => loadMemory('recall')}
                    disabled={memoryLoading}
                    className="px-3 py-2 rounded-xl bg-primary-500 text-white text-xs hover:bg-primary-600 disabled:opacity-50"
                  >
                    搜索
                  </button>
                </div>
              )}

              {memoryLoading ? (
                <div className="space-y-3">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-16 rounded-xl bg-bg-hover animate-pulse" />
                  ))}
                </div>
              ) : (
                <>
                  {memoryData.stats && memoryMode === 'visualize' && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-2 gap-3">
                        <div className="bg-bg-deep rounded-xl p-3 text-center">
                          <p className="text-xl font-bold text-primary-600">{memoryData.stats.fact_count}</p>
                          <p className="text-[10px] text-text-muted mt-0.5">长期事实</p>
                        </div>
                        <div className="bg-bg-deep rounded-xl p-3 text-center">
                          <p className="text-xl font-bold text-accent-600">{memoryData.stats.episode_count}</p>
                          <p className="text-[10px] text-text-muted mt-0.5">场景记忆</p>
                        </div>
                      </div>

                      {memoryData.stats.top_categories && memoryData.stats.top_categories.length > 0 && (
                        <div>
                          <h3 className="text-[10px] font-medium text-text-secondary mb-2">记忆分类</h3>
                          <div className="flex flex-wrap gap-2">
                            {memoryData.stats.top_categories.map(([cat, count]) => (
                              <span
                                key={cat}
                                className="px-2 py-1 rounded-lg bg-bg-deep text-[10px] text-text-secondary border border-border-subtle"
                              >
                                {cat} <span className="text-primary-500 ml-0.5">{count}</span>
                              </span>
                            ))}
                          </div>
                        </div>
                      )}

                      {memoryData.stats.top_emotions && memoryData.stats.top_emotions.length > 0 && (
                        <div>
                          <h3 className="text-[10px] font-medium text-text-secondary mb-2">情绪分布</h3>
                          <div className="flex flex-wrap gap-2">
                            {memoryData.stats.top_emotions.map(([emo, count]) => (
                              <span
                                key={emo}
                                className="px-2 py-1 rounded-lg bg-bg-deep text-[10px] text-text-secondary border border-border-subtle"
                              >
                                {emo} <span className="text-accent-500 ml-0.5">{count}</span>
                              </span>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {memoryData.facts.length > 0 && (
                    <section>
                      <h3 className="text-xs font-semibold text-text-secondary mb-2 flex items-center gap-1.5">
                        <Sparkles className="w-3 h-3 text-accent-500" />
                        我记得的事实
                      </h3>
                      <div className="space-y-2">
                        {memoryData.facts.map((fact, idx) => (
                          <div
                            key={idx}
                            className="bg-bg-deep rounded-xl p-3 text-xs leading-relaxed border border-border-subtle"
                          >
                            <p className="text-text-primary">{fact.content || fact.fact}</p>
                            {(fact.category || fact.confidence) && (
                              <div className="flex items-center gap-2 mt-2 text-[10px] text-text-dim">
                                {fact.category && <span className="px-1.5 py-0.5 bg-white rounded-md">{fact.category}</span>}
                                {fact.confidence !== undefined && (
                                  <span>置信度 {(fact.confidence * 100).toFixed(0)}%</span>
                                )}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {memoryData.episodes.length > 0 && (
                    <section>
                      <h3 className="text-xs font-semibold text-text-secondary mb-2 flex items-center gap-1.5">
                        <Clock className="w-3 h-3 text-primary-400" />
                        最近的场景
                      </h3>
                      <div className="space-y-2">
                        {memoryData.episodes.map((ep, idx) => (
                          <div
                            key={idx}
                            className="bg-bg-deep rounded-xl p-3 text-xs leading-relaxed border border-border-subtle"
                          >
                            <p className="text-text-primary">{ep.summary || ep.content}</p>
                            {ep.timestamp && (
                              <p className="text-[10px] text-text-dim mt-1.5">
                                {new Date(ep.timestamp).toLocaleString()}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {memoryData.facts.length === 0 && memoryData.episodes.length === 0 && (
                    <div className="text-center py-10 text-text-dim">
                      <MessageCircle className="w-8 h-8 mx-auto mb-2 opacity-40" />
                      <p className="text-xs">还没有足够的记忆。</p>
                      <p className="text-[11px] mt-1">多聊几句，我会慢慢记得你。</p>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Exit Modal */}
      {exitOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center px-4">
          <div className="absolute inset-0 bg-black/25 backdrop-blur-[2px]" onClick={() => setExitOpen(false)} />
          <div className="relative w-full max-w-sm bg-bg-surface rounded-2xl border border-border-default shadow-2xl p-6 text-center animate-scale-in">
            <div className="w-12 h-12 mx-auto rounded-full bg-bg-deep border border-border-default flex items-center justify-center mb-4">
              <LogOut className="w-5 h-5 text-primary-500" />
            </div>
            <p className="text-sm leading-relaxed whitespace-pre-line text-text-primary mb-6">
              {exitMessage}
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setExitOpen(false)}
                className="flex-1 py-2 rounded-xl border border-border-default text-xs font-medium text-text-secondary hover:bg-bg-hover transition-colors"
              >
                留在对话里
              </button>
              <button
                onClick={confirmExit}
                className="flex-1 py-2 rounded-xl bg-primary-500 text-white text-xs font-medium hover:bg-primary-600 transition-colors flex items-center justify-center gap-1"
              >
                退出 <ChevronRight className="w-3 h-3" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

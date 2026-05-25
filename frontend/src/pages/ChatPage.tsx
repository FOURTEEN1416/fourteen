import { useEffect, useState, useCallback, useRef } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import { useChatStore } from '../store/chatStore'
import { useErrorStore } from '../store/errorStore'
import { useSettingsStore } from '../store/settingsStore'
import { useActiveCharacter } from '../hooks/useQueries'
import { api } from '../api/client'
import MessageList from '../components/chat/MessageList'
import ChatInput from '../components/chat/ChatInput'
import EmotionPanel from '../components/emotion/EmotionPanel'
import ProactiveToast from '../components/chat/ProactiveToast'

const MAX_STREAM_RETRIES = 3

export default function ChatPage() {
  const { sendMessage } = useWebSocket()
  const sessionId = useChatStore((s) => s.sessionId)
  const isConnected = useChatStore((s) => s.isConnected)
  const setSessionId = useChatStore((s) => s.setSessionId)
  const [sessionError, setSessionError] = useState(false)
  const [sessionLoading, setSessionLoading] = useState(true)
  const [_loading, setLoading] = useState(false)
  const addMessage = useChatStore((s) => s.addMessage)
  const setEmotion = useChatStore((s) => s.setEmotion)
  const setStreaming = useChatStore((s) => s.setStreaming)
  const appendStreamToken = useChatStore((s) => s.appendStreamToken)
  const finalizeStreamMessage = useChatStore((s) => s.finalizeStreamMessage)
  const addToast = useErrorStore((s) => s.addToast)
  const { activeCharacter } = useActiveCharacter()
  const useStreaming = useSettingsStore((s) => s.useStreaming)

  const streamAbortRef = useRef<AbortController | null>(null)
  const streamRetryCountRef = useRef(0)
  const lastUserMsgRef = useRef<{ text: string; msgType: string } | null>(null)

  const createSession = useCallback(async () => {
    setSessionError(false)
    setSessionLoading(true)
    try {
      const { data } = await api.createSession()
      const sid = (data as { session_id: string }).session_id
      setSessionId(sid)
    } catch {
      setSessionError(true)
    } finally {
      setSessionLoading(false)
    }
  }, [setSessionId])

  useEffect(() => {
    if (!sessionId) {
      createSession()
    } else {
      setSessionLoading(false)
    }
  }, [sessionId, createSession])

  const runChatStream = useCallback(async (text: string, msgType: string) => {
    const controller = new AbortController()
    streamAbortRef.current = controller

    setStreaming(true)
    try {
      const { promise } = api.chatStream(text, sessionId, msgType)
      const response = await promise
      const reader = (response.data as unknown as ReadableStream).getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const payload = line.slice(6).trim()
            if (payload === '[DONE]') continue
            try {
              const parsed = JSON.parse(payload)
              if (parsed.token) {
                appendStreamToken(parsed.token)
              } else if (parsed.emotion) {
                setEmotion(parsed.emotion)
              } else if (parsed.reply) {
                appendStreamToken(parsed.reply)
              }
            } catch {
              appendStreamToken(payload)
            }
          }
        }
      }
      finalizeStreamMessage(false)
      streamRetryCountRef.current = 0
    } catch (err: any) {
      if (err?.name === 'AbortError' || err?.name === 'CanceledError') return
      const hasPartialContent = useChatStore.getState().streamingMessage?.content
      if (hasPartialContent) {
        finalizeStreamMessage(true)
      } else {
        finalizeStreamMessage(false)
        addToast({ type: 'warning', message: '流式响应中断' })
      }
    } finally {
      setStreaming(false)
      streamAbortRef.current = null
    }
  }, [sessionId, setStreaming, appendStreamToken, finalizeStreamMessage, setEmotion, addToast])

  const handleSend = useCallback(async (text: string, msgType?: string, fileUrl?: string) => {
    if (!text && !fileUrl) return

    if (isConnected) {
      sendMessage(text, true, msgType || 'text', fileUrl || '')
      return
    }

    addMessage({ role: 'user', content: text, timestamp: Date.now() })
    lastUserMsgRef.current = { text, msgType: msgType || 'text' }
    streamRetryCountRef.current = 0
    setLoading(true)

    if (useStreaming) {
      await runChatStream(text, msgType || 'text')
    } else {
      try {
        const { data } = await api.chat(text, sessionId, msgType || 'text')
        addMessage({ role: 'assistant', content: data.reply || '', emotion: data.emotion?.current_emotion, timestamp: Date.now() })
        if (data.emotion) setEmotion(data.emotion)
      } catch {
        addToast({ type: 'error', message: '发送失败' })
      }
    }
    setLoading(false)
  }, [isConnected, sessionId, useStreaming, addMessage, sendMessage, setEmotion, addToast, runChatStream])

  const handleRetryStream = useCallback(async () => {
    const last = lastUserMsgRef.current
    if (!last) return
    if (streamRetryCountRef.current >= MAX_STREAM_RETRIES) {
      addToast({ type: 'error', message: '重试次数已达上限，请重新发送' })
      return
    }
    streamRetryCountRef.current += 1
    setLoading(true)
    await runChatStream(last.text, last.msgType)
    setLoading(false)
  }, [runChatStream, addToast])

  useEffect(() => {
    return () => {
      streamAbortRef.current?.abort()
    }
  }, [])

  return (
    <div className="flex-1 flex flex-col h-full">
      <ProactiveToast />

      <div className="flex-1 flex">
        <div className="flex-1 flex flex-col min-w-0">
          <div className="border-b border-gray-200 px-4 py-3 bg-gray-50">
            <h1 className="text-sm font-semibold text-gray-800">{activeCharacter?.name ?? '十四'}</h1>
          </div>

          {!isConnected && !sessionLoading && (
            <div className="px-4 py-2 bg-yellow-50 border-t border-yellow-200 text-xs text-yellow-700 flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 inline-block" />
              {useStreaming ? '流式模式' : '普通模式'} · 未连接到服务器，消息将通过 HTTP 发送
            </div>
          )}

          {sessionError ? (
            <div className="flex-1 flex items-center justify-center p-8">
              <div className="text-center">
                <p className="text-sm text-red-400 mb-3">会话连接失败，请检查后端服务是否正常运行</p>
                <button
                  onClick={createSession}
                  className="px-4 py-2 bg-primary-500 text-white rounded-md text-sm hover:bg-primary-600 transition-colors"
                >
                  重试连接
                </button>
              </div>
            </div>
          ) : sessionLoading ? (
            <div className="flex-1 flex items-center justify-center">
              <p className="text-sm text-gray-400">连接中...</p>
            </div>
          ) : (
            <>
              <MessageList onRetryStream={handleRetryStream} />
              <ChatInput onSend={handleSend} />
            </>
          )}
        </div>

        <aside className="hidden xl:flex flex-col w-64 border-l border-gray-200 p-3 gap-3 overflow-y-auto">
          <EmotionPanel characterId={activeCharacter?.character_id} maxAffinity={8} />
        </aside>
      </div>
    </div>
  )
}

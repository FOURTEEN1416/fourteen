import client from './client'
import type { EmotionState } from '../types/api'

export interface DemoMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export interface DemoStreamDone {
  reply: string
  emotion: EmotionState | null
  process_time: number
}

/**
 * Demo 聊天流 — 直接使用 fetch 而非 axios client
 * 设计原因: demo 端点为公开体验，不需要认证 header
 * 注意: 后端已对 demo 端点添加了 API Key 认证，此处应通过 axios client 发送
 */
export async function* demoChatStream(
  message: string,
  sessionId = '',
  messageType = 'text',
  onDone?: (data: DemoStreamDone) => void,
  onError?: (err: Error) => void,
): AsyncGenerator<string, void, unknown> {
  const resp = await fetch('/api/demo/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId, message_type: messageType }),
  })
  if (!resp.ok || !resp.body) {
    const err = new Error(`Demo stream failed: ${resp.status}`)
    onError?.(err)
    throw err
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data: ')) continue
      const payload = trimmed.slice(6)
      if (payload === '[DONE]') return
      try {
        const parsed = JSON.parse(payload)
        if (parsed.token) yield parsed.token
        if (parsed.done && onDone) {
          onDone({
            reply: parsed.reply || '',
            emotion: parsed.emotion || null,
            process_time: parsed.process_time || 0,
          })
        }
        if (parsed.error) throw new Error(parsed.error)
      } catch {
        // ignore malformed line
      }
    }
  }
}

export function recallMemory(query = '', sessionId = '', topK = 5) {
  return client.get('/demo/memory/recall', {
    params: { query, session_id: sessionId, top_k: topK },
  })
}

export function visualizeMemory(limit = 50) {
  return client.get('/demo/memory/visualization', { params: { limit } })
}

export function exitDemo(sessionId = '') {
  return client.post('/demo/exit', { session_id: sessionId })
}

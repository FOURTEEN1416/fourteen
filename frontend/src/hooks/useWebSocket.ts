import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../store/chatStore'
import type { WSIncomingMessage } from '../types/api'

const WS_URL = import.meta.env.VITE_WS_URL || `ws://${window.location.hostname}:8765`
const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 30000
const RECONNECT_MULTIPLIER = 2

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const reconnectDelay = useRef(RECONNECT_BASE_MS)
  const {
    sessionId, isConnected, setConnected, setStreaming,
    addMessage, setEmotion, setProactiveMessage,
    appendStreamToken, finalizeStreamMessage,
  } = useChatStore()

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      reconnectDelay.current = RECONNECT_BASE_MS
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = undefined
      }
    }

    ws.onclose = () => {
      setConnected(false)
      setStreaming(false)
      finalizeStreamMessage()
      const delay = reconnectDelay.current
      reconnectDelay.current = Math.min(delay * RECONNECT_MULTIPLIER, RECONNECT_MAX_MS)
      reconnectTimer.current = setTimeout(connect, delay)
    }

    ws.onerror = () => {
      ws.close()
    }

    ws.onmessage = (event) => {
      try {
        const data: WSIncomingMessage = JSON.parse(event.data)

        switch (data.type) {
          case 'reply':
            addMessage({ role: 'assistant', content: data.content || '', emotion: data.emotion?.current_emotion, timestamp: Date.now() })
            if (data.emotion) setEmotion(data.emotion)
            break

          case 'stream_start':
            setStreaming(true)
            break

          case 'stream_token':
            appendStreamToken(data.content || '')
            break

          case 'stream_end':
            finalizeStreamMessage()
            setStreaming(false)
            break

          case 'proactive':
            setProactiveMessage(data.content || null)
            break

          case 'error':
            console.error('WS error:', data.message)
            break
        }
      } catch (e) {
        console.error('WS parse error:', e)
      }
    }
  }, [setConnected, setStreaming, addMessage, setEmotion, setProactiveMessage, appendStreamToken, finalizeStreamMessage])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const sendMessage = useCallback((message: string, stream = false) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return false

    addMessage({ role: 'user', content: message, timestamp: Date.now() })

    ws.send(JSON.stringify({
      type: 'chat',
      message,
      session_id: sessionId,
      stream,
    }))
    return true
  }, [sessionId, addMessage])

  const sendPing = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'ping' }))
  }, [])

  return { isConnected, sendMessage, sendPing }
}

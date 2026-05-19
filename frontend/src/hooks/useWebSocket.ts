import { useEffect, useRef, useCallback } from 'react'
import { useChatStore } from '../store/chatStore'
import type { WSIncomingMessage } from '../types/api'

const WS_URL = `ws://${window.location.hostname}:8765`

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const {
    sessionId, isConnected, setConnected, setStreaming,
    addMessage, setEmotion, setProactiveMessage,
  } = useChatStore()

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = undefined
      }
    }

    ws.onclose = () => {
      setConnected(false)
      setStreaming(false)
      reconnectTimer.current = setTimeout(connect, 3000)
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
            addMessage({ role: 'assistant', content: data.content || '', timestamp: Date.now() })
            break

          case 'stream_end':
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
  }, [setConnected, setStreaming, addMessage, setEmotion, setProactiveMessage])

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

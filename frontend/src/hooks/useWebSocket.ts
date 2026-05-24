import { useEffect, useRef, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useChatStore } from '../store/chatStore'
import { queryKeys } from './useQueries'
import type { WSIncomingMessage } from '../types/api'

// P1: 生产环境自动使用 wss://
const _isSecure = window.location.protocol === 'https:'
const WS_URL = import.meta.env.VITE_WS_URL || `${_isSecure ? 'wss' : 'ws'}://${window.location.hostname}:8765`
const RECONNECT_BASE_MS = 1000
const RECONNECT_MAX_MS = 30000
const RECONNECT_MULTIPLIER = 2

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const reconnectDelay = useRef(RECONNECT_BASE_MS)
  const mountedRef = useRef(true)
  const queryClient = useQueryClient()

  // 使用独立选择器避免不必要的重渲染
  const sessionId = useChatStore((state) => state.sessionId)
  const isConnected = useChatStore((state) => state.isConnected)
  const setConnected = useChatStore((state) => state.setConnected)
  const setStreaming = useChatStore((state) => state.setStreaming)
  const addMessage = useChatStore((state) => state.addMessage)
  const setEmotion = useChatStore((state) => state.setEmotion)
  const setProactiveMessage = useChatStore((state) => state.setProactiveMessage)
  const appendStreamToken = useChatStore((state) => state.appendStreamToken)
  const finalizeStreamMessage = useChatStore((state) => state.finalizeStreamMessage)
  const setCurrentCharacter = useChatStore((state) => state.setCurrentCharacter)
  const setEmotionStage = useChatStore((state) => state.setEmotionStage)
  const setAffinity = useChatStore((state) => state.setAffinity)
  const setLastSticker = useChatStore((state) => state.setLastSticker)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    if (!mountedRef.current) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      if (!mountedRef.current) { ws.close(); return }
      setConnected(true)
      reconnectDelay.current = RECONNECT_BASE_MS
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = undefined
      }
    }

    ws.onclose = () => {
      if (!mountedRef.current) return
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
      if (!mountedRef.current) return
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

          case 'character_switched':
            if (data.data?.character_id && data.data?.name) {
              setCurrentCharacter(data.data.character_id, data.data.name)
              const cid = data.data.character_id
              queryClient.invalidateQueries({ queryKey: queryKeys.affinity.detail(cid) })
              queryClient.invalidateQueries({ queryKey: queryKeys.emotionStage.detail(cid) })
              queryClient.invalidateQueries({ queryKey: queryKeys.vitalSigns.detail(cid) })
              queryClient.invalidateQueries({ queryKey: queryKeys.characters.all })
            }
            break

          case 'emotion_stage_changed':
            if (data.data?.new_stage) {
              setEmotionStage(data.data.new_stage)
            }
            break

          case 'affinity_changed':
            if (data.data?.new_value !== undefined) {
              setAffinity(data.data.new_value)
            }
            break

          case 'sticker_send':
            if (data.data?.sticker_id && data.data?.category) {
              setLastSticker({ sticker_id: data.data.sticker_id, category: data.data.category })
            }
            break

          case 'pong':
            break

          case 'error':
            console.error('WS error:', data.message)
            break
        }
      } catch (e) {
        console.error('WS parse error:', e)
      }
    }
  }, [setConnected, setStreaming, addMessage, setEmotion, setProactiveMessage, appendStreamToken, finalizeStreamMessage, setCurrentCharacter, setEmotionStage, setAffinity, setLastSticker, queryClient])

  // P2: React Strict Mode 兼容 - 使用 cleanup 标志防止竞态
  useEffect(() => {
    let isCleanedUp = false
    mountedRef.current = true

    // 延迟连接以避免 Strict Mode 双重挂载导致的重复连接
    const connectTimer = setTimeout(() => {
      if (!isCleanedUp && mountedRef.current) {
        connect()
      }
    }, 0)

    return () => {
      isCleanedUp = true
      mountedRef.current = false
      clearTimeout(connectTimer)
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      reconnectTimer.current = undefined
      if (wsRef.current) {
        wsRef.current.onclose = null  // 防止 onclose 触发重连
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect])

  const sendMessage = useCallback((message: string, stream = false, messageType = 'text', fileUrl = '') => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return false

    addMessage({ role: 'user', content: message, timestamp: Date.now() })

    ws.send(JSON.stringify({
      type: 'chat',
      message,
      session_id: sessionId,
      stream,
      message_type: messageType,
      file_url: fileUrl,
    }))
    return true
  }, [sessionId, addMessage])

  const sendPing = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: 'ping' }))
  }, [])

  return { isConnected, sendMessage, sendPing }
}

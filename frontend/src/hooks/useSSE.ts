import { useState, useEffect, useCallback, useRef } from 'react'

interface SSEOptions {
  onMessage: (data: string) => void
  onError?: (error: Event) => void
  maxReconnectDelay?: number
}

export function useSSE(url: string, options: SSEOptions) {
  const [connected, setConnected] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const attemptRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const maxDelay = options.maxReconnectDelay ?? 30000

  const connect = useCallback(() => {
    if (esRef.current) { esRef.current.close(); esRef.current = null }

    const es = new EventSource(url)
    esRef.current = es

    es.onopen = () => {
      setConnected(true)
      setReconnecting(false)
      attemptRef.current = 0
    }

    es.onmessage = (event) => {
      options.onMessage(event.data)
    }

    es.onerror = (e) => {
      setConnected(false)
      setReconnecting(true)
      es.close()
      esRef.current = null
      options.onError?.(e)

      const delay = Math.min(1000 * 2 ** attemptRef.current, maxDelay)
      attemptRef.current += 1
      timerRef.current = setTimeout(connect, delay)
    }
  }, [url, options.onMessage, options.onError, maxDelay])

  const disconnect = useCallback(() => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = undefined }
    if (esRef.current) { esRef.current.close(); esRef.current = null }
    setConnected(false)
    setReconnecting(false)
    attemptRef.current = 0
  }, [])

  useEffect(() => {
    connect()
    return disconnect
  }, [connect, disconnect])

  return { connected, reconnecting, connect, disconnect }
}

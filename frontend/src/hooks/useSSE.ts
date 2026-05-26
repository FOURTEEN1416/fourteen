import { useState, useEffect, useCallback, useRef } from 'react'

interface SSEOptions {
  onMessage: (data: string) => void
  onError?: (error: Event) => void
  maxReconnectDelay?: number
}

const DEFAULT_MAX_DELAY = 30000

export function useSSE(url: string, options: SSEOptions) {
  const [connected, setConnected] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const esRef = useRef<EventSource | null>(null)
  const attemptRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const onMessageRef = useRef(options.onMessage)
  const onErrorRef = useRef(options.onError)
  const connectRef = useRef<() => void>(() => {})
  const maxDelay = options.maxReconnectDelay ?? DEFAULT_MAX_DELAY

  useEffect(() => {
    onMessageRef.current = options.onMessage
    onErrorRef.current = options.onError
  })

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
      onMessageRef.current(event.data)
    }

    es.onerror = (e) => {
      setConnected(false)
      setReconnecting(true)
      es.close()
      esRef.current = null
      onErrorRef.current?.(e)

      const delay = Math.min(1000 * 2 ** attemptRef.current, maxDelay)
      attemptRef.current += 1
      timerRef.current = setTimeout(() => connectRef.current(), delay)
    }
  }, [url, maxDelay])

  useEffect(() => { connectRef.current = connect })

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

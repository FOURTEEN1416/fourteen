import { useEffect, useRef, useCallback } from 'react'

export function useSmartPoll(
  fetchFn: () => Promise<void>,
  intervalMs: number = 15000,
  enabled: boolean = true
) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const fetchRef = useRef(fetchFn)
  const isFetchingRef = useRef(false)
  const pollRef = useRef<() => Promise<void>>(async () => {})

  useEffect(() => { fetchRef.current = fetchFn })

  const poll = useCallback(async () => {
    if (document.hidden) {
      timerRef.current = setTimeout(() => pollRef.current(), intervalMs)
      return
    }
    if (isFetchingRef.current) {
      timerRef.current = setTimeout(() => pollRef.current(), intervalMs)
      return
    }
    isFetchingRef.current = true
    try {
      await fetchRef.current()
    } finally {
      isFetchingRef.current = false
    }
    timerRef.current = setTimeout(() => pollRef.current(), intervalMs)
  }, [intervalMs])

  useEffect(() => { pollRef.current = poll })

  useEffect(() => {
    if (!enabled) return

    const handleVisibilityChange = () => {
      if (document.hidden) {
        if (timerRef.current) {
          clearTimeout(timerRef.current)
          timerRef.current = undefined
        }
      } else {
        pollRef.current()
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    pollRef.current()

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
      if (timerRef.current) {
        clearTimeout(timerRef.current)
        timerRef.current = undefined
      }
    }
  }, [enabled])
}

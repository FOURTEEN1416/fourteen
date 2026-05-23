import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import type { TrainingProgress, TrainingStatusEnum } from '../types/api'

const FAST_INTERVAL = 5000
const SLOW_INTERVAL = 30000
const TRAINING_ACTIVE: TrainingStatusEnum[] = ['extracting', 'cleaning', 'training']

export function useTrainingProgress() {
  const [progress, setProgress] = useState<TrainingProgress | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const statusRef = useRef<string | undefined>(undefined)
  const mountedRef = useRef(true)

  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false } }, [])

  const refetch = useCallback(async () => {
    try {
      const { data } = await api.trainingProgress()
      if (!mountedRef.current) return
      setProgress(data as TrainingProgress)
      if ((data as TrainingProgress)?.status) {
        statusRef.current = (data as TrainingProgress).status
      }
    } catch {
      // silent
    } finally {
      if (mountedRef.current) setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    const scheduleNext = () => {
      const interval = TRAINING_ACTIVE.includes(statusRef.current as TrainingStatusEnum)
        ? FAST_INTERVAL
        : SLOW_INTERVAL
      timerRef.current = setTimeout(async () => {
        await refetch()
        if (mountedRef.current) scheduleNext()
      }, interval)
    }

    refetch()
    scheduleNext()

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [refetch])

  return { progress, isLoading, refetch }
}

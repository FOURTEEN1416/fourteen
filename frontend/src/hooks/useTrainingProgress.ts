import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import type { TrainingProgress, TrainingStatusEnum } from '../types/api'

const FAST_INTERVAL = 5000
const SLOW_INTERVAL = 30000

export function useTrainingProgress() {
  const [progress, setProgress] = useState<TrainingProgress | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  const refetch = useCallback(async () => {
    try {
      const { data } = await api.trainingProgress()
      setProgress(data as TrainingProgress)
    } catch {
      // silent
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    refetch()

    const getInterval = () => {
      const status = progress?.status
      const active: TrainingStatusEnum[] = ['extracting', 'cleaning', 'training']
      return active.includes(status as TrainingStatusEnum) ? FAST_INTERVAL : SLOW_INTERVAL
    }

    const schedule = () => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = setInterval(refetch, getInterval())
    }

    schedule()

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [refetch, progress?.status])

  return { progress, isLoading, refetch }
}

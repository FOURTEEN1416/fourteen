import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import type { DashboardStats, WeChatStatus, ProactiveEngineState, HealthStatus } from '../types/api'

const POLL_INTERVAL = 15000

export function useDashboardData() {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [wechat, setWechat] = useState<WeChatStatus | null>(null)
  const [health, setHealth] = useState<HealthStatus | null>(null)
  const [proactive, setProactive] = useState<ProactiveEngineState | null>(null)
  const [loading, setLoading] = useState(true)
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  const refetch = useCallback(async () => {
    try {
      const [h, s, w, p] = await Promise.allSettled([
        api.health(),
        api.dashboardStats(),
        api.wechatStatus(),
        api.proactiveState(),
      ])

      if (h.status === 'fulfilled') setHealth(h.value.data as HealthStatus)
      if (s.status === 'fulfilled') setStats(s.value.data as DashboardStats)
      if (w.status === 'fulfilled') setWechat(w.value.data as WeChatStatus)
      if (p.status === 'fulfilled') setProactive(p.value.data as ProactiveEngineState)
    } catch {
      // silent
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refetch()
    timerRef.current = setInterval(refetch, POLL_INTERVAL)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [refetch])

  return { stats, wechat, health, proactive, loading, refetch }
}

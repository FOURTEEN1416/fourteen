import { create } from 'zustand'
import { api } from '../api/client'
import type { DashboardStats, WeChatStatus, TrainingProgress, ProactiveEngineState, HealthStatus } from '../types/api'

interface DashboardState {
  stats: DashboardStats | null
  wechatStatus: WeChatStatus | null
  trainingProgress: TrainingProgress | null
  proactiveState: ProactiveEngineState | null
  health: HealthStatus | null
  loading: boolean
  lastRefresh: number | null
  fetchAll: () => Promise<void>
  startPolling: () => void
  stopPolling: () => void
}

let pollingTimer: ReturnType<typeof setInterval> | undefined
let trainingTimer: ReturnType<typeof setInterval> | undefined

export const useDashboardStore = create<DashboardState>((set, get) => ({
  stats: null,
  wechatStatus: null,
  trainingProgress: null,
  proactiveState: null,
  health: null,
  loading: false,
  lastRefresh: null,

  fetchAll: async () => {
    set({ loading: true })
    try {
      const [healthRes, statsRes, wechatRes, progressRes, proactiveRes] = await Promise.allSettled([
        api.health(),
        api.dashboardStats(),
        api.wechatStatus(),
        api.trainingProgress(),
        api.proactiveState(),
      ])

      set({
        health: healthRes.status === 'fulfilled' ? (healthRes.value.data as HealthStatus) : get().health,
        stats: statsRes.status === 'fulfilled' ? (statsRes.value.data as DashboardStats) : get().stats,
        wechatStatus: wechatRes.status === 'fulfilled' ? (wechatRes.value.data as WeChatStatus) : get().wechatStatus,
        trainingProgress: progressRes.status === 'fulfilled' ? (progressRes.value.data as TrainingProgress) : get().trainingProgress,
        proactiveState: proactiveRes.status === 'fulfilled' ? (proactiveRes.value.data as ProactiveEngineState) : get().proactiveState,
        lastRefresh: Date.now(),
      })
    } catch {
      // silent — partial data is fine
    } finally {
      set({ loading: false })
    }
  },

  startPolling: () => {
    get().stopPolling()
    get().fetchAll()
    pollingTimer = setInterval(() => get().fetchAll(), 15000)
  },

  stopPolling: () => {
    if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = undefined }
    if (trainingTimer) { clearInterval(trainingTimer); trainingTimer = undefined }
  },
}))

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import client, { api } from '../api/client'
import { useAuthStore } from '../store/authStore'
import { useErrorStore } from '../store/errorStore'
import type { EmotionState, DashboardStats, HealthStatus, WeChatStatus, TrainingProgress, MemoryFact, PsychProfile, PsychResetResult, PsychSnapshot, SafetyStats, SafetyLogEntry, RAGStats, VoiceStatus, PluginsList, ToolHistoryEntry, ProactiveHistoryEntry, MentalHealthSummary } from '../types/api'

/**
 * 账号维度（W1）：私人查询键必须携带**稳定的账号标识**（`users.id`）。
 *
 * 为什么不能用 token：token 会随 refresh 轮换、随登出消失，把凭证当缓存键会让
 * 同一账号的缓存被反复丢弃（无谓重取），且账号切换时**无法区分**归属。
 *
 * 为什么键里要带账号：清缓存（登出 / 切换账号时 `clearAccountScopedCache`）是
 * 兜底，键维度是主防线——**迟到的旧账号响应只会写进旧账号的键**，
 * 不可能复写到新账号正在读的键上。
 *
 * 取值为 getter：读取时刻才求值，账号切换后同一个 `queryKeys.x` 自动指向新账号，
 * 既不需要改任何调用点，也不会把「模块加载时的账号」固化进键。
 */
function accountScope(): string {
  const uid = useAuthStore.getState().user?.id
  return uid === undefined || uid === null ? 'anon' : String(uid)
}

/**
 * 账号维度的私有键（与 `queryKeys` 同源）：散落硬编码的私有键同样必须带账号，
 * 否则 A 的迟到响应会写进 B 正在读的键。
 */
function privateKey(...parts: Array<string | number | undefined>): readonly unknown[] {
  return ['acct', accountScope(), ...parts]
}

export const queryKeys = {
  get characters() {
    const scope = ['acct', accountScope(), 'characters'] as const
    return {
      all: scope,
      detail: (id: string) => [...scope, id] as const,
    }
  },

  get dashboard() { return ['acct', accountScope(), 'dashboard'] as const },
  get health() { return ['acct', accountScope(), 'health'] as const },
  get emotion() {
    const scope = ['acct', accountScope(), 'emotion'] as const
    return {
      state: [...scope, 'state'] as const,
      trend: (days: number) => [...scope, 'trend', days] as const,
      distribution: (days: number) => [...scope, 'distribution', days] as const,
    }
  },
  get persona() {
    const scope = ['acct', accountScope(), 'persona'] as const
    return {
      profile: [...scope, 'profile'] as const,
      evolution: [...scope, 'evolution'] as const,
    }
  },
  get memory() {
    const scope = ['acct', accountScope(), 'memory'] as const
    return { facts: (category?: string) => [...scope, 'facts', category] as const }
  },
  get config() { return ['acct', accountScope(), 'config'] as const },

  get training() {
    const scope = ['acct', accountScope(), 'training'] as const
    return {
      status: [...scope, 'status'] as const,
      progress: [...scope, 'progress'] as const,
    }
  },
  get wechat() {
    const scope = ['acct', accountScope(), 'wechat'] as const
    return {
      status: [...scope, 'status'] as const,
      connection: [...scope, 'connection'] as const,
      qrcode: [...scope, 'qrcode'] as const,
    }
  },
  get channels() { return ['acct', accountScope(), 'channels'] as const },
  get logs() {
    const scope = ['acct', accountScope(), 'logs'] as const
    return { all: (params?: Record<string, unknown>) => [...scope, params] as const }
  },
  get clone() {
    const scope = ['acct', accountScope(), 'clone'] as const
    return {
      contacts: (kw?: string) => [...scope, 'contacts', kw] as const,
      datasets: [...scope, 'datasets'] as const,
      stats: [...scope, 'stats'] as const,
    }
  },
  get psych() {
    const scope = ['acct', accountScope(), 'psych'] as const
    return {
      all: scope,
      profile: (characterId?: string) => [...scope, 'profile', characterId ?? ''] as const,
      snapshots: (characterId?: string) => [...scope, 'snapshots', characterId ?? ''] as const,
      mentalHealth: (characterId?: string) => [...scope, 'mentalHealth', characterId ?? ''] as const,
    }
  },
  achievements: (characterId: string) => ['acct', accountScope(), 'achievements', characterId] as const,
  get safety() {
    const scope = ['acct', accountScope(), 'safety'] as const
    return {
      stats: [...scope, 'stats'] as const,
      log: [...scope, 'log'] as const,
    }
  },
  get rag() {
    const scope = ['acct', accountScope(), 'rag'] as const
    return { stats: [...scope, 'stats'] as const }
  },
  get voice() {
    const scope = ['acct', accountScope(), 'voice'] as const
    return { status: [...scope, 'status'] as const }
  },
  get plugins() {
    const scope = ['acct', accountScope(), 'plugins'] as const
    return { all: scope }
  },
  get toolHistory() { return ['acct', accountScope(), 'tools', 'history'] as const },
  get proactiveHistory() { return ['acct', accountScope(), 'proactive', 'history'] as const },
}

export function useCharacters() {
  const { data, ...rest } = useUnifiedCharacters()
  return { ...rest, data: data?.characters ?? [] }
}

export function useActiveCharacter() {
  const { data: characters } = useCharacters()
  const active = characters?.find(c => c.is_active)
  return { activeCharacter: active ?? characters?.[0] ?? null, characters: characters ?? [] }
}

export function useDashboard() {
  return useQuery({
    queryKey: queryKeys.dashboard,
    queryFn: () => api.dashboardStats().then(r => r.data as DashboardStats),
    refetchInterval: 15 * 1000,
  })
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => api.health().then(r => r.data as HealthStatus),
    refetchInterval: 15 * 1000,
  })
}

export function useEmotionState() {
  return useQuery({
    queryKey: queryKeys.emotion.state,
    queryFn: () => api.emotionState().then(r => r.data as EmotionState),
    refetchInterval: 10 * 1000,
  })
}

export function useEmotionTrend(days = 7) {
  return useQuery({
    queryKey: queryKeys.emotion.trend(days),
    queryFn: () => api.emotionTrend(days).then(r => r.data as { trend: Array<{ timestamp: string; primary_emotion: string; intensity: number }> }),
    staleTime: 60 * 1000,
  })
}

/** 情绪分布（SP-1，2026-09-01）：会话内存态聚合，重启清零 */
export function useEmotionDistribution(days = 7) {
  return useQuery({
    queryKey: queryKeys.emotion.distribution(days),
    queryFn: () =>
      client
        .get('/emotion/distribution', { params: { days } })
        .then(r => r.data as { distribution: Array<{ emotion: string; count: number }>; total: number; days: number }),
    staleTime: 60 * 1000,
  })
}

export function useConfig() {
  return useQuery({
    queryKey: queryKeys.config,
    queryFn: () => api.config().then(r => r.data as Record<string, unknown>),
  })
}

export function useSaveConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (config: Record<string, unknown>) => api.saveConfig(config).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.config }),
  })
}

export function useWechatStatus() {
  return useQuery({
    queryKey: queryKeys.wechat.status,
    queryFn: () => api.wechatStatus().then(r => {
      const d = r.data as Record<string, unknown>
      return {
        connected: Boolean(d.connected),
        uptime_seconds: Number(d.uptime_seconds ?? 0),
        bot_id: String(d.bot_id ?? ''),
        last_activity: String(d.last_activity ?? ''),
        messages_today: Number(d.messages_today ?? 0),
        reconnect_attempts: Number(d.reconnect_attempts ?? 0),
        missed_heartbeats: Number(d.missed_heartbeats ?? 0),
        owner_user_id: d.owner_user_id,
        channels: d.channels,
      } as WeChatStatus & { bot_id?: string; owner_user_id?: unknown; channels?: unknown }
    }),
    // 本人通道状态轮询兜底
    refetchInterval: 15 * 1000,
    staleTime: 5 * 1000,
  })
}

export function useTrainingProgress() {
  return useQuery({
    queryKey: queryKeys.training.progress,
    queryFn: () => api.trainingProgress().then(r => r.data as TrainingProgress),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status && ['extracting', 'cleaning', 'training'].includes(status)) return 5000
      return 30000
    },
  })
}

export function useMemoryFacts(category?: string) {
  return useQuery({
    queryKey: queryKeys.memory.facts(category),
    queryFn: () => api.memoryFacts(category ?? '').then(r => (r.data as { facts: MemoryFact[] }).facts),
  })
}

export function usePersonaProfile() {
  return useQuery({
    queryKey: queryKeys.persona.profile,
    queryFn: () => api.personaProfile().then(r => r.data as import('../types/api').PersonaProfile),
    staleTime: 60 * 1000,
  })
}

export function usePersonaEvolutionLog() {
  return useQuery({
    queryKey: queryKeys.persona.evolution,
    queryFn: () => api.personaEvolutionLog().then(r => (r.data as { log: Array<import('../types/api').EvolutionLog['log'][0]> }).log),
    staleTime: 60 * 1000,
  })
}

export function useChannels() {
  return useQuery({
    queryKey: queryKeys.channels,
    queryFn: () => api.channels().then(r => r.data as { channels: import('../types/api').Channel[] }),
    refetchInterval: 30 * 1000,
  })
}

export function usePsychProfile(characterId?: string) {
  return useQuery({
    queryKey: queryKeys.psych.profile(characterId),
    queryFn: () => api.psychProfile(characterId).then(r => r.data as PsychProfile),
    refetchInterval: 30 * 1000,
  })
}

/** 角色成就（ADR-0014）：读取即幂等重算，60s 内不重复打后端 */
export function useAchievements(characterId: string | null | undefined) {
  return useQuery({
    queryKey: queryKeys.achievements(characterId ?? ''),
    queryFn: () =>
      client
        .get(`/characters/${characterId}/achievements`)
        .then(r => r.data as import('../types/api').AchievementsResponse),
    enabled: !!characterId,
    staleTime: 60 * 1000,
  })
}

export function usePsychSnapshots(limit = 20, characterId?: string) {
  return useQuery({
    queryKey: [...queryKeys.psych.snapshots(characterId), limit],
    queryFn: () => api.psychSnapshots(limit, characterId).then(r => (r.data as { snapshots: PsychSnapshot[] }).snapshots),
    staleTime: 30 * 1000,
  })
}

export function usePsychReset() {
  const qc = useQueryClient()
  return useMutation({
    // 可选角色维度：只清该角色名下的会话画像；缺省 = 清全部已学画像
    mutationFn: (characterId?: string) =>
      api.psychReset(characterId).then(r => r.data as PsychResetResult),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.psych.all })
    },
  })
}

export function useSafetyStats() {
  return useQuery({
    queryKey: queryKeys.safety.stats,
    queryFn: () => api.safetyStats().then(r => r.data as SafetyStats),
    refetchInterval: 15 * 1000,
  })
}

export function useSafetyLog(limit = 50) {
  return useQuery({
    queryKey: queryKeys.safety.log,
    queryFn: () => api.safetyLog(limit).then(r => (r.data as { log: SafetyLogEntry[] }).log),
  })
}

export function useSafetyConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (enabled: boolean) => api.safetyConfig(enabled).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.safety.stats }),
  })
}

export function useRAGStats() {
  return useQuery({
    queryKey: queryKeys.rag.stats,
    queryFn: () => api.ragStats().then(r => r.data as RAGStats),
  })
}

export function useVoiceStatus() {
  return useQuery({
    queryKey: queryKeys.voice.status,
    queryFn: () => api.voiceStatus().then(r => r.data as VoiceStatus),
    refetchInterval: 30 * 1000,
  })
}

export function usePlugins() {
  return useQuery({
    queryKey: queryKeys.plugins.all,
    queryFn: () => api.plugins().then(r => (r.data as PluginsList).plugins),
  })
}

export function useTogglePlugin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.togglePlugin(name, enabled).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.plugins.all }),
  })
}

export function useToolHistory(limit = 50) {
  return useQuery({
    queryKey: queryKeys.toolHistory,
    queryFn: () => api.toolHistory(limit).then(r => (r.data as { history: ToolHistoryEntry[] }).history),
  })
}

export function useProactiveHistory(limit = 50) {
  return useQuery({
    queryKey: queryKeys.proactiveHistory,
    queryFn: () => api.proactiveHistory(limit).then(r => (r.data as { history: ProactiveHistoryEntry[] }).history),
  })
}

export function useMentalHealth(characterId?: string) {
  return useQuery({
    queryKey: queryKeys.psych.mentalHealth(characterId),
    queryFn: () => api.psychMentalHealth(characterId).then(r => r.data as MentalHealthSummary),
    refetchInterval: 30 * 1000,
  })
}

// ═══ 统一角色管理 hooks ═══

export function useUnifiedCharacters(userId?: string, search?: string) {
  const hasFilter = userId !== undefined || search !== undefined
  return useQuery({
    queryKey: hasFilter
      ? [...queryKeys.characters.all, 'filter', userId, search]
      : queryKeys.characters.all,
    queryFn: () => api.listCharacters({ user_id: userId, search }),
    staleTime: 30 * 1000,
  })
}

export function useUnifiedCharacter(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.characters.detail(id!),
    queryFn: () => api.getCharacter(id!),
    enabled: !!id,
  })
}

export function useCreateCharacter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: import('../types/api').UnifiedCharacterCreate) => api.createCharacter(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.characters.all }),
  })
}

export function useUpdateCharacter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: import('../types/api').UnifiedCharacterUpdate }) => api.updateCharacter(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.characters.all }),
  })
}

export function useDeleteCharacter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.deleteCharacter(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.characters.all }),
  })
}

export function useActivateCharacter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.activateCharacter(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.characters.all })
    },
  })
}

// ═══ 角色音色绑定 hooks ═══

export function useCharacterVoice(characterId: string | undefined) {
  return useQuery({
    queryKey: privateKey('character', characterId, 'voice'),
    queryFn: () => api.getVoiceConfig(characterId!),
    enabled: !!characterId,
  })
}

export function useBindCharacterVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ characterId, data }: { characterId: string; data: import('../types/api').VoiceBindRequest }) => api.bindVoice(characterId, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: privateKey('character', variables.characterId, 'voice') })
    },
  })
}

export function useUpdateCharacterVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ characterId, data }: { characterId: string; data: import('../types/api').VoiceUpdateRequest }) => api.updateVoice(characterId, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: privateKey('character', variables.characterId, 'voice') })
    },
  })
}

export function useUnbindCharacterVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (characterId: string) => api.unbindVoice(characterId),
    onSuccess: (_data, characterId) => {
      qc.invalidateQueries({ queryKey: privateKey('character', characterId, 'voice') })
    },
  })
}

export function useTestCharacterVoice() {
  return useMutation({
    mutationFn: ({ characterId, text }: { characterId: string; text?: string }) => api.testVoice(characterId, text),
  })
}

export function useVoiceSpeakers(engine: string = 'mimo-tts') {
  return useQuery({
    queryKey: ['voice', 'speakers', engine],
    queryFn: () => api.getSpeakers(engine),
    staleTime: 5 * 60 * 1000,
  })
}

// ═══ 剧情线 hooks ═══

export function useStorylineConfig(characterId: string | undefined) {
  return useQuery({
    queryKey: privateKey('storyline', characterId, 'config'),
    queryFn: () => api.getStorylineConfig(characterId!),
    enabled: !!characterId,
    staleTime: 30 * 1000,
  })
}

export function useStorylineProgress(characterId: string | undefined) {
  return useQuery({
    queryKey: privateKey('storyline', characterId, 'progress'),
    queryFn: () => api.getStorylineProgress(characterId!),
    enabled: !!characterId,
    refetchInterval: 60 * 1000,
  })
}

export function useUpdateStorylineConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ characterId, config }: { characterId: string; config: import('../types/api').StorylineConfigRequest }) => api.updateStorylineConfig(characterId, config),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: privateKey('storyline', variables.characterId, 'config') })
      qc.invalidateQueries({ queryKey: privateKey('storyline', variables.characterId, 'progress') })
      qc.invalidateQueries({ queryKey: queryKeys.characters.all })
    },
  })
}

export function useDeleteStorylineConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (characterId: string) => api.deleteStorylineConfig(characterId),
    onSuccess: (_data, characterId) => {
      qc.invalidateQueries({ queryKey: privateKey('storyline', characterId, 'config') })
      qc.invalidateQueries({ queryKey: privateKey('storyline', characterId, 'progress') })
      qc.invalidateQueries({ queryKey: queryKeys.characters.all })
    },
    onError: (err: unknown) => {
      const message = err instanceof Error ? err.message : '剧情线删除失败'
      useErrorStore.getState().addToast({ type: 'error', message })
    },
  })
}

export function useDetectStoryline() {
  return useMutation({
    mutationFn: (characterId: string) => api.detectStoryline(characterId),
  })
}

export function useResetStoryline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (characterId: string) => api.resetStoryline(characterId),
    onSuccess: (_data, characterId) => {
      qc.invalidateQueries({ queryKey: privateKey('storyline', characterId, 'progress') })
    },
  })
}

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { shisiClient } from '../api/shisiClient'
import type { EmotionState, DashboardStats, HealthStatus, WeChatStatus, TrainingProgress, ProactiveEngineState, MemoryFact, PsychProfile, PsychSnapshot, SafetyStats, SafetyLogEntry, RAGStats, RAGSearchResult, VoiceStatus, PluginsList, FileUploadResult, ToolHistoryEntry, ProactiveHistoryEntry, MentalHealthSummary } from '../types/api'
import type { AffinityProgress, EmotionStageProgress, VitalSignsData } from '../types/shisi'
import type { CharacterState } from '../types/character'

export const queryKeys = {
  characters: { all: ['characters'] as const, detail: (id: string) => ['characters', id] as const },
  affinity: { detail: (id: string) => ['affinity', id] as const, unlocks: (id: string) => ['affinity', id, 'unlocks'] as const },
  emotionStage: { detail: (id: string) => ['emotionStage', id] as const, stages: ['emotionStage', 'stages'] as const },
  vitalSigns: { detail: (id: string) => ['vitalSigns', id] as const },
  dashboard: ['dashboard'] as const,
  health: ['health'] as const,
  emotion: { state: ['emotion', 'state'] as const, trend: (days: number) => ['emotion', 'trend', days] as const },
  persona: { profile: ['persona', 'profile'] as const, evolution: ['persona', 'evolution'] as const },
  memory: { facts: (category?: string) => ['memory', 'facts', category] as const },
  config: ['config'] as const,
  stickers: { all: ['stickers'] as const, recommend: (emotion: string) => ['stickers', 'recommend', emotion] as const },
  stats: { shisi: ['stats', 'shisi'] as const },
  training: { status: ['training', 'status'] as const, progress: ['training', 'progress'] as const },
  wechat: { status: ['wechat', 'status'] as const, connection: ['wechat', 'connection'] as const, qrcode: ['wechat', 'qrcode'] as const },
  channels: ['channels'] as const,
  logs: { all: (params?: Record<string, unknown>) => ['logs', params] as const },
  clone: { contacts: (kw?: string) => ['clone', 'contacts', kw] as const, datasets: ['clone', 'datasets'] as const, stats: ['clone', 'stats'] as const },
  psych: { profile: ['psych', 'profile'] as const, snapshots: ['psych', 'snapshots'] as const, mentalHealth: ['psych', 'mentalHealth'] as const },
  safety: { stats: ['safety', 'stats'] as const, log: ['safety', 'log'] as const },
  rag: { stats: ['rag', 'stats'] as const },
  voice: { status: ['voice', 'status'] as const },
  plugins: { all: ['plugins'] as const },
  toolHistory: ['tools', 'history'] as const,
  proactiveHistory: ['proactive', 'history'] as const,
}

export function useCharacters() {
  return useQuery({
    queryKey: queryKeys.characters.all,
    queryFn: () => shisiClient.characters.list() as Promise<CharacterState[]>,
    staleTime: 60 * 1000,
  })
}

export function useActiveCharacter() {
  const { data: characters } = useCharacters()
  const active = characters?.find(c => c.is_active)
  return { activeCharacter: active ?? characters?.[0] ?? null, characters: characters ?? [] }
}

export function useAffinity(characterId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.affinity.detail(characterId!),
    queryFn: () => shisiClient.affinity.get(characterId!) as Promise<AffinityProgress>,
    enabled: !!characterId,
  })
}

export function useAffinityUnlocks(characterId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.affinity.unlocks(characterId!),
    queryFn: () => shisiClient.affinity.unlocks(characterId!) as Promise<{ affinity: number; unlocks: Array<{ name: string; affinity_threshold: number; unlocked_at?: string }> }>,
    enabled: !!characterId,
  })
}

export function useEmotionStage(characterId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.emotionStage.detail(characterId!),
    queryFn: () => shisiClient.emotionStage.get(characterId!) as Promise<EmotionStageProgress>,
    enabled: !!characterId,
  })
}

export function useEmotionStageList() {
  return useQuery({
    queryKey: queryKeys.emotionStage.stages,
    queryFn: () => shisiClient.emotionStage.listStages() as Promise<Array<{ name: string; min: number; features: string[] }>>,
    staleTime: 5 * 60 * 1000,
  })
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
    queryFn: () => api.wechatStatus().then(r => r.data as WeChatStatus),
    refetchInterval: 10 * 1000,
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

export function useProactiveState() {
  return useQuery({
    queryKey: ['proactive', 'state'],
    queryFn: () => api.proactiveState().then(r => r.data as ProactiveEngineState),
    refetchInterval: 30 * 1000,
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

export function useVitalSigns(characterId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.vitalSigns.detail(characterId!),
    queryFn: () => shisiClient.vitalSigns.get(characterId!) as Promise<VitalSignsData>,
    enabled: !!characterId,
  })
}

export function usePsychProfile() {
  return useQuery({
    queryKey: queryKeys.psych.profile,
    queryFn: () => api.psychProfile().then(r => r.data as PsychProfile),
    refetchInterval: 30 * 1000,
  })
}

export function usePsychSnapshots(limit = 20) {
  return useQuery({
    queryKey: queryKeys.psych.snapshots,
    queryFn: () => api.psychSnapshots(limit).then(r => (r.data as { snapshots: PsychSnapshot[] }).snapshots),
    staleTime: 30 * 1000,
  })
}

export function usePsychReset() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.psychReset().then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.psych.profile })
      qc.invalidateQueries({ queryKey: queryKeys.psych.snapshots })
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

export function useMentalHealth() {
  return useQuery({
    queryKey: queryKeys.psych.mentalHealth,
    queryFn: () => api.psychMentalHealth().then(r => r.data as MentalHealthSummary),
    refetchInterval: 30 * 1000,
  })
}

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { EmotionState, DashboardStats, HealthStatus, WeChatStatus, TrainingProgress, ProactiveEngineState, MemoryFact, PsychProfile, PsychSnapshot, SafetyStats, SafetyLogEntry, RAGStats, VoiceStatus, PluginsList, ToolHistoryEntry, ProactiveHistoryEntry, MentalHealthSummary } from '../types/api'

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
  proactive: { state: ['proactive', 'state'] as const },
}

export function useCharacters() {
  return useQuery({
    queryKey: queryKeys.characters.all,
    queryFn: () => api.listCharacters({}).then(r => r.characters as any[]),
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
    queryFn: () => api.affinityGet(characterId!).then(r => (r.data as any)?.data),
    enabled: !!characterId,
  })
}

export function useAffinityUnlocks(characterId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.affinity.unlocks(characterId!),
    queryFn: () => api.affinityUnlocks(characterId!).then(r => (r.data as any)?.data),
    enabled: !!characterId,
  })
}

export function useEmotionStage(characterId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.emotionStage.detail(characterId!),
    queryFn: () => api.emotionStageGet(characterId!).then(r => (r.data as any)?.data),
    enabled: !!characterId,
  })
}

export function useEmotionStageList() {
  return useQuery({
    queryKey: queryKeys.emotionStage.stages,
    queryFn: () => api.emotionStageList().then(r => (r.data as any)?.data),
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
    queryKey: queryKeys.proactive.state,
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
    queryFn: () => api.vitalSignsGet(characterId!).then(r => (r.data as any)?.data),
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
    queryKey: [...queryKeys.psych.snapshots, limit],
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

// ═══ 统一角色管理 hooks ═══

export function useUnifiedCharacters(userId?: string, search?: string) {
  return useQuery({
    queryKey: [...queryKeys.characters.all, userId, search],
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
    queryKey: ['character', characterId, 'voice'],
    queryFn: () => api.getVoiceConfig(characterId!),
    enabled: !!characterId,
  })
}

export function useBindCharacterVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ characterId, data }: { characterId: string; data: import('../types/api').VoiceBindRequest }) => api.bindVoice(characterId, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['character', variables.characterId, 'voice'] })
    },
  })
}

export function useUpdateCharacterVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ characterId, data }: { characterId: string; data: import('../types/api').VoiceUpdateRequest }) => api.updateVoice(characterId, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['character', variables.characterId, 'voice'] })
    },
  })
}

export function useUnbindCharacterVoice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (characterId: string) => api.unbindVoice(characterId),
    onSuccess: (_data, characterId) => {
      qc.invalidateQueries({ queryKey: ['character', characterId, 'voice'] })
    },
  })
}

export function useTestCharacterVoice() {
  return useMutation({
    mutationFn: ({ characterId, text }: { characterId: string; text?: string }) => api.testVoice(characterId, text),
  })
}

export function useVoiceSpeakers(engine: string = 'edge-tts') {
  return useQuery({
    queryKey: ['voice', 'speakers', engine],
    queryFn: () => api.getSpeakers(engine),
    staleTime: 5 * 60 * 1000,
  })
}

// ═══ 剧情线 hooks ═══

export function useStorylineConfig(characterId: string | undefined) {
  return useQuery({
    queryKey: ['storyline', characterId, 'config'],
    queryFn: () => api.getStorylineConfig(characterId!),
    enabled: !!characterId,
    staleTime: 30 * 1000,
  })
}

export function useStorylineProgress(characterId: string | undefined) {
  return useQuery({
    queryKey: ['storyline', characterId, 'progress'],
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
      qc.invalidateQueries({ queryKey: ['storyline', variables.characterId, 'config'] })
      qc.invalidateQueries({ queryKey: ['storyline', variables.characterId, 'progress'] })
      qc.invalidateQueries({ queryKey: queryKeys.characters.all })
    },
  })
}

export function useDeleteStorylineConfig() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (characterId: string) => api.deleteStorylineConfig(characterId),
    onSuccess: (_data, characterId) => {
      qc.invalidateQueries({ queryKey: ['storyline', characterId, 'config'] })
      qc.invalidateQueries({ queryKey: ['storyline', characterId, 'progress'] })
      qc.invalidateQueries({ queryKey: queryKeys.characters.all })
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
      qc.invalidateQueries({ queryKey: ['storyline', characterId, 'progress'] })
    },
  })
}

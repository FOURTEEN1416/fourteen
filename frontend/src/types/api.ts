export interface EmotionState {
  primary: {
    type: string
    intensity: number
  }
  secondary: Array<{ type: string; intensity: number }>
  energy: number
  affinity: {
    level: number
    name: string
    points: number
  }
  last_update?: number
}

export interface EmotionTrend {
  trend: Array<{ timestamp: string; primary_emotion: string; intensity: number }>
  days: number
}

export interface PersonaProfile {
  core_character: Record<string, number>
  speaking_style: Record<string, number>
  emotional_preference: Record<string, number>
}

export interface EvolutionLog {
  log: Array<{
    timestamp: string
    dimension: string
    before: number
    after: number
    delta: number
    trigger: string
  }>
}

export type FactCategory = '偏好' | '习惯' | '个人信息' | '日程' | '关系' | '其他'

export interface MemoryFact {
  type: string
  content: string
  confidence: number
  category?: FactCategory
  source?: string
  timestamp?: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  emotion?: string
  timestamp: number
  interrupted?: boolean
}

export interface ChatResponse {
  reply: string
  trace_id: string
  emotion: EmotionState | null
}

export interface HealthStatus {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'unknown'
  checks: Record<string, { connected: boolean; detail?: string }>
}

export interface SystemConfig {
  [key: string]: unknown
}

export interface WSIncomingMessage {
  type: 'reply' | 'stream_start' | 'stream_token' | 'stream_end' | 'proactive' | 'pong' | 'error' | 'character_switched' | 'emotion_stage_changed' | 'affinity_changed' | 'sticker_send'
  content?: string
  emotion?: EmotionState
  trace_id?: string
  session_id?: string
  message?: string
  data?: {
    character_id?: string
    name?: string
    old_stage?: string
    new_stage?: string
    affinity?: number
    old_value?: number
    new_value?: number
    sticker_id?: string
    category?: string
  }
}

export interface DashboardStats {
  today_chats: number
  recent_memories: number
  affinity: number
  energy: number
  current_emotion: string
  system_status: string
  uptime_seconds: number
  wechat_connected: boolean
  wechat: WeChatStatus | null
  training: TrainingProgress | null
}

export interface WeChatStatus {
  connected: boolean
  uptime_seconds: number
  reconnect_attempts: number
  missed_heartbeats: number
  messages_today: number
  last_activity: string
  qr_code?: string
}

export type TrainingStatusEnum = 'idle' | 'extracting' | 'cleaning' | 'training' | 'done' | 'error' | 'stopped'

export interface TrainingProgress {
  status: TrainingStatusEnum
  progress: number
  loss: number
  extracted_turns: number
  cleaned_turns: number
  current_step: string
  step_name?: string
  eta_seconds?: number
  error?: string
}

export interface TrainingAvailability {
  available: boolean
  missing?: string[]
}

export interface CloneTestResult {
  message: string
  style_output: string
}

export type ChannelStatus = 'connected' | 'disconnected' | 'connecting' | 'error'
export type ChannelType = 'wechat' | 'web' | 'feishu' | 'dingtalk' | 'qq'

export interface Channel {
  id: string
  name: string
  status: ChannelStatus
  desc: string
  type: ChannelType
  meta?: Record<string, unknown>
  session_id?: string
}

export type UrgencyLevel = '非常想找你' | '有点想你' | '想找人说话' | '还好'

export interface UrgencyBreakdown {
  total: number
  base: number
  miss_bonus: number
  event_bonus: number
  scene_bonus: number
}

export interface ProactiveConfig {
  threshold: number
  max_daily: number
  min_interval_minutes: number
  cooldown_after_reply_minutes: number
}

export interface ProactiveEngineState {
  urgency: number
  urgency_level: UrgencyLevel
  daily_count: number
  last_proactive_at: string | null
  config: ProactiveConfig
}

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export interface LogEntry {
  time: string
  level: LogLevel
  module: string
  msg: string
}

export interface LogQueryParams {
  limit?: number
  level?: LogLevel
  search?: string
}

export const SENSITIVE_FIELD_KEYS = new Set([
  'api_key', 'secret', 'token', 'password', 'encryption_key',
])

export function isSensitiveField(key: string): boolean {
  if (SENSITIVE_FIELD_KEYS.has(key)) return true
  const suffixes = ['_key', '_secret', '_token', '_password']
  return suffixes.some((s) => key.endsWith(s))
}

export type ConfigItemType = 'toggle' | 'select' | 'range'

export interface ConfigItem {
  id: string
  label: string
  type: ConfigItemType
  value: unknown
  options?: Array<{ label: string; value: unknown }>
  min?: number
  max?: number
  step?: number
}

export interface ConfigSection {
  id: string
  label: string
  items: ConfigItem[]
}

// ── 克隆数据管理（需求3+4） ──

export interface CloneContact {
  username: string
  display_name: string
  source: string
  msg_count?: number
}

export interface CloneDataset {
  person_id: string
  person_name: string
  source: string
  message_count: number
  extracted_at: string
  has_style: boolean
  has_lora: boolean
}

export interface CloneConversation {
  user: string
  reply: string
  timestamp?: number
  is_self?: boolean
  source?: string
}

export interface CloneDatasetDetail {
  person_id: string
  person_name: string
  total: number
  page: number
  page_size: number
  conversations: CloneConversation[]
  stats: {
    total: number
    date_range: string
  }
  error?: string
}

export interface CloneStats {
  total_persons: number
  total_messages: number
  cloned_persons: number
  persons: CloneDataset[]
}

// ── 用户心理画像（OCEAN+PAD） ──

export interface OceanTraits {
  openness: number
  conscientiousness: number
  extraversion: number
  agreeableness: number
  neuroticism: number
}

export interface PadState {
  pleasure: number
  arousal: number
  dominance: number
}

export interface StyleVector {
  formality: number
  expressiveness: number
  humor: number
  directness: number
  sentiment: number
}

export interface PsychProfile {
  user_id: string
  status: 'stable' | 'learning' | 'insufficient_data' | 'unavailable'
  stability: number
  snapshots: number
  ocean: OceanTraits
  pad: PadState
  style: StyleVector
  first_seen: string
  last_updated: string
  hexaco?: HexacoTraits
  dark_triad?: DarkTriadTraits
  mental_health?: MentalHealthSnapshot
  liwc?: LiwcProfile
  cognitive?: CognitiveDistortionResult
}

export interface HexacoTraits {
  honesty_humility: number
  emotionality: number
  extraversion: number
  agreeableness: number
  conscientiousness: number
  openness: number
}

export interface DarkTriadTraits {
  narcissism: number
  machiavellianism: number
  psychopathy: number
  overall_level: 'normal' | 'elevated' | 'significant'
  matched?: string[]
}

export interface DepressionIndicators {
  sleep: number; interest: number; guilt: number; energy: number
  concentration: number; appetite: number; psychomotor: number; suicidal: number
  total_score: number; level: string; matched?: string[]
}

export interface AnxietyIndicators {
  nervousness: number; uncontrollable_worry: number; worry_too_much: number
  trouble_relaxing: number; restlessness: number; irritability: number; fear_awful: number
  total_score: number; level: string; matched?: string[]
}

export interface MentalHealthSnapshot {
  timestamp: string
  depression: DepressionIndicators
  anxiety: AnxietyIndicators
  trauma_signals: number
  self_harm_risk: number
  overall_risk: 'low' | 'moderate' | 'high' | 'critical'
}

export interface CognitiveDistortionResult {
  total_count: number
  dominant_pattern: string
  severity: 'none' | 'mild' | 'moderate' | 'frequent'
  by_type: Record<string, number>
  recent?: Array<{ type: string; subtype: string; matched: string }>
}

export interface LiwcProfile {
  emotional_tone?: number
  analytical_thinking?: number
  clout?: number
  authentic?: number
  total_words?: number
  i_ratio?: number; we_ratio?: number; you_ratio?: number
  positive_emotion_ratio?: number; negative_emotion_ratio?: number
  anxiety_ratio?: number; anger_ratio?: number; sadness_ratio?: number
  cognitive_ratio?: number; insight_ratio?: number; tentative_ratio?: number
  certainty_ratio?: number; past_ratio?: number; present_ratio?: number; future_ratio?: number
  health_ratio?: number; affiliation_ratio?: number; achievement_ratio?: number
  swear_ratio?: number; filler_ratio?: number
}

export interface MentalHealthSummary {
  available: boolean
  mental_health?: MentalHealthSnapshot | null
  cognitive?: CognitiveDistortionResult | null
  liwc?: LiwcProfile | null
  dark_triad?: DarkTriadTraits | null
  hexaco?: HexacoTraits | null
}

export interface PsychSnapshot {
  timestamp: string
  ocean: OceanTraits
  pad: PadState
  style: StyleVector
  confidence: number
  source: string
  trigger_message: string
}

// ── 安全面板 ──

export interface SafetyStats {
  enabled: boolean
  total_flagged: number
  recent_flagged: number
  by_category: Record<string, number>
  recent: SafetyLogEntry[]
}

export interface SafetyLogEntry {
  timestamp: string
  category: string
  message: string
  confidence: number
}

// ── RAG ──

export interface RAGStats {
  available: boolean
  bm25_available?: boolean
}

export interface RAGSearchResult {
  query: string
  results: Array<{ content: string; score?: number }>
  total_vector: number
  total_keyword: number
}

// ── Voice TTS ──

export interface VoiceStatus {
  enabled: boolean
  current_engine: string | null
  available_engines: string[]
  providers?: Record<string, { connected: boolean }>
  synthesize_count: number
  last_error: string | null
}

// ── 插件 ──

export interface PluginEntry {
  enabled?: boolean
  toggled_at?: string
}

export interface PluginsList {
  plugins: Record<string, PluginEntry>
}

// ── 文件上传 ──

export interface FileUploadResult {
  status: string
  filename: string
  size: number
  mime_type: string
  message_type: 'image' | 'voice' | 'file'
  url: string
}

// ── 工具历史 ──

export interface ToolHistoryEntry {
  timestamp: string
  tool: string
  action: string
}

// ── 主动消息历史 ──

export interface ProactiveHistoryEntry {
  timestamp?: string
  message?: string
  urgency?: number
  sent?: boolean
}

// ── 微信手动连接（需求1） ──

export interface WeChatConnectionStatus {
  status: 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'
  message: string
  pid?: number
  started_at?: number
}

// ── 统一角色管理 ──

export interface UnifiedCharacter {
  id: string
  name: string
  description: string
  personality: Record<string, number>
  speaking_style: Record<string, number>
  core_anchors: string[]
  user_id: string
  is_active: boolean
  created_at: string
  updated_at: string
  version: number
  voice_config?: VoiceConfig | null
}

export interface UnifiedCharacterCreate {
  name: string
  description?: string
  personality?: Record<string, number>
  speaking_style?: Record<string, number>
  core_anchors?: string[]
  user_id?: string
}

export interface UnifiedCharacterUpdate {
  name?: string
  description?: string
  personality?: Record<string, number>
  speaking_style?: Record<string, number>
  core_anchors?: string[]
  catchphrases?: string[]
}

export interface PersonaUpdate {
  personality?: Record<string, number>
  speaking_style?: Record<string, number>
  core_anchors?: string[]
}

export interface CharacterListResponse {
  characters: UnifiedCharacter[]
  total: number
}

export interface CharacterCreateResponse {
  id: string
  name: string
  status: string
}

export interface CharacterVoiceResponse {
  configured: boolean
  voice: VoiceConfig | null
}

// ── 角色音色绑定 ──

export interface VoiceConfig {
  engine: string
  speaker_name: string
  rate?: string
  pitch?: string
  volume?: string
  model?: string
  extra_params?: Record<string, unknown>
}

export interface MiMoStatus {
  enabled: boolean
  message?: string
  health?: {
    status: string
    model: string
    latency_ms: number
  }
  current_engine?: string | null
  available_engines?: string[]
}

export interface MiMoCloneResponse {
  status: string
  voice_id: string
  message?: string
}

export interface MiMoDesignResponse {
  status: string
  voice_id: string
  message?: string
}

export interface MiMoSetEngineResponse {
  status: string
  model: string
  message?: string
}

export interface VoiceBindRequest {
  engine?: string
  speaker_name?: string
  rate?: string
  pitch?: string
  volume?: string
  extra_params?: Record<string, unknown>
}

export interface VoiceUpdateRequest {
  engine?: string
  speaker_name?: string
  rate?: string
  pitch?: string
  volume?: string
  extra_params?: Record<string, unknown>
}

export interface VoiceTestRequest {
  text?: string
}

export interface SpeakerInfo {
  name: string
  gender?: string
  description?: string
}

export interface SpeakerListResponse {
  engine: string
  speakers: SpeakerInfo[]
  total: number
}

export interface VoiceBindStatusResponse {
  status: string
  character_id: string
  engine?: string
  model_path?: string
}

// ── 剧情线 ──

export interface StorylineStageTiming {
  start_minutes: number
  end_minutes: number
}

export interface StorylineStyleRule {
  style: string
  inject_prompt: boolean
}

export interface StorylineBehaviorRule {
  rule: string
  enforce: boolean
}

export interface StorylineStage {
  name: string
  display_name: string
  timing: StorylineStageTiming
  style_rules: StorylineStyleRule[]
  behavior_rules: StorylineBehaviorRule[]
  dialogue_notes: string
  transition_message: string
}

export interface StorylineEnding {
  type: string
  final_dialogue: string
  narrative: string
  memorial_items: string[]
  blank_after_end: boolean
}

export interface StorylineConfig {
  enabled: boolean
  time_per_turn: number
  time_unit_label: string
  max_duration_minutes: number
  start_day: number
  start_hour: number
  start_minute: number
  stages: StorylineStage[]
  ending: StorylineEnding
  auto_detected: boolean
  detection_confidence: number
}

export interface StorylineConfigResponse {
  enabled: boolean
  configured: boolean
  config?: StorylineConfig
}

export interface StorylineState {
  character_id: string
  story_time_minutes: number
  story_day: number
  story_hour: number
  story_minute: number
  display_time: string
  current_stage_index: number
  is_ended: boolean
  turn_count: number
}

export interface StorylineCurrentStage {
  name: string
  display_name: string
  style_rules: string[]
  behavior_rules: string[]
  dialogue_notes: string
  transition_message: string
}

export interface StorylineProgress {
  enabled: boolean
  state: StorylineState | null
  current_stage: StorylineCurrentStage | null
  progress_percent: number
  total_stages: number
}

export interface StorylineDetectResult {
  has_storyline: boolean
  confidence: number
  matched_patterns: string[]
  suggested: StorylineConfig | null
}

export interface StorylineConfigRequest {
  enabled: boolean
  time_per_turn: number
  max_duration_minutes: number
  stages: Array<{
    name: string
    display_name?: string
    timing: StorylineStageTiming
    style_rules: StorylineStyleRule[]
    behavior_rules: StorylineBehaviorRule[]
    dialogue_notes?: string
    transition_message?: string
  }>
  ending: {
    type: string
    final_dialogue?: string
    narrative?: string
    memorial_items?: string[]
    blank_after_end?: boolean
  }
}

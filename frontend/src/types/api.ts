export interface EmotionState {
  current_emotion: string
  intensity: number
  energy: number
  affinity: number
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
  [key: string]: any
}

export interface WSIncomingMessage {
  type: 'reply' | 'stream_start' | 'stream_token' | 'stream_end' | 'proactive' | 'pong' | 'error'
  content?: string
  emotion?: EmotionState
  trace_id?: string
  session_id?: string
  message?: string
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
  'api_key', 'secret', 'token', 'password', 'encryption_key', 'api_base',
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

// ── 微信手动连接（需求1） ──

export interface WeChatConnectionStatus {
  status: 'idle' | 'connecting' | 'connected' | 'disconnected' | 'error'
  message: string
  pid?: number
  started_at?: number
}

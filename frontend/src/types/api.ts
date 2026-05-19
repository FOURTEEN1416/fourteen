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

export interface MemoryFact {
  type: string
  content: string
  confidence: number
  category?: string
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
  status: string
  checks: Record<string, any>
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

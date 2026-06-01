export type ConnectionState = "disconnected" | "connecting" | "connected"

export interface SavedConnection {
  alias: string
  wxid: string
  isOnline: boolean
  isCurrent: boolean
}

export interface WeChatPageState {
  alias: string
  status: ConnectionState
  wxid: string
  onlineDuration: string
  qrCode: string | null
  savedConnections: SavedConnection[]
}

export interface PersonaCard {
  id: string
  name: string
  avatar?: string
  description?: string
  personality: {
    warmth: number
    playfulness: number
    independence: number
    jealousy: number
    stubbornness: number
  }
  core_anchors: string[]
  speaking_style: {
    formality: number
    humor: number
    liveliness: number
    gentleness: number
    catchphrases: string[]
  }
  voice: {
    engine: "edge-tts" | "gpt-sovits" | "bert-vits2"
    speaker_name: string
    rate?: number
    pitch?: number
    volume?: number
    server_url?: string
    ref_audio?: string
    ref_text?: string
  }
  proactive: {
    enabled: boolean
    daily_limit: number
    min_interval: number
    cooldown: number
    urgency_threshold: number
  }
  knowledge_docs: string[]
  created_at: string
  updated_at: string
  user_id: string
}

export interface User {
  id: string
  name: string
  avatar?: string
  email?: string
  characters: string[]
  createdAt?: string
  updatedAt?: string
  lastActive?: string
  online?: boolean
  characterCount?: number
  last_active?: string
  is_online: boolean
}

export interface EmotionState {
  current_emotion: string
  intensity: number
  baseline: number
  volatility: number
  resilience: number
}

export interface EmotionTrendPoint {
  date: string
  emotion: string
  intensity: number
}

export interface WeChatConnection {
  alias: string
  wxid: string
  status: ConnectionState
  online_since?: string
  qr_code?: string
}

export interface PluginItem {
  name: string
  description: string
  enabled: boolean
  built_in: boolean
}

export interface LogEntry {
  timestamp: string
  level: "DEBUG" | "INFO" | "WARN" | "ERROR"
  message: string
}

export type WorkspaceTab = "create" | "settings" | "status"

export interface WorkspaceTabs {
  activeTab: WorkspaceTab
  userId: string
  selectedCharacterId?: string
}

export type SettingsTab = "general" | "llm" | "voice" | "security" | "extensions" | "logs"

export type RoleSettingsTab = "basic" | "voice" | "message" | "data" | "stickers" | "timeline"

export type CreateMethod = "ai-chat" | "wechat-clone" | "file-import"

export interface CharacterListItem {
  id: string
  name: string
  description?: string
  avatar?: string
  isActive: boolean
}

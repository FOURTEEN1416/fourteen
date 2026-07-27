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
    engine: "mimo-tts"
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
  // 修复：移除冗余的 is_online 字段，统一使用 online? 避免字段重复语义冲突
}

// 修复：EmotionState 已在 types/api.ts 中统一定义（current_emotion/intensity/energy/affinity），
// 此处删除避免类型冲突。如需框架扩展字段，请使用 FrameworkEmotionState 等独立命名。

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

// 修复：LogEntry 已在 types/api.ts 中统一定义（time/level/module/msg，含 WARNING/CRITICAL），
// 此处删除避免类型冲突。SettingsLogs.tsx 已改为从 types/api 导入。

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

// RoleSettings 页面使用的扩展角色类型：UnifiedCharacter 之外的运行时字段
export interface RoleSettingsCharacter {
  id: string
  name: string
  description?: string
  personality?: Record<string, number>
  speaking_style?: Record<string, number>
  core_anchors?: string[]
  catchphrases?: string[]
  created_at?: string
  updated_at?: string
  voice_config?: import('./api').VoiceConfig | {
    engine?: string
    mimo_model?: string
    edge_speaker?: string
    sovits_model?: string
    [key: string]: unknown
  } | null
  message?: {
    proactive?: boolean
    dailyLimit?: number
    minInterval?: number
    cooldown?: number
    urgency?: number
  }
  stats?: {
    messages?: number
    memories?: number
    avgResponse?: string
    [key: string]: unknown
  }
  rag?: {
    vectorDocs?: number
    keywordIndex?: number
    hitRate?: number
    [key: string]: unknown
  }
  knowledgeDocs?: string[]
  [key: string]: unknown
}

/**
 * @deprecated 请使用 types/api.ts 中的 UnifiedCharacter 替代
 * 旧 shisi API 仍在过渡期使用此类型
 */
export interface CharacterState {
  character_id: string
  name: string
  format: string
  is_active: boolean
  affinity: number
  emotion_stage: string
  avatar_url?: string
  tags: string[]
  created_at: string
  updated_at: string
}

export interface ApiResponse<T = unknown> {
  data: T
  error?: string
  trace_id?: string
}

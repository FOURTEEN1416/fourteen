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

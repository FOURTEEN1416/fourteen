export interface EmotionStageProgress {
  character_id: string
  current_stage: string
  stage_index: number
  affinity: number
  features: string[]
  total_stages: number
}

export interface AffinityProgress {
  character_id: string
  affinity: number
  min: number
  max: number
  percentage: number
  unlocks: Array<{ threshold: number; unlock_type: string; name: string }>
}

export interface VitalSignsData {
  character_id: string
  heart_rate: number
  temperature: number
  breath_rate: number
  last_emotion: string
}

export interface WechatCommandHelp {
  command: string
  description: string
  example: string
}

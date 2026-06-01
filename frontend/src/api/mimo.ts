/**
 * MiMo 语音引擎 API
 *
 * 覆盖 MiMo Cloud 语音克隆、语音设计、语音合成功能。
 * 与 system.ts 中的 voiceStatus/voiceSynthesize/getSpeakers 互补。
 */

/** MiMo 可用语音列表 */
export interface MiMoVoice {
  name: string
  displayName?: string
  gender?: 'male' | 'female' | 'neutral'
  engine: string
  isClone?: boolean
}

/** 语音设计参数 */
export interface VoiceDesignRequest {
  gender: 'male' | 'female' | 'neutral'
  age: number
  style: string
  pitch: number
  speed: number
}

import client from './client'

/** POST /api/mimo/clone — 语音克隆（上传音频） */
export function mimoClone(formData: FormData) {
  return client.post('/mimo/clone', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** POST /api/mimo/design — 语音设计（用描述生成音色） */
export function mimoDesign(params: {
  voice_name: string
  description: string
  gender?: string
  age_group?: string
}) {
  const fd = new FormData()
  fd.append('voice_name', params.voice_name)
  fd.append('description', params.description)
  if (params.gender) fd.append('gender', params.gender)
  if (params.age_group) fd.append('age_group', params.age_group)
  return client.post('/mimo/design', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** POST /api/mimo/synthesize — 语音合成测试 */
export function mimoSynthesize(text: string) {
  const fd = new FormData()
  fd.append('text', text)
  return client.post('/mimo/synthesize', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** POST /api/mimo/set-engine — 切换 MiMo 引擎模型 */
export function mimoSetEngine(model: string) {
  const fd = new FormData()
  fd.append('model', model)
  return client.post('/mimo/set-engine', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** POST /api/mimo/switch-voice — 切换当前音色（按 voice_id） */
export function mimoSwitchVoice(voiceId: string) {
  const fd = new FormData()
  fd.append('voice_id', voiceId)
  return client.post('/mimo/switch-voice', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** GET /api/mimo/status — MiMo 引擎状态 */
export function mimoStatus() {
  return client.get('/mimo/status')
}

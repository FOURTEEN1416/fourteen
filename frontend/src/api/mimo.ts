/**
 * MiMo 语音引擎 API
 *
 * 覆盖 MiMo Cloud 语音克隆、语音设计、语音合成功能。
 * 与 system.ts 中的 voiceStatus/voiceSynthesize/getSpeakers 互补。
 *
 * W7 契约（2026-09-27）：
 * - 合成端点返回音频 Blob（responseType: 'blob'），按 MIME 实际播放；
 * - 克隆/设计返回的 voice_id 落后端 catalog（持久化），UI 经
 *   getSpeakers 重新加载并选择/绑定；
 * - engine（mimo-tts）/ model（MiMo 模型名）/ voice_id 三者类型分明。
 */

/** MiMo 可用语音列表 */
export interface MiMoVoice {
  name: string
  displayName?: string
  gender?: 'male' | 'female' | 'neutral'
  engine: string
  isClone?: boolean
  /** preset = 静态预设；clone/design = catalog 自定义音色 */
  kind?: 'preset' | 'clone' | 'design' | 'custom'
  /** 音色描述（catalog 条目） */
  description?: string
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

/** POST /api/mimo/clone — 语音克隆（上传音频）；返回 voice_id + catalog_saved */
export function mimoClone(formData: FormData) {
  return client.post('/mimo/clone', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** POST /api/mimo/design — 语音设计（用描述生成音色）；返回 voice_id + catalog_saved */
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

/** POST /api/mimo/synthesize — 语音合成试听（返回音频 Blob） */
export function mimoSynthesize(text: string, opts?: { voiceId?: string; model?: string; emotion?: string }) {
  const fd = new FormData()
  fd.append('text', text)
  if (opts?.voiceId) fd.append('voice_id', opts.voiceId)
  if (opts?.model) fd.append('model', opts.model)
  if (opts?.emotion) fd.append('emotion', opts.emotion)
  return client.post('/mimo/synthesize', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    responseType: 'blob',
  })
}

/** POST /api/mimo/set-engine — 切换 MiMo 引擎模型（传模型名，非引擎名） */
export function mimoSetEngine(model: string) {
  const fd = new FormData()
  fd.append('model', model)
  return client.post('/mimo/set-engine', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

/** POST /api/mimo/switch-voice — 切换引擎默认音色（按 voice_id/预设名） */
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

/**
 * 播放音频 Blob（W7：试听正确生命周期——ObjectURL 用后即回收，错误上抛可见）。
 * 返回播放完毕（ended/error）后 resolve 的清理函数执行结果。
 */
export function playAudioBlob(blob: Blob): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob)
    const audio = new Audio(url)
    const cleanup = () => URL.revokeObjectURL(url)
    audio.onended = () => {
      cleanup()
      resolve()
    }
    audio.onerror = () => {
      cleanup()
      reject(new Error('音频播放失败：格式不受支持或数据为空'))
    }
    audio.play().catch((err: unknown) => {
      cleanup()
      reject(err instanceof Error ? err : new Error('浏览器拒绝了自动播放'))
    })
  })
}

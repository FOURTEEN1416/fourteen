import client from './client'
import type {
  VoiceBindRequest,
  VoiceUpdateRequest,
  CharacterVoiceResponse,
  SpeakerListResponse,
} from '../types/api'

// ── 音色绑定 CRUD ──

/** GET /api/characters/{characterId}/voice — 查询角色音色绑定 */
export function getVoiceConfig(characterId: string): Promise<CharacterVoiceResponse> {
  return client.get(`/characters/${characterId}/voice`).then(r => r.data as CharacterVoiceResponse)
}

/** POST /api/characters/{characterId}/voice — 绑定音色 */
export function bindVoice(characterId: string, body: VoiceBindRequest): Promise<{ status: string; character_id: string; engine: string }> {
  return client.post(`/characters/${characterId}/voice`, body).then(r => r.data as { status: string; character_id: string; engine: string })
}

/** PUT /api/characters/{characterId}/voice — 部分更新音色 */
export function updateVoice(characterId: string, body: VoiceUpdateRequest): Promise<{ status: string; character_id: string }> {
  return client.put(`/characters/${characterId}/voice`, body).then(r => r.data as { status: string; character_id: string })
}

/** DELETE /api/characters/{characterId}/voice — 解绑音色 */
export function unbindVoice(characterId: string): Promise<{ status: string; character_id: string }> {
  return client.delete(`/characters/${characterId}/voice`).then(r => r.data as { status: string; character_id: string })
}

/** GET /api/voice/speakers — 获取引擎发音人列表 */
export function getSpeakers(engine: string = 'edge-tts'): Promise<SpeakerListResponse> {
  return client.get('/voice/speakers', { params: { engine } }).then(r => r.data as SpeakerListResponse)
}

/** POST /api/characters/{characterId}/voice/test — 测试音色合成 */
export function testVoice(characterId: string, text: string = '你好，我是你的专属语音助手'): Promise<Blob> {
  return client.post(`/characters/${characterId}/voice/test`, { text }, { responseType: 'blob' }).then(r => r.data as Blob)
}

// ── Namespace 对象 ──

export const voiceApi = {
  getConfig: getVoiceConfig,
  bind: bindVoice,
  update: updateVoice,
  unbind: unbindVoice,
  speakers: getSpeakers,
  test: testVoice,
}

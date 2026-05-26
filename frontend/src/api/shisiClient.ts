import client from './client'
import type { CharacterState, ApiResponse } from '../types/character'
import type { EmotionStageProgress, AffinityProgress, VitalSignsData } from '../types/shisi'

const BASE = '/shisi'

const unwrap = <T>(r: { data: unknown }): T => (r.data as ApiResponse<T>).data as T

export const shisiClient = {
  characters: {
    list: () => client.get<CharacterState[]>(`${BASE}/characters`).then(unwrap<CharacterState[]>),
    get: (id: string) => client.get(`${BASE}/characters/${id}`).then(unwrap),
    switch: (id: string) => client.post(`${BASE}/characters/switch`, { character_id: id }).then(unwrap),
    delete: (id: string) => client.delete(`${BASE}/characters/${id}`).then(unwrap),
    // Backend: POST /api/shisi/characters/export/{character_id}
    export_: (id: string) => client.post(`${BASE}/characters/export/${id}`).then(unwrap),
    // Backend: POST /api/shisi/characters/import (multipart UploadFile)
    import_: (file: File) => {
      const fd = new FormData()
      fd.append('file', file)
      return client.post(`${BASE}/characters/import`, fd, { headers: { 'Content-Type': 'multipart/form-data' } }).then(unwrap)
    },
    // Backend: PUT /api/shisi/characters/{character_id} (body: {card: {...}})
    update: (id: string, card: Record<string, unknown>) => client.put(`${BASE}/characters/${id}`, { card }).then(unwrap),
  },
  emotionStage: {
    get: (cid: string) => client.get<EmotionStageProgress>(`${BASE}/emotion-stage/${cid}`).then(unwrap<EmotionStageProgress>),
    listStages: () => client.get(`${BASE}/emotion-stage/stages`).then(unwrap),
    // Backend: POST /api/shisi/emotion-stage/{character_id}/evaluate?affinity=...
    evaluate: (cid: string, affinity: number) => client.post(`${BASE}/emotion-stage/${cid}/evaluate`, null, { params: { affinity } }).then(unwrap),
  },
  affinity: {
    get: (cid: string) => client.get<AffinityProgress>(`${BASE}/affinity/${cid}`).then(unwrap<AffinityProgress>),
    update: (cid: string, delta: number, reason: string) => client.post(`${BASE}/affinity/${cid}/update`, { delta, reason, source: 'web' }).then(unwrap),
    // Backend: GET /api/shisi/affinity/{character_id}/unlocks
    unlocks: (cid: string) => client.get(`${BASE}/affinity/${cid}/unlocks`).then(unwrap),
    // Backend: POST /api/shisi/affinity/{character_id}/decay
    decay: (cid: string) => client.post(`${BASE}/affinity/${cid}/decay`).then(unwrap),
  },
  vitalSigns: {
    get: (cid: string) => client.get<VitalSignsData>(`${BASE}/vital-signs/${cid}`).then(unwrap<VitalSignsData>),
  },
  stats: {
    get: () => client.get(`${BASE}/stats`).then(unwrap),
  },
  stickers: {
    list: (category?: string) => client.get(`${BASE}/stickers`, { params: { category } }).then(unwrap),
    delete: (id: string) => client.delete(`${BASE}/stickers/${id}`).then(unwrap),
    // Backend: POST /api/shisi/stickers/recommend
    recommend: (emotionTags: string[], limit = 5) => client.post(`${BASE}/stickers/recommend`, { emotion_tags: emotionTags, limit }).then(unwrap),
    // Backend: POST /api/shisi/stickers/import (multipart)
    importZip: (formData: FormData) => client.post(`${BASE}/stickers/import`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(unwrap),
    upload: (formData: FormData) => client.post(`${BASE}/stickers/import`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(unwrap),
    // Backend: PUT /api/shisi/stickers/characters/{character_id}/stickers
    bindToCharacter: (characterId: string, stickerIds: string[], unlockThreshold = 0) => client.put(`${BASE}/stickers/characters/${characterId}/stickers`, { sticker_ids: stickerIds, unlock_threshold: unlockThreshold }).then(unwrap),
  },
  memory: {
    // Backend: GET /api/shisi/memory/favorites?character_id=...
    favorites: (cid: string) => client.get(`${BASE}/memory/favorites`, { params: { character_id: cid } }).then(unwrap),
    // Backend: POST /api/shisi/memory/favorite (body: {character_id, memory_id})
    addFavorite: (cid: string, memoryId: string) => client.post(`${BASE}/memory/favorite`, { character_id: cid, memory_id: memoryId }).then(unwrap),
    // Backend: DELETE /api/shisi/memory/favorite/{fav_id}?character_id=...
    removeFavorite: (favId: string, cid?: string) => client.delete(`${BASE}/memory/favorite/${favId}`, { params: { character_id: cid } }).then(unwrap),
    // Backend: POST /api/shisi/memory/forward (body: {from_character, to_character, memory_id, content?})
    forward: (fromCid: string, toCid: string, memoryId: string, content?: string) => client.post(`${BASE}/memory/forward`, { from_character: fromCid, to_character: toCid, memory_id: memoryId, content }).then(unwrap),
    // Backend: DELETE /api/shisi/memory/{memory_id}?character_id=...&confirm=true
    delete: (memoryId: string, cid?: string) => client.delete(`${BASE}/memory/${memoryId}`, { params: { character_id: cid, confirm: true } }).then(unwrap),
  },
  persona: {
    get: (cid: string) => client.get(`${BASE}/persona/characters/${cid}`).then(unwrap),
    update: (cid: string, fields: Record<string, unknown>) => client.put(`${BASE}/persona/characters/${cid}`, { card: fields }).then(unwrap),
    preview: (cid: string) => client.get(`${BASE}/persona/characters/${cid}/preview`).then(unwrap),
  },
  voiceTraining: {
    // Backend: POST /api/shisi/voice/training/upload (multipart)
    upload: (files: File[], modelName = 'default') => {
      const fd = new FormData()
      files.forEach(f => fd.append('files', f))
      fd.append('model_name', modelName)
      return client.post(`${BASE}/voice/training/upload`, fd, { headers: { 'Content-Type': 'multipart/form-data' } }).then(unwrap)
    },
    // Backend: POST /api/shisi/voice/training/preprocess
    preprocess: (modelName: string) => client.post(`${BASE}/voice/training/preprocess`, { model_name: modelName }).then(unwrap),
    // Backend: POST /api/shisi/voice/training/train
    train: (modelName: string, epochs = 8, batchSize = 4) => client.post(`${BASE}/voice/training/train`, { model_name: modelName, epochs, batch_size: batchSize }).then(unwrap),
    // Backend: GET /api/shisi/voice/training/status
    status: () => client.get(`${BASE}/voice/training/status`).then(unwrap),
  },
}

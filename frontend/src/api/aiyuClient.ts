import client from './client'
import type { CharacterState, ApiResponse } from '../types/character'
import type { EmotionStageProgress, AffinityProgress, VitalSignsData } from '../types/aiyu'

const BASE = '/aiyu'

export const aiyuClient = {
  characters: {
    list: () => client.get<CharacterState[]>(`${BASE}/characters`).then(r => r.data),
    get: (id: string) => client.get(`${BASE}/characters/${id}`).then(r => r.data),
    switch: (id: string) => client.post(`${BASE}/characters/switch`, { character_id: id }).then(r => r.data),
    delete: (id: string) => client.delete(`${BASE}/characters/${id}`).then(r => r.data),
    export_: (id: string) => client.get(`${BASE}/characters/${id}/export`).then(r => r.data),
    import_: (data: unknown) => client.post(`${BASE}/characters/import`, data).then(r => r.data),
  },
  emotionStage: {
    get: (cid: string) => client.get<EmotionStageProgress>(`${BASE}/emotion-stage/${cid}`).then(r => r.data),
    listStages: () => client.get(`${BASE}/emotion-stage/stages`).then(r => r.data),
    triggerTransition: (cid: string, targetStage: string) => client.post(`${BASE}/emotion-stage/${cid}/transition`, { target_stage: targetStage }).then(r => r.data),
  },
  affinity: {
    get: (cid: string) => client.get<AffinityProgress>(`${BASE}/affinity/${cid}`).then(r => r.data),
    update: (cid: string, delta: number, reason: string) => client.post(`${BASE}/affinity/${cid}/update`, { delta, reason, source: 'web' }).then(r => r.data),
    history: (cid: string) => client.get(`${BASE}/affinity/${cid}/history`).then(r => r.data),
  },
  vitalSigns: {
    get: (cid: string) => client.get<VitalSignsData>(`${BASE}/vital-signs/${cid}`).then(r => r.data),
  },
  stats: {
    get: () => client.get(`${BASE}/stats`).then(r => r.data),
  },
  stickers: {
    list: () => client.get(`${BASE}/stickers`).then(r => r.data),
    get: (id: string) => client.get(`${BASE}/stickers/${id}`).then(r => r.data),
    upload: (formData: FormData) => client.post(`${BASE}/stickers/upload`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data),
    delete: (id: string) => client.delete(`${BASE}/stickers/${id}`).then(r => r.data),
    recommend: (emotion: string) => client.get(`${BASE}/stickers/recommend`, { params: { emotion } }).then(r => r.data),
    importZip: (formData: FormData) => client.post(`${BASE}/stickers/import`, formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then(r => r.data),
  },
  memory: {
    favorites: (cid: string) => client.get(`${BASE}/memory/${cid}/favorites`).then(r => r.data),
    addFavorite: (cid: string, memoryId: string) => client.post(`${BASE}/memory/${cid}/favorites`, { memory_id: memoryId }).then(r => r.data),
    removeFavorite: (cid: string, memoryId: string) => client.delete(`${BASE}/memory/${cid}/favorites/${memoryId}`).then(r => r.data),
    forwards: (cid: string) => client.get(`${BASE}/memory/${cid}/forwards`).then(r => r.data),
    forward: (fromCid: string, toCid: string, memoryId: string) => client.post(`${BASE}/memory/${fromCid}/forward`, { to_character_id: toCid, memory_id: memoryId }).then(r => r.data),
  },
  persona: {
    get: (cid: string) => client.get(`${BASE}/persona/${cid}`).then(r => r.data),
    update: (cid: string, fields: Record<string, unknown>) => client.put(`${BASE}/persona/${cid}`, fields).then(r => r.data),
    preview: (cid: string) => client.get(`${BASE}/persona/${cid}/preview`).then(r => r.data),
  },
}

/**
 * 统一角色（Character）API — 全部为 /api/characters/* 端点
 * 包含：角色 CRUD、人设、角色卡、收藏/转发、剧情线、音色绑定
 */
import client from './client'
import type {
  UnifiedCharacter,
  UnifiedCharacterCreate,
  UnifiedCharacterUpdate,
  CharacterListResponse,
  CharacterCreateResponse,
  StorylineConfigResponse,
  StorylineProgress,
  StorylineDetectResult,
  StorylineConfigRequest,
  CharacterVoiceResponse,
  VoiceBindRequest,
  VoiceUpdateRequest,
} from '../types/api'

// ── 类型 ──

export interface PersonaFields {
  personality?: Record<string, number>
  speaking_style?: Record<string, number>
  core_anchors?: string[]
}

export interface ListCharactersParams {
  user_id?: string
  search?: string
}

export interface FavoriteItem {
  id: string
  content?: string
  message?: string
  timestamp?: string
  [key: string]: unknown
}

export interface PersonaCardData {
  name: string
  description: string
  personality: string
  scenario: string
  first_mes: string
  mes_example: string
  creator_notes: string
  tags: string[]
  spec?: string
  spec_version?: string
}

export interface PreviewResponse {
  preview: string
}

// ════════════════════════════════════════════════════
//  角色 CRUD
// ════════════════════════════════════════════════════

/** GET /api/characters — 获取角色列表 */
export function listCharacters(params?: ListCharactersParams): Promise<CharacterListResponse> {
  return client.get('/characters', { params }).then(r => r.data as CharacterListResponse)
}

/** POST /api/characters — 创建新角色 */
export function createCharacter(payload: UnifiedCharacterCreate): Promise<CharacterCreateResponse> {
  return client.post('/characters', payload).then(r => r.data as CharacterCreateResponse)
}

/** GET /api/characters/{id} — 获取单个角色详情 */
export function getCharacter(id: string): Promise<UnifiedCharacter> {
  return client.get(`/characters/${id}`).then(r => r.data as UnifiedCharacter)
}

/** PUT /api/characters/{id} — 更新角色 */
export function updateCharacter(id: string, payload: UnifiedCharacterUpdate): Promise<{ status: string; character_id: string }> {
  return client.put(`/characters/${id}`, payload).then(r => r.data as { status: string; character_id: string })
}

/** DELETE /api/characters/{id} — 删除角色 */
export function deleteCharacter(id: string): Promise<{ status: string; character_id: string }> {
  return client.delete(`/characters/${id}`).then(r => r.data as { status: string; character_id: string })
}

/** POST /api/characters/{id}/activate — 激活角色 */
export function activateCharacter(id: string): Promise<{ status: string; character_id: string }> {
  return client.post(`/characters/${id}/activate`).then(r => r.data as { status: string; character_id: string })
}

// ════════════════════════════════════════════════════
//  人设 (Persona)
// ════════════════════════════════════════════════════

/** GET /api/characters/{id}/persona — 获取人设字段 */
export function getPersona(id: string): Promise<{
  name: string
  personality: Record<string, number>
  speaking_style: Record<string, number>
  core_anchors: string[]
}> {
  return client.get(`/characters/${id}/persona`).then(r => r.data as {
    name: string
    personality: Record<string, number>
    speaking_style: Record<string, number>
    core_anchors: string[]
  })
}

/** PUT /api/characters/{id}/persona — 更新人设字段 */
export function updatePersona(id: string, fields: {
  personality?: Record<string, number>
  speaking_style?: Record<string, number>
  core_anchors?: string[]
}): Promise<{ status: string; character_id: string }> {
  return client.put(`/characters/${id}/persona`, fields).then(r => r.data as { status: string; character_id: string })
}

// ════════════════════════════════════════════════════
//  角色卡 (Persona Card)
// ════════════════════════════════════════════════════

/** GET /api/characters/{id}/persona-card — 获取完整角色卡 */
export function getPersonaCard(characterId: string): Promise<PersonaCardData> {
  return client.get(`/characters/${characterId}/persona-card`).then(r => r.data as PersonaCardData)
}

/** PUT /api/characters/{id}/persona-card — 更新完整角色卡 */
export function updatePersonaCard(characterId: string, card: PersonaCardData) {
  return client.put(`/characters/${characterId}/persona-card`, { card })
}

/** GET /api/characters/{id}/persona-card/preview — 预览角色卡 */
export function previewPersonaCard(characterId: string): Promise<PreviewResponse> {
  return client.get(`/characters/${characterId}/persona-card/preview`).then(r => r.data as PreviewResponse)
}

// ════════════════════════════════════════════════════
//  收藏 / 转发 (Favorites / Memory Forward)
// ════════════════════════════════════════════════════

/** GET /api/characters/{id}/favorites — 获取角色收藏列表 */
export function listFavorites(characterId: string): Promise<FavoriteItem[]> {
  return client
    .get<{ favorites: FavoriteItem[]; total: number }>(`/characters/${characterId}/favorites`)
    .then(r => r.data.favorites)
}

/** POST /api/characters/{id}/favorites — 添加收藏 */
export function addFavorite(characterId: string, memoryId: string) {
  return client.post(`/characters/${characterId}/favorites`, null, {
    params: { memory_id: memoryId },
  })
}

/** DELETE /api/characters/{id}/favorites/{memoryId} — 取消收藏 */
export function removeFavorite(characterId: string, memoryId: string) {
  return client.delete(`/characters/${characterId}/favorites/${memoryId}`)
}

/** POST /api/characters/{cid}/favorites/forward — 转发收藏到其他角色 */
export function forwardFavorite(fromCid: string, toCid: string, memoryId: string, content = '') {
  return client.post(`/characters/${fromCid}/favorites/forward`, {
    to_character: toCid,
    memory_id: memoryId,
    content,
  })
}

// ════════════════════════════════════════════════════
//  剧情线 (Storyline)
// ════════════════════════════════════════════════════

export function getStorylineConfig(id: string): Promise<StorylineConfigResponse> {
  return client.get(`/characters/${id}/storyline`).then(r => r.data as StorylineConfigResponse)
}

export function updateStorylineConfig(id: string, config: StorylineConfigRequest): Promise<{ status: string; character_id: string }> {
  return client.put(`/characters/${id}/storyline`, config).then(r => r.data as { status: string; character_id: string })
}

export function deleteStorylineConfig(id: string): Promise<{ status: string; character_id: string }> {
  return client.delete(`/characters/${id}/storyline`).then(r => r.data as { status: string; character_id: string })
}

export function getStorylineProgress(id: string): Promise<StorylineProgress> {
  return client.get(`/characters/${id}/storyline/progress`).then(r => r.data as StorylineProgress)
}

export function detectStoryline(id: string): Promise<StorylineDetectResult> {
  return client.post(`/characters/${id}/storyline/detect`).then(r => r.data as StorylineDetectResult)
}

export function resetStoryline(id: string): Promise<{ status: string; character_id: string }> {
  return client.post(`/characters/${id}/storyline/reset`).then(r => r.data as { status: string; character_id: string })
}

// ════════════════════════════════════════════════════
//  音色绑定 (Voice CRUD under /api/characters/{id}/voice)
// ════════════════════════════════════════════════════

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

/** POST /api/characters/{characterId}/voice/test — 测试音色合成 */
export function testVoice(characterId: string, text: string = '你好，我是你的专属语音助手'): Promise<Blob> {
  return client.post(`/characters/${characterId}/voice/test`, { text }, { responseType: 'blob' }).then(r => r.data as Blob)
}

// ════════════════════════════════════════════════════
//  角色卡导入/导出 (Shisi 桥接，尚未有统一等效)
// ════════════════════════════════════════════════════

/** POST /api/shisi/characters/export/{id} — 导出角色卡 */
export function exportCharacter(id: string): Promise<Blob> {
  return client.post(`/shisi/characters/export/${id}`, null, { responseType: 'blob' }).then(r => r.data as Blob)
}

/** POST /api/shisi/characters/import — 导入角色卡 */
export function importCharacter(file: File): Promise<any> {
  const fd = new FormData()
  fd.append('file', file)
  return client.post('/shisi/characters/import', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then(r => r.data)
}

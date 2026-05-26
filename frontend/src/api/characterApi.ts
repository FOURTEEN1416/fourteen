import client from './client'
import type {
  UnifiedCharacter,
  UnifiedCharacterCreate,
  UnifiedCharacterUpdate,
  CharacterListResponse,
  CharacterCreateResponse,
} from '../types/api'

// ── 类型定义 ──

export interface PersonaFields {
  personality?: Record<string, number>
  speaking_style?: Record<string, number>
  core_anchors?: string[]
}

export interface ListCharactersParams {
  user_id?: string
  search?: string
}

// ── API 函数 ──

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

/** POST /api/shisi/characters/export/{id} — 导出角色卡（指向旧 shisi API） */
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

// ── Namespace 对象 ──

export const characterApi = {
  list: listCharacters,
  create: createCharacter,
  get: getCharacter,
  update: updateCharacter,
  delete: deleteCharacter,
  activate: activateCharacter,
  getPersona,
  updatePersona,
  export: exportCharacter,
  import: importCharacter,
}

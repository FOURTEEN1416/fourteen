/** 统一角色卡 API — 桥接到 shisi CharacterManager，返回完整 CharaCardV2 格式 */
import client from './client'

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

const BASE = '/api/characters'

export const personaCardApi = {
  /** 获取角色完整角色卡 */
  get: (characterId: string): Promise<PersonaCardData> =>
    client.get(`${BASE}/${characterId}/persona-card`).then(r => r.data as PersonaCardData),

  /** 更新角色完整角色卡 */
  update: (characterId: string, card: PersonaCardData) =>
    client.put(`${BASE}/${characterId}/persona-card`, { card }),

  /** 预览角色卡（转为 PersonaEngine 配置） */
  preview: (characterId: string): Promise<PreviewResponse> =>
    client.get(`${BASE}/${characterId}/persona-card/preview`).then(r => r.data as PreviewResponse),
}

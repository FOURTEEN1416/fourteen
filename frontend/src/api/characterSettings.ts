/**
 * 「我 × 这个角色」设置 API — /api/characters/{id}/my-settings（2026-11-07 P1/P5）
 *
 * 语义：每个用户对每个角色有一份完整设置快照（唯一真源
 * `utils/character_settings.py`）；服务端按 token+绑定派生会话键，
 * 客户端绝不传键。空 patch 的 PUT 会被后端 400，故保存总是提交全量。
 */
import client from './client'

export interface MyCharacterSettingsResponse {
  status: string
  character_id: string
  scope: 'user_character'
  config: Record<string, unknown>
  groups?: Record<string, string[]>
}

export function getMyCharacterSettings(characterId: string) {
  return client
    .get(`/characters/${encodeURIComponent(characterId)}/my-settings`)
    .then((r) => r.data as MyCharacterSettingsResponse)
}

export function saveMyCharacterSettings(characterId: string, config: Record<string, unknown>) {
  return client
    .put(`/characters/${encodeURIComponent(characterId)}/my-settings`, { config })
    .then((r) => r.data as MyCharacterSettingsResponse)
}

export function resetMyCharacterSettings(characterId: string) {
  return client
    .delete(`/characters/${encodeURIComponent(characterId)}/my-settings`)
    .then((r) => r.data as { status: string; reset: boolean })
}

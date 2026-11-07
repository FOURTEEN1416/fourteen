/**
 * 用户自填画像 API — /api/user/self-profile（2026-11-07 P8）
 *
 * 语义：用户本人填写「我是谁」的自我介绍；后端写 EventLedger（唯一写权威），
 * 主链画像槽会优先展示它。空串 = 清空。
 */
import client from './client'

export interface SelfProfileResponse {
  status: string
  user_key_present?: boolean
  self_intro: string
}

export function getMySelfProfile() {
  return client.get('/user/self-profile').then((r) => r.data as SelfProfileResponse)
}

export function saveMySelfProfile(selfIntro: string) {
  return client
    .put('/user/self-profile', { self_intro: selfIntro })
    .then((r) => r.data as SelfProfileResponse)
}

/** P7（2026-11-07）：「最近发给我的主动消息」——本人作用域读面 */
export interface MyProactiveHistoryResponse {
  history: Array<{
    id: number
    kind: string
    channel: string
    character_id?: string
    message: string
    status: string
    created_at: number
    fail_reason?: string
  }>
  total: number
  bound: boolean
}

export function getMyProactiveHistory(characterId?: string, limit = 5) {
  return client
    .get('/user/proactive/history', {
      params: { ...(characterId ? { character_id: characterId } : {}), limit },
    })
    .then((r) => r.data as MyProactiveHistoryResponse)
}

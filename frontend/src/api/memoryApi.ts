/** 统一记忆 API — 桥接到 shisi FavoriteManager/ForwardManager */
import client from './client'

export interface FavoriteItem {
  id: string
  content?: string
  message?: string
  timestamp?: string
  [key: string]: unknown
}

const BASE = '/api/characters'

export const memoryApi = {
  /** 获取角色收藏列表 */
  listFavorites: (characterId: string): Promise<FavoriteItem[]> =>
    client
      .get<{ favorites: FavoriteItem[]; total: number }>(`${BASE}/${characterId}/favorites`)
      .then(r => r.data.favorites),

  /** 添加收藏 */
  addFavorite: (characterId: string, memoryId: string) =>
    client.post(`${BASE}/${characterId}/favorites`, null, {
      params: { memory_id: memoryId },
    }),

  /** 取消收藏 */
  removeFavorite: (characterId: string, memoryId: string) =>
    client.delete(`${BASE}/${characterId}/favorites/${memoryId}`),

  /** 转发收藏到其他角色 */
  forward: (fromCid: string, toCid: string, memoryId: string, content = '') =>
    client.post(`${BASE}/${fromCid}/favorites/forward`, {
      to_character: toCid,
      memory_id: memoryId,
      content,
    }),
}

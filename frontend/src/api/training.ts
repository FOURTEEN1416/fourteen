import client from './client'

/** GET /api/training/status — 风格提取状态 */
export function trainingStatus() {
  return client.get('/training/status')
}

/** GET /api/training/progress — 风格提取进度 */
export function trainingProgress() {
  return client.get('/training/progress')
}

/** POST /api/training/apply — 应用克隆 */
export function trainingApply(characterId?: string) {
  return client.post('/training/apply', null, { params: { ...(characterId ? { character_id: characterId } : {}) } })
}

import client from './client'

/** GET /api/training/status — 训练状态 */
export function trainingStatus() {
  return client.get('/training/status')
}

/** GET /api/training/progress — 训练进度 */
export function trainingProgress() {
  return client.get('/training/progress')
}

/** POST /api/training/clean — 清洗数据 */
export function trainingClean(acceptScore: number) {
  return client.post('/training/clean', null, { params: { accept_score: acceptScore } })
}

/** POST /api/training/test — 测试克隆 */
export function trainingTest(message: string) {
  return client.post('/training/test', null, { params: { message } })
}

/** POST /api/training/apply — 应用克隆 */
export function trainingApply(characterId?: string) {
  return client.post('/training/apply', null, { params: { ...(characterId ? { character_id: characterId } : {}) } })
}

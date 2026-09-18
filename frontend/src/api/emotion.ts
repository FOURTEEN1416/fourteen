/**
 * emotion 领域 API。
 *
 * 自 client.ts 迁入（FF-0006：client.ts 只做实例装配 + re-export，
 * 领域函数必须落在领域文件中）。消费方：hooks/useQueries.ts
 * （经 client.ts 的 `api` 命名空间调用，保持向后兼容）。
 */
import client from './client'

/** GET /api/emotion/state — 当前情感状态 */
export function emotionState() {
  return client.get('/emotion/state')
}

/** GET /api/emotion/trend — 情感趋势（默认 7 天） */
export function emotionTrend(days = 7) {
  return client.get('/emotion/trend', { params: { days } })
}

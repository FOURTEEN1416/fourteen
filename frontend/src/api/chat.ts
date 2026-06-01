import client from './client'

/** POST /api/chat — 发送消息 */
export function chat(message: string, sessionId = '', messageType = 'text') {
  return client.post('/chat', { message, session_id: sessionId, message_type: messageType })
}

/** POST /api/chat/stream — 流式对话 */
export function chatStream(message: string, sessionId = '', messageType = 'text') {
  const controller = new AbortController()
  const promise = client.post('/chat/stream',
    { message, session_id: sessionId, message_type: messageType },
    {
      responseType: 'stream',
      adapter: 'fetch',
      signal: controller.signal,
    },
  )
  return { promise, cancel: () => controller.abort() }
}

/** POST /api/session — 创建会话 */
export function createSession(userId = 'default', channel = 'web') {
  return client.post('/session', { user_id: userId, channel })
}

/** GET /api/sessions — 会话列表 */
export function listSessions() {
  return client.get('/sessions')
}

/** GET /api/chat/history — 聊天历史 */
export function chatHistory(sessionId = '', limit = 20) {
  return client.get('/chat/history', { params: { session_id: sessionId, limit } })
}

/** GET /api/emotion/state — 当前情感状态 */
export function emotionState() {
  return client.get('/emotion/state')
}

/** GET /api/emotion/trend — 情感趋势 */
export function emotionTrend(days = 7) {
  return client.get('/emotion/trend', { params: { days } })
}

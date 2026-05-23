import axios from 'axios'
import { useErrorStore } from '../store/errorStore'

const API_BASE = '/api'

const client = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
})

const apiKey = import.meta.env.VITE_API_KEY || ''
if (apiKey) {
  client.defaults.headers.common['X-API-Key'] = apiKey
}

// Global error interceptor — catches all 4xx/5xx and shows toast
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status
    const data = error.response?.data
    const msg = data?.detail || data?.message || error.message || '请求失败'

    // Skip 401 for now (no auth yet)
    if (status === 401) return Promise.reject(error)

    // Rate limit hint
    if (status === 429) {
      useErrorStore.getState().addToast({ type: 'warning', message: '请求太频繁，请稍后再试' })
      return Promise.reject(error)
    }

    // Network / timeout
    if (error.code === 'ERR_NETWORK' || error.code === 'ECONNABORTED') {
      useErrorStore.getState().addToast({ type: 'error', message: '无法连接到后端服务' })
      return Promise.reject(error)
    }

    // Server errors
    if (status && status >= 500) {
      useErrorStore.getState().addToast({ type: 'error', message: `服务器错误 (${status})` })
      return Promise.reject(error)
    }

    // General error toast for 4xx
    useErrorStore.getState().addToast({ type: 'warning', message: msg })
    return Promise.reject(error)
  }
)

export default client

export const api = {
  chat: (message: string, sessionId = '', messageType = 'text') =>
    client.post('/chat', { message, session_id: sessionId, message_type: messageType }),

  chatStream: (message: string, sessionId = '', messageType = 'text') =>
    fetch(`${API_BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(apiKey ? { 'X-API-Key': apiKey } : {}) },
      body: JSON.stringify({ message, session_id: sessionId, message_type: messageType }),
    }),

  health: () => client.get('/health'),
  stats: () => client.get('/stats'),
  dashboardStats: () => client.get('/stats/dashboard'),
  createSession: (userId = 'default', channel = 'web') =>
    client.post('/session', { user_id: userId, channel }),
  listSessions: () => client.get('/sessions'),
  chatHistory: (sessionId = '', limit = 20) =>
    client.get('/chat/history', { params: { session_id: sessionId, limit } }),
  emotionState: () => client.get('/emotion/state'),
  emotionTrend: (days = 7) => client.get('/emotion/trend', { params: { days } }),
  personaProfile: () => client.get('/persona/profile'),
  personaEvolutionLog: (limit = 50) => client.get('/persona/evolution-log', { params: { limit } }),
  memoryFacts: (category = '', limit = 50) =>
    client.get('/memory/facts', { params: { category, limit } }),
  tools: () => client.get('/tools'),
  proactiveState: () => client.get('/proactive/state'),
  config: () => client.get('/config'),
  saveConfig: (config: Record<string, any>) => client.post('/config', { config }),
  toggleTool: (name: string, enabled: boolean) => client.post(`/tools/${name}/toggle`, { enabled }),
  updateProactiveConfig: (cfg: { threshold?: number; max_daily?: number; min_interval_minutes?: number; cooldown_after_reply_minutes?: number }) =>
    client.post('/proactive/config', cfg),
  logs: (params?: { limit?: number; level?: string; search?: string }) =>
    client.get('/logs', { params }),
  channels: () => client.get('/channels'),
  wechatStatus: () => client.get('/channels/wechat/status'),
  wechatReconnect: () => client.post('/channels/wechat/reconnect'),
  trainingStatus: () => client.get('/training/status'),
  trainingProgress: () => client.get('/training/progress'),
  trainingExtract: (target: string, source: string) =>
    client.post('/training/extract', null, { params: { target, source } }),
  trainingClean: (acceptScore: number) =>
    client.post('/training/clean', null, { params: { accept_score: acceptScore } }),
  trainingTrain: (epochs: number, loraRank: number) =>
    client.post('/training/train', null, { params: { epochs, lora_rank: loraRank } }),
  trainingStop: () => client.post('/training/stop'),
  trainingTest: (message: string) =>
    client.post('/training/test', null, { params: { message } }),
  trainingApply: () => client.post('/training/apply'),

  // ── 手动微信连接（需求1） ──
  wechatConnect: () => client.post('/channels/wechat/connect'),
  wechatDisconnect: () => client.post('/channels/wechat/disconnect'),
  wechatConnectionStatus: () => client.get('/channels/wechat/connection-status'),

  // ── 克隆数据管理（需求3+4） ──
  cloneContacts: (keyword = '') =>
    client.get('/clone/contacts', { params: { keyword } }),
  cloneDatasets: () => client.get('/clone/datasets'),
  cloneDatasetDetail: (personId: string, params?: {
    page?: number; pageSize?: number; keyword?: string;
    dateFrom?: string; dateTo?: string; onlyUser?: boolean
  }) => client.get(`/clone/datasets/${personId}`, { params: {
    page: params?.page, page_size: params?.pageSize,
    keyword: params?.keyword, date_from: params?.dateFrom,
    date_to: params?.dateTo, only_user: params?.onlyUser,
  }}),
  cloneDeleteDataset: (personId: string) =>
    client.delete(`/clone/datasets/${personId}`),
  cloneDeleteConversation: (personId: string, index: number) =>
    client.delete(`/clone/datasets/${personId}/conversation`, { params: { index } }),
  cloneBatchDeleteConversations: (personId: string, indices: number[]) =>
    client.post(`/clone/datasets/${personId}/conversations/batch-delete`, null, { params: { indices: indices.join(',') } }),
  cloneStats: () => client.get('/clone/stats'),
}

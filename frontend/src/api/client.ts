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

const ERROR_CODE_MAP: Record<string, string> = {
  LLM_TIMEOUT: 'AI思考时间较长，请稍后重试',
  NETWORK_ERROR: '无法连接服务器，请检查网络',
  AUTH_ERROR: '认证已过期，请重新登录',
  RATE_LIMIT: '操作过于频繁，请30秒后重试',
  FEATURE_UNAVAILABLE: '该功能暂不可用',
}

// Global error interceptor — catches all 4xx/5xx and shows toast
client.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status
    const data = error.response?.data
    const errorCode = data?.error_code
    const rawDetail = data?.detail
    let detailStr = ''
    if (typeof rawDetail === 'string') {
      detailStr = rawDetail
    } else if (Array.isArray(rawDetail)) {
      detailStr = rawDetail
        .map((e: unknown) => {
          if (typeof e === 'string') return e
          if (e && typeof e === 'object' && 'msg' in e) {
            const errObj = e as { msg?: string; loc?: unknown[] }
            const loc = Array.isArray(errObj.loc) ? errObj.loc.join('.') : ''
            return loc ? `${loc}: ${errObj.msg}` : (errObj.msg ?? '')
          }
          return String(e)
        })
        .join('; ')
    } else if (rawDetail && typeof rawDetail === 'object') {
      detailStr = JSON.stringify(rawDetail)
    }

    if (errorCode && ERROR_CODE_MAP[errorCode]) {
      const type = errorCode === 'AUTH_ERROR' ? 'error' : errorCode === 'RATE_LIMIT' ? 'warning' : 'warning'
      useErrorStore.getState().addToast({ type, message: ERROR_CODE_MAP[errorCode] })
      return Promise.reject(error)
    }

    const msg = detailStr || data?.message || error.message || '请求失败'

    if (status === 401) return Promise.reject(error)

    if (status === 404) {
      useErrorStore.getState().addToast({ type: 'warning', message: '该功能暂不可用' })
      return Promise.reject(error)
    }

    if (status === 429) {
      useErrorStore.getState().addToast({ type: 'warning', message: '操作过于频繁，请30秒后重试' })
      return Promise.reject(error)
    }

    if (error.code === 'ERR_NETWORK' || error.code === 'ECONNABORTED') {
      useErrorStore.getState().addToast({ type: 'error', message: '无法连接服务器，请检查网络' })
      return Promise.reject(error)
    }

    if (status && status >= 500) {
      useErrorStore.getState().addToast({ type: 'error', message: `服务器错误 (${status})` })
      return Promise.reject(error)
    }

    useErrorStore.getState().addToast({ type: 'warning', message: msg })
    return Promise.reject(error)
  }
)

export default client

export const api = {
  chat: (message: string, sessionId = '', messageType = 'text') =>
    client.post('/chat', { message, session_id: sessionId, message_type: messageType }),

  chatStream: (message: string, sessionId = '', messageType = 'text') => {
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
  },

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

  // ── 微信二维码 ──
  wechatQrCode: () => client.get('/wechat/qrcode'),

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

  // ── 用户心理画像 ──
  psychProfile: () => client.get('/psych/profile'),
  psychSnapshots: (limit = 20) => client.get('/psych/snapshots', { params: { limit } }),
  psychReset: () => client.delete('/psych/profile'),
  psychMentalHealth: () => client.get('/psych/mental-health'),
  psychLiwc: () => client.get('/psych/liwc'),

  // ── 安全面板 ──
  safetyStats: () => client.get('/safety/stats'),
  safetyLog: (limit = 50) => client.get('/safety/log', { params: { limit } }),
  safetyConfig: (enabled: boolean) => client.post('/safety/config', null, { params: { enabled } }),

  // ── RAG 知识库 ──
  ragStats: () => client.get('/rag/stats'),
  ragSearch: (query: string, topK = 5) => client.post('/rag/search', null, { params: { query, top_k: topK } }),
  ragUpload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return client.post('/rag/documents', form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },

  // ── Voice TTS ──
  voiceStatus: () => client.get('/voice/status'),
  voiceSynthesize: (text: string, engine = '') => {
    const form = new FormData()
    form.append('text', text)
    if (engine) form.append('engine', engine)
    return client.post('/voice/synthesize', form, { headers: { 'Content-Type': 'multipart/form-data' }, responseType: 'blob' })
  },

  // ── 插件管理 ──
  plugins: () => client.get('/plugins'),
  togglePlugin: (name: string, enabled: boolean) => client.post(`/plugins/${name}/toggle`, null, { params: { enabled } }),

  // ── 文件上传 ──
  uploadFile: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return client.post('/files/upload', form, { headers: { 'Content-Type': 'multipart/form-data' } })
  },

  // ── 工具历史 ──
  toolHistory: (limit = 50) => client.get('/tools/history', { params: { limit } }),

  // ── 主动消息历史 ──
  proactiveHistory: (limit = 50) => client.get('/proactive/history', { params: { limit } }),
}

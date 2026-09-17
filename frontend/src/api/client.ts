/**
 * API 客户端 & 领域函数总线
 *
 * ═══ Decision 5: 数据架构约定 ═══
 * • 服务端数据（角色列表、训练状态、日志等）→ React Query (useQueries.ts)
 *     从本文件 import { api } 调用，queryKey 见 useQueries.ts
 * • UI 交互状态（弹窗、表单输入、选中项、切片 UI 状态）→ Zustand store
 *     见 ../store/ 目录
 * • 例外：少量页面仍用 useState 直接管理服务端数据（UsersPage），
 *     逐步迁移到 React Query + api.* 模式
 *
 * Option A: accessToken 存内存（getAccessToken），refreshToken 由 httpOnly cookie 管理。
 */
import axios from 'axios'
import { useErrorStore } from '../store/errorStore'
import { getAccessToken, setAccessToken, useAuthStore } from '../store/authStore'
import { refreshToken as refreshTokenApi } from './auth'

// ── Domain API imports (for re-export and api namespace) ──
import {
  trainingStatus, trainingProgress, trainingClean,
  trainingTest, trainingApply,
} from './training'
import {
  cloneContacts, cloneDatasets, cloneDatasetDetail, cloneDeleteDataset,
  cloneDeleteConversation, cloneBatchDeleteConversations, cloneUpload, cloneStats,
} from './clone'
import {
  health, stats, dashboardStats, config, saveConfig,
  personaProfile, personaEvolutionLog, memoryFacts,
  tools, toolsHealth, toggleTool, toolHistory, proactiveState, proactiveHistory, updateProactiveConfig,
  logs, channels, wechatStatus, wechatReconnect, wechatConnect, wechatDisconnect,
  wechatConnectionStatus, wechatQrCode,
  safetyStats, safetyLog, safetyConfig,
  ragStats, ragSearch, ragUpload,
  voiceStatus, voiceSynthesize, getSpeakers,
  plugins, togglePlugin, uploadFile,
  psychProfile, psychSnapshots, psychReset, psychMentalHealth, psychLiwc,
} from './system'
import {
  wechatCreateConnection, wechatListConnections, wechatUpdateConnection, wechatDeleteConnection,
} from './wechat'
import {
  listCharacters, createCharacter, getCharacter, updateCharacter, deleteCharacter, activateCharacter,
  getPersona, updatePersona,
  getPersonaCard, updatePersonaCard, previewPersonaCard,
  listFavorites, addFavorite, removeFavorite, forwardFavorite,
  getStorylineConfig, updateStorylineConfig, deleteStorylineConfig,
  getStorylineProgress, detectStoryline, resetStoryline,
  getVoiceConfig, bindVoice, updateVoice, unbindVoice, testVoice,
  exportCharacter, importCharacter, previewCharacterFromDescription,
  listPresets, getPreset,
} from './characters'
import {
  adminListUsers, adminUpdateUser, adminDeleteUser, adminCreateUser,
} from './admin'

const API_BASE = '/api'

const client = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
  withCredentials: true, // 确保跨域时发送 httpOnly cookie
})

const apiKey = import.meta.env.VITE_API_KEY || ''
if (apiKey) {
  client.defaults.headers.common['X-API-Key'] = apiKey
}

// ── JWT Bearer token interceptor ──
// Attach access token from memory (not localStorage) to every request
client.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── 401 auto-refresh state ──
let _isRefreshing = false
let _pendingQueue: Array<{
  resolve: (token: string) => void
  reject: (error: unknown) => void
}> = []

function processQueue(error: unknown, token: string | null = null) {
  _pendingQueue.forEach(({ resolve, reject }) => {
    if (error) {
      reject(error)
    } else {
      resolve(token!)
    }
  })
  _pendingQueue = []
}

/**
 * 把后端返回的 detail 归一化为可读字符串。
 *
 * 后端有两种形态：
 *   • 业务错误 → detail 是字符串（如 "Invalid login credentials"）
 *   • 请求校验失败（422）→ detail 是对象数组，元素形如
 *       { type, loc, msg, input, ctx }
 * 若把后者原样交给 setState / addToast，React 渲染对象 child 会抛
 * 「Minified React error #31 (object with keys {type, loc, msg, input, ctx})」，
 * 整页白屏。故统一压平成 "body.login: Field required; ..." 形式。
 */
export function normalizeDetail(rawDetail: unknown): string {
  if (typeof rawDetail === 'string') return rawDetail
  if (Array.isArray(rawDetail)) {
    return rawDetail
      .map((e: unknown) => {
        if (typeof e === 'string') return e
        if (e && typeof e === 'object' && 'msg' in e) {
          const errObj = e as { msg?: unknown; loc?: unknown }
          const loc = Array.isArray(errObj.loc) ? errObj.loc.join('.') : ''
          const msg = typeof errObj.msg === 'string' ? errObj.msg : ''
          return loc ? `${loc}: ${msg}` : msg
        }
        return String(e)
      })
      .filter((line) => line.length > 0)
      .join('; ')
  }
  if (rawDetail && typeof rawDetail === 'object') return JSON.stringify(rawDetail)
  return ''
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
  async (error) => {
    const status = error.response?.status
    const data = error.response?.data
    const originalRequest = error.config as (typeof error.config) & { _isRetry?: boolean }

    // ── 第 0 步：detail 归一化（保护所有下游取用点）──
    // 页面级 catch 常直接读 err.response?.data?.detail 并塞进 state / addToast；
    // 若此处仍是 422 的对象数组，React 渲染对象 child 会抛 error #31 导致白屏。
    if (data && typeof data === 'object' && 'detail' in data) {
      ;(data as { detail?: unknown }).detail = normalizeDetail((data as { detail?: unknown }).detail)
    }

    // ── 403 BYOK_REQUIRED：用户自带 Key 引导（W1，2026-08-28）──
    const byokCode = (error.response?.headers?.['x-error-code'] ??
      (data as { detail?: string } | undefined)?.detail) as string | undefined
    if (
      status === 403 &&
      (byokCode === 'BYOK_REQUIRED' ||
        (typeof data?.detail === 'string' && data.detail.includes('自带 API Key')))
    ) {
      if (typeof window !== 'undefined' && !window.location.pathname.startsWith('/settings/llm')) {
        window.location.assign('/settings/llm?byok=1')
        return new Promise(() => {}) // 跳转中挂起
      }
    }

    // ── 401 auto-refresh ──
    // Uses httpOnly cookie (browser auto-sends) instead of stored refresh_token
    if (
      status === 401 &&
      originalRequest &&
      !originalRequest._isRetry &&
      !originalRequest.url?.includes('/auth/refresh')
    ) {
      if (_isRefreshing) {
        // Queue concurrent 401s — they'll all retry with the new token
        return new Promise((resolve, reject) => {
          _pendingQueue.push({
            resolve: (token: string) => {
              originalRequest.headers.Authorization = `Bearer ${token}`
              originalRequest._isRetry = true
              resolve(client(originalRequest))
            },
            reject,
          })
        })
      }

      _isRefreshing = true
      originalRequest._isRetry = true

      try {
        // httpOnly cookie auto-sent by browser — no need to pass refresh_token
        const res = await refreshTokenApi()
        setAccessToken(res.access_token)
        useAuthStore.getState().setAuth(res.user, res.access_token)

        // Unblock queued requests with the new token
        processQueue(null, res.access_token)

        // Retry the original request
        originalRequest.headers.Authorization = `Bearer ${res.access_token}`
        return client(originalRequest)
      } catch (refreshError) {
        processQueue(refreshError, null)
        useAuthStore.getState().clearAuth()
        window.location.href = '/login'
        return Promise.reject(refreshError)
      } finally {
        _isRefreshing = false
      }
    }

    // ── Existing error handling (falls through for non-401 / no refresh token) ──
    const errorCode = data?.error_code
    // detail 已在上方归一化为字符串（见 normalizeDetail），此处无需再判别类型
    const detailStr = typeof data?.detail === 'string' ? data.detail : ''

    if (errorCode && ERROR_CODE_MAP[errorCode]) {
      const type = errorCode === 'AUTH_ERROR' ? 'error' : errorCode === 'RATE_LIMIT' ? 'warning' : 'warning'
      useErrorStore.getState().addToast({ type, message: ERROR_CODE_MAP[errorCode] })
      return Promise.reject(error)
    }

    const msg = detailStr || data?.message || error.message || '请求失败'

    // 401 without a refresh token → not authenticated, just reject
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

// ── Named re-exports for backward compat (import { chat } from '../api/client') ──
// emotion 域自 chat.ts 死代码清理后迁入（useQueries 消费中）；chat 会话域已随僵尸 chatStore 一并删除
export function emotionState() {
  return client.get('/emotion/state')
}
export function emotionTrend(days = 7) {
  return client.get('/emotion/trend', { params: { days } })
}
export { trainingStatus, trainingProgress, trainingClean, trainingTest, trainingApply }
export { cloneContacts, cloneDatasets, cloneDatasetDetail, cloneDeleteDataset, cloneDeleteConversation, cloneBatchDeleteConversations, cloneUpload, cloneStats }
export {
  listCharacters, createCharacter, getCharacter, updateCharacter, deleteCharacter, activateCharacter,
  getPersona, updatePersona,
  getPersonaCard, updatePersonaCard, previewPersonaCard,
  listFavorites, addFavorite, removeFavorite, forwardFavorite,
  getStorylineConfig, updateStorylineConfig, deleteStorylineConfig,
  getStorylineProgress, detectStoryline, resetStoryline,
  getVoiceConfig, bindVoice, updateVoice, unbindVoice, testVoice,
  exportCharacter, importCharacter, previewCharacterFromDescription,
  listPresets, getPreset,
}
export { adminListUsers, adminUpdateUser, adminDeleteUser, adminCreateUser }
export {
  wechatCreateConnection, wechatListConnections, wechatUpdateConnection, wechatDeleteConnection,
}
export {
  health, stats, dashboardStats, config, saveConfig,
  personaProfile, personaEvolutionLog, memoryFacts,
  tools, toolsHealth, toggleTool, toolHistory, proactiveState, proactiveHistory, updateProactiveConfig,
  logs, channels, wechatStatus, wechatReconnect, wechatConnect, wechatDisconnect,
  wechatConnectionStatus, wechatQrCode,
  psychProfile, psychSnapshots, psychReset, psychMentalHealth, psychLiwc,
  safetyStats, safetyLog, safetyConfig,
  ragStats, ragSearch, ragUpload,
  voiceStatus, voiceSynthesize, getSpeakers,
  plugins, togglePlugin,
  uploadFile,
}

// Legacy `api` namespace object — keeps `import { api } from '../api/client'` working
export const api = {
  emotionState, emotionTrend,
  health, stats, dashboardStats, config, saveConfig,
  personaProfile, personaEvolutionLog, memoryFacts,
  tools, toolsHealth, toggleTool, toolHistory, proactiveState, proactiveHistory, updateProactiveConfig,
  logs, channels, wechatStatus, wechatReconnect, wechatConnect, wechatDisconnect,
  wechatConnectionStatus, wechatQrCode,
  trainingStatus, trainingProgress, trainingClean,
  trainingTest, trainingApply,
  cloneContacts, cloneDatasets, cloneDatasetDetail, cloneDeleteDataset,
  cloneDeleteConversation, cloneBatchDeleteConversations, cloneUpload, cloneStats,
  psychProfile, psychSnapshots, psychReset, psychMentalHealth, psychLiwc,
  safetyStats, safetyLog, safetyConfig,
  ragStats, ragSearch, ragUpload,
  voiceStatus, voiceSynthesize, getSpeakers,
  plugins, togglePlugin,
  uploadFile,
  // characters domain
  listCharacters, createCharacter, getCharacter, updateCharacter, deleteCharacter, activateCharacter,
  getPersona, updatePersona,
  getPersonaCard, updatePersonaCard, previewPersonaCard,
  listFavorites, addFavorite, removeFavorite, forwardFavorite,
  getStorylineConfig, updateStorylineConfig, deleteStorylineConfig,
  getStorylineProgress, detectStoryline, resetStoryline,
  getVoiceConfig, bindVoice, updateVoice, unbindVoice, testVoice,
  exportCharacter, importCharacter, previewCharacterFromDescription,
  listPresets, getPreset,
  // admin domain
  adminListUsers, adminUpdateUser, adminDeleteUser, adminCreateUser,
  // wechat connections
  wechatCreateConnection, wechatListConnections, wechatUpdateConnection, wechatDeleteConnection,
}

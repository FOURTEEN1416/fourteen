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
  chat, chatStream, createSession, listSessions, chatHistory, emotionState, emotionTrend,
} from './chat'
import {
  trainingStatus, trainingProgress, trainingExtract, trainingClean,
  trainingTrain, trainingStop, trainingTest, trainingApply,
} from './training'
import {
  cloneContacts, cloneDatasets, cloneDatasetDetail, cloneDeleteDataset,
  cloneDeleteConversation, cloneBatchDeleteConversations, clonePreview, cloneUpload, cloneStats,
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
  bindWechat, listMyBindings, updateBinding, unbindWechat,
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
  listUsers, getUserDetail, getUserChatHistory, getUserEmotion,
  setUserRole, resetUser, deleteUser,
} from './users'
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
export { chat, chatStream, createSession, listSessions, chatHistory, emotionState, emotionTrend }
export { trainingStatus, trainingProgress, trainingExtract, trainingClean, trainingTrain, trainingStop, trainingTest, trainingApply }
export { cloneContacts, cloneDatasets, cloneDatasetDetail, cloneDeleteDataset, cloneDeleteConversation, cloneBatchDeleteConversations, clonePreview, cloneUpload, cloneStats }
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
export { listUsers, getUserDetail, getUserChatHistory, getUserEmotion, setUserRole, resetUser, deleteUser }
export { adminListUsers, adminUpdateUser, adminDeleteUser, adminCreateUser }
export {
  wechatCreateConnection, wechatListConnections, wechatUpdateConnection, wechatDeleteConnection,
  bindWechat, listMyBindings, updateBinding, unbindWechat,
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
  chat, chatStream, createSession, listSessions, chatHistory, emotionState, emotionTrend,
  health, stats, dashboardStats, config, saveConfig,
  personaProfile, personaEvolutionLog, memoryFacts,
  tools, toolsHealth, toggleTool, toolHistory, proactiveState, proactiveHistory, updateProactiveConfig,
  logs, channels, wechatStatus, wechatReconnect, wechatConnect, wechatDisconnect,
  wechatConnectionStatus, wechatQrCode,
  trainingStatus, trainingProgress, trainingExtract, trainingClean,
  trainingTrain, trainingStop, trainingTest, trainingApply,
  cloneContacts, cloneDatasets, cloneDatasetDetail, cloneDeleteDataset,
  cloneDeleteConversation, cloneBatchDeleteConversations, clonePreview, cloneUpload, cloneStats,
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
  // users domain
  listUsers, getUserDetail, getUserChatHistory, getUserEmotion, setUserRole, resetUser, deleteUser,
  // admin domain
  adminListUsers, adminUpdateUser, adminDeleteUser, adminCreateUser,
  // wechat bindings
  wechatCreateConnection, wechatListConnections, wechatUpdateConnection, wechatDeleteConnection,
  bindWechat, listMyBindings, updateBinding, unbindWechat,
}

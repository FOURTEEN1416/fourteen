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
 */
import axios from 'axios'
import { useErrorStore } from '../store/errorStore'

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
  cloneDeleteConversation, cloneBatchDeleteConversations, cloneStats,
} from './clone'
import {
  health, stats, dashboardStats, config, saveConfig,
  personaProfile, personaEvolutionLog, memoryFacts,
  tools, toggleTool, toolHistory, proactiveState, proactiveHistory, updateProactiveConfig,
  logs, channels, wechatStatus, wechatReconnect, wechatConnect, wechatDisconnect,
  wechatConnectionStatus, wechatQrCode,
  psychProfile, psychSnapshots, psychReset, psychMentalHealth, psychLiwc,
  safetyStats, safetyLog, safetyConfig,
  ragStats, ragSearch, ragUpload,
  voiceStatus, voiceSynthesize, getSpeakers,
  plugins, togglePlugin, uploadFile,
  affinityGet, affinityUpdate, affinityUnlocks, affinityDecay,
  emotionStageGet, emotionStageList, emotionStageEvaluate,
  vitalSignsGet, shisiStats,
  stickerList, stickerDelete, stickerRecommend, stickerImportZip, stickerUpload, stickerBindToCharacter,
  shisiMemoryFavorites, shisiMemoryAddFavorite, shisiMemoryRemoveFavorite,
  shisiMemoryForward, shisiMemoryDelete,
  voiceTrainingUpload, voiceTrainingPreprocess, voiceTrainingTrain, voiceTrainingStatus,
  shisiCharactersList, shisiPersonaGet, shisiPersonaUpdate, shisiPersonaPreview,
} from './system'
import {
  listCharacters, createCharacter, getCharacter, updateCharacter, deleteCharacter, activateCharacter,
  getPersona, updatePersona,
  getPersonaCard, updatePersonaCard, previewPersonaCard,
  listFavorites, addFavorite, removeFavorite, forwardFavorite,
  getStorylineConfig, updateStorylineConfig, deleteStorylineConfig,
  getStorylineProgress, detectStoryline, resetStoryline,
  getVoiceConfig, bindVoice, updateVoice, unbindVoice, testVoice,
  exportCharacter, importCharacter,
} from './characters'
import {
  listUsers, getUserDetail, getUserChatHistory, getUserEmotion,
  setUserRole, resetUser, deleteUser,
} from './users'

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

// ── Named re-exports for backward compat (import { chat } from '../api/client') ──
export { chat, chatStream, createSession, listSessions, chatHistory, emotionState, emotionTrend }
export { trainingStatus, trainingProgress, trainingExtract, trainingClean, trainingTrain, trainingStop, trainingTest, trainingApply }
export { cloneContacts, cloneDatasets, cloneDatasetDetail, cloneDeleteDataset, cloneDeleteConversation, cloneBatchDeleteConversations, cloneStats }
export {
  listCharacters, createCharacter, getCharacter, updateCharacter, deleteCharacter, activateCharacter,
  getPersona, updatePersona,
  getPersonaCard, updatePersonaCard, previewPersonaCard,
  listFavorites, addFavorite, removeFavorite, forwardFavorite,
  getStorylineConfig, updateStorylineConfig, deleteStorylineConfig,
  getStorylineProgress, detectStoryline, resetStoryline,
  getVoiceConfig, bindVoice, updateVoice, unbindVoice, testVoice,
  exportCharacter, importCharacter,
}
export { listUsers, getUserDetail, getUserChatHistory, getUserEmotion, setUserRole, resetUser, deleteUser }
export {
  health, stats, dashboardStats, config, saveConfig,
  personaProfile, personaEvolutionLog, memoryFacts,
  tools, toggleTool, toolHistory, proactiveState, proactiveHistory, updateProactiveConfig,
  logs, channels, wechatStatus, wechatReconnect, wechatConnect, wechatDisconnect,
  wechatConnectionStatus, wechatQrCode,
  psychProfile, psychSnapshots, psychReset, psychMentalHealth, psychLiwc,
  safetyStats, safetyLog, safetyConfig,
  ragStats, ragSearch, ragUpload,
  voiceStatus, voiceSynthesize, getSpeakers,
  plugins, togglePlugin, uploadFile,
  affinityGet, affinityUpdate, affinityUnlocks, affinityDecay,
  emotionStageGet, emotionStageList, emotionStageEvaluate,
  vitalSignsGet, shisiStats,
  stickerList, stickerDelete, stickerRecommend, stickerImportZip, stickerUpload, stickerBindToCharacter,
  shisiMemoryFavorites, shisiMemoryAddFavorite, shisiMemoryRemoveFavorite,
  shisiMemoryForward, shisiMemoryDelete,
  voiceTrainingUpload, voiceTrainingPreprocess, voiceTrainingTrain, voiceTrainingStatus,
  shisiCharactersList, shisiPersonaGet, shisiPersonaUpdate, shisiPersonaPreview,
}

// Legacy `api` namespace object — keeps `import { api } from '../api/client'` working
export const api = {
  chat, chatStream, createSession, listSessions, chatHistory, emotionState, emotionTrend,
  health, stats, dashboardStats, config, saveConfig,
  personaProfile, personaEvolutionLog, memoryFacts,
  tools, toggleTool, toolHistory, proactiveState, proactiveHistory, updateProactiveConfig,
  logs, channels, wechatStatus, wechatReconnect, wechatConnect, wechatDisconnect,
  wechatConnectionStatus, wechatQrCode,
  trainingStatus, trainingProgress, trainingExtract, trainingClean,
  trainingTrain, trainingStop, trainingTest, trainingApply,
  cloneContacts, cloneDatasets, cloneDatasetDetail, cloneDeleteDataset,
  cloneDeleteConversation, cloneBatchDeleteConversations, cloneStats,
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
  exportCharacter, importCharacter,
  // users domain
  listUsers, getUserDetail, getUserChatHistory, getUserEmotion, setUserRole, resetUser, deleteUser,
  // shisi legacy — flattened into api object
  affinityGet, affinityUpdate, affinityUnlocks, affinityDecay,
  emotionStageGet, emotionStageList, emotionStageEvaluate,
  vitalSignsGet, shisiStats,
  stickerList, stickerDelete, stickerRecommend, stickerImportZip, stickerUpload, stickerBindToCharacter,
  shisiMemoryFavorites, shisiMemoryAddFavorite, shisiMemoryRemoveFavorite,
  shisiMemoryForward, shisiMemoryDelete,
  voiceTrainingUpload, voiceTrainingPreprocess, voiceTrainingTrain, voiceTrainingStatus,
  shisiCharactersList, shisiPersonaGet, shisiPersonaUpdate, shisiPersonaPreview,
}

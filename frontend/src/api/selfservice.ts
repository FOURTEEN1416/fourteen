/**
 * 账号自服务 API — /api/auth/consent/*、/api/auth/account/*（W17）
 *
 * 后端真源：`api/routers/auth_routes.py`（HTTP 接线）+ `api/consent.py`（同意状态机）
 * + `api/lifecycle.py`（注销作业 / 导出清单 / 聊天分页）。本模块只做**传输层**，
 * 不复制任何判定语义：状态与回执一律以服务端返回为准（W11 纪律）。
 *
 * 契约边界：
 * - 撤回同意 → 服务端落 WITHDRAWN 哨兵档，四通道（HTTP 聊天 / WS / 微信 / 后台外发）
 *   即刻 fail-closed；重同意走既有 `POST /api/auth/consent`（`./auth` 的 `consent()`）。
 * - 注销 → 立即冻结 + 异步清除；响应**永不含「已删除」宣称**（W9 纪律），
 *   故受理判据只认作业状态白名单，不认 `deleted` 之类字面。
 * - 聊天导出 → 只能导出本人会话键；他人 `session_key` 后端一律 404（防枚举）。
 */
import client from './client'

// ── 类型（与后端响应逐字段同构）──────────────────────────

/** 后端 `consent_state_of` 的四个取值，无第五态 */
export type ConsentState = 'granted' | 'missing' | 'withdrawn' | 'outdated'

export interface ConsentStatusResponse {
  status: ConsentState
  agreement_version: string
}

export interface AccountDeleteReceipt {
  job_id: string
  user_id: number
  status: string
  completed: boolean
  steps: Record<string, unknown>
}

export interface AccountCategories {
  chats: number
  facts: number
  reflections: number
  reminders: number
  profile: number
  diary: number
  consents: number
  wechat_bindings: number
  wechat_channels: number
}

export interface AccountManifest {
  user_id: number
  session_keys: string[]
  categories: AccountCategories
}

export interface ChatExportMessage {
  id: number
  role: string
  content: string
  emotion_tag?: string | null
  created_at?: string | null
  character_id?: string | null
  turn_id?: string | null
  session_id?: string | null
  user_key?: string | null
}

export interface ChatExportPage {
  messages: ChatExportMessage[]
  next_before_id: number | null
}

export interface ChatsExport {
  user_id: number
  exported_at: string
  /** 会话键 → 按写入 id 升序的全部原文 */
  sessions: Record<string, ChatExportMessage[]>
  total_messages: number
}

/** 注销作业的受理态白名单（`_public_job` 的 status 取值域） */
export const DELETE_ACCEPTED_STATUSES = ['queued', 'purging', 'completed'] as const

const CHAT_PAGE_LIMIT = 500
/** 分页保护：后端每页 ≤1000 条，游标只减不增；异常返回自增游标时避免死循环 */
const MAX_PAGES_PER_SESSION = 1000

// ── API 函数 ─────────────────────────────────────────────

/** GET /api/auth/consent/status — 本人当前同意状态 */
export function getConsentStatus(): Promise<ConsentStatusResponse> {
  return client.get('/auth/consent/status').then((r) => r.data as ConsentStatusResponse)
}

/** POST /api/auth/consent/withdraw — 撤回同意，返回服务端最新状态 */
export function withdrawConsent(): Promise<{ status: ConsentState }> {
  return client.post('/auth/consent/withdraw').then((r) => r.data as { status: ConsentState })
}

/** POST /api/auth/account/delete — 申请注销（冻结 + 异步清除） */
export function deleteAccount(): Promise<AccountDeleteReceipt> {
  return client.post('/auth/account/delete').then((r) => r.data as AccountDeleteReceipt)
}

/** GET /api/auth/account/export — 本人数据清单（类别计数 + 会话键） */
export function exportAccountManifest(): Promise<AccountManifest> {
  return client.get('/auth/account/export').then((r) => r.data as AccountManifest)
}

/** GET /api/auth/account/export/chats — 单个本人会话的聊天分页 */
export function exportChatPage(
  sessionKey: string,
  beforeId = 0,
  limit: number = CHAT_PAGE_LIMIT,
): Promise<ChatExportPage> {
  return client
    .get('/auth/account/export/chats', {
      params: { session_key: sessionKey, before_id: beforeId, limit },
    })
    .then((r) => r.data as ChatExportPage)
}

/**
 * 按清单逐会话键走到游标尽头，汇成一份可下载的导出对象。
 *
 * 游标是 `id < before_id` 的**倒序取、正序回**：后一页比前一页更早，
 * 故合并后必须按写入 id 升序还原时间线（历史排序真源纪律：只认 id，不认时间戳）。
 */
export async function exportAllChats(): Promise<ChatsExport> {
  const manifest = await exportAccountManifest()
  const sessions: Record<string, ChatExportMessage[]> = {}

  for (const key of manifest.session_keys) {
    const collected: ChatExportMessage[] = []
    let cursor = 0
    for (let page = 0; page < MAX_PAGES_PER_SESSION; page += 1) {
      const res = await exportChatPage(key, cursor)
      collected.push(...res.messages)
      if (res.next_before_id === null || res.next_before_id === undefined) break
      cursor = res.next_before_id
    }
    sessions[key] = collected.sort((a, b) => a.id - b.id)
  }

  return {
    user_id: manifest.user_id,
    exported_at: new Date().toISOString(),
    sessions,
    total_messages: Object.values(sessions).reduce((n, list) => n + list.length, 0),
  }
}

/**
 * 注销是否受理：只认后端作业状态白名单。
 * `completed === false` 不是失败——清除本来就是异步的，此刻「未删完」是正常态。
 */
export function isDeleteAccepted(receipt: AccountDeleteReceipt | undefined | null): boolean {
  if (!receipt || typeof receipt.status !== 'string') return false
  return (DELETE_ACCEPTED_STATUSES as readonly string[]).includes(receipt.status)
}

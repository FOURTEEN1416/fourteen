import { describe, it, expect, vi, beforeEach } from 'vitest'
import client from '../../api/client'
import {
  getConsentStatus,
  withdrawConsent,
  deleteAccount,
  exportAccountManifest,
  exportChatPage,
  exportAllChats,
  isDeleteAccepted,
  DELETE_ACCEPTED_STATUSES,
} from '../../api/selfservice'

// ════════════════════════════════════════════════════════════════
//  W17 红测：自服务五端点的前端消费面
//  后端真源（api/routers/auth_routes.py + api/lifecycle.py，只读核对）：
//    GET  /api/auth/consent/status   → {status, agreement_version}
//    POST /api/auth/consent/withdraw → {status}
//    POST /api/auth/account/delete   → {job_id,user_id,status,completed,steps}
//                                        （W9 纪律：清除完成前**不含 deleted 宣称**）
//    GET  /api/auth/account/export   → {user_id,session_keys,categories{…}}
//    GET  /api/auth/account/export/chats?session_key=&before_id=&limit=
//                                    → {messages,next_before_id}
// ════════════════════════════════════════════════════════════════

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

const get = vi.mocked(client.get)
const post = vi.mocked(client.post)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('consent 面', () => {
  it('GET /auth/consent/status 原样返回状态与版本', async () => {
    get.mockResolvedValue({ data: { status: 'granted', agreement_version: '2026-09-01' } } as never)

    const res = await getConsentStatus()

    expect(get).toHaveBeenCalledWith('/auth/consent/status')
    expect(res.status).toBe('granted')
    expect(res.agreement_version).toBe('2026-09-01')
  })

  it('POST /auth/consent/withdraw 返回后端最新状态（页面据此更新，不自评）', async () => {
    post.mockResolvedValue({ data: { status: 'withdrawn' } } as never)

    const res = await withdrawConsent()

    expect(post).toHaveBeenCalledWith('/auth/consent/withdraw')
    expect(res.status).toBe('withdrawn')
  })
})

describe('account/delete 面', () => {
  it('POST /auth/account/delete 透传作业回执', async () => {
    const receipt = { job_id: 'acct-7', user_id: 7, status: 'queued', completed: false, steps: {} }
    post.mockResolvedValue({ data: receipt } as never)

    const res = await deleteAccount()

    expect(post).toHaveBeenCalledWith('/auth/account/delete')
    expect(res).toEqual(receipt)
  })

  it('受理判据只认后端作业状态，且永不把 completed=false 说成已删除', () => {
    expect(DELETE_ACCEPTED_STATUSES).toEqual(['queued', 'purging', 'completed'])
    expect(isDeleteAccepted({ job_id: 'j', user_id: 1, status: 'queued', completed: false, steps: {} })).toBe(true)
    expect(isDeleteAccepted({ job_id: 'j', user_id: 1, status: 'failed', completed: false, steps: {} })).toBe(false)
    // 非受理语义（后端若哪天回 deleted）不能被判据静默吞掉
    expect(isDeleteAccepted({ job_id: 'j', user_id: 1, status: 'deleted', completed: true, steps: {} })).toBe(false)
  })
})

describe('导出面', () => {
  const MANIFEST = {
    user_id: 7,
    session_keys: ['7:peerA', '7:peerB'],
    categories: {
      chats: 3, facts: 1, reflections: 0, reminders: 0, profile: 1,
      diary: 0, consents: 2, wechat_bindings: 0, wechat_channels: 0,
    },
  }

  it('GET /auth/account/export 取回清单', async () => {
    get.mockResolvedValue({ data: MANIFEST } as never)

    const res = await exportAccountManifest()

    expect(get).toHaveBeenCalledWith('/auth/account/export')
    expect(res.categories.chats).toBe(3)
  })

  it('GET /auth/account/export/chats 带 session_key / before_id / limit 参数', async () => {
    get.mockResolvedValue({ data: { messages: [], next_before_id: null } } as never)

    await exportChatPage('7:peerA', 0)

    expect(get).toHaveBeenCalledWith('/auth/account/export/chats', {
      params: { session_key: '7:peerA', before_id: 0, limit: 500 },
    })
  })

  it('exportAllChats 按清单逐会话键翻页取全（id 游标走到 null）', async () => {
    get.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/auth/account/export') return { data: MANIFEST } as never
      const params = config?.params ?? {}
      const key = params.session_key as string
      const beforeId = params.before_id as number
      if (key === '7:peerA' && beforeId === 0) {
        return {
          data: {
            messages: [{ id: 1, role: 'user', content: '早' }, { id: 2, role: 'assistant', content: '早呀' }],
            next_before_id: 1,
          },
        } as never
      }
      if (key === '7:peerA' && beforeId === 1) {
        return { data: { messages: [{ id: 0, role: 'user', content: '在吗' }], next_before_id: null } } as never
      }
      return { data: { messages: [{ id: 9, role: 'user', content: 'B' }], next_before_id: null } } as never
    })

    const res = await exportAllChats()

    expect(res.user_id).toBe(7)
    expect(Object.keys(res.sessions)).toEqual(['7:peerA', '7:peerB'])
    expect(res.sessions['7:peerA'].map((m) => m.id)).toEqual([0, 1, 2])
    expect(res.sessions['7:peerB'].map((m) => m.id)).toEqual([9])
    expect(res.total_messages).toBe(4)
  })

  it('会话键为空时导出空结构（不当错误，也不编造消息）', async () => {
    get.mockResolvedValue({
      data: { ...MANIFEST, session_keys: [], categories: { ...MANIFEST.categories, chats: 0 } },
    } as never)

    const res = await exportAllChats()

    expect(res.sessions).toEqual({})
    expect(res.total_messages).toBe(0)
    expect(get).toHaveBeenCalledTimes(1)
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import client from '../../api/client'
import {
  topicStateGetConfig,
  topicStateSnapshot,
  updateTopicStateConfig,
} from '../../api/system'

// ════════════════════════════════════════════════════════════════
//  话题状态 API 客户端（前端消费面）
//  后端真源（api/routers/topic_routes.py）：
//    GET  /api/topic-state/config
//    POST /api/topic-state/config  → partial merge，snake_case
//    GET  /api/topic-state/{session_key}?character_id=...
//  端点无 prefix，故前端不得自行拼 /training 前缀。
// ════════════════════════════════════════════════════════════════

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

const get = vi.mocked(client.get)
const post = vi.mocked(client.post)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('topicStateGetConfig', () => {
  it('GET 全串路径 /topic-state/config（无 training 前缀）并原样透传回执', async () => {
    get.mockResolvedValue({
      data: { status: 'ok', config: { enabled: true, active_cap: 2 } },
    } as never)

    const res = await topicStateGetConfig()

    expect(get).toHaveBeenCalledWith('/topic-state/config')
    expect(res.data.status).toBe('ok')
    expect(res.data.config.active_cap).toBe(2)
  })
})

describe('updateTopicStateConfig', () => {
  it('POST partial merge：键名与后端 snake_case 一致，不补默认键', async () => {
    post.mockResolvedValue({ data: { status: 'ok', config: {} } } as never)

    await updateTopicStateConfig({ enabled: true, switch_theta1: 0.25 })

    expect(post).toHaveBeenCalledWith('/topic-state/config', {
      enabled: true,
      switch_theta1: 0.25,
    })
    const body = post.mock.calls[0][1] as Record<string, unknown>
    expect('judge_every_turns' in body).toBe(false)
    expect('retention_days' in body).toBe(false)
  })
})

describe('topicStateSnapshot', () => {
  it('session_key 走 encodeURIComponent，character_id 走 query 参数', async () => {
    get.mockResolvedValue({
      data: { status: 'ok', enabled: true, active: [], parked: [], closed: [], next_plan: null },
    } as never)

    await topicStateSnapshot('2:wxid@im.wechat', 'role-a')

    expect(get).toHaveBeenCalledWith('/topic-state/2%3Awxid%40im.wechat', {
      params: { character_id: 'role-a' },
    })
  })

  it('默认角色 id 为 default', async () => {
    get.mockResolvedValue({ data: { status: 'ok' } } as never)

    await topicStateSnapshot('2:web:8hexdemo')

    expect(get).toHaveBeenCalledWith('/topic-state/2%3Aweb%3A8hexdemo', {
      params: { character_id: 'default' },
    })
  })
})

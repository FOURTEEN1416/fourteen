import { describe, it, expect, vi, beforeEach } from 'vitest'
import client from '../../api/client'
import { humanizeGetConfig, updateHumanizeConfig } from '../../api/system'

// ════════════════════════════════════════════════════════════════
//  拟人度 API 客户端（前端消费面）
//  后端真源（api/routers/training_routes.py:471-497，只读核对）：
//    GET  /api/humanize/config  → {status:'ok', config:{…10 字段…, delay_mode_label}}
//    POST /api/humanize/config  → 同形状；partial merge（只传要改的键）
//  端点落 training_routes（无 prefix、全串路径），故前端不得自行拼 /training 前缀。
// ════════════════════════════════════════════════════════════════

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

const get = vi.mocked(client.get)
const post = vi.mocked(client.post)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('humanizeGetConfig', () => {
  it('GET 全串路径 /humanize/config（无 training 前缀）并原样透传回执', async () => {
    const payload = {
      status: 'ok',
      config: { delay_mode: 'dynamic', per_char_ms: 45, max_segments: 2, delay_mode_label: '逐字(按字数)' },
    }
    get.mockResolvedValue({ data: payload } as never)

    const res = await humanizeGetConfig()

    expect(get).toHaveBeenCalledWith('/humanize/config')
    expect(res.data.status).toBe('ok')
    expect(res.data.config.max_segments).toBe(2)
  })
})

describe('updateHumanizeConfig', () => {
  it('POST partial merge：只带传入键，键名与后端 snake_case 一致', async () => {
    post.mockResolvedValue({ data: { status: 'ok', config: {} } } as never)

    await updateHumanizeConfig({ delay_mode: 'random', random_min_ms: 500, random_max_ms: 900 })

    expect(post).toHaveBeenCalledWith('/humanize/config', {
      delay_mode: 'random',
      random_min_ms: 500,
      random_max_ms: 900,
    })
    // 不得替调用方补 max_segments 等只读键（写侧非 2 → 400）
    const body = post.mock.calls[0][1] as Record<string, unknown>
    expect('max_segments' in body).toBe(false)
  })

  it('空对象（无改动）也不报错：后端空 body 回 ok', async () => {
    post.mockResolvedValue({ data: { status: 'ok', config: {} } } as never)

    const res = await updateHumanizeConfig({})

    expect(post).toHaveBeenCalledWith('/humanize/config', {})
    expect(res.data.status).toBe('ok')
  })
})

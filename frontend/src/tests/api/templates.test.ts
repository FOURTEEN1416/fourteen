import { describe, it, expect, vi, beforeEach } from 'vitest'
import client from '../../api/client'
import { listTemplates, cloneTemplate, isCloneAccepted } from '../../api/templates'

// ════════════════════════════════════════════════════════════════
//  W17 红测：角色模板面（W12 阶段1 后端的前端消费）
//  后端真源（api/routers/character_template_routes.py，只读核对）：
//    GET  /api/character-templates            → {templates:[{id,name,description,tags}], total}
//    POST /api/character-templates/{id}/clone → 201 {id,name,status:"created"}
//    无 Bearer → 401；不存在/非无主/未过策展/开关关 → 统一 404
// ════════════════════════════════════════════════════════════════

vi.mock('../../api/client', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

const get = vi.mocked(client.get)
const post = vi.mocked(client.post)

beforeEach(() => {
  vi.clearAllMocks()
})

describe('listTemplates', () => {
  it('取回策展后的模板列表与总数', async () => {
    get.mockResolvedValue({
      data: { templates: [{ id: '62105bca', name: '林挽夏', description: '…', tags: ['文学'] }], total: 1 },
    } as never)

    const res = await listTemplates()

    expect(get).toHaveBeenCalledWith('/character-templates')
    expect(res.templates[0].name).toBe('林挽夏')
    expect(res.total).toBe(1)
  })

  it('开关关闭时后端返回空清单（前端不把它当错误）', async () => {
    get.mockResolvedValue({ data: { templates: [], total: 0 } } as never)

    const res = await listTemplates()

    expect(res.templates).toEqual([])
  })
})

describe('cloneTemplate', () => {
  it('POST 克隆端点并按新 id 命名空间转义', async () => {
    post.mockResolvedValue({ data: { id: 'abcd1234', name: '林挽夏', status: 'created' } } as never)

    const res = await cloneTemplate('62105bca')

    expect(post).toHaveBeenCalledWith('/character-templates/62105bca/clone')
    expect(res.id).toBe('abcd1234')
  })

  it('成功判据 = 业务回执 created（不是 HTTP 2xx 就算成）', () => {
    expect(isCloneAccepted({ id: 'a', name: 'b', status: 'created' })).toBe(true)
    expect(isCloneAccepted({ id: '', name: 'b', status: 'created' })).toBe(false)
    expect(isCloneAccepted({ id: 'a', name: 'b', status: 'failed' })).toBe(false)
    expect(isCloneAccepted(undefined)).toBe(false)
    expect(isCloneAccepted({ status: 'created' })).toBe(false)
  })
})

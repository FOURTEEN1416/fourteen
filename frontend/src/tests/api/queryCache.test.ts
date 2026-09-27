import { describe, it, expect, beforeEach } from 'vitest'
import { queryClient, clearAccountScopedCache } from '../../api/queryClient'

// ════════════════════════════════════════════════════════════════
//  W11-D3 红测：账号切换必须清空 React Query 缓存，
//  防止 A 登出 → B 登录后旧账号数据闪现（多用户隔离硬约束的客户端面）。
// ════════════════════════════════════════════════════════════════

describe('clearAccountScopedCache', () => {
  beforeEach(() => {
    queryClient.clear()
  })

  it('清空全部查询缓存与 mutation 状态', async () => {
    queryClient.setQueryData(['dashboard'], { messages: 42 })
    queryClient.setQueryData(['emotion', 'state'], { primary: 'happy' })
    expect(queryClient.getQueryData(['dashboard'])).toEqual({ messages: 42 })

    clearAccountScopedCache()

    expect(queryClient.getQueryData(['dashboard'])).toBeUndefined()
    expect(queryClient.getQueryData(['emotion', 'state'])).toBeUndefined()
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0)
  })

  it('幂等：空缓存上重复调用不抛错', () => {
    expect(() => {
      clearAccountScopedCache()
      clearAccountScopedCache()
    }).not.toThrow()
  })
})

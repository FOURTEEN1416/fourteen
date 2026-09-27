import { describe, it, expect, beforeEach } from 'vitest'
import { queryKeys } from '../../hooks/useQueries'
import { useAuthStore, type UserInfo } from '../../store/authStore'

// ════════════════════════════════════════════════════════════════
//  W1：私人查询键必须带**稳定账号维度**（users.id），且不得用 token。
//  旧实现（HEAD a2c… 之前）键形如 ['dashboard'] / ['emotion','state']：
//  A 的迟到响应会命中 B 正在读的同一个键 → 跨账号复写。
// ════════════════════════════════════════════════════════════════

function makeUser(id: number, name: string): UserInfo {
  return {
    id,
    email: `${name}@test.local`,
    username: name,
    display_name: name,
    avatar_url: '',
    role: 'viewer',
    is_active: true,
    is_verified: true,
    created_at: '2026-01-01T00:00:00Z',
    last_login_at: null,
  }
}

describe('queryKeys 账号维度', () => {
  beforeEach(() => {
    useAuthStore.setState({ user: null, isAuthenticated: false })
  })

  it('未登录时私人键落在 anon 维度（不是裸键）', () => {
    expect([...queryKeys.dashboard]).toEqual(['acct', 'anon', 'dashboard'])
    expect([...queryKeys.emotion.state]).toEqual(['acct', 'anon', 'emotion', 'state'])
  })

  it('登录后键携带稳定账号 id（不是 token）', () => {
    useAuthStore.getState().setAuth(makeUser(7, 'alice'), 'tok-super-secret')
    expect([...queryKeys.dashboard]).toEqual(['acct', '7', 'dashboard'])
    expect([...queryKeys.characters.all]).toEqual(['acct', '7', 'characters'])
    expect([...queryKeys.characters.detail('c1')]).toEqual(['acct', '7', 'characters', 'c1'])
    expect([...queryKeys.psych.profile('c1')]).toEqual(['acct', '7', 'psych', 'profile', 'c1'])
    expect([...queryKeys.achievements('c1')]).toEqual(['acct', '7', 'achievements', 'c1'])
  })

  it('token 变化不影响键（同账号键稳定）；换账号键必变', () => {
    useAuthStore.getState().setAuth(makeUser(7, 'alice'), 'tok-1')
    const first = [...queryKeys.dashboard]
    useAuthStore.getState().setAuth(makeUser(7, 'alice'), 'tok-2')
    expect([...queryKeys.dashboard]).toEqual(first)

    useAuthStore.getState().setAuth(makeUser(8, 'bob'), 'tok-3')
    expect([...queryKeys.dashboard]).toEqual(['acct', '8', 'dashboard'])
    expect([...queryKeys.dashboard]).not.toEqual(first)
  })

  it('键里绝不含凭证', () => {
    useAuthStore.getState().setAuth(makeUser(7, 'alice'), 'tok-leak-me')
    const flat = JSON.stringify([
      queryKeys.dashboard,
      queryKeys.memory.facts('personal'),
      queryKeys.wechat.status,
      queryKeys.logs.all({ limit: 10 }),
    ])
    expect(flat).not.toContain('tok-leak-me')
  })

  it('同一账号重复取值结构相等（缓存命中依赖结构相等）', () => {
    useAuthStore.getState().setAuth(makeUser(7, 'alice'), 'tok-1')
    expect([...queryKeys.logs.all({ limit: 10 })]).toEqual([...queryKeys.logs.all({ limit: 10 })])
  })
})

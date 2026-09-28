import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useAuth } from '../../hooks/useAuth'
import { useAuthStore, type UserInfo } from '../../store/authStore'
import { useErrorStore } from '../../store/errorStore'
import { queryClient } from '../../api/queryClient'

// ════════════════════════════════════════════════════════════════
//  W11-D3 红测：logout / 换账号登录 必须清空 React Query 缓存。
//  旧实现只清 token/user/localStorage —— B 账号登录瞬间会闪现 A 的
//  仪表盘/情绪等旧缓存数据（多用户隔离硬约束的客户端面）。
// ════════════════════════════════════════════════════════════════

const { mockLogin, mockLogout, mockRegister, mockRegisterWithInvite } = vi.hoisted(() => ({
  mockLogin: vi.fn(),
  mockLogout: vi.fn(),
  mockRegister: vi.fn(),
  mockRegisterWithInvite: vi.fn(),
}))

vi.mock('../../api/auth', () => ({
  login: (...args: unknown[]) => mockLogin(...args),
  logout: (...args: unknown[]) => mockLogout(...args),
  register: (...args: unknown[]) => mockRegister(...args),
  registerWithInvite: (...args: unknown[]) => mockRegisterWithInvite(...args),
  consent: vi.fn(),
  refreshToken: vi.fn(),
}))

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

const USER_A = makeUser(1, 'alice')
const USER_B = makeUser(2, 'bob')

function tokenResponse(user: UserInfo) {
  return {
    access_token: `tok-${user.id}`,
    refresh_token: `r-${user.id}`,
    token_type: 'bearer',
    user,
    needs_consent: false,
  }
}

describe('useAuth 账号切换与查询缓存', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    queryClient.clear()
    localStorage.clear()
    useAuthStore.setState({
      user: null,
      isAuthenticated: false,
      isInitialized: true,
      needsConsent: false,
    })
    mockLogout.mockResolvedValue(undefined)
    mockLogin.mockResolvedValue(tokenResponse(USER_B))
  })

  it('logout：清认证态 + 清 React Query 缓存（旧缓存不得跨账号存活）', async () => {
    useAuthStore.getState().setAuth(USER_A, 'tok-a')
    queryClient.setQueryData(['dashboard'], { messages: 999 })

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.logout()
    })

    expect(mockLogout).toHaveBeenCalledTimes(1)
    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(queryClient.getQueryData(['dashboard'])).toBeUndefined()
  })

  it('logout API 失败也照样清缓存与本地态', async () => {
    useAuthStore.getState().setAuth(USER_A, 'tok-a')
    queryClient.setQueryData(['dashboard'], { messages: 999 })
    mockLogout.mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.logout()
    })

    expect(useAuthStore.getState().isAuthenticated).toBe(false)
    expect(queryClient.getQueryData(['dashboard'])).toBeUndefined()
  })

  it('A 登出后 B 登录：切换账号时清缓存', async () => {
    useAuthStore.getState().setAuth(USER_A, 'tok-a')
    queryClient.setQueryData(['dashboard'], { messages: 999 })
    mockLogin.mockResolvedValue(tokenResponse(USER_B))

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.login('bob@test.local', 'pw')
    })

    await waitFor(() => {
      expect(useAuthStore.getState().user?.id).toBe(USER_B.id)
    })
    expect(queryClient.getQueryData(['dashboard'])).toBeUndefined()
  })

  it('同一账号重复登录：不清缓存（避免无谓闪烁）', async () => {
    useAuthStore.getState().setAuth(USER_A, 'tok-a')
    queryClient.setQueryData(['dashboard'], { messages: 999 })
    mockLogin.mockResolvedValue(tokenResponse(USER_A))

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.login('alice@test.local', 'pw')
    })

    await waitFor(() => {
      expect(useAuthStore.getState().user?.id).toBe(USER_A.id)
    })
    expect(queryClient.getQueryData(['dashboard'])).toEqual({ messages: 999 })
  })
})

// ════════════════════════════════════════════════════════════════
//  W17：注册成功后的「初始角色」提示。
//  字段由并行窗 W16 在注册响应中新增（initial_character），本窗按
//  **「字段存在才提示」**容错接入：W16 未接线/未返回时必须零提示、零报错，
//  绝不拿默认角色名编造。登录路径不提示（不是"刚为你准备"的语义现场）。
// ════════════════════════════════════════════════════════════════

function toasts() {
  return useErrorStore.getState().toasts.map((t) => `${t.type}:${t.message}`)
}

describe('W17 注册初始角色提示', () => {
  beforeEach(() => {
    useErrorStore.setState({ toasts: [], lastError: null })
  })

  it('注册响应含 initial_character.name → 成功提示「已为你准备初始角色：X」', async () => {
    mockRegister.mockResolvedValue({
      ...tokenResponse(USER_B),
      initial_character: { id: 'c-1', name: '林挽夏' },
    })

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.register({ email: 'bob@test.local', username: 'bob', password: 'pw' })
    })

    expect(toasts().some((t) => t === `success:已为你准备初始角色：林挽夏`)).toBe(true)
  })

  it('注册响应无该字段（W16 未接线）→ 零提示、注册照常落地', async () => {
    mockRegister.mockResolvedValue(tokenResponse(USER_B))

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.register({ email: 'bob@test.local', username: 'bob', password: 'pw' })
    })

    expect(toasts()).toHaveLength(0)
    expect(useAuthStore.getState().user?.id).toBe(USER_B.id)
  })

  it('邀请码注册同样提示（两条注册路径同构）', async () => {
    mockRegisterWithInvite.mockResolvedValue({
      ...tokenResponse(USER_B),
      initial_character: { id: 'c-2', name: '米彩' },
    })

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.registerWithInvite({
        invite_code: 'ABCDEFGH',
        email: 'bob@test.local',
        username: 'bob',
        password: 'pw',
      })
    })

    expect(toasts().some((t) => t.includes('已为你准备初始角色：米彩'))).toBe(true)
  })

  it('登录响应即使带该字段也不提示（只在注册现场说"已为你准备"）', async () => {
    mockLogin.mockResolvedValue({
      ...tokenResponse(USER_B),
      initial_character: { id: 'c-1', name: '林挽夏' },
    })

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.login('bob@test.local', 'pw')
    })

    expect(toasts()).toHaveLength(0)
  })

  it('字段存在但无可用名称（空串/缺 name）→ 不编造提示', async () => {
    mockRegister.mockResolvedValue({
      ...tokenResponse(USER_B),
      initial_character: { id: 'c-1', name: '' },
    })

    const { result } = renderHook(() => useAuth())
    await act(async () => {
      await result.current.register({ email: 'bob@test.local', username: 'bob', password: 'pw' })
    })

    expect(toasts()).toHaveLength(0)
  })
})

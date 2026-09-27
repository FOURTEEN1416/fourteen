import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useAuth } from '../../hooks/useAuth'
import { useAuthStore, type UserInfo } from '../../store/authStore'
import { queryClient } from '../../api/queryClient'

// ════════════════════════════════════════════════════════════════
//  W11-D3 红测：logout / 换账号登录 必须清空 React Query 缓存。
//  旧实现只清 token/user/localStorage —— B 账号登录瞬间会闪现 A 的
//  仪表盘/情绪等旧缓存数据（多用户隔离硬约束的客户端面）。
// ════════════════════════════════════════════════════════════════

const { mockLogin, mockLogout } = vi.hoisted(() => ({
  mockLogin: vi.fn(),
  mockLogout: vi.fn(),
}))

vi.mock('../../api/auth', () => ({
  login: (...args: unknown[]) => mockLogin(...args),
  logout: (...args: unknown[]) => mockLogout(...args),
  register: vi.fn(),
  registerWithInvite: vi.fn(),
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

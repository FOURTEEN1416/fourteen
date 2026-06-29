import { describe, it, expect, beforeEach } from 'vitest'
import { useAuthStore, type UserInfo } from '../../store/authStore'

// 构造一个合法的 UserInfo（UserRole 来自 api/admin）
const mockUser: UserInfo = {
  id: 1,
  email: 'test@example.com',
  username: 'testuser',
  display_name: 'Test User',
  avatar_url: 'https://example.com/avatar.png',
  role: 'viewer',
  is_active: true,
  is_verified: true,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
}

describe('useAuthStore', () => {
  // 每个测试前重置 store
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      isInitialized: false,
    })
    // 清理 localStorage 遗留的 persist 数据
    localStorage.clear()
  })

  it('starts with unauthenticated state', () => {
    const state = useAuthStore.getState()
    expect(state.isAuthenticated).toBe(false)
    expect(state.user).toBeNull()
    expect(state.accessToken).toBeNull()
  })

  it('setAuth transitions to authenticated state', () => {
    const store = useAuthStore.getState()
    store.setAuth(mockUser, 'access-123')

    const state = useAuthStore.getState()
    expect(state.isAuthenticated).toBe(true)
    expect(state.accessToken).toBe('access-123')
    expect(state.user).toEqual(mockUser)
  })

  it('clearAuth resets back to unauthenticated', () => {
    // 先登录
    useAuthStore.getState().setAuth(mockUser, 'access-123')
    expect(useAuthStore.getState().isAuthenticated).toBe(true)

    // 再登出
    useAuthStore.getState().clearAuth()

    const state = useAuthStore.getState()
    expect(state.isAuthenticated).toBe(false)
    expect(state.user).toBeNull()
    expect(state.accessToken).toBeNull()
  })
})

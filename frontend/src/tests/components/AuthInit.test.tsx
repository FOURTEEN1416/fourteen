/**
 * AuthInit 单飞锁回归测试（refresh 竞态根治，2026-09-01）。
 *
 * 病史：StrictMode 双挂载 → 两个并发 POST /auth/refresh → 后端旋转式 session
 * 使旧 cookie 立即失效 → 败者 401 → clearAuth 弹回 /login。
 * 修复：模块级 in-flight 锁，并发只发一次。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import { StrictMode } from 'react'

const { mockRefreshToken, mockGetState, mockSetState, mockSetAuth, mockClearAuth } = vi.hoisted(() => ({
  mockRefreshToken: vi.fn(),
  mockGetState: vi.fn(),
  mockSetState: vi.fn(),
  mockSetAuth: vi.fn(),
  mockClearAuth: vi.fn(),
}))

vi.mock('../../store/authStore', () => ({
  useAuthStore: {
    getState: mockGetState,
    setState: mockSetState,
  },
}))

vi.mock('../../api/auth', () => ({
  refreshToken: mockRefreshToken,
}))

import AuthInit from '../../components/auth/AuthInit'

const FAKE_USER = { id: 1, username: 'tester' }

function renderStrict(ui: React.ReactNode) {
  return render(<StrictMode>{ui}</StrictMode>)
}

describe('AuthInit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('StrictMode 双挂载只发一次 refresh（单飞锁）', async () => {
    mockGetState.mockReturnValue({ user: FAKE_USER, setAuth: mockSetAuth, clearAuth: mockClearAuth })
    mockRefreshToken.mockImplementation(
      () => new Promise(resolve => setTimeout(() => resolve({ user: FAKE_USER, access_token: 'tok', needs_consent: false }), 20)),
    )

    renderStrict(
      <AuthInit>
        <div>child</div>
      </AuthInit>,
    )

    // 等待 in-flight promise 完成
    await new Promise(r => setTimeout(r, 50))
    expect(mockRefreshToken).toHaveBeenCalledTimes(1)
    expect(mockSetAuth).toHaveBeenCalledTimes(1)
    expect(mockClearAuth).not.toHaveBeenCalled()
  })

  it('无用户时不发 refresh，直接标记 initialized', async () => {
    mockGetState.mockReturnValue({ user: null })

    renderStrict(
      <AuthInit>
        <div>child</div>
      </AuthInit>,
    )

    await new Promise(r => setTimeout(r, 20))
    expect(mockRefreshToken).not.toHaveBeenCalled()
    expect(mockSetState).toHaveBeenCalledWith({ isInitialized: true })
  })

  it('refresh 失败时清空认证而非卡死', async () => {
    mockGetState.mockReturnValue({ user: FAKE_USER, setAuth: mockSetAuth, clearAuth: mockClearAuth })
    mockRefreshToken.mockRejectedValue(new Error('revoked'))

    renderStrict(
      <AuthInit>
        <div>child</div>
      </AuthInit>,
    )

    await new Promise(r => setTimeout(r, 30))
    expect(mockClearAuth).toHaveBeenCalled()
    expect(mockSetState).toHaveBeenCalledWith({ isInitialized: true })
  })
})

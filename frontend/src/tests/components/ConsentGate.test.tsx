import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import ConsentGate from '../../components/auth/ConsentGate'

// ── vi.hoisted() 工厂：mock 必须在 vi.mock 之前创建 ──
const {
  mockAgreeConsent,
  mockLogout,
  mockStoreState,
} = vi.hoisted(() => ({
  mockAgreeConsent: vi.fn(),
  mockLogout: vi.fn(),
  // 模拟 useAuthStore() 的返回切片
  mockStoreState: {
    value: { isAuthenticated: true, isInitialized: true, needsConsent: true },
  },
}))

vi.mock('../../store/authStore', () => ({
  useAuthStore: () => mockStoreState.value,
}))

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    agreeConsent: mockAgreeConsent,
    logout: mockLogout,
  }),
}))

describe('ConsentGate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStoreState.value = {
      isAuthenticated: true,
      isInitialized: true,
      needsConsent: true,
    }
  })

  // ──────────────────────────────────────────────
  //  1. needsConsent=false 时不渲染
  // ──────────────────────────────────────────────
  it('renders nothing when consent not needed', () => {
    mockStoreState.value = { isAuthenticated: true, isInitialized: true, needsConsent: false }
    const { container } = render(<ConsentGate />)
    expect(container.innerHTML).toBe('')
  })

  // ──────────────────────────────────────────────
  //  2. 未初始化时不渲染（避免刷新瞬间闪弹窗）
  // ──────────────────────────────────────────────
  it('renders nothing before auth initialized', () => {
    mockStoreState.value = { isAuthenticated: true, isInitialized: false, needsConsent: true }
    const { container } = render(<ConsentGate />)
    expect(container.innerHTML).toBe('')
  })

  // ──────────────────────────────────────────────
  //  3. 未登录时不渲染
  // ──────────────────────────────────────────────
  it('renders nothing when not authenticated', () => {
    mockStoreState.value = { isAuthenticated: false, isInitialized: true, needsConsent: true }
    const { container } = render(<ConsentGate />)
    expect(container.innerHTML).toBe('')
  })

  // ──────────────────────────────────────────────
  //  4. 需要同意时全屏渲染协议文本与版本号
  // ──────────────────────────────────────────────
  it('renders agreement text and version when consent needed', () => {
    render(<ConsentGate />)

    expect(screen.getByTestId('consent-gate')).toBeDefined()
    expect(screen.getByTestId('agreement-body')).toBeDefined()
    // 标题 + 版本
    expect(screen.getByText('用户协议与隐私声明')).toBeDefined()
    expect(screen.getByText(/v1\.0\.0/)).toBeDefined()
    // 正文关键条款抽查：使用即同意 + 心理热线
    expect(screen.getByText(/使用即同意/)).toBeDefined()
    expect(screen.getByText(/12356/)).toBeDefined()
    // 操作按钮
    expect(screen.getByRole('button', { name: '我已阅读并同意' })).toBeDefined()
    expect(screen.getByRole('button', { name: '不同意并退出登录' })).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  5. 点击同意 → 调 agreeConsent → 成功后弹窗消失
  // ──────────────────────────────────────────────
  it('calls agreeConsent and hides gate on agree', async () => {
    mockAgreeConsent.mockImplementation(async () => {
      // 模拟 store 更新：同意后 needsConsent=false
      mockStoreState.value = { isAuthenticated: true, isInitialized: true, needsConsent: false }
    })
    const { rerender } = render(<ConsentGate />)

    fireEvent.click(screen.getByRole('button', { name: '我已阅读并同意' }))

    await waitFor(() => {
      expect(mockAgreeConsent).toHaveBeenCalledTimes(1)
    })

    // store 更新后重渲染 → 弹窗消失
    rerender(<ConsentGate />)
    expect(screen.queryByTestId('consent-gate')).toBeNull()
  })

  // ──────────────────────────────────────────────
  //  6. 同意失败 → 显示错误提示，弹窗保留
  // ──────────────────────────────────────────────
  it('shows error and keeps gate when agree fails', async () => {
    mockAgreeConsent.mockRejectedValueOnce(new Error('network'))
    render(<ConsentGate />)

    fireEvent.click(screen.getByRole('button', { name: '我已阅读并同意' }))

    expect(await screen.findByText('提交失败，请重试')).toBeDefined()
    // 弹窗仍在
    expect(screen.getByTestId('consent-gate')).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  7. 点击不同意 → 退出登录
  // ──────────────────────────────────────────────
  it('calls logout on disagree', () => {
    render(<ConsentGate />)

    fireEvent.click(screen.getByRole('button', { name: '不同意并退出登录' }))

    expect(mockLogout).toHaveBeenCalledTimes(1)
    expect(mockAgreeConsent).not.toHaveBeenCalled()
  })
})

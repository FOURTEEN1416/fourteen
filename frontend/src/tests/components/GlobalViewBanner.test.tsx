import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import GlobalViewBanner from '../../components/admin/GlobalViewBanner'
import { useAuthStore } from '../../store/authStore'
import { useGlobalViewStore } from '../../store/globalViewStore'

// V2-1（2026-10-29）：全局运维视图常驻横幅——开启时提示越权视角 + 一键关闭。
const { mockSetGlobalView, mockGetGlobalView } = vi.hoisted(() => ({
  mockSetGlobalView: vi.fn(),
  mockGetGlobalView: vi.fn(),
}))

vi.mock('../../api/admin', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/admin')>()),
  getGlobalView: (...args: unknown[]) => mockGetGlobalView(...args),
  setGlobalView: (...args: unknown[]) => mockSetGlobalView(...args),
}))

const ADMIN_USER = {
  id: 1, email: 'a@t', username: 'admin', display_name: 'Admin',
  role: 'admin' as const, is_active: true, is_verified: true,
}

describe('GlobalViewBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAuthStore.setState({ user: null } as never)
    useGlobalViewStore.setState({ enabled: false, loaded: false, loading: false })
  })

  it('非 admin 恒不渲染（即使开关误开）', () => {
    useGlobalViewStore.setState({ enabled: true, loaded: true })
    render(<GlobalViewBanner />)
    expect(screen.queryByText(/全局运维视图已开启/)).toBeNull()
  })

  it('admin + 开启：渲染横幅并自动拉取开关状态', async () => {
    useAuthStore.setState({ user: ADMIN_USER } as never)
    mockGetGlobalView.mockResolvedValue({ enabled: true })
    useGlobalViewStore.setState({ enabled: false, loaded: false })
    render(<GlobalViewBanner />)
    await waitFor(() => expect(mockGetGlobalView).toHaveBeenCalled())
    expect(await screen.findByText(/全局运维视图已开启/)).toBeDefined()
  })

  it('admin + 关闭：不渲染', () => {
    useAuthStore.setState({ user: ADMIN_USER } as never)
    useGlobalViewStore.setState({ enabled: false, loaded: true })
    render(<GlobalViewBanner />)
    expect(screen.queryByText(/全局运维视图已开启/)).toBeNull()
  })

  it('横幅「关闭全局视图」按钮调用 setGlobalView(false) 并更新 store', async () => {
    useAuthStore.setState({ user: ADMIN_USER } as never)
    mockSetGlobalView.mockResolvedValue({ status: 'ok', enabled: false })
    useGlobalViewStore.setState({ enabled: true, loaded: true })
    render(<GlobalViewBanner />)
    fireEvent.click(screen.getByText('关闭全局视图'))
    await waitFor(() => expect(mockSetGlobalView).toHaveBeenCalledWith(false))
    await waitFor(() => expect(useGlobalViewStore.getState().enabled).toBe(false))
  })
})

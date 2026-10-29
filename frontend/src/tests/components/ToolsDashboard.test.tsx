import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import ToolsDashboard from '../../pages/ToolsDashboard'
import { useAuthStore } from '../../store/authStore'
import { useErrorStore } from '../../store/errorStore'

const { mockTools, mockToolsHealth, mockToggleTool } = vi.hoisted(() => ({
  mockTools: vi.fn(),
  mockToolsHealth: vi.fn(),
  mockToggleTool: vi.fn(),
}))

vi.mock('../../api/system', () => ({
  tools: mockTools,
  toolsHealth: mockToolsHealth,
  toggleTool: mockToggleTool,
}))

function setRole(role: 'admin' | 'viewer' | null) {
  if (role === null) {
    useAuthStore.setState({ user: null, isAuthenticated: false })
    return
  }
  useAuthStore.setState({
    user: {
      id: role === 'admin' ? 1 : 2,
      email: `${role}@test.local`,
      username: role,
      display_name: role,
      avatar_url: '',
      role,
      is_active: true,
      is_verified: true,
      created_at: '2026-01-01T00:00:00Z',
      last_login_at: null,
    },
    isAuthenticated: true,
  })
}

function renderDashboard() {
  return render(<ToolsDashboard />)
}

describe('ToolsDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useErrorStore.setState({ toasts: [] })
    // 2026-10-29 NEW-1：启停开关为 admin-only（后端 require_role("admin")），
    // 既有用例以管理员身份验证开关行为。
    setRole('admin')
    mockTools.mockResolvedValue({
      data: {
        tools: ['weather', 'search'],
        inventory: [
          { name: 'weather', status: 'enabled', reason: '' },
          { name: 'search', status: 'enabled', reason: '' },
          { name: 'image_gen', status: 'unavailable', reason: '图片生成服务未配置' },
          { name: 'scheduler', status: 'disabled', reason: '运行时开关禁用，可重新启用' },
        ],
      },
    })
    mockToolsHealth.mockResolvedValue({
      data: {
        available: true,
        total: 9,
        online: 8,
        tools: {
          weather: { available: true, error: '' },
          search: { available: true, error: '' },
          image_gen: {
            available: false,
            error: '图片生成服务未配置',
            config_hint: '设置 IMAGE_GEN_PROVIDER 环境变量',
          },
        },
      },
    })
    mockToggleTool.mockResolvedValue({ data: { status: 'ok' } })
  })

  it('renders loading state then unified inventory', async () => {
    renderDashboard()
    expect(await screen.findByRole('status')).toBeDefined()
    expect(await screen.findByText('weather')).toBeDefined()
    expect(screen.getByText('search')).toBeDefined()
    // 禁用与不可用的工具同样可见（禁用后刷新不丢入口）
    expect(screen.getByText('scheduler')).toBeDefined()
    expect(screen.getByText('image_gen')).toBeDefined()
  })

  it('shows real availability from health endpoint', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('8/9 可用')).toBeDefined()
    })
  })

  it('displays error and config hint for unavailable tools', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('图片生成服务未配置')).toBeDefined()
    })
    expect(screen.getByText('设置 IMAGE_GEN_PROVIDER 环境变量')).toBeDefined()
  })

  it('renders disabled badge and reason for disabled tools', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('已禁用')).toBeDefined()
    })
    expect(screen.getByText('运行时开关禁用，可重新启用')).toBeDefined()
  })

  it('toggles a tool and updates local state', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('weather')).toBeDefined()
    })
    const weatherToggle = screen.getByLabelText('禁用 weather')
    fireEvent.click(weatherToggle)
    await waitFor(() => {
      expect(mockToggleTool).toHaveBeenCalledWith('weather', false)
    })
  })

  it('offers re-enable toggle for disabled tools', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('scheduler')).toBeDefined()
    })
    const schedulerToggle = screen.getByLabelText('启用 scheduler')
    fireEvent.click(schedulerToggle)
    await waitFor(() => {
      expect(mockToggleTool).toHaveBeenCalledWith('scheduler', true)
    })
  })

  // ── NEW-1（2026-10-29）：全局作用域横幅 + 非 admin 只读 + 失败 toast ──

  it('shows global-scope banner for admins', async () => {
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('全局作用域')).toBeDefined()
    })
    expect(screen.getByText(/当前具备管理员权限/)).toBeDefined()
  })

  it('renders read-only view for non-admin users (no toggle hits 403)', async () => {
    setRole('viewer')
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('weather')).toBeDefined()
    })
    // 开关不渲染（后端 require_role("admin") 必 403），改为只读标记
    expect(screen.queryByLabelText('禁用 weather')).toBeNull()
    expect(screen.getAllByText('只读').length).toBeGreaterThan(0)
    expect(screen.getByText(/当前账号为只读视图/)).toBeDefined()
    // 工具可用性信息仍然可见（不收信息）
    expect(screen.getByText('search')).toBeDefined()
  })

  it('shows error toast when toggle fails (NEW-1: 不再静默弹回)', async () => {
    mockToggleTool.mockRejectedValueOnce(new Error('403'))
    renderDashboard()
    await waitFor(() => {
      expect(screen.getByText('weather')).toBeDefined()
    })
    fireEvent.click(screen.getByLabelText('禁用 weather'))
    await waitFor(() => {
      const toasts = useErrorStore.getState().toasts
      expect(toasts.some((t) => t.type === 'error' && t.message.includes('未保存'))).toBe(true)
    })
  })
})

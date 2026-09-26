import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import ToolsDashboard from '../../pages/ToolsDashboard'

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

function renderDashboard() {
  return render(<ToolsDashboard />)
}

describe('ToolsDashboard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
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
})

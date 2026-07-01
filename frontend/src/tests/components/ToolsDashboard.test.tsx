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
    mockTools.mockResolvedValue({ data: { tools: ['weather', 'search'] } })
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

  it('renders loading state then tool list', async () => {
    renderDashboard()
    expect(await screen.findByRole('status')).toBeDefined()
    expect(await screen.findByText('weather')).toBeDefined()
    expect(screen.getByText('search')).toBeDefined()
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
})

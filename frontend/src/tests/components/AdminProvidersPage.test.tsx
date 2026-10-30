import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import AdminProvidersPage from '../../pages/AdminProvidersPage'
import { useAuthStore } from '../../store/authStore'
import { useErrorStore } from '../../store/errorStore'

// ════════════════════════════════════════════════════════════════
//  NEW-20①：供应商「禁用」是危险方向（改变全体用户可用模型与回退路径），
//  先确认；「启用」保持一键直生效。页头补默认/回退链徽章序列。
//  断言只到「是否出现确认框 / 文本 / 请求是否发出」，
//  不断言确认钮内部结构与 class（按钮收编归 NEW-31）。
// ════════════════════════════════════════════════════════════════

const { mockListAllProviders, mockToggleProvider } = vi.hoisted(() => ({
  mockListAllProviders: vi.fn(),
  mockToggleProvider: vi.fn(),
}))

vi.mock('../../api/llmProviders', () => ({
  listAllProviders: (...args: unknown[]) => mockListAllProviders(...args),
  toggleProvider: (...args: unknown[]) => mockToggleProvider(...args),
  createProvider: vi.fn(),
  updateProvider: vi.fn(),
  deleteProvider: vi.fn(),
}))

const EMPTY_GUIDE = { apply_url: '', free_quota: '', steps: [], tips: [], warnings: [] }

const PROVIDERS = [
  {
    key: 'agnes', name: 'Agnes', description: '主力供应商', sort_order: 1,
    guide: EMPTY_GUIDE, is_special: false, is_preset: true, enabled: true,
  },
  {
    key: 'glm', name: '智谱', description: '备选供应商', sort_order: 2,
    guide: EMPTY_GUIDE, is_special: false, is_preset: true, enabled: true,
  },
  {
    key: 'deepseek', name: 'DeepSeek', description: '已停用的自定义供应商', sort_order: 3,
    guide: EMPTY_GUIDE, is_special: false, is_preset: false, enabled: false,
  },
  {
    key: 'auto', name: '自动选择', description: '内置自动选择', sort_order: 0,
    guide: EMPTY_GUIDE, is_special: true, is_preset: true, enabled: true,
  },
]

function makeAdmin() {
  useAuthStore.setState({
    user: {
      id: 1,
      email: 'admin@test.local',
      username: 'admin',
      display_name: 'admin',
      avatar_url: '',
      role: 'admin',
      is_active: true,
      is_verified: true,
      created_at: '2026-01-01T00:00:00Z',
      last_login_at: null,
    },
    isAuthenticated: true,
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  useErrorStore.setState({ toasts: [], lastError: null })
  makeAdmin()
  mockListAllProviders.mockResolvedValue({
    providers: PROVIDERS,
    default_provider: 'agnes',
    fallback_chain: ['glm'],
  })
  mockToggleProvider.mockResolvedValue({ detail: 'ok', enabled: true })
})

describe('AdminProvidersPage — 供应商启停二次确认', () => {
  it('禁用：先弹确认（含后果说明），未确认不发请求，取消也不发', async () => {
    render(<AdminProvidersPage />)
    await screen.findByText('Agnes')

    // 第一行是启用的 agnes → 按钮文案「禁用」
    fireEvent.click(screen.getAllByRole('button', { name: '禁用' })[0])

    expect(screen.getByText('确认禁用供应商')).toBeDefined()
    expect(screen.getByText(/所有用户可用模型与回退路径/)).toBeDefined()
    expect(mockToggleProvider).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(screen.queryByText('确认禁用供应商')).toBeNull()
    expect(mockToggleProvider).not.toHaveBeenCalled()
  })

  it('禁用：确认后才调用 toggleProvider(key,false)', async () => {
    render(<AdminProvidersPage />)
    await screen.findByText('Agnes')

    fireEvent.click(screen.getAllByRole('button', { name: '禁用' })[0])
    fireEvent.click(screen.getByRole('button', { name: '确认禁用' }))

    await waitFor(() => {
      expect(mockToggleProvider).toHaveBeenCalledWith('agnes', false)
    })
    expect(screen.queryByText('确认禁用供应商')).toBeNull()
  })

  it('确认文案点明位置：默认供应商 / 回退链第 N 位', async () => {
    render(<AdminProvidersPage />)
    await screen.findByText('Agnes')

    // agnes = default_provider
    fireEvent.click(screen.getAllByRole('button', { name: '禁用' })[0])
    expect(screen.getByText(/当前默认供应商/)).toBeDefined()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    // glm 在 fallback_chain 第 1 位
    fireEvent.click(screen.getAllByRole('button', { name: '禁用' })[1])
    expect(screen.getByText(/回退链第 1 位/)).toBeDefined()
  })

  it('启用：不弹任何确认框，直接调用 toggleProvider(key,true)', async () => {
    render(<AdminProvidersPage />)
    await screen.findByText('DeepSeek')

    fireEvent.click(screen.getByRole('button', { name: '启用' }))

    expect(screen.queryByText('确认禁用供应商')).toBeNull()
    await waitFor(() => {
      expect(mockToggleProvider).toHaveBeenCalledWith('deepseek', true)
    })
  })
})

describe('AdminProvidersPage — 页头模型路径徽章序列', () => {
  it('默认在清单内：渲染「默认：名称」+ 回退链各档位', async () => {
    render(<AdminProvidersPage />)

    expect(await screen.findByText('默认：Agnes')).toBeDefined()
    expect(screen.getByText('第 1 位')).toBeDefined()
    // 链上名称同时出现在徽章与供应商行（Agnes 行 + 智谱行 + 链徽章）
    expect(screen.getAllByText('智谱')).toHaveLength(2)
  })

  it('default_provider 不在清单（auto）：降级文案，不出现 undefined / 空徽章', async () => {
    mockListAllProviders.mockResolvedValue({
      providers: PROVIDERS.filter((p) => p.key !== 'auto'),
      default_provider: 'auto',
      fallback_chain: [],
    })
    render(<AdminProvidersPage />)

    expect(await screen.findByText('默认：auto（内置自动选择）')).toBeDefined()
    expect(screen.getByText('未配置回退链')).toBeDefined()
    expect(document.body.textContent).not.toContain('undefined')
  })
})

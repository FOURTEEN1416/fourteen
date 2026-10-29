import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import SettingsLLM from '../../pages/SettingsLLM'

// ── hoisted mock fns ──
const { mockFetchConfig, mockSaveConfig, mockTestLlmConfig } = vi.hoisted(() => ({
  mockFetchConfig: vi.fn(),
  mockSaveConfig: vi.fn(),
  mockTestLlmConfig: vi.fn(),
}))

const { mockListProviders } = vi.hoisted(() => ({
  mockListProviders: vi.fn(),
}))

const { mockUseAuthStore } = vi.hoisted(() => ({
  mockUseAuthStore: vi.fn(),
}))

// 部分模拟:保留 system.ts 全部导出(client.ts 会 re-export),只覆盖 config/saveConfig
vi.mock('../../api/system', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/system')>()
  return {
    ...actual,
    config: (...args: unknown[]) => mockFetchConfig(...args),
    saveConfig: (...args: unknown[]) => mockSaveConfig(...args),
    userLlmConfig: vi.fn(() => Promise.resolve({ data: { llm: {} } })),
    saveUserLlmConfig: vi.fn(() => Promise.resolve({ data: { llm: {} } })),
    testUserLlmConfig: (...args: unknown[]) => mockTestLlmConfig(...args),
  }
})

vi.mock('../../api/llmProviders', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/llmProviders')>()
  return {
    ...actual,
    listProviders: (...args: unknown[]) => mockListProviders(...args),
  }
})

vi.mock('../../store/authStore', () => ({
  useAuthStore: mockUseAuthStore,
}))

// ── fixtures ──
const DEFAULT_CONFIG = {
  data: {
    llm: {
      provider: 'deepseek',
      model: 'deepseek-chat',
      api_key: '****',
      api_base: 'https://api.deepseek.com/v1',
      temperature: 0.7,
      max_tokens: 4096,
      cache: {
        enabled: true,
        ttl: 3600,
      },
    },
  },
}

const PROVIDERS_FIXTURE = {
  providers: [
    {
      key: 'auto',
      name: '自动回退(推荐)',
      description: '按优先级依次尝试可用供应商',
      sort_order: 0,
      is_special: true,
      is_preset: true,
      guide: { apply_url: '', free_quota: '', steps: [], tips: [], warnings: [] },
    },
    {
      key: 'deepseek',
      name: 'DeepSeek',
      description: 'DeepSeek 平台',
      sort_order: 1,
      is_special: false,
      is_preset: true,
      model: 'deepseek-chat',
      api_base: 'https://api.deepseek.com/v1',
      guide: { apply_url: 'https://platform.deepseek.com', free_quota: '送 5000 万 token', steps: [], tips: [], warnings: [] },
    },
    {
      key: 'deadprov',
      name: '已停用供应商',
      description: '用于禁用态测试',
      sort_order: 2,
      is_special: false,
      enabled: false,
      guide: { apply_url: '', free_quota: '', steps: [], tips: [], warnings: [] },
    },
  ],
  default_provider: 'auto',
}

describe('SettingsLLM', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 默认 admin 用户,走 fetchConfig/saveConfig 路径
    mockUseAuthStore.mockImplementation((selector: (s: { user: { role: string } | null }) => unknown) =>
      selector({ user: { role: 'admin' } }),
    )
    mockListProviders.mockResolvedValue(PROVIDERS_FIXTURE)
  })

  it('shows loading state initially', () => {
    // keep loading by never resolving
    mockFetchConfig.mockReturnValue(new Promise(() => {}))
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )
    expect(screen.getByRole('status')).toBeDefined()
  })

  it('populates form fields from config API response', async () => {
    mockFetchConfig.mockResolvedValue(DEFAULT_CONFIG)
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )

    // wait for form to appear (save button = form loaded)
    await waitFor(() => {
      expect(screen.getByText('保存设置')).toBeDefined()
    })

    // provider radio selected
    const deepseekRadio = screen.getByRole('radio', { name: /deepseek/i }) as HTMLInputElement
    expect(deepseekRadio.checked).toBe(true)

    // API base input
    const apiBaseInput = screen.getByPlaceholderText('https://api.deepseek.com/v1') as HTMLInputElement
    expect(apiBaseInput.value).toBe('https://api.deepseek.com/v1')

    // API Key input（NEW-13④：'****' 回显 → 「已保存密钥」徽标 + 更换式 placeholder，明文不回显）
    const apiKeyInput = screen.getByPlaceholderText('已保存（输入新值可更换）') as HTMLInputElement
    expect(apiKeyInput.value).toBe('')
    expect(screen.getByTestId('key-saved-badge')).toBeDefined()

    // Model input
    const modelInput = screen.getByPlaceholderText('deepseek-chat') as HTMLInputElement
    expect(modelInput.value).toBe('deepseek-chat')

    // Temperature (default 0.85 from component if not loaded, but our fixture sets 0.7)
    const tempSlider = screen.getByRole('slider') as HTMLInputElement
    expect(tempSlider.value).toBe('0.7')

    // Max tokens (default 2048, fixture 4096)
    const maxTokensInput = screen.getByDisplayValue('4096') as HTMLInputElement
    expect(maxTokensInput).toBeDefined()

    // Cache toggle checked
    const cacheToggle = screen.getByRole('switch') as HTMLButtonElement
    expect(cacheToggle.getAttribute('aria-checked')).toBe('true')

    // Cache duration: fixture ttl=3600 → cacheDuration=60 分钟
    const cacheDurationInput = screen.getByDisplayValue('60') as HTMLInputElement
    expect(cacheDurationInput).toBeDefined()
  })

  it('clicking save calls saveConfig with correct payload', async () => {
    mockFetchConfig.mockResolvedValue(DEFAULT_CONFIG)
    mockSaveConfig.mockResolvedValue({})
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('保存设置')).toBeDefined()
    })

    // Click save
    const saveBtn = screen.getByText('保存设置')
    fireEvent.click(saveBtn)

    await waitFor(() => {
      expect(mockSaveConfig).toHaveBeenCalledWith({
        llm: {
          provider: 'deepseek',
          model: 'deepseek-chat',
          primary_model: 'deepseek-chat',
          api_base: 'https://api.deepseek.com/v1',
          temperature: 0.7,
          max_tokens: 4096,
          cache: {
            enabled: true,
            ttl: 3600,
          },
        },
      })
    })
  })

  it('shows success message after save', async () => {
    mockFetchConfig.mockResolvedValue(DEFAULT_CONFIG)
    mockSaveConfig.mockResolvedValue({})
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('保存设置')).toBeDefined()
    })

    fireEvent.click(screen.getByText('保存设置'))

    await waitFor(() => {
      expect(screen.getByText('设置已保存')).toBeDefined()
    })
  })

  it('handles error from fetch gracefully', async () => {
    mockFetchConfig.mockRejectedValue(new Error('Network Error'))
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )

    // after loading, error should appear
    await waitFor(() => {
      expect(screen.getByText('Network Error')).toBeDefined()
    })
  })

  it('cache panel shows not_integrated notice (W10)', async () => {
    mockFetchConfig.mockResolvedValue(DEFAULT_CONFIG)
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )

    // 缓存未接入对话链路：面板必须明示 not_integrated，不宣称省 token
    await waitFor(() => {
      expect(screen.getByTestId('cache-not-integrated')).toBeDefined()
    })
    expect(screen.getByTestId('cache-not-integrated').textContent).toContain('not_integrated')
  })

  it('cache toggle works (llm_cache.enabled)', async () => {
    mockFetchConfig.mockResolvedValue(DEFAULT_CONFIG)
    mockSaveConfig.mockResolvedValue({})
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('保存设置')).toBeDefined()
    })

    const cacheToggle = screen.getByRole('switch') as HTMLButtonElement
    expect(cacheToggle.getAttribute('aria-checked')).toBe('true')

    // Click toggle to disable cache
    fireEvent.click(cacheToggle)
    expect(cacheToggle.getAttribute('aria-checked')).toBe('false')

    // Save and verify payload
    fireEvent.click(screen.getByText('保存设置'))

    await waitFor(() => {
      expect(mockSaveConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          llm: expect.objectContaining({
            cache: expect.objectContaining({ enabled: false, ttl: 3600 }),
          }),
        }),
      )
    })
  })

  it('handles error from save gracefully (silent, no crash)', async () => {
    mockFetchConfig.mockResolvedValue(DEFAULT_CONFIG)
    mockSaveConfig.mockRejectedValue(new Error('Save failed'))
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )

    await waitFor(() => {
      expect(screen.getByText('保存设置')).toBeDefined()
    })

    // Click save — should not crash
    fireEvent.click(screen.getByText('保存设置'))

    await waitFor(() => {
      expect(screen.getByText('Save failed')).toBeDefined()
    })
  })
})


// ── NEW-13（2026-10-29）：配置反馈环——测试连接 / byok 跳转提示 / 禁用供应商拦截 ──
describe('SettingsLLM NEW-13 反馈环', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetchConfig.mockResolvedValue(DEFAULT_CONFIG)
    mockListProviders.mockResolvedValue(PROVIDERS_FIXTURE)
    mockUseAuthStore.mockImplementation((selector: (s: { user: { role: string } | null }) => unknown) =>
      selector({ user: { role: 'admin' } }))
  })

  it('测试连接成功：渲染延迟与回包预览', async () => {
    mockTestLlmConfig.mockResolvedValue({ ok: true, latency_ms: 812, preview: 'pong' })
    render(
      <MemoryRouter initialEntries={['/settings/llm']}>
        <Routes>
          <Route path="/settings/llm" element={<SettingsLLM />} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('保存设置')).toBeDefined())
    fireEvent.click(screen.getByText('测试连接'))
    expect(await screen.findByTestId('llm-test-result')).toBeDefined()
    expect(screen.getByTestId('llm-test-result').textContent).toContain('连接成功')
    expect(screen.getByTestId('llm-test-result').textContent).toContain('812')
  })

  it('测试连接失败：就地回显错误（不再延迟到聊天才爆）', async () => {
    mockTestLlmConfig.mockResolvedValue({ ok: false, error: '配置校验失败：API Key 缺失' })
    render(
      <MemoryRouter initialEntries={['/settings/llm']}>
        <Routes>
          <Route path="/settings/llm" element={<SettingsLLM />} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('保存设置')).toBeDefined())
    fireEvent.click(screen.getByText('测试连接'))
    expect(await screen.findByTestId('llm-test-result')).toBeDefined()
    expect(screen.getByTestId('llm-test-result').textContent).toContain('API Key 缺失')
  })

  it('?byok=1 跳转参数渲染配置引导（②）', async () => {
    render(
      <MemoryRouter initialEntries={['/settings/llm?byok=1']}>
        <Routes>
          <Route path="/settings/llm" element={<SettingsLLM />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByTestId('byok-redirect-hint')).toBeDefined()
  })

  it('已禁用供应商保存被拦截（⑤）', async () => {
    mockFetchConfig.mockResolvedValue({
      data: { llm: { ...DEFAULT_CONFIG.data.llm, provider: 'deadprov' } },
    })
    render(
      <MemoryRouter initialEntries={['/settings/llm']}>
        <Routes>
          <Route path="/settings/llm" element={<SettingsLLM />} />
        </Routes>
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('保存设置')).toBeDefined())
    fireEvent.click(screen.getByText('保存设置'))
    await waitFor(() => {
      expect(screen.getByText(/已被禁用/)).toBeDefined()
    })
    expect(mockSaveConfig).not.toHaveBeenCalled()
  })
})

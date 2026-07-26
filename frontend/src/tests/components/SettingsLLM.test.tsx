import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SettingsLLM from '../../pages/SettingsLLM'

// ── hoisted mock fns ──
const { mockFetchConfig, mockSaveConfig } = vi.hoisted(() => ({
  mockFetchConfig: vi.fn(),
  mockSaveConfig: vi.fn(),
}))

vi.mock('../../api/system', () => ({
  config: (...args: unknown[]) => mockFetchConfig(...args),
  saveConfig: (...args: unknown[]) => mockSaveConfig(...args),
}))

// ── fixture ──
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

describe('SettingsLLM', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows loading state initially', () => {
    // keep loading by never resolving
    mockFetchConfig.mockReturnValue(new Promise(() => {}))
    render(
      <MemoryRouter>
        <SettingsLLM />
      </MemoryRouter>,
    )
    expect(screen.getByText('加载配置中...')).toBeDefined()
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

    // API Key input (type=password, but value still rendered)
    const apiKeyInput = screen.getByPlaceholderText('sk-...') as HTMLInputElement
    expect(apiKeyInput.value).toBe('')

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

    // Cache duration (default 30, fixture 60)
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

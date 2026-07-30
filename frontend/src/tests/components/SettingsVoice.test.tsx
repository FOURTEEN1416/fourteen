import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SettingsVoice from '../../pages/SettingsVoice'

// ── hoisted mock fns ──
const { mockMimoClone, mockMimoDesign, mockMimoSynthesize, mockMimoSetEngine, mockMimoSwitchVoice, mockMimoStatus } = vi.hoisted(
  () => ({
    mockMimoClone: vi.fn(),
    mockMimoDesign: vi.fn(),
    mockMimoSynthesize: vi.fn(),
    mockMimoSetEngine: vi.fn(),
    mockMimoSwitchVoice: vi.fn(),
    mockMimoStatus: vi.fn(),
  }),
)

const { mockGetSpeakers } = vi.hoisted(() => ({
  mockGetSpeakers: vi.fn(),
}))

vi.mock('../../api/mimo', () => ({
  mimoClone: (...args: unknown[]) => mockMimoClone(...args),
  mimoDesign: (...args: unknown[]) => mockMimoDesign(...args),
  mimoSynthesize: (...args: unknown[]) => mockMimoSynthesize(...args),
  mimoSetEngine: (...args: unknown[]) => mockMimoSetEngine(...args),
  mimoSwitchVoice: (...args: unknown[]) => mockMimoSwitchVoice(...args),
  mimoStatus: (...args: unknown[]) => mockMimoStatus(...args),
}))

vi.mock('../../api/system', () => ({
  getSpeakers: (...args: unknown[]) => mockGetSpeakers(...args),
}))

// ── fixtures ──

const FAKE_VOICES = {
  data: {
    voices: [
      { name: 'xiaoyu', gender: 'female', style: 'gentle', language: 'zh', sample_url: 'http://example.com/sample.mp3' },
      { name: 'haoxue', gender: 'female', style: 'lively', language: 'zh', sample_url: '' },
    ],
  },
}

describe('SettingsVoice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Default: resolve voice calls so component can load
    mockGetSpeakers.mockResolvedValue(FAKE_VOICES)
    mockMimoStatus.mockResolvedValue({ data: { enabled: true } })
    mockMimoClone.mockResolvedValue({ data: { status: 'ok' } })
    mockMimoDesign.mockResolvedValue({ data: { status: 'ok' } })
    mockMimoSynthesize.mockResolvedValue({ data: { status: 'ok' } })
    mockMimoSetEngine.mockResolvedValue({ data: { status: 'ok' } })
    mockMimoSwitchVoice.mockResolvedValue({ data: { status: 'ok' } })
  })

  // ── Test 1: Engine section ──

  it('renders 语音引擎 section with engine selector', async () => {
    render(<SettingsVoice />)

    // 等待挂载时的异步数据拉取完成，避免状态更新在 test 结束后触发 act() 警告
    await waitFor(() => {
      expect(mockGetSpeakers).toHaveBeenCalled()
    })

    // Engine section heading
    expect(screen.getByText('语音引擎')).toBeDefined()
    // The engine switcher shows the default engine button
    expect(screen.getByRole('button', { name: 'MiMo Cloud' })).toBeDefined()
  })

  // ── Test 2: Engine options ──
  // 当前 ENGINE_OPTIONS 仅保留 MiMo Cloud(Edge-TTS/GPT-SoVITS/Bert-VITS2 已从 UI 移除,
  // 后端多引擎 fallback 仍由 llm_provider 处理,见 AGENTS.md L6)

  it('renders MiMo Cloud engine option', async () => {
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(mockGetSpeakers).toHaveBeenCalled()
    })

    expect(screen.getByRole('button', { name: 'MiMo Cloud' })).toBeDefined()
  })

  // ── Test 3: Clone section shows file upload ──

  it('clone section shows file upload input', async () => {
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(mockGetSpeakers).toHaveBeenCalled()
    })

    // Voice clone section heading
    expect(screen.getByText('语音克隆')).toBeDefined()

    // Text input for voice name
    const nameInput = screen.getByPlaceholderText('自定义语音名称')
    expect(nameInput).toBeDefined()

    // File upload label
    expect(screen.getByText('上传参考音频')).toBeDefined()

    // Clone button
    expect(screen.getByText('开始克隆')).toBeDefined()
  })

  // ── Test 4: Design section shows gender/style form ──

  it('design section shows gender and style form', async () => {
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(mockGetSpeakers).toHaveBeenCalled()
    })

    // Voice design section heading
    expect(screen.getByText('语音设计')).toBeDefined()

    // Gender selector
    expect(screen.getByText('性别')).toBeDefined()
    // Style selector
    expect(screen.getByText('风格')).toBeDefined()

    // Apply design button
    expect(screen.getByText('应用设计')).toBeDefined()
  })

  // ── Test 5: Select engine calls mimoSetEngine ──
  // 已删除:ENGINE_OPTIONS 仅保留 MiMo Cloud,无其他引擎可切换。
  // 后端多引擎 fallback 由 llm_provider 自动处理,前端 UI 不再暴露引擎切换。

  // ── Test 6: Click 试听 calls mimoSynthesize ──

  it('click 试听 button calls mimoSynthesize', async () => {
    render(<SettingsVoice />)

    // Wait for initial load
    await waitFor(() => {
      expect(mockGetSpeakers).toHaveBeenCalled()
    })

    // Find the synthesize section
    expect(screen.getByText('语音合成测试')).toBeDefined()

    // Type text to synthesize
    const textarea = screen.getByPlaceholderText('输入要合成的文本...')
    fireEvent.change(textarea, { target: { value: '你好世界' } })

    // Click the 试听 button
    const playBtn = screen.getByText('试听')
    expect(playBtn).toBeDefined()
    fireEvent.click(playBtn)

    await waitFor(() => {
      expect(mockMimoSynthesize).toHaveBeenCalledWith('你好世界')
    })
  })

  // ── Test 7: Shows loading state during async operations ──

  it('shows loading state while fetching voices', async () => {
    // Keep the promise pending (never resolve)
    mockGetSpeakers.mockReturnValue(new Promise(() => {}))
    mockMimoStatus.mockReturnValue(new Promise(() => {}))

    render(<SettingsVoice />)

    // Voice list should show loading indicator
    expect(screen.getByText('加载中...')).toBeDefined()
  })
})

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SettingsVoice from '../../pages/SettingsVoice'

// ── hoisted mock fns ──
const { mockMimoClone, mockMimoDesign, mockMimoSynthesize, mockMimoSetEngine, mockMimoSwitchVoice, mockMimoStatus, mockPlayAudioBlob } = vi.hoisted(
  () => ({
    mockMimoClone: vi.fn(),
    mockMimoDesign: vi.fn(),
    mockMimoSynthesize: vi.fn(),
    mockMimoSetEngine: vi.fn(),
    mockMimoSwitchVoice: vi.fn(),
    mockMimoStatus: vi.fn(),
    mockPlayAudioBlob: vi.fn(),
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
  playAudioBlob: (...args: unknown[]) => mockPlayAudioBlob(...args),
}))

vi.mock('../../api/system', () => ({
  getSpeakers: (...args: unknown[]) => mockGetSpeakers(...args),
}))

// ── fixtures ──

// W7：/voice/speakers 合并返回 预设 + catalog 自定义音色（克隆产物刷新可找回）
const FAKE_VOICES = {
  data: {
    speakers: [
      { name: 'female-tianmei', display_name: '甜美女声', kind: 'preset' },
      { name: 'vc_cloned_1', display_name: '我的克隆音色', kind: 'clone', description: '克隆音色: 测试' },
    ],
  },
}

const FAKE_AUDIO_BLOB = new Blob([new Uint8Array([1, 2, 3])], { type: 'audio/mpeg' })

describe('SettingsVoice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // Default: resolve voice calls so component can load
    mockGetSpeakers.mockResolvedValue(FAKE_VOICES)
    mockMimoStatus.mockResolvedValue({ data: { enabled: true } })
    // W7：克隆/设计返回 voice_id 且登记 catalog
    mockMimoClone.mockResolvedValue({ data: { status: 'ok', voice_id: 'vc_cloned_1', catalog_saved: true } })
    mockMimoDesign.mockResolvedValue({ data: { status: 'ok', voice_id: 'vc_designed_1', catalog_saved: true } })
    // W7：合成返回音频 Blob
    mockMimoSynthesize.mockResolvedValue({ data: FAKE_AUDIO_BLOB })
    mockMimoSetEngine.mockResolvedValue({ data: { status: 'ok' } })
    mockMimoSwitchVoice.mockResolvedValue({ data: { status: 'ok' } })
    mockPlayAudioBlob.mockResolvedValue(undefined)
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
    // The engine switcher shows the default engine (MiMo-only 展示卡片，不再调 set-engine)
    expect(screen.getByText('MiMo Cloud')).toBeDefined()
  })

  // ── Test 2: Engine options（W7：MiMo-only，引擎卡片为展示项不触发 API）──

  it('renders MiMo Cloud engine option', async () => {
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(mockGetSpeakers).toHaveBeenCalled()
    })

    expect(screen.getByText('MiMo Cloud')).toBeDefined()
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

  // ── Test 5: W7 — catalog 克隆音色出现在列表并可选中 ──

  it('lists catalog clone voices from merged speakers', async () => {
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(screen.getByText('我的克隆音色')).toBeDefined()
    })
    expect(screen.getByText('甜美女声')).toBeDefined()
  })

  // ── Test 6: Click 试听 calls mimoSynthesize and plays returned blob ──

  it('click 试听 calls mimoSynthesize then plays audio blob', async () => {
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
      // W7：当前选中音色（首个 = 预设 female-tianmei）随请求传递
      expect(mockMimoSynthesize).toHaveBeenCalledWith('你好世界', { voiceId: 'female-tianmei' })
    })
    await waitFor(() => {
      // 返回的 Blob 真正进入播放器
      expect(mockPlayAudioBlob).toHaveBeenCalledWith(FAKE_AUDIO_BLOB)
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

  // ── Test 8: W7 — engine 卡片点击不再发送 engine 名给 set-engine ──

  it('does not call mimoSetEngine with engine name', async () => {
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(mockGetSpeakers).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByText('MiMo Cloud'))
    // 类型分明：engine 名（mimo-tts）不进模型白名单端点
    expect(mockMimoSetEngine).not.toHaveBeenCalled()
  })
})

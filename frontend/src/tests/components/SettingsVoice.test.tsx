import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SettingsVoice from '../../pages/SettingsVoice'
import type { UserInfo } from '../../store/authStore'

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

// ── P0 收尾批：authStore mock（角色真源，页面据此区分 admin / 普通用户）──
const { authState } = vi.hoisted(() => ({
  authState: { user: null as unknown },
}))

vi.mock('../../store/authStore', () => ({
  useAuthStore: vi.fn((selector?: (s: unknown) => unknown) =>
    selector ? selector(authState) : authState,
  ),
}))

// 用户夹具：仅 role 字段参与分支判定，其余字段按 UserInfo 形状补齐
const makeUser = (role: 'admin' | 'viewer'): UserInfo => ({
  id: 1,
  email: 'u@test.com',
  username: 'tester',
  display_name: '测试用户',
  avatar_url: '',
  role,
  is_active: true,
  is_verified: true,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
})

// 按用例设定登录角色（组件对未登录视同普通用户——后端同样不放行）
const setAuthUser = (role: 'admin' | 'viewer') => {
  authState.user = makeUser(role)
}

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
    // P0 收尾批：既有用例成文于 admin 门之前（人人可切全局音色），默认 admin 保原语义；
    // 角色分支用例各自显式 setAuthUser 覆盖
    setAuthUser('admin')
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

  // ══ P0 收尾批：admin 门端点的前端适配 ══
  // 后端实况（api/routers/mimo_voice_routes.py）：switch-voice 与 set-engine 均
  // require_role("admin")（改全局 TTS 单例）；clone/design/synthesize 走限速非门。
  // 且 clone/design 校验全局 provider 当前模型 → set-engine 是克隆/设计的技术前置。

  it('非 admin 点击音色仅本地选中供试听，不调用全局切换 switch-voice，并给说明文案', async () => {
    setAuthUser('viewer')
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(screen.getByText('我的克隆音色')).toBeDefined()
    })

    fireEvent.click(screen.getByText('我的克隆音色'))

    // 全局切换是 admin 门端点——普通用户点击不得发起（必然 403）
    await waitFor(() => {
      expect(mockMimoSwitchVoice).not.toHaveBeenCalled()
    })
    // 说明文案指路角色设置的个人音色绑定
    expect(screen.getByText(/角色设置中绑定个人音色/)).toBeDefined()
  })

  it('admin 点击音色仍调用全局切换 switch-voice（既有链路钉住）', async () => {
    setAuthUser('admin')
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(screen.getByText('我的克隆音色')).toBeDefined()
    })

    fireEvent.click(screen.getByText('我的克隆音色'))
    await waitFor(() => {
      expect(mockMimoSwitchVoice).toHaveBeenCalledWith('vc_cloned_1')
    })
  })

  it('非 admin 克隆：set-engine 403 不阻断，仍尝试克隆并给降级提示', async () => {
    setAuthUser('viewer')
    mockMimoSetEngine.mockRejectedValue({
      response: { status: 403, data: { detail: '需要管理员权限' } },
    })
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(screen.getByText('我的克隆音色')).toBeDefined()
    })

    fireEvent.change(screen.getByPlaceholderText('自定义语音名称'), {
      target: { value: '我的声音' },
    })
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(fileInput, {
      target: { files: [new File(['x'], 'ref.wav', { type: 'audio/wav' })] },
    })
    fireEvent.click(screen.getByText('开始克隆'))

    // 关键行为：set-engine 失败被降级捕获，克隆仍被尝试
    // （平台模型恰为 voiceclone 时克隆可成功；不恰则后端 400 明细可见）
    await waitFor(() => {
      expect(mockMimoSetEngine).toHaveBeenCalledWith('mimo-v2.5-tts-voiceclone')
    })
    await waitFor(() => {
      expect(mockMimoClone).toHaveBeenCalled()
    })
    // 降级提示可见
    await waitFor(() => {
      expect(screen.getByText(/音色模型切换为管理员能力/)).toBeDefined()
    })
    // 克隆成功路径完整走完（done 态展示音色 ID）
    await waitFor(() => {
      expect(screen.getByText(/音色 ID/)).toBeDefined()
    })
  })

  it('非 admin 设计：set-engine 403 不阻断，仍尝试设计', async () => {
    setAuthUser('viewer')
    mockMimoSetEngine.mockRejectedValue({
      response: { status: 403, data: { detail: '需要管理员权限' } },
    })
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(screen.getByText('我的克隆音色')).toBeDefined()
    })

    fireEvent.click(screen.getByText('应用设计'))
    await waitFor(() => {
      expect(mockMimoSetEngine).toHaveBeenCalledWith('mimo-v2.5-tts-voicedesign')
    })
    await waitFor(() => {
      expect(mockMimoDesign).toHaveBeenCalled()
    })
  })

  it('admin 克隆：set-engine 成功后克隆（既有链路钉住）', async () => {
    setAuthUser('admin')
    render(<SettingsVoice />)

    await waitFor(() => {
      expect(screen.getByText('我的克隆音色')).toBeDefined()
    })

    fireEvent.change(screen.getByPlaceholderText('自定义语音名称'), {
      target: { value: '管理员声音' },
    })
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(fileInput, {
      target: { files: [new File(['x'], 'ref.wav', { type: 'audio/wav' })] },
    })
    fireEvent.click(screen.getByText('开始克隆'))

    await waitFor(() => {
      expect(mockMimoSetEngine).toHaveBeenCalledWith('mimo-v2.5-tts-voiceclone')
    })
    await waitFor(() => {
      expect(mockMimoClone).toHaveBeenCalled()
    })
  })
})

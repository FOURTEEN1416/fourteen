/**
 * CharacterSettingsPanel（「我 × 这个角色」设置面板）回归（2026-11-07 P5/P1）。
 *
 * 锁五件事：
 * 1. 表单渲染**服务端真值**（不是前端兜底默认）；
 * 2. 保存提交的是当前 config（全量快照语义，服务端负责校验），成功后 toast；
 * 3. 恢复出厂 = DELETE → 重新 GET；
 * 4. 加载失败显式错误态 + 重试（409 归因「未绑定微信」，其他失败**不得**冒充成绑定问题）；
 * 5. D-C（2026-11-08）：回复模式进快照（`reply_mode`），空串=未设置=跟随全局默认。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import CharacterSettingsPanel from '../../components/roles/CharacterSettingsPanel'
import { useErrorStore } from '../../store/errorStore'

const { mockGet, mockSave, mockReset } = vi.hoisted(() => ({
  mockGet: vi.fn(),
  mockSave: vi.fn(),
  mockReset: vi.fn(),
}))

vi.mock('../../api/characterSettings', () => ({
  getMyCharacterSettings: (...args: unknown[]) => mockGet(...args),
  saveMyCharacterSettings: (...args: unknown[]) => mockSave(...args),
  resetMyCharacterSettings: (...args: unknown[]) => mockReset(...args),
}))

const SERVER_CONFIG = {
  delay_mode: 'dynamic',
  per_char_ms: 55,
  max_segments: 3,
  proactive_split: true,
  typing_enabled: false,
  typo_rate: 2,
  enabled: true,
  paused: true,
  threshold: 3.5,
  max_daily_messages: 12,
  llm_enabled: false,
  intensity: 'high',
  style_hint: '温柔',
  dice_enabled: true,
  dice_dry_run: false,
  peak_line: 30,
  topic_enabled: true,
  llm_judge_enabled: false,
}

function renderPanel() {
  return render(<CharacterSettingsPanel characterId="c1" characterName="小雅" />)
}

function toasts() {
  return useErrorStore.getState().toasts.map((t) => `${t.type}:${t.message}`)
}

beforeEach(() => {
  vi.clearAllMocks()
  useErrorStore.setState({ toasts: [], lastError: null })
  mockGet.mockResolvedValue({
    status: 'ok',
    character_id: 'c1',
    scope: 'user_character',
    config: { ...SERVER_CONFIG },
  })
  mockSave.mockImplementation((_id: string, config: Record<string, unknown>) =>
    Promise.resolve({ status: 'ok', character_id: 'c1', scope: 'user_character', config }))
  mockReset.mockResolvedValue({ status: 'ok', reset: true })
})

describe('CharacterSettingsPanel', () => {
  it('渲染服务端真值，不是前端兜底默认', async () => {
    renderPanel()

    expect(await screen.findByTestId('character-settings-panel')).toBeDefined()
    expect(mockGet).toHaveBeenCalledWith('c1')
    // 每字毫秒 55（兜底默认是 45）→ 滑块值必须来自服务端
    expect(screen.getByDisplayValue('55')).toBeDefined()
    // 风格提示来自服务端
    expect(screen.getByDisplayValue('温柔')).toBeDefined()
    // 拆条上限 3 条选中
    expect(screen.getByRole('button', { name: '3 条' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('保存：提交当前 config 全量，成功后给 toast', async () => {
    renderPanel()
    fireEvent.click(await screen.findByText('保存本角色设置'))

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [calledId, calledConfig] = mockSave.mock.calls[0] as [string, Record<string, unknown>]
    expect(calledId).toBe('c1')
    expect(calledConfig.per_char_ms).toBe(55)
    expect(calledConfig.typing_enabled).toBe(false)
    expect(toasts().some((t) => t.startsWith('success:'))).toBe(true)
  })

  it('保存失败：透出后端 detail（越界等），不谎报成功', async () => {
    mockSave.mockRejectedValue({ response: { data: { detail: 'per_char_ms 越界: 999（允许 5..200）' } } })
    renderPanel()
    fireEvent.click(await screen.findByText('保存本角色设置'))

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    expect(toasts().some((t) => t.includes('越界'))).toBe(true)
    expect(toasts().some((t) => t.startsWith('success:'))).toBe(false)
  })

  it('恢复出厂：DELETE 后重新 GET，回复模式回落「跟随全局默认」', async () => {
    mockGet.mockResolvedValue({
      status: 'ok',
      character_id: 'c1',
      scope: 'user_character',
      config: { ...SERVER_CONFIG, reply_mode: 'novel' },
    })
    renderPanel()
    await screen.findByTestId('character-settings-panel')
    expect(screen.getByRole('button', { name: /小说式/ }).getAttribute('aria-pressed')).toBe('true')

    // DELETE 之后快照按出厂默认重物化：reply_mode 回到空串（未设置=跟随全局默认）
    mockGet.mockResolvedValue({
      status: 'ok',
      character_id: 'c1',
      scope: 'user_character',
      config: { ...SERVER_CONFIG, reply_mode: '' },
    })
    fireEvent.click(screen.getByText('恢复出厂默认'))

    await waitFor(() => expect(mockReset).toHaveBeenCalledWith('c1'))
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2))
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /跟随全局默认/ }).getAttribute('aria-pressed')).toBe('true')
    })
    expect(screen.getByTestId('reply-mode-inherit-hint')).toBeDefined()
  })

  it('加载失败（409 未绑定）：显式错误态 + 重试，归因到「扫码绑定」而非内部话术', async () => {
    mockGet.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: '尚未绑定微信，无法确定你的会话键（不猜键）；请先在微信扫码绑定' },
      },
    })
    renderPanel()

    // 卡片给**可自助解决**的引导语，不把后端内部话术（不猜键/会话键）当主文案
    expect(
      await screen.findByText('尚未绑定微信通道，绑好后这里才能保存你的设置（微信页扫码绑定即可）'),
    ).toBeDefined()
    expect(screen.queryByText(/不猜键/)).toBeNull()
    expect(screen.queryByText('保存本角色设置')).toBeNull()
    expect(screen.queryByTestId('character-settings-panel')).toBeNull()

    mockGet.mockResolvedValue({
      status: 'ok', character_id: 'c1', scope: 'user_character', config: { ...SERVER_CONFIG },
    })
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByTestId('character-settings-panel')).toBeDefined()
  })

  it('加载失败（500 / 网络）：错误态不得冒充成「绑定同步中」，给通用重试引导', async () => {
    mockGet.mockRejectedValue({ response: { status: 500, data: { detail: 'Internal Server Error' } } })
    renderPanel()

    expect(await screen.findByText('设置加载失败，请重试；持续失败请联系管理员')).toBeDefined()
    // 归因错误的旧文案必须消失
    expect(screen.queryByText(/正在同步你的微信绑定/)).toBeNull()
    expect(screen.queryByText(/尚未绑定微信通道/)).toBeNull()
  })

  it('加载失败（网络中断，无 response）：同样走通用错误态', async () => {
    mockGet.mockRejectedValue(new Error('Network Error'))
    renderPanel()

    expect(await screen.findByText('设置加载失败，请重试；持续失败请联系管理员')).toBeDefined()
  })
})

// ── D-C（2026-11-08 用户裁决）：回复模式进「我 × 角色」快照 ──
describe('CharacterSettingsPanel · 回复模式（D-C）', () => {
  it('快照 reply_mode 为空串（服务端「未设置」形态）→ 「跟随全局默认」选中 + 提示可见', async () => {
    // 服务端 character_settings 的默认值就是空串（空=未设置=跟随全局默认）
    mockGet.mockResolvedValue({
      status: 'ok',
      character_id: 'c1',
      scope: 'user_character',
      config: { ...SERVER_CONFIG, reply_mode: '' },
    })
    renderPanel()
    await screen.findByTestId('character-settings-panel')

    const inherit = screen.getByRole('button', { name: /跟随全局默认/ })
    expect(inherit.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByTestId('reply-mode-inherit-hint')).toBeDefined()
    // 另两个值不选中
    expect(screen.getByRole('button', { name: /沉浸式/ }).getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('button', { name: /小说式/ }).getAttribute('aria-pressed')).toBe('false')
  })

  it('回复模式区块排在「② 说话节奏」之前（位置契约）', async () => {
    renderPanel()
    await screen.findByTestId('character-settings-panel')

    const titles = screen.getAllByRole('heading').map((h) => h.textContent ?? '')
    const replyIdx = titles.indexOf('① 回复模式（这个角色怎么回你）')
    const delayIdx = titles.indexOf('② 说话节奏')
    expect(replyIdx).toBeGreaterThanOrEqual(0)
    expect(delayIdx).toBeGreaterThanOrEqual(0)
    expect(replyIdx).toBeLessThan(delayIdx)
  })

  it('选「小说式」→ 保存把 reply_mode=novel 写进快照', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /小说式/ }))
    expect(screen.getByRole('button', { name: /小说式/ }).getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(screen.getByText('保存本角色设置'))
    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [, calledConfig] = mockSave.mock.calls[0] as [string, Record<string, unknown>]
    expect(calledConfig.reply_mode).toBe('novel')
  })

  it('服务端快照已有 reply_mode=immersive → 控件回显，点「跟随全局默认」后保存写空串', async () => {
    mockGet.mockResolvedValue({
      status: 'ok',
      character_id: 'c1',
      scope: 'user_character',
      config: { ...SERVER_CONFIG, reply_mode: 'immersive' },
    })
    renderPanel()
    await screen.findByTestId('character-settings-panel')
    expect(screen.getByRole('button', { name: /沉浸式/ }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.queryByTestId('reply-mode-inherit-hint')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /跟随全局默认/ }))
    fireEvent.click(screen.getByText('保存本角色设置'))

    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [, calledConfig] = mockSave.mock.calls[0] as [string, Record<string, unknown>]
    // 后端 PUT 是局部合并（write 做 {**current, **patch}）：必须显式写空串，
    // 缺键会保留旧值 → 「跟随全局默认」点不动。
    expect(calledConfig.reply_mode).toBe('')
  })

  it('快照里的非法 reply_mode 值不会被原样提交（按未设置处理）', async () => {
    mockGet.mockResolvedValue({
      status: 'ok',
      character_id: 'c1',
      scope: 'user_character',
      config: { ...SERVER_CONFIG, reply_mode: 'hacked-mode' },
    })
    renderPanel()
    await screen.findByTestId('character-settings-panel')
    // 非法值按「跟随全局默认」展示
    expect(screen.getByRole('button', { name: /跟随全局默认/ }).getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(screen.getByText('保存本角色设置'))
    await waitFor(() => expect(mockSave).toHaveBeenCalledTimes(1))
    const [, calledConfig] = mockSave.mock.calls[0] as [string, Record<string, unknown>]
    // 非法值不发给服务端，归一为空串（=未设置）
    expect(calledConfig.reply_mode).toBe('')
  })
})

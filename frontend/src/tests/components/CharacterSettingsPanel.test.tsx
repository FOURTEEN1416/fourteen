/**
 * CharacterSettingsPanel（「我 × 这个角色」设置面板）回归（2026-11-07 P5/P1）。
 *
 * 锁四件事：
 * 1. 表单渲染**服务端真值**（不是前端兜底默认）；
 * 2. 保存提交的是当前 config（全量快照语义，服务端负责校验），成功后 toast；
 * 3. 恢复出厂 = DELETE → 重新 GET；
 * 4. 加载失败显式错误态 + 重试，绝不拿兜底值冒充现状。
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

  it('恢复出厂：DELETE 后重新 GET', async () => {
    renderPanel()
    fireEvent.click(await screen.findByText('恢复出厂默认'))

    await waitFor(() => expect(mockReset).toHaveBeenCalledWith('c1'))
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2))
  })

  it('加载失败：显式错误态 + 重试，不渲染表单', async () => {
    mockGet.mockRejectedValue({ response: { data: { detail: '未绑定微信' } } })
    renderPanel()

    expect(await screen.findByText('未绑定微信')).toBeDefined()
    expect(screen.queryByText('保存本角色设置')).toBeNull()
    expect(screen.queryByTestId('character-settings-panel')).toBeNull()

    mockGet.mockResolvedValue({
      status: 'ok', character_id: 'c1', scope: 'user_character', config: { ...SERVER_CONFIG },
    })
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByTestId('character-settings-panel')).toBeDefined()
  })
})

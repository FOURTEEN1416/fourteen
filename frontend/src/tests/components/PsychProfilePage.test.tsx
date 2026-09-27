import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import PsychProfilePage from '../../pages/PsychProfilePage'
import { useErrorStore } from '../../store/errorStore'
import { queryClient } from '../../api/queryClient'

// ════════════════════════════════════════════════════════════════
//  W11-D5 红测：成功必须是业务成功。
//  DELETE /api/psych/profile 返回 {"status":"reset"|"failed"}（200 包体）；
//  旧实现 HTTP 200 即走绿 toast「心理画像已重置」——业务 failed 也谎报成功。
// ════════════════════════════════════════════════════════════════

const { mockPsychReset } = vi.hoisted(() => ({
  mockPsychReset: vi.fn(),
}))

// 注意：必须 importOriginal 展开真实模块 —— api/client.ts 从 ./system 引入了
// health 等一大批函数，工厂式整体替换会让 client.ts 的具名导入解析失败
// （整份 suite 无法 collect）。
vi.mock('../../api/system', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/system')>()
  return {
    ...actual,
    psychReset: (...args: unknown[]) => mockPsychReset(...args),
  }
})

// 页面数据 hooks mock；usePsychReset 保留真实实现（业务判定在被测对象里）
const { mockUseActiveCharacter, mockUsePsychProfile, mockUsePsychSnapshots } = vi.hoisted(() => ({
  mockUseActiveCharacter: vi.fn(),
  mockUsePsychProfile: vi.fn(),
  mockUsePsychSnapshots: vi.fn(),
}))

vi.mock('../../hooks/useQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hooks/useQueries')>()
  return {
    ...actual,
    useActiveCharacter: (...args: unknown[]) => mockUseActiveCharacter(...args),
    usePsychProfile: (...args: unknown[]) => mockUsePsychProfile(...args),
    usePsychSnapshots: (...args: unknown[]) => mockUsePsychSnapshots(...args),
  }
})

const STABLE_PROFILE = {
  status: 'stable',
  stability: 0.8,
  snapshots: 3,
  ocean: { openness: 0.6, conscientiousness: 0.5, extraversion: 0.4, agreeableness: 0.7, neuroticism: 0.3 },
}

function toasts() {
  return useErrorStore.getState().toasts
}

async function openResetAndConfirm() {
  // usePsychReset 保留真实实现（业务判定在被测对象里），它内部需要 QueryClient
  render(
    <QueryClientProvider client={queryClient}>
      <PsychProfilePage />
    </QueryClientProvider>,
  )
  // 触发重置对话框
  fireEvent.click(await screen.findByText('重置画像'))
  fireEvent.click(screen.getByText('确认重置'))
  await waitFor(() => {
    expect(mockPsychReset).toHaveBeenCalledTimes(1)
  })
}

describe('PsychProfilePage 重置业务判定（W11-D5）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    queryClient.clear()
    useErrorStore.setState({ toasts: [], lastError: null })
    mockUseActiveCharacter.mockReturnValue({ activeCharacter: null, characters: [] })
    mockUsePsychProfile.mockReturnValue({ data: STABLE_PROFILE, isLoading: false })
    mockUsePsychSnapshots.mockReturnValue({ data: [] })
  })

  it('业务失败（status=failed）→ 错误提示，绝不绿 toast「已重置」', async () => {
    mockPsychReset.mockResolvedValue({ data: { status: 'failed' } })

    await openResetAndConfirm()

    await waitFor(() => {
      expect(toasts().some((t) => t.type === 'error' && t.message.includes('重置失败'))).toBe(true)
    })
    expect(toasts().some((t) => t.type === 'success')).toBe(false)
  })

  it('业务成功（status=reset）→ 绿 toast「心理画像已重置」', async () => {
    mockPsychReset.mockResolvedValue({ data: { status: 'reset' } })

    await openResetAndConfirm()

    await waitFor(() => {
      expect(toasts().some((t) => t.type === 'success' && t.message === '心理画像已重置')).toBe(true)
    })
  })
})

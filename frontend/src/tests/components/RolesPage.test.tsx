import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { ReactNode } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RolesPage from '../../pages/RolesPage'
import { queryKeys } from '../../hooks/useQueries'
import { activateCharacter } from '../../api/characters'
import type { UnifiedCharacter } from '../../types/api'

vi.mock('../../api/characters', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/characters')>()
  return {
    ...actual,
    activateCharacter: vi.fn(async () => ({ status: 'activated' })),
  }
})

const CARDS: UnifiedCharacter[] = [
  {
    id: 'active1',
    name: '林挽夏',
    description: '活跃角色',
    is_active: true,
    core_anchors: [],
  } as unknown as UnifiedCharacter,
  {
    id: 'other1',
    name: '米彩',
    description: '待切换角色',
    is_active: false,
    core_anchors: [],
  } as unknown as UnifiedCharacter,
]

vi.mock('../../hooks/useQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hooks/useQueries')>()
  return {
    ...actual,
    useUnifiedCharacters: () => ({
      data: { characters: CARDS },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    }),
  }
})

function Shell({ qc, children }: { qc: QueryClient; children: ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('RolesPage 激活后缓存失效（W17 遗留①收口）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('切换角色成功后 invalidate 账号维度键，而非裸 characters 键', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
    render(
      <Shell qc={qc}>
        <MemoryRouter>
          <RolesPage />
        </MemoryRouter>
      </Shell>,
    )
    const btn = await screen.findByText(/设为活跃|激活|使用/)
    fireEvent.click(btn)
    await waitFor(() => expect(activateCharacter).toHaveBeenCalledWith('other1'))
    const calledKeys = invalidateSpy.mock.calls
      .map(c => (c[0] as { queryKey?: readonly unknown[] }).queryKey)
      .filter(Boolean)
    expect(calledKeys).toContainEqual(queryKeys.characters.all)
    expect(calledKeys).not.toContainEqual(['characters'])
  })
})

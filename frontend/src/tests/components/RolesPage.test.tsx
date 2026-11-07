import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { ReactNode } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import RolesPage from '../../pages/RolesPage'
import { queryKeys } from '../../hooks/useQueries'
import { activateCharacter, deactivateCharacter } from '../../api/characters'
import type { UnifiedCharacter } from '../../types/api'

vi.mock('../../api/characters', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/characters')>()
  return {
    ...actual,
    activateCharacter: vi.fn(async () => ({ status: 'activated' })),
    deactivateCharacter: vi.fn(async () => ({ status: 'reset', character_id: 'default' })),
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

const { mockUseUnifiedCharacters } = vi.hoisted(() => ({ mockUseUnifiedCharacters: vi.fn() }))

vi.mock('../../hooks/useQueries', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hooks/useQueries')>()
  return {
    ...actual,
    useUnifiedCharacters: (...args: unknown[]) => mockUseUnifiedCharacters(...args),
  }
})

function Shell({ qc, children }: { qc: QueryClient; children: ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

describe('RolesPage 激活后缓存失效（W17 遗留①收口）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: CARDS },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    })
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
    // NEW-16 初始角色告知条文案也含「设为活跃」，这里必须锁定按钮本体
    const btn = await screen.findByRole('button', { name: /设为活跃/ })
    fireEvent.click(btn)
    await waitFor(() => expect(activateCharacter).toHaveBeenCalledWith('other1'))
    const calledKeys = invalidateSpy.mock.calls
      .map(c => (c[0] as { queryKey?: readonly unknown[] }).queryKey)
      .filter(Boolean)
    expect(calledKeys).toContainEqual(queryKeys.characters.all)
    expect(calledKeys).not.toContainEqual(['characters'])
  })
})

// ── NEW-9（2026-10-29）：查询错误不得伪装成「暂无角色」空态 ──
describe('RolesPage 查询错误态', () => {
  it('isError 时渲染错误态与重试按钮，且不出现「暂无角色」', async () => {
    const refetch = vi.fn()
    mockUseUnifiedCharacters.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    })
    render(
      <Shell qc={new QueryClient()}>
        <MemoryRouter>
          <RolesPage />
        </MemoryRouter>
      </Shell>,
    )
    expect(await screen.findByText('角色列表加载失败，请检查网络后重试')).toBeDefined()
    expect(screen.queryByText('暂无角色')).toBeNull()
    fireEvent.click(screen.getByText('重试'))
    expect(refetch).toHaveBeenCalledTimes(1)
  })
})

// ── NEW-31（2026-10-30）：加载态收敛 shared/Skeleton + 空态收敛 shared/EmptyState ──
describe('RolesPage 加载态与空态收编', () => {
  const renderPage = () =>
    render(
      <Shell qc={new QueryClient()}>
        <MemoryRouter>
          <RolesPage />
        </MemoryRouter>
      </Shell>,
    )

  it('加载态用 shared/Skeleton 拼装（不再手写 animate-pulse 灰块），带 role=status', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    })
    const { container } = renderPage()

    const grid = screen.getByRole('status', { name: '加载角色列表' })
    // 4 张卡 × (标题 1 + 描述 2 + 底部 2) 条骨架
    expect(container.querySelectorAll('.animate-skeleton').length).toBeGreaterThanOrEqual(16)
    // 旧实现是整卡 animate-pulse 灰块——Skeleton 走的是 animate-skeleton 关键帧
    expect(grid.querySelectorAll('.animate-pulse').length).toBe(0)
  })

  it('空态用 shared/EmptyState，且两个动作与 NEW-16 引导文案都在', async () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: { characters: [] },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    })
    const { container } = renderPage()

    expect(await screen.findByText('暂无角色')).toBeDefined()
    // EmptyState 的 action 槽位承载两个入口（能力入口不因收编而消失）
    expect(screen.getByRole('button', { name: '创建角色' })).toBeDefined()
    expect(screen.getByRole('button', { name: '刷新列表' })).toBeDefined()

    const description = screen.getByText('注册时如未自动分配初始角色，可在这里创建一个开始使用')
    // ── 以下四条钉住「确实换成了 shared/EmptyState」，旧手写块会全红 ──
    // ① 描述走 EmptyState 的 description 槽位样式（旧手写是 mx-auto/max-w-sm/leading-relaxed）
    expect(description.className).toContain('max-w-[200px]')
    expect(description.className).not.toContain('max-w-sm')
    // ② 空态根节点是 EmptyState 的居中骨架（py-12 + items-center），不再是手写 p-8 块
    const root = description.parentElement as HTMLElement
    expect(root.className).toContain('py-12')
    expect(root.className).toContain('items-center')
    expect(root.className).toContain('text-center')
    // ③ 旧手写空态的标记消失：p-8 容器与 mx-auto 图标
    expect(container.querySelector('.p-8')).toBeNull()
    expect(container.querySelector('.mx-auto.mb-3')).toBeNull()
    // ④ 标题走 EmptyState 的 title 槽位样式（旧是裸 <p className="text-sm">）
    expect(screen.getByText('暂无角色').className).toContain('font-medium')
    expect(screen.getByText('暂无角色').className).not.toContain('text-sm text-gray-500')
  })
})

// ── 2026-11-07 用户裁决：内置十四=default 是正式默认角色卡（前后端对齐）──
describe('RolesPage 内置默认卡', () => {
  const renderPage = (qc = new QueryClient()) =>
    render(
      <Shell qc={qc}>
        <MemoryRouter>
          <RolesPage />
        </MemoryRouter>
      </Shell>,
    )

  it('后端点亮内置卡（无其他激活）→ 默认人设卡「当前使用中」，且网格不重复渲染内置卡', () => {
    mockUseUnifiedCharacters.mockReturnValue({
      data: {
        characters: [
          { id: 'default', name: '十四', description: '', user_id: 'system', builtin: true, is_active: true, core_anchors: [] },
          { id: 'a', name: 'A', description: '', is_active: false, core_anchors: [] },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    })
    renderPage()
    const card = screen.getByTestId('default-persona-card')
    expect(card.textContent).toContain('当前使用中')
    expect(screen.queryByRole('button', { name: '恢复默认' })).toBeNull()
    // 内置卡由固定卡表示，网格只渲染卡文件角色
    expect(screen.getByText('A')).toBeDefined()
    expect(screen.queryByText('十四')).toBeNull()
  })

  it('其他卡激活（内置卡熄灭）时点「恢复默认」→ 调端点并失效角色查询', async () => {
    const qc = new QueryClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
    mockUseUnifiedCharacters.mockReturnValue({
      data: {
        characters: [
          { id: 'default', name: '十四', description: '', user_id: 'system', builtin: true, is_active: false, core_anchors: [] },
          ...CARDS,
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    })
    renderPage(qc)

    fireEvent.click(await screen.findByRole('button', { name: '恢复默认' }))
    await waitFor(() => expect(deactivateCharacter).toHaveBeenCalledTimes(1))

    const calledKeys = invalidateSpy.mock.calls
      .map(c => (c[0] as { queryKey?: readonly unknown[] }).queryKey)
      .filter(Boolean)
    expect(calledKeys).toContainEqual(queryKeys.characters.all)
  })
})

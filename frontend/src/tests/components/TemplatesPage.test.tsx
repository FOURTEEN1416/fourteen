import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import TemplatesPage from '../../pages/TemplatesPage'
import { useErrorStore } from '../../store/errorStore'

// ════════════════════════════════════════════════════════════════
//  W17 红测：角色模板面（/templates）
//  后端真源（只读核对 api/routers/character_template_routes.py）：
//    GET  /api/character-templates            → {templates:[{id,name,description,tags}], total}
//    POST /api/character-templates/{id}/clone → 201 {id, name, status: "created"}
//  纪律：① 成功判定只认业务回执（isCloneAccepted）；② 克隆成功必须让
//       「我的角色」查询失效，否则 30s staleTime 内跳回角色页看不到新卡。
// ════════════════════════════════════════════════════════════════

const { mockListTemplates, mockCloneTemplate, mockIsCloneAccepted, mockNavigate } = vi.hoisted(() => ({
  mockListTemplates: vi.fn(),
  mockCloneTemplate: vi.fn(),
  mockIsCloneAccepted: vi.fn(),
  mockNavigate: vi.fn(),
}))

vi.mock('../../api/templates', () => ({
  listTemplates: (...a: unknown[]) => mockListTemplates(...a),
  cloneTemplate: (...a: unknown[]) => mockCloneTemplate(...a),
  isCloneAccepted: (...a: unknown[]) => mockIsCloneAccepted(...(a as [{ status?: string; id?: string }])),
}))

vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>()
  return { ...actual, useNavigate: () => mockNavigate }
})

const TEMPLATES = {
  total: 2,
  templates: [
    { id: 'tpl-linwanxia', name: '林挽夏', description: '温柔的文学少女', tags: ['温柔', '文学'] },
    { id: 'tpl-sunyingsha', name: '孙颖莎', description: '运动员', tags: ['阳光'] },
  ],
}

let queryClient: QueryClient

function mount() {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </MemoryRouter>
  )
  render(<TemplatesPage />, { wrapper: Wrapper })
  return invalidate
}

function toasts() {
  return useErrorStore.getState().toasts.map((t) => `${t.type}:${t.message}`)
}

/** 克隆按钮在请求中改名「创建中…」，两种态都要能取到 */
const CLONE_BUTTON = /使用此角色|创建中/

function cloneButton(index: number): HTMLButtonElement {
  return screen.getAllByRole('button', { name: CLONE_BUTTON })[index] as HTMLButtonElement
}

/** 点击第一张模板卡（林挽夏）的「使用此角色」 */
async function clickClone() {
  await screen.findAllByRole('button', { name: /使用此角色/ })
  fireEvent.click(cloneButton(0))
}

beforeEach(() => {
  vi.clearAllMocks()
  useErrorStore.setState({ toasts: [], lastError: null })
  mockListTemplates.mockResolvedValue(TEMPLATES)
  mockIsCloneAccepted.mockImplementation((r) => r?.status === 'created' && typeof r?.id === 'string' && r.id.length > 0)
})

describe('模板清单', () => {
  it('展示后端真实模板（名称/描述/标签），不编造数量', async () => {
    mount()

    expect(await screen.findByText('林挽夏')).toBeInTheDocument()
    expect(screen.getByText('孙颖莎')).toBeInTheDocument()
    expect(screen.getByText('温柔的文学少女')).toBeInTheDocument()
    expect(screen.getByText('文学')).toBeInTheDocument()
    expect(mockListTemplates).toHaveBeenCalledTimes(1)
  })

  it('空清单（模板开关未开或无策展项）显示空态而非报错', async () => {
    mockListTemplates.mockResolvedValue({ total: 0, templates: [] })

    mount()

    await screen.findByText(/暂无可用模板/)
    expect(screen.queryByText('林挽夏')).not.toBeInTheDocument()
  })

  it('加载失败：明确报错 + 重试，不拿空态冒充', async () => {
    mockListTemplates.mockRejectedValue(new Error('boom'))

    mount()

    await screen.findByText(/无法加载角色模板/)
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })
})

describe('使用此角色（克隆）', () => {
  it('回执 created + 新 id → 成功 toast + 失效我的角色查询 + 跳角色配置', async () => {
    mockCloneTemplate.mockResolvedValue({ id: 'c-new', name: '林挽夏', status: 'created' })
    const invalidate = mount()

    await clickClone()

    await waitFor(() => expect(mockCloneTemplate).toHaveBeenCalledWith('tpl-linwanxia'))
    await waitFor(() => expect(mockNavigate).toHaveBeenCalledWith('/roles'))
    expect(invalidate).toHaveBeenCalled()
    expect(toasts().some((t) => t.startsWith('success:'))).toBe(true)
    await waitFor(() => expect(cloneButton(0).disabled).toBe(false))
  })

  it('假成功守卫：HTTP 成功但回执不是 created → 不跳转、给 warning、按钮可再点', async () => {
    mockCloneTemplate.mockResolvedValue({ id: '', name: '林挽夏', status: 'failed' })
    mount()

    await clickClone()

    await waitFor(() => expect(mockCloneTemplate).toHaveBeenCalled())
    await waitFor(() => expect(toasts().length).toBeGreaterThan(0))
    expect(mockNavigate).not.toHaveBeenCalled()
    expect(toasts().some((t) => t.startsWith('success:'))).toBe(false)
    // 失败必须回到可重试态（按钮解禁），并让 finally 里的状态更新落定
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: /使用此角色/ })).toHaveLength(2),
    )
    expect(cloneButton(0).disabled).toBe(false)
  })

  it('请求异常 → error 回执，不静默失败', async () => {
    mockCloneTemplate.mockRejectedValue(new Error('403'))

    mount()

    await clickClone()

    await waitFor(() => expect(toasts().some((t) => t.startsWith('error:'))).toBe(true))
    expect(mockNavigate).not.toHaveBeenCalled()
    await waitFor(() => expect(cloneButton(0).disabled).toBe(false))
  })
})

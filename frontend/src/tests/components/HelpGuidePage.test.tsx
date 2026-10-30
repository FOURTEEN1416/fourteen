import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import HelpGuidePage from '../../pages/HelpGuidePage'
import { useAuthStore } from '../../store/authStore'

// F1（2026-10-30）：HelpGuidePage 经 useActiveCharacter 造 needsRole 条目链接，
// 本文件无 QueryClientProvider——模块级 mock 该 hook，按用例注入活跃角色。
const mocks = vi.hoisted(() => ({
  activeCharacter: null as null | { id: number; is_active: boolean },
}))
vi.mock('../../hooks/useQueries', () => ({
  useActiveCharacter: () => ({
    activeCharacter: mocks.activeCharacter,
    characters: mocks.activeCharacter ? [mocks.activeCharacter] : [],
  }),
}))

// V2-4（2026-10-29）：功能教学 checklist——全部模块 + 进度按 user 持久化 + 导览重放位。
// driver.js 的真 DOM 高亮在 jsdom 下不可驱动，只测页面契约（清单/进度/按钮），不测动画。

const ADMIN_USER = {
  id: 7, email: 'a@t', username: 'admin', display_name: 'Admin',
  role: 'admin' as const, is_active: true, is_verified: true,
}

describe('HelpGuidePage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mocks.activeCharacter = null
    useAuthStore.setState({ user: ADMIN_USER } as never)
  })

  it('渲染全部教学卡片与进度', () => {
    render(
      <MemoryRouter>
        <HelpGuidePage />
      </MemoryRouter>,
    )
    expect(screen.getByText('微信连接 · 开始对话')).toBeDefined()
    expect(screen.getByText('LLM 配置 · 自带 Key（BYOK）')).toBeDefined()
    // 进度初值 0
    expect(screen.getByTestId('guide-progress').textContent).toMatch(/^0\/\d+ 已学会$/)
    // 需活跃角色标注在案（V2-4②：无活跃角色时隐藏组的显式说明）
    expect(screen.getAllByText('需活跃角色').length).toBeGreaterThan(0)
  })

  it('勾选「已学会」更新进度并按 user_id 持久化', () => {
    render(
      <MemoryRouter>
        <HelpGuidePage />
      </MemoryRouter>,
    )
    const checkboxes = screen.getAllByLabelText('标记为已学会')
    fireEvent.click(checkboxes[0])
    expect(screen.getByTestId('guide-progress').textContent).toMatch(/^1\/\d+ 已学会$/)
    // localStorage 键带 user_id（V2-4①：进度按 user 持久化）；值形 {"/wechat":true}
    expect(localStorage.getItem('guide-progress-7')).toBe('{"/wechat":true}')
  })

  it('渲染导览重放按钮与 /intro 深链', () => {
    render(
      <MemoryRouter>
        <HelpGuidePage />
      </MemoryRouter>,
    )
    expect(screen.getByTestId('guide-replay-tour')).toBeDefined()
    expect(screen.getByText('新手五步教程 / 免费 Key 申请')).toBeDefined()
  })

  it('F1：needsRole 条目按活跃角色真 id 造链；无活跃角色回落 /roles 并带提示', () => {
    // 无活跃角色：死路由不再产生，回落 /roles 且带「先激活一个角色后可直达」
    const { unmount } = render(
      <MemoryRouter>
        <HelpGuidePage />
      </MemoryRouter>,
    )
    const fallback = screen
      .getAllByRole('link')
      .find((a) => a.getAttribute('href') === '/roles' && a.getAttribute('title') === '先激活一个角色后可直达')
    expect(fallback).toBeDefined()
    unmount()

    // 有活跃角色：/roles/current/* 替换为真 id，不再产出死链
    mocks.activeCharacter = { id: 5, is_active: true }
    render(
      <MemoryRouter>
        <HelpGuidePage />
      </MemoryRouter>,
    )
    const hrefs = screen.getAllByRole('link').map((a) => a.getAttribute('href'))
    expect(hrefs).toContain('/roles/5/settings')
    expect(hrefs).toContain('/roles/5/status')
    expect(hrefs).toContain('/roles/5/storyline')
    expect(hrefs.some((h) => h?.startsWith('/roles/current/'))).toBe(false)
  })
})

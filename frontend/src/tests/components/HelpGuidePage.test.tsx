import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import HelpGuidePage from '../../pages/HelpGuidePage'
import { useAuthStore } from '../../store/authStore'

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
})

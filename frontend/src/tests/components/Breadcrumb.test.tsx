import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import Breadcrumb from '../../components/layout/Breadcrumb'

const { mockLogout } = vi.hoisted(() => ({ mockLogout: vi.fn().mockResolvedValue(undefined) }))

vi.mock('../../hooks/useQueries', () => ({
  useUnifiedCharacters: () => ({ data: { characters: [] } }),
  useActiveCharacter: () => ({ data: null }),
  useWechatStatus: () => ({ data: null }),
}))

vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    user: { id: 1, username: 'admin', display_name: '十四的主人', role: 'admin' },
    logout: mockLogout,
  }),
}))

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname}</div>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Breadcrumb />
      <LocationProbe />
    </MemoryRouter>,
  )
}

describe('Breadcrumb', () => {
  beforeEach(() => {
    mockLogout.mockClear()
    mockLogout.mockResolvedValue(undefined)
  })

  // ── NEW-2（2026-10-29）：控制台退出登录入口 ──

  it('shows identity and a logout entry in the top bar', () => {
    renderAt('/roles/abc/status')
    expect(screen.getByText('十四的主人')).toBeDefined()
    expect(screen.getByRole('button', { name: '退出登录' })).toBeDefined()
  })

  it('logs out and navigates to /login', async () => {
    renderAt('/wechat')
    fireEvent.click(screen.getByRole('button', { name: '退出登录' }))
    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(screen.getByTestId('loc').textContent).toBe('/login')
    })
  })

  // ── D20-⑦（2026-10-29）：三个死映射删除 ──

  it('no longer maps dead settings tabs (general / extensions)', () => {
    renderAt('/settings/extensions')
    expect(screen.queryByText('扩展管理')).toBeNull()
    expect(screen.getByText('extensions')).toBeDefined()
  })

  it('no longer maps dead admin logs tab', () => {
    renderAt('/admin/logs')
    expect(screen.queryByText('日志审计')).toBeNull()
    expect(screen.getByText('logs')).toBeDefined()
  })

  it('still maps live settings tabs', () => {
    renderAt('/settings/llm')
    // 面包屑与移动端抽屉导航都会渲染该标签，断言存在即可
    expect(screen.getAllByText('LLM 配置').length).toBeGreaterThan(0)
  })

  it('still maps live voice tab', () => {
    renderAt('/settings/voice')
    expect(screen.getAllByText('语音引擎').length).toBeGreaterThan(0)
  })
})

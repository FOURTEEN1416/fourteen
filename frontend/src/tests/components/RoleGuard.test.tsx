/**
 * RoleGuard（管理员路由守卫）回归（2026-11-07 对抗验收）。
 *
 * 对抗验收发现：普通用户直访 /admin/* 管理员路由时被静默弹回，
 * 落在一个无关页面且不知道原因。钉两件事：
 * 1. 无权限 → 弹「仅管理员可用」提示 + 跳转 fallback，不渲染子组件；
 * 2. 有权限 → 正常渲染，且绝不弹提示。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import RoleGuard from '../../components/auth/RoleGuard'
import { useAuthStore } from '../../store/authStore'
import { useErrorStore } from '../../store/errorStore'

function makeUser(role: 'admin' | 'viewer') {
  useAuthStore.setState({
    user: {
      id: role === 'admin' ? 1 : 2,
      email: `${role}@test.local`,
      username: role,
      display_name: role,
      avatar_url: '',
      role,
      is_active: true,
      is_verified: true,
      created_at: '2026-01-01T00:00:00Z',
      last_login_at: null,
    },
    isAuthenticated: true,
    isInitialized: true,
  })
}

function renderGuard() {
  return render(
    <MemoryRouter initialEntries={['/admin/users']}>
      <Routes>
        <Route
          path="/admin/users"
          element={
            <RoleGuard roles={['admin'] as const}>
              <div data-testid="ops-content">运维页内容</div>
            </RoleGuard>
          }
        />
        <Route path="/" element={<div data-testid="home">首页</div>} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('RoleGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useErrorStore.setState({ toasts: [], lastError: null })
  })

  it('普通用户直访管理员路由 → 弹提示并跳转，不渲染内容', () => {
    makeUser('viewer')
    renderGuard()

    // 跳转到 fallback（/），页面内容不渲染
    expect(screen.queryByTestId('ops-content')).toBeNull()
    // 不再静默：明确告知原因
    const toasts = useErrorStore.getState().toasts.map((t) => `${t.type}:${t.message}`)
    expect(toasts.some((t) => t.startsWith('warning:') && t.includes('仅管理员'))).toBe(true)
  })

  it('管理员访问 → 正常渲染，不弹提示', () => {
    makeUser('admin')
    renderGuard()

    expect(screen.getByTestId('ops-content')).toBeDefined()
    expect(useErrorStore.getState().toasts).toHaveLength(0)
  })
})

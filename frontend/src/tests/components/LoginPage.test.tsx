import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import LoginPage from '../../pages/LoginPage'

// ── vi.hoisted() 工厂：mock 函数必须在 vi.mock 之前创建 ──
const {
  mockLogin,
  mockRegister,
  mockRegisterWithInvite,
  mockNavigate,
  mockIsAuthenticated,
} = vi.hoisted(() => ({
  mockLogin: vi.fn(),
  mockRegister: vi.fn(),
  mockRegisterWithInvite: vi.fn(),
  mockNavigate: vi.fn(),
  mockIsAuthenticated: { value: false },
}))

// ── mock useAuth ──
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({
    login: mockLogin,
    register: mockRegister,
    registerWithInvite: mockRegisterWithInvite,
    isAuthenticated: mockIsAuthenticated.value,
  }),
}))

// ── mock react-router-dom：保留真实实现但 override useNavigate ──
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  }
})

// ── render helper ──
function renderComponent() {
  return render(
    <MemoryRouter initialEntries={['/login']}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockIsAuthenticated.value = false
  })

  // ──────────────────────────────────────────────
  //  1. 默认渲染登录表单
  // ──────────────────────────────────────────────
  it('renders login form by default', () => {
    renderComponent()

    // 标题 & 副标题
    expect(screen.getByText('唯一的你——十四')).toBeDefined()
    expect(screen.getByText('登录管理控制台')).toBeDefined()

    // 登录输入框
    expect(screen.getByPlaceholderText('请输入邮箱或用户名')).toBeDefined()
    // 密码输入框
    expect(screen.getByPlaceholderText('输入密码')).toBeDefined()
    // 提交按钮
    expect(screen.getByRole('button', { name: '登 录' })).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  2. 切换到注册模式
  // ──────────────────────────────────────────────
  it('toggles to register mode', () => {
    renderComponent()

    fireEvent.click(screen.getByText('注册'))

    // 副标题变为"创建新账户"
    expect(screen.getByText('创建新账户')).toBeDefined()

    // 注册表单字段
    expect(screen.getByPlaceholderText('your@email.com')).toBeDefined()
    expect(screen.getByPlaceholderText('至少3个字符')).toBeDefined()
    expect(screen.getByPlaceholderText('你的昵称')).toBeDefined()

    // 注册按钮
    expect(screen.getByRole('button', { name: '注 册' })).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  3. 切换回登录模式
  // ──────────────────────────────────────────────
  it('toggles back to login mode', () => {
    renderComponent()

    fireEvent.click(screen.getByText('注册'))
    fireEvent.click(screen.getByText('登录'))

    expect(screen.getByText('登录管理控制台')).toBeDefined()
    expect(screen.getByPlaceholderText('请输入邮箱或用户名')).toBeDefined()
    expect(screen.getByRole('button', { name: '登 录' })).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  4. 注册模式显示邀请码勾选框
  // ──────────────────────────────────────────────
  it('shows invite code checkbox in register mode', () => {
    renderComponent()

    fireEvent.click(screen.getByText('注册'))

    // 勾选框存在
    const checkbox = screen.getByLabelText('我有邀请码')
    expect(checkbox).toBeDefined()

    // 勾选后显示邀请码输入
    fireEvent.click(checkbox)
    expect(screen.getByPlaceholderText('请输入8位邀请码')).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  5. 登录失败显示错误
  // ──────────────────────────────────────────────
  it('shows login error on failure', async () => {
    mockLogin.mockRejectedValueOnce({
      response: { data: { detail: '邮箱或密码错误' } },
    })

    renderComponent()

    fireEvent.change(screen.getByPlaceholderText('请输入邮箱或用户名'), {
      target: { value: 'wrong@test.com' },
    })
    fireEvent.change(screen.getByPlaceholderText('输入密码'), {
      target: { value: 'wrongpass' },
    })
    fireEvent.click(screen.getByRole('button', { name: '登 录' }))

    expect(await screen.findByText('邮箱或密码错误')).toBeDefined()
  })

  // ──────────────────────────────────────────────
  //  6. 登录成功跳转到 /wechat
  // ──────────────────────────────────────────────
  it('navigates to /wechat on successful login', async () => {
    mockLogin.mockResolvedValueOnce(undefined)

    renderComponent()

    fireEvent.change(screen.getByPlaceholderText('请输入邮箱或用户名'), {
      target: { value: 'test@test.com' },
    })
    fireEvent.change(screen.getByPlaceholderText('输入密码'), {
      target: { value: 'correct' },
    })
    fireEvent.click(screen.getByRole('button', { name: '登 录' }))

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/wechat', { replace: true })
    })
  })

  // ──────────────────────────────────────────────
  //  7. 已登录则重定向
  // ──────────────────────────────────────────────
  it('redirects when already authenticated', () => {
    mockIsAuthenticated.value = true

    const { container } = renderComponent()

    // 组件使用 <Navigate> 重定向到 /wechat，当前 Routes 没有匹配路由，container 为空
    expect(container.innerHTML).toBe('')
  })

  // ──────────────────────────────────────────────
  //  8. 提交时显示 loading 状态
  // ──────────────────────────────────────────────
  it('shows loading state during submit', async () => {
    // 永不 resolve 的 promise → loading 一直为 true
    mockLogin.mockReturnValue(new Promise(() => {}))

    renderComponent()

    fireEvent.change(screen.getByPlaceholderText('请输入邮箱或用户名'), {
      target: { value: 'test' },
    })
    fireEvent.change(screen.getByPlaceholderText('输入密码'), {
      target: { value: 'password' },
    })

    const submitBtn = screen.getByRole('button', { name: '登 录' })
    fireEvent.click(submitBtn)

    // Button 组件在 loading=true 时设置 disabled
    await waitFor(() => {
      expect(submitBtn).toBeDisabled()
    })
  })

  // ──────────────────────────────────────────────
  //  9. 使用邀请码注册提交正确数据
  // ──────────────────────────────────────────────
  it('submits register with invite code correctly', async () => {
    mockRegisterWithInvite.mockResolvedValueOnce(undefined)

    renderComponent()

    // 切换到注册模式
    fireEvent.click(screen.getByText('注册'))

    // 填写所有字段
    fireEvent.change(screen.getByPlaceholderText('your@email.com'), {
      target: { value: 'test@test.com' },
    })
    fireEvent.change(screen.getByPlaceholderText('至少3个字符'), {
      target: { value: 'testuser' },
    })
    fireEvent.change(screen.getByPlaceholderText('你的昵称'), {
      target: { value: 'Test User' },
    })
    // 注册模式下密码 placeholder 变为"至少6个字符"
    fireEvent.change(screen.getByPlaceholderText('至少6个字符'), {
      target: { value: 'password123' },
    })

    // 勾选邀请码
    fireEvent.click(screen.getByLabelText('我有邀请码'))
    // 填写邀请码
    fireEvent.change(screen.getByPlaceholderText('请输入8位邀请码'), {
      target: { value: 'ABCD1234' },
    })

    // 提交
    fireEvent.click(screen.getByRole('button', { name: '注 册' }))

    await waitFor(() => {
      expect(mockRegisterWithInvite).toHaveBeenCalledWith({
        invite_code: 'ABCD1234',
        email: 'test@test.com',
        username: 'testuser',
        password: 'password123',
        display_name: 'Test User',
      })
    })

    // 成功之后跳转
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/wechat', { replace: true })
    })
  })

  // ──────────────────────────────────────────────
  //  10. 底部「了解产品」链接指向 /intro（SP-11）
  // ──────────────────────────────────────────────
  it('has a bottom link to /intro labeled 了解产品', () => {
    renderComponent()

    const link = screen.getByRole('link', { name: '了解产品 →' })
    expect(link).toBeDefined()
    expect(link.getAttribute('href')).toBe('/intro')
  })
})

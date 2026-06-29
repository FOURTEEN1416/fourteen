import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import AdminUsersPage from '../../pages/AdminUsersPage'

// ════════════════════════════════════════════════════════════════
//  vi.hoisted() — 所有 mock fn 和常量必须在 vi.mock 工厂之前定义
// ════════════════════════════════════════════════════════════════

const {
  mockAdminListUsers,
  mockAdminCreateUser,
  mockAdminUpdateUser,
  mockAdminDeleteUser,
  mockAddToast,
  mockAdminUser,
  authStoreState,
  FAKE_USERS,
} = vi.hoisted(() => {
  const mockAdminUser = {
    id: 1,
    email: 'admin@test.com',
    username: 'admin',
    display_name: 'Admin',
    avatar_url: '',
    role: 'admin' as const,
    is_active: true,
    is_verified: true,
    created_at: '2026-01-01T00:00:00Z',
    last_login_at: null,
  }

  const FAKE_USERS = {
    users: [
      {
        id: 1,
        email: 'admin@test.com',
        username: 'admin',
        display_name: 'Admin User',
        role: 'admin' as const,
        is_active: true,
        is_verified: true,
        created_at: '2026-01-01T00:00:00Z',
        last_login_at: '2026-06-01T00:00:00Z',
        avatar_url: '',
      },
      {
        id: 2,
        email: 'editor@test.com',
        username: 'editor',
        display_name: 'Editor User',
        role: 'editor' as const,
        is_active: true,
        is_verified: true,
        created_at: '2026-02-01T00:00:00Z',
        last_login_at: null,
        avatar_url: '',
      },
      {
        id: 3,
        email: 'viewer@test.com',
        username: 'viewer',
        display_name: 'Viewer User',
        role: 'viewer' as const,
        is_active: false,
        is_verified: false,
        created_at: '2026-03-01T00:00:00Z',
        last_login_at: null,
        avatar_url: '',
      },
    ],
    total: 3,
  }

  return {
    mockAdminListUsers: vi.fn(),
    mockAdminCreateUser: vi.fn(),
    mockAdminUpdateUser: vi.fn(),
    mockAdminDeleteUser: vi.fn(),
    mockAddToast: vi.fn(),
    mockAdminUser,
    authStoreState: { user: mockAdminUser },
    FAKE_USERS,
  }
})

// ════════════════════════════════════════════════════════════════
//  Module mocks（工厂函数访问 hoisted 变量）
// ════════════════════════════════════════════════════════════════

vi.mock('../../api/admin', () => ({
  adminListUsers: (...args: unknown[]) => mockAdminListUsers(...args),
  adminCreateUser: (...args: unknown[]) => mockAdminCreateUser(...args),
  adminUpdateUser: (...args: unknown[]) => mockAdminUpdateUser(...args),
  adminDeleteUser: (...args: unknown[]) => mockAdminDeleteUser(...args),
}))

vi.mock('../../store/authStore', () => ({
  useAuthStore: vi.fn((selector?: (s: unknown) => unknown) =>
    selector ? selector(authStoreState) : authStoreState,
  ),
}))

vi.mock('../../store/errorStore', () => ({
  useErrorStore: vi.fn((selector?: (s: unknown) => unknown) => {
    const state = { addToast: mockAddToast }
    return selector ? selector(state) : state
  }),
}))

// ════════════════════════════════════════════════════════════════
//  Helpers
// ════════════════════════════════════════════════════════════════

function renderComponent() {
  return render(
    <MemoryRouter>
      <AdminUsersPage />
    </MemoryRouter>,
  )
}

/** 生成分页测试用的大批量用户数据 */
function generateUsersPage(pageNum: number, totalUsers: number): { users: unknown[]; total: number } {
  const perPage = 20
  const start = (pageNum - 1) * perPage + 1
  const end = Math.min(start + perPage - 1, totalUsers)
  const users = []
  for (let i = start; i <= end; i++) {
    users.push({
      id: i,
      email: `user${i}@test.com`,
      username: `user${i}`,
      display_name: `User ${i}`,
      role: i === 1 ? ('admin' as const) : ('editor' as const),
      is_active: true,
      is_verified: true,
      created_at: '2026-01-01T00:00:00Z',
      last_login_at: null,
      avatar_url: '',
    })
  }
  return { users, total: totalUsers }
}

/** 等待表格数据渲染（桌面或移动视图至少有一个匹配） */
async function waitForUserData(text: string) {
  await waitFor(() => {
    expect(screen.getAllByText(text).length).toBeGreaterThan(0)
  })
}

// ════════════════════════════════════════════════════════════════
//  Test Suite
// ════════════════════════════════════════════════════════════════

describe('AdminUsersPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 重置 authStore 状态为 admin
    authStoreState.user = mockAdminUser
    // 默认 mock 返回 3 个用户
    mockAdminListUsers.mockResolvedValue(FAKE_USERS)
  })

  // ────────────────────────────────────────────────
  //  1. 非 admin 用户显示「无权限访问」
  // ────────────────────────────────────────────────
  it('renders "无权限访问" empty state when current user is not admin', async () => {
    authStoreState.user = {
      ...mockAdminUser,
      role: 'viewer' as 'admin',
    }
    renderComponent()

    expect(screen.getByText('无权限访问')).toBeDefined()
    expect(screen.getByText('仅管理员可访问系统用户管理页面')).toBeDefined()
    // 非 admin 不会调用列表 API
    expect(mockAdminListUsers).not.toHaveBeenCalled()
  })

  // ────────────────────────────────────────────────
  //  2. admin 用户渲染用户表格
  // ────────────────────────────────────────────────
  it('renders user table when admin', async () => {
    renderComponent()

    // 标题
    expect(screen.getByText('系统用户管理')).toBeDefined()

    // 等待列表加载（桌面 table + 移动 card 各渲染一次，用 getAllByText）
    await waitForUserData('Admin User')
    expect(screen.getAllByText('Admin User').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Editor User').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Viewer User').length).toBeGreaterThan(0)

    // 总用户数 — 匹配 "共 3 条" 格式
    expect(screen.getByText(/共\s*3\s*条/)).toBeDefined()
    // API 被调用
    expect(mockAdminListUsers).toHaveBeenCalled()
  })

  // ────────────────────────────────────────────────
  //  3. 加载中显示骨架屏
  // ────────────────────────────────────────────────
  it('shows loading skeleton during fetch', () => {
    mockAdminListUsers.mockReturnValue(new Promise(() => {})) // 永不 resolve
    const { container } = renderComponent()

    const skeletonEls = container.querySelectorAll('.animate-skeleton')
    expect(skeletonEls.length).toBeGreaterThan(0)
  })

  // ────────────────────────────────────────────────
  //  4. 加载失败显示错误信息
  // ────────────────────────────────────────────────
  it('shows error message when fetch fails', async () => {
    mockAdminListUsers.mockRejectedValue(new Error('网络错误'))
    renderComponent()

    await waitFor(() => {
      expect(screen.getByText('加载失败')).toBeDefined()
    })
    expect(screen.getByText('网络错误')).toBeDefined()
    // 重试按钮
    expect(screen.getByText('重试')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  5. 搜索框带 400ms 防抖
  // ────────────────────────────────────────────────
  it('search box filters with 400ms debounce', async () => {
    renderComponent()

    await waitForUserData('Admin User')

    // 清除初始调用计数
    mockAdminListUsers.mockClear()

    const searchInput = screen.getByPlaceholderText('搜索邮箱、用户名或显示名称...')
    fireEvent.change(searchInput, { target: { value: 'editor' } })

    // 防抖期间不立即调用
    expect(mockAdminListUsers).not.toHaveBeenCalled()

    // 等待防抖 + 重新拉取
    await waitFor(() => {
      expect(mockAdminListUsers).toHaveBeenCalled()
    })

    // 验证调用参数中包含了搜索词
    const lastCall = mockAdminListUsers.mock.lastCall
    expect(lastCall?.[2]).toBe('editor')
  })

  // ────────────────────────────────────────────────
  //  6. 角色过滤下拉
  // ────────────────────────────────────────────────
  it('role filter dropdown works', async () => {
    renderComponent()

    await waitForUserData('Admin User')

    mockAdminListUsers.mockClear()

    // 选择「管理员」过滤（取第一个 combobox = 角色过滤下拉）
    const select = screen.getAllByRole('combobox')[0]
    fireEvent.change(select, { target: { value: 'admin' } })

    await waitFor(() => {
      expect(mockAdminListUsers).toHaveBeenCalled()
    })

    const lastCall = mockAdminListUsers.mock.lastCall
    expect(lastCall?.[3]).toBe('admin')
  })

  // ────────────────────────────────────────────────
  //  7. 点击「创建用户」按钮打开创建弹窗
  // ────────────────────────────────────────────────
  it('click "创建用户" button opens create modal', async () => {
    renderComponent()

    await waitForUserData('Admin User')

    // 点击创建用户按钮（用 role 定位，唯一匹配）
    fireEvent.click(screen.getByRole('button', { name: '创建用户' }))

    // Modal 中出现 form field 标签（用"密码"确认弹窗已打开）
    await waitFor(() => {
      expect(screen.getByText('密码')).toBeDefined()
    })
    // "密码"已确认弹窗打开，不重复校验"显示名称"（table header 也有此列）
  })

  // ────────────────────────────────────────────────
  //  8. 提交创建表单
  // ────────────────────────────────────────────────
  it('submit create form with valid data calls adminCreateUser', async () => {
    mockAdminCreateUser.mockResolvedValue({ id: 99 } as unknown)
    renderComponent()

    await waitForUserData('Admin User')

    // 打开弹窗
    fireEvent.click(screen.getByRole('button', { name: '创建用户' }))

    // 填写表单
    const emailInput = screen.getByPlaceholderText('user@example.com')
    const usernameInput = screen.getByPlaceholderText('username')
    const passwordInput = screen.getByPlaceholderText('至少 6 个字符')

    fireEvent.change(emailInput, { target: { value: 'newuser@test.com' } })
    fireEvent.change(usernameInput, { target: { value: 'newuser' } })
    fireEvent.change(passwordInput, { target: { value: 'password123' } })

    // 点击创建提交按钮（accessible name "创建"，与 header 按钮 "创建用户" 不同）
    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(mockAdminCreateUser).toHaveBeenCalledWith({
        email: 'newuser@test.com',
        username: 'newuser',
        password: 'password123',
        display_name: '',
        role: 'viewer',
      })
    })

    expect(mockAddToast).toHaveBeenCalledWith({ type: 'success', message: '用户创建成功' })
  })

  // ────────────────────────────────────────────────
  //  9. 表单验证错误
  // ────────────────────────────────────────────────
  it('shows validation errors in create form', async () => {
    renderComponent()

    await waitForUserData('Admin User')

    // 打开弹窗（用"密码"避免与 table header 的"邮箱"冲突）
    fireEvent.click(screen.getByRole('button', { name: '创建用户' }))
    await waitFor(() => {
      expect(screen.getByText('密码')).toBeDefined()
    })

    // ── 9a. 空邮箱 → "邮箱不能为空"
    const usernameInput = screen.getByPlaceholderText('username')
    const passwordInput = screen.getByPlaceholderText('至少 6 个字符')
    fireEvent.change(usernameInput, { target: { value: 'testuser' } })
    fireEvent.change(passwordInput, { target: { value: 'password123' } })

    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(screen.getByText('邮箱不能为空')).toBeDefined()
    })

    // ── 9b. 无效邮箱 "abc" → "邮箱格式不正确"
    const emailInput = screen.getByPlaceholderText('user@example.com')
    fireEvent.change(emailInput, { target: { value: 'abc' } })

    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(screen.getByText('邮箱格式不正确')).toBeDefined()
    })

    // ── 9c. 用户名 "ab" → "用户名至少 3 个字符"
    fireEvent.change(emailInput, { target: { value: 'valid@test.com' } })
    fireEvent.change(usernameInput, { target: { value: 'ab' } })

    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(screen.getByText('用户名至少 3 个字符')).toBeDefined()
    })

    // ── 9d. 密码 "123" → "密码至少 6 个字符"
    fireEvent.change(usernameInput, { target: { value: 'validuser' } })
    fireEvent.change(passwordInput, { target: { value: '123' } })

    fireEvent.click(screen.getByRole('button', { name: '创建' }))

    await waitFor(() => {
      expect(screen.getByText('密码至少 6 个字符')).toBeDefined()
    })
  })

  // ────────────────────────────────────────────────
  //  10. 点击编辑按钮 → 编辑弹窗带预填数据
  // ────────────────────────────────────────────────
  it('click edit button opens edit modal with pre-filled data', async () => {
    renderComponent()

    await waitForUserData('Admin User')

    // 直接用 userEvent 试试
    const editButtons = screen.getAllByTitle('编辑用户')
    expect(editButtons.length).toBeGreaterThan(0)

    // 用唯一 email 定位 Admin User 行的编辑按钮
    // admin@test.com 在 desktop table td 和 mobile card p 中各出现一次
    // 取 closest('tr') 非空的 = desktop table
    const adminEmail = screen.getAllByText('admin@test.com').find(el => el.closest('tr'))
    expect(adminEmail).toBeDefined()
    const adminRow = adminEmail!.closest('tr')!
    const adminEditBtn = within(adminRow).getByTitle('编辑用户')
    act(() => { fireEvent.click(adminEditBtn) })

    // 弹窗标题包含用户名
    await waitFor(() => {
      expect(screen.getByText(/编辑用户.*Admin User/i)).toBeDefined()
    })

    // Email 字段预填
    const emailInput = screen.getByDisplayValue('admin@test.com')
    expect(emailInput).toBeDefined()
    expect(screen.getByDisplayValue('admin')).toBeDefined()
    // 保存按钮
    expect(screen.getByRole('button', { name: '保存' })).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  11. 提交编辑表单
  // ────────────────────────────────────────────────
  it('submit edit form calls adminUpdateUser', async () => {
    mockAdminUpdateUser.mockResolvedValue({} as unknown)
    renderComponent()

    await waitForUserData('Admin User')

    // 点击编辑（模式同 test #10）
    const adminEmail = screen.getAllByText('admin@test.com').find(el => el.closest('tr'))
    expect(adminEmail).toBeDefined()
    const adminRow = adminEmail!.closest('tr')!
    const adminEditBtn = within(adminRow).getByTitle('编辑用户')
    act(() => { fireEvent.click(adminEditBtn) })

    await waitFor(() => {
      expect(screen.getByText(/编辑用户.*Admin User/i)).toBeDefined()
    })

    // 修改邮箱
    const emailInput = screen.getByDisplayValue('admin@test.com')
    fireEvent.change(emailInput, { target: { value: 'updated@test.com' } })

    // 点击保存
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => {
      expect(mockAdminUpdateUser).toHaveBeenCalled()
    })

    const updateCall = mockAdminUpdateUser.mock.lastCall
    expect(updateCall?.[0]).toBe(1) // userId
    expect(updateCall?.[1].email).toBe('updated@test.com')
    expect(mockAddToast).toHaveBeenCalledWith({ type: 'success', message: '用户更新成功' })
  })

  // ────────────────────────────────────────────────
  //  12. 点击删除按钮 → 确认弹窗
  // ────────────────────────────────────────────────
  it('click delete button opens confirm dialog', async () => {
    renderComponent()

    await waitForUserData('Admin User')

    // 找到第二个删除按钮（editor 用户，id=2，不是当前 admin）
    const deleteButtons = screen.getAllByTitle('删除用户')
    expect(deleteButtons.length).toBeGreaterThan(1)
    fireEvent.click(deleteButtons[1])

    // 确认弹窗出现
    await waitFor(() => {
      expect(screen.getByText('确认删除用户')).toBeDefined()
    })
    expect(screen.getByText('删除')).toBeDefined() // confirmText="删除"
    expect(screen.getByText('取消')).toBeDefined()
  })

  // ────────────────────────────────────────────────
  //  13. 确认删除调用 adminDeleteUser
  // ────────────────────────────────────────────────
  it('confirm delete calls adminDeleteUser', async () => {
    mockAdminDeleteUser.mockResolvedValue({ status: 'ok', user_id: 2 } as unknown)
    renderComponent()

    await waitForUserData('Admin User')

    // 删除第二个用户（editor, id=2）
    const deleteButtons = screen.getAllByTitle('删除用户')
    fireEvent.click(deleteButtons[1])

    await waitFor(() => {
      expect(screen.getByText('确认删除用户')).toBeDefined()
    })

    // 点击确认（confirmText="删除"）
    fireEvent.click(screen.getByText('删除'))

    await waitFor(() => {
      expect(mockAdminDeleteUser).toHaveBeenCalledWith(2)
    })

    expect(mockAddToast).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'success' }),
    )
  })

  // ────────────────────────────────────────────────
  //  14. 分页按钮
  // ────────────────────────────────────────────────
  it('pagination buttons (next/prev) work', async () => {
    // Mock 返回 2 页数据
    mockAdminListUsers.mockResolvedValue(generateUsersPage(1, 25))
    renderComponent()

    // 等待初始数据加载
    await waitForUserData('User 1')

    // 页码 1 + 2（用 button 角色精确定位分页按钮，避免误中表格里的用户 ID）
    const pageButtons = screen.getAllByRole('button').filter(b => /^[12]$/.test(b.textContent?.trim() ?? ''))
    expect(pageButtons.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText(/共\s*25\s*条/)).toBeDefined()

    mockAdminListUsers.mockClear()

    // 点击第 2 页（精确定位分页按钮）
    const nextPageBtn = screen.getAllByRole('button').find(b => b.textContent?.trim() === '2')
    expect(nextPageBtn).toBeDefined()
    fireEvent.click(nextPageBtn!)

    await waitFor(() => {
      expect(mockAdminListUsers).toHaveBeenCalled()
    })

    const pageCall = mockAdminListUsers.mock.lastCall
    expect(pageCall?.[0]).toBe(2)
  })
})

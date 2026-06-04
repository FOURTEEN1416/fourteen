/**
 * 端到端完整用户旅程测试
 *
 * 覆盖 4 个核心场景：
 *   1. full user journey  — 注册 → 查看绑定微信 → 进入绑定详情
 *   2. unauthenticated    — 未登录访问受保护页面被重定向
 *   3. search             — 微信绑定搜索按昵称过滤
 *   4. admin role guard   — 非管理员无法访问后台
 *
 * 所有 API 调用均用 mock 模式，不依赖后端在 :8000 运行。
 */
import { test, expect, type Page } from '@playwright/test'

// ── Mock 数据 ──────────────────────────────────────

const MOCK_TOKEN_RESPONSE = {
  access_token: 'mock-access-token-e2e',
  refresh_token: 'mock-refresh-token-e2e',
  token_type: 'bearer',
  user: {
    id: 999,
    email: 'e2e@test.com',
    username: 'e2euser',
    display_name: 'E2E User',
    avatar_url: '',
    role: 'admin' as const,
    is_active: true,
    is_verified: true,
    created_at: '2026-06-02T00:00:00Z',
    last_login_at: null,
  },
}

const MOCK_VIEWER_TOKEN_RESPONSE = {
  ...MOCK_TOKEN_RESPONSE,
  user: { ...MOCK_TOKEN_RESPONSE.user, role: 'viewer' as const },
}

const MOCK_WECHAT_STATUS = {
  connected: false,
  uptime_seconds: 0,
  messages_today: 0,
  reconnect_attempts: 0,
  last_activity: null,
  qr_code: null,
}

const SAMPLE_BINDINGS = [
  {
    id: 1,
    user_id: 16,
    wxid: 'wx_test_001',
    nickname: '小花',
    avatar: '',
    character_card_id: 'gentle_teacher',
    bound_at: '2026-06-04T00:00:00Z',
  },
  {
    id: 2,
    user_id: 16,
    wxid: 'wx_test_002',
    nickname: '小明',
    avatar: '',
    character_card_id: 'default',
    bound_at: '2026-06-04T00:00:00Z',
  },
]

const SEARCH_BINDINGS = [
  {
    id: 1, user_id: 16, wxid: 'wx_alice_001', nickname: 'Alice',
    avatar: '', character_card_id: 'default', bound_at: '2026-06-04T00:00:00Z',
  },
  {
    id: 2, user_id: 16, wxid: 'wx_bob_002', nickname: 'Bob',
    avatar: '', character_card_id: 'default', bound_at: '2026-06-04T00:00:00Z',
  },
  {
    id: 3, user_id: 16, wxid: 'wx_charlie_003', nickname: 'Charlie',
    avatar: '', character_card_id: 'default', bound_at: '2026-06-04T00:00:00Z',
  },
]

const SAMPLE_PRESETS = [
  {
    id: 'gentle_teacher', name: '温柔老师', description: '一位耐心的老师',
    tags: ['温柔'], has_first_mes: true, has_scenario: true, char_count: 1200,
  },
  {
    id: 'tsundere', name: '傲娇女友', description: '可爱的女友',
    tags: ['傲娇'], has_first_mes: true, has_scenario: true, char_count: 800,
  },
]

// ── Helpers ────────────────────────────────────────

/** 拦截 POST /api/auth/register 和 /api/auth/login */
async function mockAuth(page: Page, tokenResponse = MOCK_TOKEN_RESPONSE) {
  await page.route('**/api/auth/register', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(tokenResponse),
    })
  })
  await page.route('**/api/auth/login', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(tokenResponse),
    })
  })
}

/** 拦截 POST /api/auth/refresh — 用于页面刷新后恢复登录状态 */
async function mockRefreshToken(page: Page, tokenResponse = MOCK_TOKEN_RESPONSE) {
  await page.route('**/api/auth/refresh', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(tokenResponse),
    })
  })
}

/** 拦截 GET /api/channels/wechat/status */
async function mockWechatStatus(page: Page) {
  await page.route('**/api/channels/wechat/status', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_WECHAT_STATUS),
    })
  })
}

/** 拦截 GET /api/wechat/bindings */
async function mockListBindings(page: Page, bindings: any[] = []) {
  await page.route('**/api/wechat/bindings', async (route) => {
    // Only intercept GET (list), not PUT/DELETE on specific bindings
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ bindings, total: bindings.length }),
      })
    } else {
      await route.fallback()
    }
  })
}

/** 拦截 GET /api/presets */
async function mockListPresets(page: Page, presets: any[] = []) {
  await page.route('**/api/presets', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ presets, total: presets.length }),
    })
  })
}

/** 拦截 GET /api/dashboard */
async function mockDashboard(page: Page) {
  await page.route('**/api/dashboard*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        today_chats: 12, recent_memories: 4, affinity: 65, energy: 80,
        current_emotion: 'happy', system_status: 'healthy', uptime_seconds: 3600,
        wechat_connected: true, wechat: {}, training: { status: 'idle', progress: 0, loss: 0 },
      }),
    })
  })
}

/** 执行注册流程（通过 UI 操作） */
async function performRegister(page: Page) {
  await page.goto('/login', { waitUntil: 'networkidle' })
  await page.waitForSelector('h1', { timeout: 15000 })

  // 切换到注册模式
  await page.locator('button:has-text("注册")').click()
  await expect(page.locator('text=创建新账户')).toBeVisible()

  // 填写注册表单
  await page.locator('input[type="email"]').fill('e2e@test.com')
  await page.locator('input[placeholder="至少3个字符"]').fill('e2euser')
  await page.locator('input[type="password"]').fill('password123')

  // 提交注册
  await page.locator('button[type="submit"]').click()
}

// ── Tests ──────────────────────────────────────────

test.describe('Full User Flow', () => {

  test('full user journey: register → bind → view characters', async ({ page }) => {
    // ── 设置所有 mock ──
    await mockAuth(page)
    await mockRefreshToken(page)
    await mockWechatStatus(page)
    await mockListBindings(page, SAMPLE_BINDINGS)
    await mockListPresets(page, SAMPLE_PRESETS)

    // ── 注册新账号 ──
    await performRegister(page)

    // 等待跳转到 /wechat（受保护页面）
    await page.waitForURL(/\/wechat/, { timeout: 15000 })
    await page.waitForLoadState('networkidle')

    // ── 导航到 /users（我的微信） ──
    // 使用 goto 触发完整页面加载；mockRefreshToken 确保登录状态恢复
    await page.goto('/users', { waitUntil: 'networkidle' })
    await page.waitForSelector('h1', { timeout: 15000 })

    // 验证标题
    await expect(page.locator('h1')).toContainText('我的微信')

    // 验证 2 个绑定卡片可见（昵称显示在卡片中）
    await expect(page.locator('text=小花')).toBeVisible()
    await expect(page.locator('text=小明')).toBeVisible()

    // ── 点击第一个绑定卡片 ──
    // 卡片是 <button> 元素，包含昵称文本
    await page.locator('button:has-text("小花")').click()

    // 应导航到 /bindings/wx_test_001
    await page.waitForURL(/\/bindings\/wx_test_001/, { timeout: 15000 })
    await page.waitForLoadState('networkidle')

    // 验证绑定详情页面已加载（heading 显示昵称）
    await expect(page.locator('h1')).toContainText('小花')

    // 验证 wxid 显示在页面上
    await expect(page.locator('text=wx_test_001')).toBeVisible()
  })

  test('unauthenticated user is redirected from protected pages', async ({ page }) => {
    // 不设置任何 auth mock → 用户未登录

    // 访问 /users → 应重定向到 /login
    await page.goto('/users', { waitUntil: 'networkidle' })
    await page.waitForURL(/\/login/, { timeout: 15000 })
    await expect(page.locator('h1')).toContainText('唯一的你')

    // 访问 /wechat → 应重定向到 /login
    await page.goto('/wechat', { waitUntil: 'networkidle' })
    await page.waitForURL(/\/login/, { timeout: 15000 })
    await expect(page.locator('h1')).toContainText('唯一的你')
  })

  test('binding search filters by nickname', async ({ page }) => {
    // ── 设置 mock ──
    await mockAuth(page)
    await mockRefreshToken(page)
    await mockWechatStatus(page)
    await mockListBindings(page, SEARCH_BINDINGS)

    // ── 注册 ──
    await performRegister(page)
    await page.waitForURL(/\/wechat/, { timeout: 15000 })

    // ── 导航到 /users ──
    await page.goto('/users', { waitUntil: 'networkidle' })
    await page.waitForSelector('h1', { timeout: 15000 })

    // 确认 3 个绑定都可见
    await expect(page.locator('text=Alice')).toBeVisible()
    await expect(page.locator('text=Bob')).toBeVisible()
    await expect(page.locator('text=Charlie')).toBeVisible()

    // ── 搜索 "ali" ──
    const searchInput = page.locator('input[placeholder="搜索微信昵称或 wxid..."]')
    await searchInput.fill('ali')

    // 只有 Alice 可见
    await expect(page.locator('text=Alice')).toBeVisible()
    await expect(page.locator('text=Bob')).not.toBeVisible()
    await expect(page.locator('text=Charlie')).not.toBeVisible()
  })

  test('admin page requires admin role', async ({ page }) => {
    // 使用 viewer role 的 mock 登录
    await mockAuth(page, MOCK_VIEWER_TOKEN_RESPONSE)
    await mockRefreshToken(page, MOCK_VIEWER_TOKEN_RESPONSE)
    await mockWechatStatus(page)

    // ── 注册 ──
    await performRegister(page)
    await page.waitForURL(/\/wechat/, { timeout: 15000 })

    // ── 导航到 /admin/users（需要 admin 角色） ──
    await page.goto('/admin/users', { waitUntil: 'networkidle' })

    // RoleGuard 会将 viewer 重定向到 '/' → 再重定向到 '/wechat'
    await page.waitForURL(/\/wechat/, { timeout: 15000 })
    await page.waitForLoadState('networkidle')

    // 确认落在 /wechat 页面上
    await expect(page.locator('h1')).toContainText('微信接入')
  })
})

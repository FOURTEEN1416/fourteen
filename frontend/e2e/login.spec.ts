/**
 * 端到端登录冒烟测试
 *
 * 覆盖 3 个核心场景：
 *   1. login page renders        — 页面渲染正确性
 *   2. register + login flow     — 注册新账号 → 跳转到 /wechat
 *   3. login by existing account — 登录 → 访问受保护页面
 *
 * 所有 API 调用均用 mock 模式（拦截 /api/auth/* /api/channels/*），
 * 不依赖后端在 :8000 运行。
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

const MOCK_WECHAT_STATUS = {
  connected: false,
  uptime_seconds: 0,
  messages_today: 0,
  reconnect_attempts: 0,
  last_activity: null,
  qr_code: null,
}

// ── Helpers ────────────────────────────────────────

/** 拦截 POST /api/auth/register 和 /api/auth/login */
async function mockAuth(page: Page) {
  await page.route('**/api/auth/register', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_TOKEN_RESPONSE),
    })
  })
  await page.route('**/api/auth/login', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_TOKEN_RESPONSE),
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

// ── Tests ──────────────────────────────────────────

test.describe('Login Smoke Tests', () => {

  test('login page renders', async ({ page }) => {
    await page.goto('/login', { waitUntil: 'networkidle' })

    // 等待 React 挂载完成
    await page.waitForSelector('h1', { timeout: 15000 })

    // 1) 页面标题
    await expect(page.locator('h1')).toContainText('唯一的你')
    await expect(page.locator('text=登录管理控制台')).toBeVisible()

    // 2) 登录表单字段
    await expect(page.locator('input[type="text"]')).toBeVisible()   // 邮箱/用户名
    await expect(page.locator('input[type="password"]')).toBeVisible() // 密码

    // 3) 切换注册模式链接
    await expect(page.locator('button:has-text("注册")')).toBeVisible()
  })

  test('register + login flow', async ({ page }) => {
    // 拦截 API 调用
    await mockAuth(page)
    await mockWechatStatus(page)

    // 访问登录页
    await page.goto('/login')
    await page.waitForLoadState('networkidle')

    // 切换到注册模式
    await page.locator('button:has-text("注册")').click()
    await expect(page.locator('text=创建新账户')).toBeVisible()

    // 填写注册表单
    await page.locator('input[type="email"]').fill('e2e@test.com')
    await page.locator('input[placeholder="至少3个字符"]').fill('e2euser')
    await page.locator('input[type="password"]').fill('password123')

    // 提交注册
    await page.locator('button[type="submit"]').click()

    // 等待跳转到 /wechat（受保护页面）
    await page.waitForURL(/\/wechat/, { timeout: 15000 })
    await page.waitForLoadState('networkidle')

    // 确认受保护页面已加载
    await expect(page.locator('h1')).toContainText('微信接入')
  })

  test('login by existing account', async ({ page }) => {
    // 拦截 API 调用
    await mockAuth(page)
    await mockWechatStatus(page)

    // 访问登录页
    await page.goto('/login')
    await page.waitForLoadState('networkidle')

    // 填写登录表单
    await page.locator('input[type="text"]').fill('e2e@test.com')
    await page.locator('input[type="password"]').fill('password123')

    // 提交登录
    await page.locator('button[type="submit"]').click()

    // 等待跳转到 /wechat
    await page.waitForURL(/\/wechat/, { timeout: 15000 })
    await page.waitForLoadState('networkidle')

    // 确认受保护页面已加载
    await expect(page.locator('h1')).toContainText('微信接入')
  })
})

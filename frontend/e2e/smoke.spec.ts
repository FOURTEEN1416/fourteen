/**
 * SP-12 E2E 冒烟 —— 登录 → 核心页面渲染 → 聊天 API 主链路。
 * 前置：scripts/e2e_setup.py 已建种子库；后端 APP_DATABASE_URL 指向 e2e 库、API_KEY_ENABLED=false。
 * 凭证经环境变量注入（默认对齐种子值）。
 */
import { test, expect } from '@playwright/test'

const ADMIN = process.env.E2E_ADMIN ?? 'e2e-admin@test.local'
const PASSWORD = process.env.E2E_PASSWORD ?? 'E2eTest#2026'

test.describe('SP-12 冒烟', () => {
  test('登录页：账号密码登录成功并跳转 /wechat', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByText('唯一的你')).toBeVisible()

    // 切到登录 tab（默认可能是注册）；两个 tab 都在页面上，按可见输入填充
    const emailInput = page.locator('input[type="email"], input[placeholder*="邮箱"]').first()
    await emailInput.fill(ADMIN)
    const pwdInput = page.locator('input[type="password"]').first()
    await pwdInput.fill(PASSWORD)
    await page.getByRole('button', { name: '登 录', exact: true }).click()

    await page.waitForURL('**/wechat', { timeout: 15_000 })
    await expect(page.getByRole('heading', { name: '微信接入' })).toBeVisible()
  })

  test('角色页：卡片网格渲染（文件系统角色库）', async ({ page }) => {
    // API 登录取 token，注入 localStorage（前端 authStore 持久化位置以实际实现为准——
    // 退路：直接走 UI 登录）
    await page.goto('/login')
    const emailInput = page.locator('input[type="email"], input[placeholder*="邮箱"]').first()
    await emailInput.fill(ADMIN)
    await page.locator('input[type="password"]').first().fill(PASSWORD)
    await page.getByRole('button', { name: '登 录', exact: true }).click()
    await page.waitForURL('**/wechat', { timeout: 15_000 })

    await page.goto('/roles')
    await expect(page.getByRole('heading', { name: '角色配置' })).toBeVisible({ timeout: 15_000 })
    // 本地角色库 53 张，网格应有内容；至少「创建角色」入口存在
    await expect(page.getByText('创建角色').first()).toBeVisible()
  })

  test('设置页：LLM 供应商清单真实加载（API 集成冒烟）', async ({ page }) => {
    await page.goto('/login')
    await page.locator('input[type="email"], input[placeholder*="邮箱"]').first().fill(ADMIN)
    await page.locator('input[type="password"]').first().fill(PASSWORD)
    await page.getByRole('button', { name: '登 录', exact: true }).click()
    await page.waitForURL('**/wechat', { timeout: 15_000 })

    await page.goto('/settings/llm')
    await expect(page.getByText('供应商').first()).toBeVisible({ timeout: 15_000 })
  })

  test('聊天主链路：POST /api/chat 返回回复（LLM 真实调用，慢容差）', async ({ request }) => {
    // API 级聊天冒烟：登录换 token → chat 端点
    const login = await request.post('/api/auth/login', {
      data: { login: ADMIN, password: PASSWORD },
    })
    expect(login.ok()).toBeTruthy()
    const { access_token: accessToken } = await login.json()

    const chat = await request.post('/api/chat', {
      headers: { Authorization: `Bearer ${accessToken}` },
      data: { message: '你好，冒烟测试', session_id: 'e2e-smoke' },
      timeout: 60_000,
    })
    expect(chat.status()).toBe(200)
    const body = await chat.json()
    expect((body.reply ?? '').length).toBeGreaterThan(0)
  })

  test('健康探针：/api/health 200（无认证）', async ({ request }) => {
    const res = await request.get('/api/health')
    expect(res.ok()).toBeTruthy()
    const body = await res.json()
    expect(body.status).toBe('ok')
  })
})

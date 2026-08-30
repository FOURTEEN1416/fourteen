/**
 * W5-mobile E2E —— 375px 移动端视口冒烟。
 * 前置：与 smoke.spec.ts 相同（scripts/e2e_setup.py 种子库 + 后端 8000 + vite 5199）。
 * 覆盖：移动视口渲染无横向溢出 / 桌面侧栏隐藏+汉堡抽屉开合与导航跳转 / 设置页溢出检查。
 */
import { test, expect, type Page } from '@playwright/test'

const ADMIN = process.env.E2E_ADMIN ?? 'e2e-admin@test.local'
const PASSWORD = process.env.E2E_PASSWORD ?? 'E2eTest#2026'

// iPhone SE / 主流安卓竖屏宽度
test.use({ viewport: { width: 375, height: 667 } })

/** 无横向溢出断言：文档滚动宽度不超过视口（±1px 子像素容差） */
async function expectNoHorizontalOverflow(page: Page) {
  const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(scrollWidth).toBeLessThanOrEqual(376)
}

/** UI 登录（与 smoke 同路径） */
async function loginViaUI(page: Page) {
  await page.goto('/login')
  await page.locator('input[type="email"], input[placeholder*="邮箱"]').first().fill(ADMIN)
  await page.locator('input[type="password"]').first().fill(PASSWORD)
  await page.getByRole('button', { name: '登 录', exact: true }).click()
  await page.waitForURL('**/wechat', { timeout: 15_000 })
}

/** 已知竞态（W4 登记）：vite dev StrictMode 双发 /auth/refresh × 旋转式 session 偶发 401 弹回 /login → 重登一次再跳 */
async function gotoAuthed(page: Page, path: string) {
  await page.goto(path)
  if (page.url().includes('/login')) {
    await loginViaUI(page)
    await page.goto(path)
  }
}

test.describe('W5-mobile 375px 冒烟', () => {

  test('介绍页：375px 渲染且无横向溢出', async ({ page }) => {
    await page.goto('/intro')
    await expect(page.getByText('唯一的你').first()).toBeVisible()
    await expectNoHorizontalOverflow(page)
  })

  test('登录页：375px 渲染且无横向溢出', async ({ page }) => {
    await page.goto('/login')
    await expect(page.getByText('唯一的你')).toBeVisible()
    await expectNoHorizontalOverflow(page)
  })

  test('移动导航：桌面侧栏隐藏 + 汉堡抽屉开合跳转', async ({ page }) => {
    await loginViaUI(page)

    // 桌面 Sidebar（无 role 的 aside）在 375px 隐藏（<md 抽屉化）
    const desktopSidebar = page.locator('aside:not([role="dialog"])')
    await expect(desktopSidebar).toBeHidden()

    // 汉堡按钮唤起抽屉
    const hamburger = page.getByRole('button', { name: '打开菜单' })
    await expect(hamburger).toBeVisible()
    await hamburger.click()

    const drawer = page.getByRole('dialog', { name: '导航菜单' })
    await expect(drawer).toBeVisible()
    // 打开状态：面板滑入（translate-x-0）
    await expect(drawer).toHaveClass(/translate-x-0/)

    // 抽屉内导航跳转并自动关闭（关闭后 aria-hidden 生效，role 查询不可见 → 用 locator 断言 class）
    await drawer.getByRole('link', { name: '角色配置' }).click()
    await page.waitForURL('**/roles', { timeout: 15_000 })
    const drawerPanel = page.locator('aside[aria-label="导航菜单"]')
    await expect(drawerPanel).toHaveClass(/-translate-x-full/)

    await expectNoHorizontalOverflow(page)
  })

  test('平板 800px：左侧固定侧栏恢复可见，汉堡按钮隐藏', async ({ page }) => {
    // ≥md(768) 回到左侧固定布局；汉堡/抽屉仅 <md 存在
    await page.setViewportSize({ width: 800, height: 1024 })
    await loginViaUI(page)
    const desktopSidebar = page.locator('aside:not([role="dialog"])')
    await expect(desktopSidebar).toBeVisible()
    await expect(page.getByRole('button', { name: '打开菜单' })).toBeHidden()
  })

  test('设置页：375px 渲染无横向溢出（SettingsLLM 连接参数堆叠）', async ({ page }) => {
    await loginViaUI(page)
    await gotoAuthed(page, '/settings/llm')
    await expect(page.getByRole('heading', { name: 'LLM 供应商' })).toBeVisible({ timeout: 15_000 })
    await expectNoHorizontalOverflow(page)
  })
})

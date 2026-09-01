/**
 * W4 E2E 冒烟扩容 —— 新能力三条：角色日记 / 重要日期 / BYOK 引导。
 * 前置同 smoke.spec.ts：scripts/e2e_setup.py 已建种子库；后端 APP_DATABASE_URL 指向 e2e 库、API_KEY_ENABLED=false。
 * 边界：不改业务代码；不触碰 smoke.spec.ts 既有断言。
 *
 * 策略说明：
 * - 日记摘要在后端为内存态（DiarySummarizer._daily_summaries，启动即空，无种子写入端点），
 *   E2E 库必然无日记 → 按任务包降级为「空态不报错」：断言状态中心正常渲染 + /api/memory/diary 契约。
 *   （DiaryCard 组件按设计在空态不渲染，故不冒烟卡片展开，避免为测试改业务代码）
 * - BYOK 完整引导流（403 → toast → /settings/llm）需 byok_required=true 后端 + 非 admin 用户，
 *   成本高 → 按任务包降级为 /api/meta 的 byok_required 字段结构断言（公开端点）。
 */
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

const ADMIN = process.env.E2E_ADMIN ?? 'e2e-admin@test.local'
const PASSWORD = process.env.E2E_PASSWORD ?? 'E2eTest#2026'
/** 重要日期测试行：名称带窗口前缀（AGENTS §8.4 data/ 为跨窗共享 Junction，生成物必须可识别） */
const TEST_DATE_NAME = 'E2E-W4 测试纪念日'
const TEST_DATE_VALUE = '12-25'

/** UI 登录（与 smoke.spec.ts 同一入口语义：登录 tab 填账号密码 → 等跳转 /wechat → 按需过 W2 同意门） */
async function uiLogin(page: Page): Promise<void> {
  await page.goto('/login')
  await page.locator('input[type="email"], input[placeholder*="邮箱"]').first().fill(ADMIN)
  await page.locator('input[type="password"]').first().fill(PASSWORD)
  await page.getByRole('button', { name: '登 录', exact: true }).click()
  await page.waitForURL('**/wechat', { timeout: 15_000 })
  // W2-CONSENT 同意门：种子用户未落同意记录时全屏拦截一切点击；同意一次即 POST /api/auth/consent 落库
  const gate = page.getByTestId('consent-gate')
  if (await gate.isVisible().catch(() => false)) {
    await page.getByRole('button', { name: '我已阅读并同意' }).click()
    await expect(gate).toBeHidden({ timeout: 10_000 })
  }
}

/**
 * 进入受保护路由。韧性背景：vite dev 下 React StrictMode 双发 /auth/refresh，
 * 而后端 refresh 为旋转式（删旧 session 存新），败者收到 401 → AuthGuard 弹回 /login（时序竞态，
 * CI 生产构建无 StrictMode 不触发）。检测到弹回即重登再试，最多 3 次。
 */
async function gotoProtected(page: Page, path: string): Promise<void> {
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.goto(path)
    try {
      await page.waitForURL('**/login', { timeout: 2_000 })
      await uiLogin(page)
    } catch {
      return // 2 秒内未弹回登录页 → 已稳定停留在目标路由
    }
  }
}

/** 取角色库第一个角色 id（依赖角色库非空；本地开发库满足） */
async function firstCharacterId(request: APIRequestContext): Promise<string> {
  const res = await request.get('/api/characters')
  expect(res.ok()).toBeTruthy()
  const body = await res.json()
  const list: Array<{ id: string }> = body.characters ?? []
  // 环境自适应：CI/干净 E2E 库无角色（0 合法），只断言接口契约成立（数组结构）；
  // 非空性校验仅在本地有数据时执行，空库时由调用方跳过依赖角色的用例
  expect(Array.isArray(list)).toBe(true)
  test.skip(list.length === 0, 'E2E 库无角色（干净环境），跳过依赖角色的用例')
  return list[0].id
}

/** 登录取 JWT（require_role("admin") 类端点需要 Bearer，与 API_KEY_ENABLED 无关） */
async function accessToken(request: APIRequestContext): Promise<string> {
  const login = await request.post('/api/auth/login', {
    data: { login: ADMIN, password: PASSWORD },
  })
  expect(login.ok()).toBeTruthy()
  const body = await login.json()
  return body.access_token
}

test.describe('W4 新能力冒烟', () => {
  test('角色日记：状态中心正常渲染（E2E 库无日记 → 空态不报错）+ diary 接口契约', async ({
    page,
    request,
  }) => {
    // API 契约：/api/memory/diary 公开可访问，entries 恒为数组（空库为空数组）
    const diary = await request.get('/api/memory/diary?limit=5')
    expect(diary.ok()).toBeTruthy()
    const body = await diary.json()
    expect(Array.isArray(body.entries)).toBeTruthy()

    // UI：登录 → 状态中心渲染，无日记时页面完整可用（DiaryCard 按设计空态隐藏）
    await uiLogin(page)
    const characterId = await firstCharacterId(request)
    await gotoProtected(page, `/roles/${encodeURIComponent(characterId)}/status`)
    await expect(page.getByRole('heading', { name: '状态中心' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('当前情绪', { exact: true })).toBeVisible()
    await expect(page.getByText('亲密等级', { exact: true })).toBeVisible()
    await expect(page.getByText('记忆体系', { exact: true })).toBeVisible()
  })

  test('重要日期：设置页 Basic tab 区块渲染 + 添加一行 + 保存成功（测试后还原共享文件）', async ({
    page,
    request,
  }) => {
    const characterId = await firstCharacterId(request)
    const encodedId = encodeURIComponent(characterId)

    // 记录测试前基线（并防御性清掉上次运行可能的残留测试行）
    const before = await request.get(`/api/characters/${encodedId}/important-dates`)
    expect(before.ok()).toBeTruthy()
    const baseline: Array<{ name: string; date: string; kind: string }> =
      ((await before.json()).dates ?? []).filter((d: { name: string }) => d.name !== TEST_DATE_NAME)

    await uiLogin(page)
    await gotoProtected(page, `/roles/${encodedId}/settings`)
    await expect(page.getByText('重要日期', { exact: true })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByText('生日 / 纪念日命中当天时', { exact: false })).toBeVisible()

    // 添加一行并填写（Basic 为默认 tab，无需点击切换）
    await page.getByRole('button', { name: '+ 添加日期' }).click()
    await page.getByPlaceholder('名称（如：我的生日）').fill(TEST_DATE_NAME)
    await page.getByPlaceholder('MM-DD', { exact: true }).fill(TEST_DATE_VALUE)

    // 保存 → success toast → 服务端持久化确认（PUT important-dates）
    await page.getByRole('button', { name: '保存日期' }).click()
    await expect(page.getByText('重要日期已保存')).toBeVisible()
    const after = await request.get(`/api/characters/${encodedId}/important-dates`)
    expect(after.ok()).toBeTruthy()
    const saved = (await after.json()).dates ?? []
    const added = saved.find((d: { name: string }) => d.name === TEST_DATE_NAME)
    expect(added).toBeTruthy()
    expect(added.date).toBe(TEST_DATE_VALUE)

    // 还原 data/important_dates.json 至测试前状态（空列表由后端弹出该角色键）
    const token = await accessToken(request)
    const restore = await request.put(`/api/characters/${encodedId}/important-dates`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { dates: baseline },
    })
    expect(restore.ok()).toBeTruthy()
  })

  test('BYOK 引导契约：/api/meta 公开端点返回 byok_required 布尔字段', async ({ request }) => {
    const res = await request.get('/api/meta')
    expect(res.ok()).toBeTruthy()
    const body = await res.json()
    // W1-BYOK 契约：前端统一 403 拦截依据该字段；结构存在性即冒烟目标
    expect(typeof body.byok_required).toBe('boolean')
    expect(typeof body.version).toBe('string')
  })
})

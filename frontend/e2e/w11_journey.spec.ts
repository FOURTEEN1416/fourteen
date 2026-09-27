/**
 * W11 实施窗 E2E —— 前端用户任务与声明统一（两账号 · 真实 API）
 *
 * 前置（由调用方提供，本文件不自建）：
 *   - 隔离库后端：scripts/e2e_setup.py 产出 data/e2e_users.db，
 *     启动：AI_GF_ENV=dev DISABLE_SCHEDULER=1 APP_DATABASE_URL="sqlite+aiosqlite:///data/e2e_users.db" \
 *           API_KEY_ENABLED=false python -m uvicorn api.run_api:app --host 127.0.0.1 --port 8000
 *   - 前端 dev：VITE_DEV_PORT=5199 VITE_API_BASE=http://127.0.0.1:8000 bun run dev
 *   - 账号：admin e2e-admin@test.local / E2eTest#2026
 *          viewer w11-b@test.local / W11Test#2026（注册后 needs_consent=true）
 *
 * 纪律：全程走真实 API 登录与真实路由；不向 zustand store / localStorage 塞认证态。
 *   例外（显式标注）：① 500 错误态用 page.route 注入故障（不制造真实 500）；
 *   ② 安全开关的「业务失败不翻转」用 page.route 注入 not_available 回执
 *   —— 因为真实写入会改动仓库 config/system.yaml，浏览器层不做破坏性真实写入。
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'

const API = 'http://127.0.0.1:8000'

const ADMIN = { login: 'e2e-admin@test.local', password: 'E2eTest#2026' }
const VIEWER = { login: 'w11-b@test.local', password: 'W11Test#2026' }

// ── helpers ──────────────────────────────────────────────

async function agreeIfPrompted(page: Page) {
  const gate = page.getByTestId('consent-gate')
  // 协议门由 AuthInit 完成后异步挂载（且覆盖整个控制台、拦截点击），
  // 必须「等它出现」再决定，不能立刻 isVisible() 判断——否则漏判会让后续点击全被遮挡。
  try {
    await gate.waitFor({ state: 'visible', timeout: 6_000 })
  } catch {
    return // 该账号已同意过，本次不弹门
  }
  await page.getByRole('button', { name: '我已阅读并同意' }).click()
  await gate.waitFor({ state: 'hidden', timeout: 20_000 })
}

/** 通过真实登录表单进入控制台（真实 API，无 store 注入） */
async function uiLogin(page: Page, cred: { login: string; password: string }) {
  await page.goto('/login')
  await page.locator('input[placeholder="请输入邮箱或用户名"]').fill(cred.login)
  await page.locator('input[placeholder="输入密码"]').fill(cred.password)
  await page.getByRole('button', { name: '登 录', exact: true }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20_000 })
  await agreeIfPrompted(page)
}

/** 真实 API 取 token（用于只读校验与后端强制校验，不注入前端） */
async function apiToken(request: APIRequestContext, cred: { login: string; password: string }) {
  const res = await request.post(`${API}/api/auth/login`, { data: { login: cred.login, password: cred.password } })
  expect(res.ok(), 'API 登录应成功').toBeTruthy()
  return (await res.json()).access_token as string
}

/**
 * 角色 id 用 **admin** token 发现：`GET /api/characters` 是「按绑定用户」过滤的，
 * 隔离库里 viewer 尚未绑定任何角色（列表为空），而角色 id 本身是全局标识。
 * 这仍是真实 API 调用，只是不借用被测账号的会话。
 *
 * 公开 clone / CI 不投递 gitignored 角色卡（角色库可能为空，曾致 run 36308183821 红）：
 * 库空时以 admin **真实创建**一张临时卡兜底——行为等价、不放宽断言；
 * 调用方必须在结束时 await cleanup() 删除临时卡，避免污染权威角色库。
 */
async function anyRoleId(
  request: APIRequestContext,
): Promise<{ id: string; cleanup: (() => Promise<void>) | null }> {
  const token = await apiToken(request, ADMIN)
  const res = await request.get(`${API}/api/characters`, { headers: { Authorization: `Bearer ${token}` } })
  expect(res.ok(), 'GET /api/characters 应成功').toBeTruthy()
  const body = await res.json()
  const list = Array.isArray(body) ? body : (body.characters ?? [])
  if (list.length > 0) {
    return { id: list[0].id as string, cleanup: null }
  }
  const created = await createRoleAs(request, ADMIN, `W11-E2E-temp-${Date.now()}`)
  return { id: created.id, cleanup: () => deleteRoleAs(request, created.token, created.id) }
}

/**
 * 以指定账号**真实创建**一张角色卡（同时完成「创建 + 归属绑定」两步旅程）。
 * 测试结束必须删除，避免污染仓库权威角色库 config/characters。
 */
async function createRoleAs(
  request: APIRequestContext,
  cred: { login: string; password: string },
  name: string,
) {
  const token = await apiToken(request, cred)
  const res = await request.post(`${API}/api/characters`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { name, description: 'W11 E2E 临时角色', personality: {}, speaking_style: {}, core_anchors: [] },
  })
  expect(res.ok(), '创建角色应成功').toBeTruthy()
  const body = await res.json()
  expect(body.status, '创建回执应为 created').toBe('created')
  return { id: body.id as string, token }
}

async function deleteRoleAs(request: APIRequestContext, token: string, id: string) {
  const res = await request.delete(`${API}/api/characters/${id}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(res.ok(), '清理临时角色应成功').toBeTruthy()
}

// ── D1：安全面板真实契约 + 权限隐藏 ─────────────────────

test.describe.serial('W11 · 安全面板（D1）', () => {
  test('viewer：显示本人真实统计与空态，且无 admin-only 开关', async ({ page }) => {
    await uiLogin(page, VIEWER)
    await page.goto('/settings/security')

    await expect(page.getByText('内容安全面板')).toBeVisible()
    // 真实字段（旧实现读 total_detections/today_blocked/block_rate ⇒ 恒 0/—）
    await expect(page.getByText('累计拦截')).toBeVisible()
    await expect(page.getByText('近期事件')).toBeVisible()
    await expect(page.getByText('命中类别')).toBeVisible()
    // 空结果 ≠ 错误：空库应显示空态文案
    await expect(page.getByText('暂无安全事件')).toBeVisible()
    await expect(page.getByText('无法加载安全数据')).toHaveCount(0)
    // admin-only 开关对 viewer 隐藏（后端 require_role("admin") 继续强制）
    await expect(page.getByText('内容安全过滤器')).toHaveCount(0)
  })

  test('后端继续强制：viewer 直调 admin-only 安全开关 → 403', async ({ request }) => {
    const token = await apiToken(request, VIEWER)
    const res = await request.post(`${API}/api/safety/config?enabled=false`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(res.status()).toBe(403)
  })

  test('admin：开关可见且状态真实；业务失败（not_available）不翻转、给 warning', async ({ page }) => {
    await uiLogin(page, ADMIN)
    await page.goto('/settings/security')

    await expect(page.getByText('内容安全过滤器')).toBeVisible()
    await expect(page.getByText('运行中')).toBeVisible()

    // 故障注入：后端以 HTTP 200 + status=not_available 回执（业务失败）
    await page.route('**/api/safety/config*', (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'not_available' }) }),
    )
    await page.getByRole('button', { name: '内容安全过滤器开关' }).click()

    // 绝不假成功：状态保持「运行中」，且给出 warning
    await expect(page.getByText('运行中')).toBeVisible()
    await expect(page.getByText('已暂停')).toHaveCount(0)
    await expect(page.getByText('内容安全开关未生效（后端不可用），设置保持不变')).toBeVisible()
  })

  test('错误态：500 显示错误与重试，不拿 0 冒充数据', async ({ page }) => {
    await uiLogin(page, VIEWER)
    await page.route('**/api/safety/stats*', (route) => route.fulfill({ status: 500, body: '{}' }))
    await page.goto('/settings/security')

    await expect(page.getByText('无法加载安全数据')).toBeVisible()
    await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
    await expect(page.getByText('累计拦截')).toHaveCount(0)
  })
})

// ── D2：角色设置 tab 深链与刷新保留 ─────────────────────

test.describe.serial('W11 · 角色设置深链（D2）', () => {
  test('深链 /settings/voice 刷新后仍在语音 tab；非法 tab 回退基础', async ({ page, request }) => {
    await uiLogin(page, ADMIN)
    const role = await anyRoleId(request)
    const roleId = role.id
    const base = `/roles/${encodeURIComponent(roleId)}/settings`
    try {
      await page.goto(`${base}/voice`)
      await expect(page.getByText('MiMo Cloud TTS')).toBeVisible()

      // 刷新保留（旧实现 useState('basic') ⇒ 刷新必落基础）
      await page.reload()
      await expect(page.getByText('MiMo Cloud TTS')).toBeVisible()

      // 非法 tab → basic
      await page.goto(`${base}/hacker`)
      await expect(page.getByText('性格特质')).toBeVisible()
    } finally {
      await role.cleanup?.()
    }
  })

  test('点击 tab 写入 URL（深链可分享）', async ({ page, request }) => {
    await uiLogin(page, ADMIN)
    const role = await anyRoleId(request)
    const roleId = role.id
    const base = `/roles/${encodeURIComponent(roleId)}/settings`
    try {
      await page.goto(base)
      await expect(page.getByText('性格特质')).toBeVisible()
      await page.getByRole('button', { name: '时间线' }).click()
      await expect(page).toHaveURL((url) => url.pathname === `${base}/timeline`)
    } finally {
      await role.cleanup?.()
    }
  })
})

// ── D4：主动面板作用域（角色页禁全局广播） ───────────────

test.describe.serial('W11 · 主动面板作用域（D4）', () => {
  test('viewer：消息 tab 仅全局调度说明，无 admin 表单与全局广播按钮', async ({ page, request }) => {
    await uiLogin(page, VIEWER)
    // 真实创建并归属到 viewer（角色资源按归属校验，别人的卡对 viewer 不可见）
    const { id: roleId, token } = await createRoleAs(request, VIEWER, `W11-E2E-viewer-${Date.now()}`)
    try {
      await page.goto(`/roles/${encodeURIComponent(roleId)}/settings/message`)

      await expect(page.getByText(/全局引擎统一调度/)).toBeVisible()
      await expect(page.getByText('LLM 主动决策（人设·画像·控制台）')).toHaveCount(0)
      await expect(page.getByText('保存频率配置')).toHaveCount(0)
      await expect(page.getByText('立即发送一条主动消息')).toHaveCount(0)
    } finally {
      await deleteRoleAs(request, token, roleId)
    }
  })

  test('admin：全局作用域横幅 + 表单可见，但无全局广播发送按钮', async ({ page, request }) => {
    await uiLogin(page, ADMIN)
    const role = await anyRoleId(request)
    const roleId = role.id
    try {
      await page.goto(`/roles/${encodeURIComponent(roleId)}/settings/message`)

      await expect(page.getByText('LLM 主动决策（人设·画像·控制台）')).toBeVisible()
      await expect(page.getByText('保存频率配置')).toBeVisible()
      // 作用域声明：参数是全局的，不是本角色专属
      await expect(page.getByText(/全局配置：以下参数作用于全部角色的主动消息调度/)).toBeVisible()
      // 角色页禁止全局广播
      await expect(page.getByText('立即发送一条主动消息')).toHaveCount(0)
    } finally {
      await role.cleanup?.()
    }
  })

  test('admin：主动引擎不可用（503）时显式报错 + 重试，不渲染默认假表单', async ({ page, request }) => {
    await uiLogin(page, ADMIN)
    const role = await anyRoleId(request)
    const roleId = role.id
    try {
      await page.route('**/api/proactive/config*', (route) =>
        route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'unavailable' }) }),
      )
      await page.goto(`/roles/${encodeURIComponent(roleId)}/settings/message`)

      await expect(page.getByText(/主动消息引擎未初始化/)).toBeVisible()
      await expect(page.getByRole('button', { name: '重试' })).toBeVisible()
      await expect(page.getByText('保存频率配置')).toHaveCount(0)
    } finally {
      await role.cleanup?.()
    }
  })
})

// ── 账号维度：登出代码路径 + 换号登录 ───────────────────

test.describe.serial('W11 · 账号维度', () => {
  test('真实登出代码路径（协议门「不同意并退出登录」）后换号登录，视图按新账号渲染', async ({ page, request }) => {
    // 1) 新建一个未同意账号（真实 API），走协议门 → 不同意 → 真实 logout()
    const stamp = Date.now()
    const email = `w11-fresh-${stamp}@test.local`
    const reg = await request.post(`${API}/api/auth/register`, {
      data: { email, username: `w11f${stamp % 1000000}`, password: 'W11Test#2026', display_name: 'W11F' },
    })
    expect(reg.ok(), '新账号注册应成功').toBeTruthy()

    await page.goto('/login')
    await page.locator('input[placeholder="请输入邮箱或用户名"]').fill(email)
    await page.locator('input[placeholder="输入密码"]').fill('W11Test#2026')
    await page.getByRole('button', { name: '登 录', exact: true }).click()
    await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20_000 })

    const gate = page.getByTestId('consent-gate')
    await expect(gate).toBeVisible({ timeout: 20_000 })
    await page.getByRole('button', { name: '不同意并退出登录' }).click()
    await expect(page).toHaveURL(/\/login$/, { timeout: 20_000 })

    // 2) 换号登录 viewer —— 视图必须按新账号渲染（无 admin-only 面）
    await uiLogin(page, VIEWER)
    await page.goto('/settings/security')
    await expect(page.getByText('累计拦截')).toBeVisible()
    await expect(page.getByText('内容安全过滤器')).toHaveCount(0)
  })

  test('admin 视图与 viewer 视图互不串（同一浏览器上下文）', async ({ page }) => {
    await uiLogin(page, ADMIN)
    await page.goto('/settings/security')
    await expect(page.getByText('内容安全过滤器')).toBeVisible()

    // 产品现状：控制台**没有登出入口**（唯一登出路径是协议门的「不同意并退出登录」），
    // 故此处以「会话失效」路径回登录页：清 cookie + 清持久化并重载，让内存态一并丢弃。
    await page.context().clearCookies()
    await page.evaluate(() => window.localStorage.clear())
    await page.reload()
    await uiLogin(page, VIEWER)
    await page.goto('/settings/security')

    await expect(page.getByText('累计拦截')).toBeVisible()
    await expect(page.getByText('内容安全过滤器')).toHaveCount(0)
    await expect(page.getByText('已暂停')).toHaveCount(0)
  })
})

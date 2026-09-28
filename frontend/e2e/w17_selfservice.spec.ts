/**
 * W17 实施窗 E2E —— 前端总接入（自服务面 / 模板面 / 注册初始角色提示）
 *
 * 前置（由调用方提供，本文件不自建）：
 *   - 隔离库种子：python scripts/e2e_setup.py  → data/e2e_users.db
 *   - 后端启动：AI_GF_ENV=dev DISABLE_SCHEDULER=1 \
 *       APP_DATABASE_URL="sqlite+aiosqlite:///data/e2e_users.db" API_KEY_ENABLED=false \
 *       python -m uvicorn api.run_api:app --host 127.0.0.1 --port 8000
 *   - 前端 dev：playwright.config.ts 的 webServer 自启（VITE_DEV_PORT=5199）
 *   - 账号：admin e2e-admin@test.local / E2eTest#2026
 *          viewer w11-b@test.local / W11Test#2026（注册后 needs_consent=true）
 *
 * 纪律：全程走真实 API 登录与真实路由；不向 store / localStorage 塞认证态。
 * 例外（显式标注，沿用 w11_journey.spec.ts 头部同一条纪律）：
 *   ① 注销账号用 page.route 注入受理回执 —— 破坏性真实写入不在浏览器层做
 *      （data/ 与 config/characters 在多窗间是 junction/权威资产，一次真注销
 *       会触发后台 purge 波及共享运行时状态）；门禁与跳转/文案断言仍是真的。
 *   ② 注册响应的 initial_character 用 route.fetch() 打补丁注入 —— W16 未并窗时
 *       该字段还不存在；注入件是**真实注册响应**，只补这一个字段，token 仍真。
 *
 * CI 无卡守卫：模板面依赖 gitignored 的 config/characters（worktree/CI 均可能为空）。
 *   不 skip 掉整条断言，而是**先用 API 取权威总数、再要求 UI 与之一致**：
 *   有卡 → 卡片名逐一点亮 + 克隆旅程；无卡 → 必须显示诚实空态而非报错。
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test'
import * as fs from 'node:fs'

const API = 'http://127.0.0.1:8000'

const ADMIN = { login: 'e2e-admin@test.local', password: 'E2eTest#2026' }
const VIEWER = { login: 'w11-b@test.local', password: 'W11Test#2026' }

// ── helpers（与 w11_journey 同构，真实登录不注入认证态）────────

async function agreeIfPrompted(page: Page) {
  const gate = page.getByTestId('consent-gate')
  try {
    await gate.waitFor({ state: 'visible', timeout: 6_000 })
  } catch {
    return
  }
  await page.getByRole('button', { name: '我已阅读并同意' }).click()
  await gate.waitFor({ state: 'hidden', timeout: 20_000 })
}

async function uiLogin(page: Page, cred: { login: string; password: string }) {
  await page.goto('/login')
  await page.locator('input[placeholder="请输入邮箱或用户名"]').fill(cred.login)
  await page.locator('input[placeholder="输入密码"]').fill(cred.password)
  await page.getByRole('button', { name: '登 录', exact: true }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20_000 })
  await agreeIfPrompted(page)
}

async function apiToken(request: APIRequestContext, cred: { login: string; password: string }) {
  const res = await request.post(`${API}/api/auth/login`, { data: { login: cred.login, password: cred.password } })
  expect(res.ok(), 'API 登录应成功').toBeTruthy()
  return (await res.json()).access_token as string
}

/** 读下载文件内容（Playwright 把下载落到临时路径，读完即删） */
async function readDownloadText(page: Page, downloadPromise: Promise<unknown>) {
  const [download] = await Promise.all([page.waitForEvent('download'), downloadPromise])
  const dl = download as Awaited<ReturnType<Page['waitForEvent']>> & {
    suggestedFilename: () => string
    path: () => Promise<string | null>
  }
  const filename = dl.suggestedFilename()
  const path = await dl.path()
  const text = path ? fs.readFileSync(path, 'utf-8') : ''
  return { filename, text }
}

/** 数据清单渲染完成（类别标签是纯文本，导出/撤回用例的共同前置） */
async function expectDataReady(page: Page) {
  // exact：「对话记录」同时出现在注销说明文案里，非精确匹配会撞 strict mode
  await expect(page.getByText('对话记录', { exact: true })).toBeVisible()
}

// ── 自服务面：/settings/account ────────────────────────────

test.describe.serial('W17 · 自服务面（账号与数据）', () => {
  test('viewer：显示后端真实同意状态与本人数据计数', async ({ page }) => {
    await uiLogin(page, VIEWER)
    await page.goto('/settings/account')

    await expect(page.getByText('协议与同意')).toBeVisible()
    // 真实状态来自 GET /api/auth/consent/status（协议门已同意 → granted + 服务端版本）
    await expect(page.getByText(/已同意 v\d/)).toBeVisible()
    await expect(page.getByText('无法加载账号数据')).toHaveCount(0)

    // 类别标签来自 GET /api/auth/account/export 的 categories
    await expectDataReady(page)
    await expect(page.getByText('记忆事实')).toBeVisible()
    await expect(page.getByText('微信绑定')).toBeVisible()
    // 有回执才有按钮：撤回入口对已同意账号可见
    await expect(page.getByRole('button', { name: /撤回同意/ })).toBeVisible()
  })

  test('导出我的数据 → 落成带日期的文件且内容可解析', async ({ page }) => {
    await uiLogin(page, VIEWER)
    await page.goto('/settings/account')
    await expectDataReady(page)

    const { filename, text } = await readDownloadText(
      page,
      page.getByRole('button', { name: /导出我的数据/ }).click(),
    )
    expect(filename).toMatch(/^account-export-\d{4}-\d{2}-\d{2}\.json$/)
    const payload = JSON.parse(text)
    expect(payload.categories).toBeDefined()
    expect(typeof payload.categories.chats).toBe('number')
    expect(Array.isArray(payload.session_keys)).toBe(true)
  })

  test('导出聊天记录 → chats-export 文件，含会话与总条数', async ({ page }) => {
    await uiLogin(page, VIEWER)
    await page.goto('/settings/account')
    await expectDataReady(page)

    const { filename, text } = await readDownloadText(
      page,
      page.getByRole('button', { name: /导出聊天记录/ }).click(),
    )
    expect(filename).toMatch(/^chats-export-\d{4}-\d{2}-\d{2}\.json$/)
    const payload = JSON.parse(text)
    expect(payload.sessions).toBeDefined()
    expect(typeof payload.total_messages).toBe('number')
  })

  test('撤回同意 → 状态按回执更新 + 后端消费面真实 fail-closed，重新同意即恢复', async ({ page, request }) => {
    await uiLogin(page, VIEWER)
    const token = await apiToken(request, VIEWER)
    // 探针用 POST /api/session（api/consent.py 门禁与 /api/chat 同源，但不触模型、秒级返回）
    const probe = () =>
      request.post(`${API}/api/session`, {
        headers: { Authorization: `Bearer ${token}` },
        data: {},
      })

    // 撤回前：门禁放行（200 拿到 session_id；绝不是 403 CONSENT_REQUIRED）
    const before = await probe()
    expect(before.status(), '撤回前不应被同意门禁拦截').not.toBe(403)

    await page.goto('/settings/account')
    await page.getByRole('button', { name: /撤回同意/ }).click()
    await expect(page.getByText(/已撤回同意 v\d/)).toBeVisible({ timeout: 20_000 })

    // 服务端实况：撤回后四通道 fail-closed（api/consent.py::require_current_consent）
    const after = await probe()
    expect(after.status()).toBe(403)
    const detail = JSON.stringify(await after.json())
    expect(detail).toContain('CONSENT_REQUIRED')

    // 恢复入口：重新同意复用 POST /api/auth/consent
    await page.getByRole('button', { name: '重新同意' }).click()
    await expect(page.getByText(/已同意 v\d/)).toBeVisible({ timeout: 20_000 })

    const restored = await probe()
    expect(restored.status(), '重新同意后门禁应放开').not.toBe(403)
    expect(await restored.json()).toHaveProperty('session_id')
  })

  test('注销：输入「注销」才可执行；受理回执→清缓存回登录页，文案绝不宣称「已删除」', async ({ page }) => {
    // 例外①：破坏性真实写入不在浏览器层做，注入后端受理态（queued）回执
    await page.route('**/api/auth/account/delete', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ job_id: 'acct-injected', user_id: 0, status: 'queued', completed: false, steps: {} }),
      }),
    )
    await uiLogin(page, VIEWER)
    await page.goto('/settings/account')

    const input = page.getByTestId('delete-confirm-input')
    const button = page.getByRole('button', { name: /注销账号/ })
    await expect(button).toBeDisabled()

    await input.fill('确认注销')
    await expect(button).toBeDisabled()

    await input.fill('注销')
    await expect(button).toBeEnabled()
    await button.click()

    await page.waitForURL((u) => u.pathname === '/login', { timeout: 20_000 })
    await expect(page.getByText(/已受理/)).toBeVisible()
    await expect(page.getByText(/已删除/)).toHaveCount(0)
  })
})

// ── 模板面：/templates ────────────────────────────────────

test.describe.serial('W17 · 模板面（角色模板）', () => {
  test('侧栏入口可达，清单与后端权威总数一致（CI 无卡时显示诚实空态）', async ({ page, request }) => {
    await uiLogin(page, VIEWER)
    const token = await apiToken(request, VIEWER)
    const apiRes = await request.get(`${API}/api/character-templates`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(apiRes.ok()).toBeTruthy()
    const body = await apiRes.json()
    const total: number = body.total ?? 0
    const names: string[] = (body.templates ?? []).map((t: { name: string }) => t.name)

    await page.getByRole('link', { name: '角色模板' }).click()
    await page.waitForURL('**/templates')
    await expect(page.getByRole('heading', { name: '角色模板' })).toBeVisible()

    if (total === 0) {
      // 无卡/开关关闭：空态而非错误，且不得渲染任何克隆按钮
      await expect(page.getByText(/暂无可用模板/)).toBeVisible()
      await expect(page.getByText('无法加载角色模板')).toHaveCount(0)
      await expect(page.getByRole('button', { name: /使用此角色/ })).toHaveCount(0)
      return
    }
    for (const name of names.slice(0, 3)) {
      await expect(page.getByText(name, { exact: true })).toBeVisible()
    }
    await expect(page.getByRole('button', { name: /使用此角色/ })).toHaveCount(total)
  })

  test('使用此角色 → 克隆归属本人并出现在我的角色列表（随后清理）', async ({ page, request }) => {
    const token = await apiToken(request, VIEWER)
    const listRes = await request.get(`${API}/api/character-templates`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    const body = await listRes.json()
    const first = (body.templates ?? [])[0]
    // CI 无卡守卫：模板库为空时这条旅程无从走起（无卡可克隆是事实，不是放宽断言）
    test.skip(!first, '模板清单为空（worktree/CI 未投递 gitignored 角色卡）')

    await uiLogin(page, VIEWER)
    await page.goto('/templates')
    await page.getByRole('button', { name: /使用此角色/ }).first().click()

    await page.waitForURL('**/roles', { timeout: 20_000 })
    await expect(page.getByText(/暂无角色/)).toHaveCount(0)

    // 后端实况：克隆件已归属 viewer（GET /api/characters 按归属过滤）
    const mine = await request.get(`${API}/api/characters`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(mine.ok()).toBeTruthy()
    const mineBody = await mine.json()
    const list = Array.isArray(mineBody) ? mineBody : (mineBody.characters ?? [])
    const cloned = list.find((c: { name: string }) => c.name === first.name)
    expect(cloned, '克隆出的角色应出现在本人角色列表').toBeDefined()

    // 清理：删除刚克隆的副本，避免污染仓库权威角色库（config/characters）
    const del = await request.delete(`${API}/api/characters/${encodeURIComponent(cloned.id)}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    expect(del.ok(), '清理克隆副本应成功').toBeTruthy()
  })
})

// ── 注册：initial_character 容错提示 ──────────────────────

test.describe.serial('W17 · 注册初始角色提示', () => {
  /** 走真实注册表单，返回后端注册响应体（真实字段，不伪造 token） */
  async function uiRegister(page: Page, email: string, username: string) {
    await page.goto('/login')
    await page.getByRole('button', { name: '注册', exact: true }).click()
    await page.locator('input[type="email"]').fill(email)
    await page.locator('input[placeholder="至少3个字符"]').fill(username)
    await page.locator('input[type="password"]').fill('W17Test#2026')

    const respPromise = page.waitForResponse((r) => r.url().includes('/api/auth/register'), { timeout: 20_000 })
    await page.getByRole('button', { name: '注 册', exact: true }).click()
    const resp = await respPromise
    await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20_000 })
    await agreeIfPrompted(page)
    return resp
  }

  test('字段存在 → 控制台提示「已为你准备初始角色：X」（注入真实响应的该字段）', async ({ page }) => {
    const stamp = Date.now()
    // 例外②：W16 未并窗时注册响应还没有该字段；route.fetch 拿**真实响应**只补这一个字段
    await page.route('**/api/auth/register', async (route) => {
      const res = await route.fetch({ data: JSON.stringify(route.request().postDataJSON()) })
      const json = await res.json()
      json.initial_character = { id: 'w17-probe', name: '探针角色' }
      await route.fulfill({ response: res, body: JSON.stringify(json) })
    })

    await uiRegister(page, `w17-init-${stamp}@test.local`, `w17i${String(stamp).slice(-6)}`)

    await expect(page.getByText(/已为你准备初始角色：探针角色/)).toBeVisible({ timeout: 20_000 })
  })

  test('字段缺席（W16 未接线）→ 零提示、注册照常进入控制台', async ({ page }) => {
    const stamp = Date.now()
    const resp = await uiRegister(page, `w17-plain-${stamp}@test.local`, `w17p${String(stamp).slice(-6)}`)
    const json = await resp.json()

    // 断言跟着后端实况走：字段真在响应里就必须提示，不在就必须不提
    const name = json?.initial_character?.name
    if (name) {
      await expect(page.getByText(new RegExp(`已为你准备初始角色：${name}`))).toBeVisible({ timeout: 20_000 })
    } else {
      await expect(page.getByText(/已为你准备初始角色/)).toHaveCount(0)
      expect(json?.user?.id, '注册应真实落库').toBeTruthy()
    }
  })
})

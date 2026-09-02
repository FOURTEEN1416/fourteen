/**
 * 视觉现状图册截图脚本 — 遍历 FEATURE_MAP 全部挂载页面实拍
 * 运行前提：后端 127.0.0.1:8000 已就绪；本脚本自行启动检测，前端由外层编排启动在 5199
 * 输出：docs/visual-map/shots/F-xx.png
 */
import { chromium } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

const BASE = process.env.TOUR_BASE_URL || 'http://127.0.0.1:5199'
const API = process.env.TOUR_API_URL || 'http://127.0.0.1:8000'
const ADMIN = { login: process.env.TOUR_ADMIN || 'admin', password: process.env.TOUR_PASSWORD || '' }
const API_KEY = process.env.TOUR_API_KEY || ''
const authHeaders = { 'Content-Type': 'application/json', ...(API_KEY ? { 'X-API-Key': API_KEY } : {}) }
const OUT_DIR = path.resolve(process.cwd(), '../docs/visual-map/shots')

fs.mkdirSync(OUT_DIR, { recursive: true })

async function apiLogin() {
  const res = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: authHeaders,
    body: JSON.stringify(ADMIN),
  })
  if (!res.ok) throw new Error(`API login failed: ${res.status} ${await res.text()}`)
  return res.json()
}

async function ensureDemoCharacter(token) {
  const listRes = await fetch(`${API}/api/characters`, { headers: { ...(API_KEY ? { 'X-API-Key': API_KEY } : {}) } })
  const list = await listRes.json().catch(() => ({ characters: [] }))
  if (!listRes.ok) console.log(`[warn] list characters ${listRes.status}, treating as empty`)
  if (list.characters && list.characters.length > 0) {
    console.log(`[data] found ${list.characters.length} existing character(s), using first: ${list.characters[0].name}`)
    return list.characters[0].id
  }
  const createRes = await fetch(`${API}/api/characters`, {
    method: 'POST',
    headers: { ...authHeaders, Authorization: `Bearer ${token}` },
    body: JSON.stringify({
      name: '演示角色·小十四',
      description: '用于视觉图册截图的演示角色',
      personality: '温柔体贴，偶尔傲娇',
      speaking_style: '轻松自然，带点俏皮',
      catchphrases: ['嗯哼～'],
      core_anchors: ['表面傲娇内心温柔'],
    }),
  })
  if (!createRes.ok) throw new Error(`create character failed: ${createRes.status} ${await createRes.text()}`)
  const created = await createRes.json()
  console.log(`[data] created demo character id=${created.id}`)
  return created.id
}

async function main() {
  const session = await apiLogin()
  const roleId = await ensureDemoCharacter(session.access_token)

  const routes = [
    ['A_F-01_login', '/', '公开页 · 登录页（未登录态）', false],
    ['A_F-02_demo', '/demo', '公开页 · Demo 体验页', false],
    ['B_F-03_wechat', '/wechat', '微信连接控制台', true],
    ['C_F-04_roles', '/roles', '角色列表', true],
    ['C_F-05_create-role', '/roles/create', '创建角色（三方式 tab）', true],
    [`C_F-06_role-settings`, `/roles/${roleId}/settings`, `角色设置六 tab（角色=${roleId}）`, true],
    [`C_F-07_status-center`, `/roles/${roleId}/status`, `状态中心（角色=${roleId}）`, true],
    [`C_F-08_storyline`, `/roles/${roleId}/storyline`, `剧情线编辑器（角色=${roleId}）`, true],
    ['D_F-09_settings-llm', '/settings/llm', '系统设置 · LLM', true],
    ['D_F-10_settings-voice', '/settings/voice', '系统设置 · 语音', true],
    ['D_F-11_settings-tools', '/settings/tools', '系统设置 · 工具面板', true],
    ['D_F-12_settings-security', '/settings/security', '系统设置 · 安全', true],
    ['D_F-13_settings-logs', '/settings/logs', '系统设置 · 日志', true],
    ['E_F-14_admin-users', '/admin/users', '管理后台 · 用户管理', true],
    ['E_F-15_admin-providers', '/admin/providers', '管理后台 · LLM 供应商', true],
  ]

  const browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  const page = await context.newPage()

  // ── UI 真实登录（accessToken 内存闭包 + refreshToken httpOnly cookie）──
  await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' }).catch(() => {})
  await page.fill('input[placeholder="请输入邮箱或用户名"]', ADMIN.login)
  await page.fill('input[type="password"]', ADMIN.password)
  await Promise.all([
    page.waitForURL('**/wechat', { timeout: 15000 }),
    page.keyboard.press('Enter'),
  ])
  console.log('[auth] UI login OK -> /wechat')
  await page.waitForTimeout(1500)

  const results = []
  for (const [file, route, label] of routes) {
    try {
      await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 20000 })
      await page.waitForTimeout(2200) // React Query 拉数据 + 入场动画
      const shot = path.join(OUT_DIR, `${file}.png`)
      await page.screenshot({ path: shot, fullPage: true })
      results.push([file, route, label, 'OK'])
      console.log(`[shot] ${file} <- ${route}`)
    } catch (e) {
      results.push([file, route, label, `FAIL: ${e.message.slice(0, 80)}`])
      console.log(`[fail] ${file}: ${e.message.slice(0, 100)}`)
    }
  }

  // 未登录兜底态补拍（若首页即登录页则上面已覆盖）
  await browser.close()

  const manifest = results.map(([f, r, l, s]) => `| ${f} | \`${r}\` | ${l} | ${s} |`).join('\n')
  fs.writeFileSync(path.join(OUT_DIR, '_manifest.txt'), manifest, 'utf8')
  const okCount = results.filter(r => r[3] === 'OK').length
  console.log(`\nDONE: ${okCount}/${results.length} shots saved to ${OUT_DIR}`)
}

main().catch(e => { console.error('TOUR-FATAL:', e); process.exit(1) })

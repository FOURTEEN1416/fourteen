# Progress Log

> Last updated: 2026-06-03 14:47
> Project: 唯一的你 — P0 全面修复 + CI 加固 + 品牌清洗

## Phase 1.1: 后端 httpOnly Cookie 支持 (DONE ✅)
- [x] auth_routes.py: 添加 _set_refresh_cookie / _get_refresh_token 辅助函数
- [x] register/login/refresh 端点设置 httpOnly cookie (Secure条件/SameSite=Lax/path=/api/auth/7d)
- [x] refresh 端点支持从 cookie 读取 refresh_token (body→cookie 回退)
- [x] logout 清除 cookie + 吊销 token
- [x] RefreshRequest.refresh_token 改为可选 (str = "")
- [x] ruff 检查通过, pytest 625/625 passed
- [x] 后端 625 passed, 1 skipped

## Phase 1.2: 前端内存闭包 accessToken (DONE ✅)
- [x] authStore.ts: 模块级 _accessToken 闭包 (getAccessToken/setAccessToken), 移除 persist token
- [x] useAuth.ts: login/register/logout 适配 cookie 模式 (不传 refresh_token)
- [x] api/auth.ts: refreshToken/logout 签名改为可选参数 (向后兼容)
- [x] api/client.ts: 请求拦截器改用 getAccessToken(); 401 自动刷新依赖 cookie
- [x] App.tsx AuthInit: 改用 refreshToken() 无参数 (cookie auto-sent)
- [x] 测试文件 useAuthStore.test.ts: setTokens→setAuth, 移除 refreshToken 引用
- [x] **tsc --noEmit: 零错误** ✅
- [x] **vitest: 4 passed** ✅
- [x] **pytest: 625 passed** ✅

## Phase 1.3: 前端测试适配 (DONE ✅)
- [x] Playwright 测试验证 login/register/logout 流程 (3/3 passed)
- [x] 确认 Vite proxy 透传 Set-Cookie 正常

## Phase 2: CI 加固 (DONE ✅)
- [x] .github/workflows/ci.yml: ruff/tsc 改为 blocking (continue-on-error: false)
- [x] .github/workflows/ci.yml: 添加 mypy job
- [x] .github/workflows/ci.yml: 添加 vitest job
- [x] .github/workflows/ci.yml: 添加 playwright job
- [x] .github/workflows/ci.yml: 添加 weekly-hygiene 汇总 job

## Phase 3: 内测前清理 (DONE ✅)
- [x] ruff 全面过一遍 (97→0 errors)
- [x] mypy 过一遍 (3→0 errors)
- [x] tsc 过一遍 (已通过)
- [x] vitest 过一遍 (4/4 passed)
- [x] 清理旧 mock/孤立页 (0 orphan)
- [x] shisi 旧路由 17 处引用清理
- [x] 品牌标签清洗 ('AI虚拟伴侣'→'AI伙伴')

## Phase 4: E2E 验证 (DONE ✅)
- [x] Playwright E2E 框架搭建 (3/3 passed)
- [x] 邀请码注册 Playwright E2E 测试

## Phase 5: P0 修复 (DONE ✅)
- [x] P0-1: invites.ts 双重 baseURL 修复
- [x] P0-2: JWT_SECRET fail-fast
- [x] P0-5: RoleGuard is_active 校验
- [x] P0-6: accessToken 内存闭包 + httpOnly cookie
- [x] P0-7: Orchestrator character_id 兼容
- [x] P0-8: process_message_stream
- [x] P0-3/4: 跳过（用户决策）

## P1 Backlog (待内测前清理)
- [ ] 18 个 P1 条目（见 docs/P1_BACKLOG.md）

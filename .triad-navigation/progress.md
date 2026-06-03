# Progress Log

> Last updated: 2026-06-03 07:03
> Project: AI Girlfriend — Token Security (Option A) + CI Cleanup

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

## Phase 1.3: 前端测试适配 (TODO)
- [ ] Playwright 测试验证 login/register/logout 流程
- [ ] 确认 Vite proxy 透传 Set-Cookie 正常

## Phase 2: CI 加固 (TODO)
- [ ] .github/workflows/ci.yml: ruff/tsc 改为 blocking (continue-on-error: false)
- [ ] .github/workflows/ci.yml: 添加 mypy job
- [ ] .github/workflows/ci.yml: 添加 vitest job
- [ ] .github/workflows/ci.yml: 添加 playwright job

## Phase 3: 内测前清理 (TODO)
- [ ] ruff 全面过一遍
- [ ] mypy 过一遍
- [ ] tsc 过一遍 (已通过)
- [ ] vitest 过一遍 (已通过)
- [ ] 清理旧 mock/孤立页

## Phase 4: E2E 验证 (TODO)
- [ ] Windows 本地跑通 5 流程: 注册/邀请/聊天/角色/管理

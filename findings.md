# Findings — 唯一的你 多智能体协同

## 项目状态（2026-06-03）
- 分支：main（领先 origin/main 1 commit）
- 后端：625 pytest passed, 1 skipped
- 前端：4 vitest + 3 playwright 实跑通过
- 三体导航 Phase 14 已完成，P0 投产阻塞清零

## 🔐 Token 安全现状（2026-06-03 审计）

### 当前实现（不安全）
| 项目 | 存储方式 | 风险 |
|------|---------|------|
| accessToken | localStorage (via zustand persist) | XSS 可读 |
| refreshToken | localStorage (via zustand persist) | XSS 可读，可永续会话 |

### 目标（Option A）
| 项目 | 存储方式 | 安全等级 |
|------|---------|---------|
| accessToken | JavaScript 内存变量（闭包） | ✅ 最佳 |
| refreshToken | httpOnly + Secure + SameSite=Strict Cookie | ✅ 完美 |

### 需要改的文件
**后端（auth_routes.py + auth_jwt.py）：**
- login/register/refresh 端点：Set-Cookie: refresh_token=httpOnly; Secure; SameSite=Strict; Path=/api/auth
- /auth/refresh 端点：接受 cookie 中的 refresh_token（同时保留 body 方式做兼容）
- /auth/logout：清除 cookie

**前端（authStore.ts）：**
- 移除 zustand persist 对 token 的持久化
- accessToken 存 RxJS BehaviorSubject 或简单闭包变量
- refreshToken 完全不存前端

**前端（useAuth.ts）：**
- login/register 后只取 accessToken 存内存
- refresh 调用不发 refresh_token（浏览器自动带 cookie）

**前端（client.ts）：**
- accessToken 从闭包变量读取（非 localStorage）
- refresh 逻辑改为直接调 /auth/refresh（不传 refresh_token）

### 需要特别考虑
- **页面刷新**：accessToken 在内存会丢失 → 需要 refresh cookie 自动续期
- **并发 401**：现有队列逻辑需要适配新流程

## CI 现状
- 已有：pytest（push/PR/schedule）
- 已有：ruff（continue-on-error: true — 问题是它允许失败）
- 已有：tsc（continue-on-error: true — 同问题）
- 已有：bun run build
- 缺少：mypy（目前只有手动，commit 3af3a14 曾清理）
- 缺少：vitest（只有本地）
- 缺少：playwright（只有本地）
- 缺少：ruff 应改为 blocking（非 continue-on-error）

## 内测前清理项
1. 未跟踪文件 docs/audits/ 和 logs/ — 需决定是否 gitignore
2. CI 中 ruff 和 tsc 的 continue-on-error: true 需改为 blocking
3. 缺少 mypy/vitest/playwright CI job
4. auth 全流程尚未完整验证

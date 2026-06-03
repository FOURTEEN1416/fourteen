# Task Plan — AI Girlfriend 多智能体协同执行

## 目标
基于默默已做的决策，多智能体并行执行：
1. **Token 安全加固**（Option A：accessToken 内存 + refreshToken httpOnly cookie）
2. **CI 加固**（添加 mypy/vitest/playwright，ruff/tsc 改 blocking）
3. **内测前清理**（扫尾项）
4. **Windows 本地全流程验证**（5流程：注册→邀请→聊天→角色→管理）

## 执行策略
任务拆小，每完成一个再派发下一个，实时验证。

---

## Phase 1: 🔐 Token 安全加固

### Step 1.1: 后端 — refreshToken httpOnly Cookie
- [ ] auth_routes.py: login/register/refresh 端点返回 Set-Cookie
- [ ] auth_routes.py: /auth/refresh 支持从 cookie 读 refresh_token
- [ ] auth_routes.py: /auth/logout 清除 cookie
- **文件**: api/routers/auth_routes.py
- **依赖**: 无

### Step 1.2: 前端 — accessToken 移至内存
- [ ] authStore.ts: 移除 persist，accessToken 用闭包变量存储
- [ ] useAuth.ts: 登录后只存 accessToken 到内存，refreshToken 不存
- [ ] client.ts: 从闭包读 accessToken；refresh 逻辑不传 refresh_token
- **文件**: frontend/src/store/authStore.ts, hooks/useAuth.ts, api/client.ts
- **依赖**: Step 1.1（接口对齐）

### Step 1.3: 验证 Token 流程
- [ ] 验证先跑 	sc --noEmit 零错误
- [ ] 验证 unx vitest run 通过
- [ ] 验证前后端联调登录/刷新/登出

---

## Phase 2: 🔧 CI 加固（30min）

### Step 2.1: ruff 改 blocking + 添加 mypy
- [ ] ci.yml: ruff 去掉 continue-on-error: true
- [ ] ci.yml: 添加 mypy 检查 job
- **文件**: .github/workflows/ci.yml

### Step 2.2: 添加 vitest + playwright CI job
- [ ] ci.yml: 添加 vitest run job
- [ ] ci.yml: 添加 playwright test job（需浏览器）
- **文件**: .github/workflows/ci.yml

### Step 2.3: tsc 改 blocking + schedule 增强
- [ ] ci.yml: tsc 去掉 continue-on-error: true
- **文件**: .github/workflows/ci.yml

---

## Phase 3: 🧹 内测前清理（2-3h）

### Step 3.1: 未跟踪文件处理
- [ ] 决定 docs/audits/ 和 logs/ 是否需要 gitignore
- [ ] 清理不需要的文件

### Step 3.2: 全面 lint/type 检查
- [ ] uff check . 全通过
- [ ] mypy api/ my_character/ 全通过
- [ ] 	sc --noEmit 全通过
- [ ] unx vitest run 全通过

---

## Phase 4: ✅ 全流程验证（1h）

### Step 4.1: 启动前后端
- [ ] start_all.cmd 启动
- [ ] 验证 :5173 可达
- [ ] 验证 :8000/docs 可达

### Step 4.2: 5 流程 E2E 验证
- [ ] 注册流程（普通注册 + 邀请码注册）
- [ ] 邀请码管理（admin 创建/查询/删除）
- [ ] 聊天流程（创建角色 + 对话）
- [ ] 角色管理（设置/编辑/删除）
- [ ] 管理功能（用户管理/系统设置）

---

## Phase 5: 🔀 最终验证
- [ ] 全量 pytest 回归（625+）
- [ ] 全量 vitest 回归（4+）
- [ ] 全量 playwright 回归（3+）
- [ ] git status 确认无遗漏文件
- [ ] commit & push

## 当前阶段
Phase 1.1 — 后端 Token Cookie

# CONTROL — 闭环控制（Fitness Functions + 审计节奏）

> 项目：唯一的你
> 更新日期：2026-06-03（P0 修复 + CI 加固 + 品牌清洗） | 审计人：默默 (doc-updater)

---

## 活跃 Fitness Functions

### CI 中实际运行（Hardened，12个）

| # | 检测项 | 实现方式 | 有效 |
|---|--------|----------|------|
| FF-0001 | ADR 目录完整性 | CI: test -d docs/adr | ✅ |
| FF-0002 | ADR 引用完整性 | CI: grep Supersedes | ✅ |
| FF-0003 | pages/ 禁止 import 旧 API | CI: grep 黑名单 | ✅ |
| FF-0006 | client.ts 只 re-export | CI: grep export function | ✅ |
| FF-0007 | store 禁止调用 API | CI: grep import from api/ | ✅ |
| FF-014 | auth_routes.py 端点完整性 | CI: `ff-auth-endpoints` | ✅ |
| FF-015 | 前端路由守卫覆盖 | CI: `ff-route-guard` | ✅ |
| FF-016 | 8 子路由 mount 完整性 | CI: `ff-sub-router-mount` + pytest | ✅ |
| FF-017 | main_routes.py 端点零残留 | CI: `pytest::test_main_routes_residual_is_zero` | ✅ |
| **FF-020** | **ruff 零错误（CI blocking）** | `ruff check . --output-format=github`（no continue-on-error） | ✅ CI 强制 |
| **FF-022** | **Vitest 前端单元测试通过** | `bun run test`（CI frontend job） | ✅ CI 强制 |
| **FF-023** | **Playwright E2E 通过** | `bunx playwright test`（CI frontend job） | ✅ CI 强制 |

### 手动验证（5个）

| FF | 检测项 | 执行方式 | 优先级 | 状态 |
|----|--------|----------|--------|------|
| FF-011 | 前端 API ↔ 后端路由对齐 | 手动交叉验证（见COMPASS矩阵） | P1 | ⚠️ 待验 |
| FF-012 | Mock 数据零容忍 | 人工 review 每个 page | P1 | ✅ 全部真实 API |
| FF-013 | 侧边栏路由一致性 | 人工验证 user/role/create 三级 | P2 | ✅ |
| FF-018 | 前端 ESLint 零错误零警告 | `npm run lint` 零退出码 | P1 | ✅ |
| FF-019 | shisi ruff+mypy 零错误 | `ruff check shisi/` + `mypy shisi/` | P2 | ✅ |

### 新增：CI 每周卫生（weekly-hygiene）

| job | 触发 | 内容 |
|-----|------|------|
| `weekly-hygiene` | `schedule` 事件（cron） | 聚合后端 + 前端全量检查结果，确保 lint/type/test/E2E 全部通过 |

### CI 加固变更（2026-06-03）

- **ruff check**：移除 `continue-on-error: true` → blocking（FF-020 守护）
- **mypy check**：新增 step，`--ignore-missing-imports`，硬性通过
- **vitest**：frontend job 新增 `bun run test`
- **Playwright E2E**：frontend job 新增 `bunx playwright test`
- **weekly-hygiene**：新增汇总 job，仅 schedule 触发时跑

---

## FF-011: 前后端 API 对齐

详见 COMPASS.md「前后端 API 对齐状态」矩阵。

**已知未对齐：**
1. `/shisi/*` — system.ts 17 处旧路由引用，需迁移到新 /api/* 端点
2. `query.ts` — 前端存在但后端对应路由未确认

---

## FF-012: Mock 数据零容忍

**状态（2026-06-03 确认）**：

| 页面 | Mock 状态 | 证据 |
|------|----------|------|
| UsersPage | ✅ 已修复 | API listUsers 已接 |
| SettingsSecurity | ✅ 已修复 | API safety/* 已接 |
| SettingsLLM | ✅ 已修复 | 真实参数表单 |
| ToolsDashboard | ✅ 已修复 | 工具开关UI |
| SettingsLogs | ✅ 已修复 | logs API 已接 |
| WeChatPage | ✅ 已验证 | API channels/wechat/status 已接 |
| UserWorkspace | ✅ 已验证 | API listCharacters 已接 |
| CreateRole | ✅ 已验证 | chat API + characterBuilderStore |
| RoleSettings | ✅ 已验证 | 已接真实 API |
| StatusCenter | ✅ 已验证 | 已接 API dashboardStats |
| StorylinePage | ✅ 已验证 | 已接 API storyline |
| SettingsVoice | ✅ 已验证 | 已接 mimo API |
| LoginPage | ✅ N/A | authStore + auth API |

---

## FF-013: 侧边栏路由一致性

**验证矩阵（需更新，上次验证 2026-05-30）**：

| 路由 | 侧边栏层级 | 用户区 | 创建角色 | 角色功能 | 人设卡 | 角色列表 |
|------|-----------|--------|---------|---------|--------|---------|
| /wechat | global | — | — | — | — | — |
| /users | global | — | — | — | — | — |
| /users/:id | user | ✅ | ✅ | — | — | ✅ |
| /users/:id/roles/create | role | ✅ | ✅ | ✅ | ✅ (builder) | ✅ |
| /users/:id/roles/:rid/settings | role | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 审计节奏

| 频率 | 检查项 |
|------|--------|
| 每次 PR | FF-0001~0007 (CI 自动) |
| 每周 | FF-011 (API对齐) + FF-012 (Mock检查) |
| 每季度 | L5-L8 更新 + ADR 有效性 + Comprehension Audit + Bus Factor |

---

## 重点待办（2026-06-03）

### ✅ 已完成汇总
- [x] **P0-1 修复**：invites.ts 双重 baseURL → `/api/api/...` 404 ✅（commit `5b4e90a`）
- [x] **P0-2 修复**：JWT_SECRET 弱默认改 fail-fast ✅（commit `1a6efe6`）
- [x] **P0-5 修复**：RoleGuard 校验 user.is_active ✅（commit `27b77cd`）
- [x] **P0-6 修复**：accessToken 内存闭包 + refreshToken httpOnly cookie ✅（commit `986117b`）
- [x] **P0-7 修复**：Orchestrator 兼容 character_id 签名 ✅（commit `0b25faf`）
- [x] **P0-8 修复**：OptimizedOrchestrator 加 process_message_stream ✅（commit `feb259e`）
- [x] **CI 加固**：ruff/mypy/vitest/playwright 全部 blocking（commit `cbaa724`）
- [x] **品牌清洗**：全库 'AI虚拟伴侣'→'AI伙伴'，persona_card 默认名 '十四'（commit `251c68c`）
- [x] **前端测试框架落地**：Vitest 4/4 + Playwright 3/3 ✅
- [x] **P1 Backlog 文档化**：18 个 P1 条目（`docs/P1_BACKLOG.md`）
- [x] **FF-018~023 全部落库**：ESLint / shisi / 全工程 ruff+mypy / pytest / vitest / playwright

### P2 — 加固（待办）
- [ ] AdminInvitesPage 注册路由
- [ ] CONTRIBUTING.md 贡献指南
- [ ] Bus Factor 改善（知识转移 + 文档）
- [ ] 前端组件目录规范化（shared/ 和 common/ 边界梳理）
- [ ] 18 个 P1 条目清理（见 `docs/P1_BACKLOG.md`）

---

> 更新记录：2026-06-01 — P0 LoginPage 接入 / P1 shisi 死代码清理 / Mock 4页复检 / FF-014/015 CI 实现
> 2026-06-01（重构）— main_routes.py 拆分为 8 个子路由（71 端点）/ FF-016/017 落库 / test_api_routes.py 14/14 PASS / 2.15s
> 2026-06-01（+1d）— **P0 main_routes.py 拆分**（1313→95 行，8 子路由 71 端点）/ verify_refactor.py 6/6 / test_api_routes.py 14/14 / **FF-016/017 上线**
> 2026-06-01（+2d）— **全工程 ruff 0 errors**（97→0，含 79 自动修 + 8 文件手工改）/ mypy 0 / pytest 538 / **FF-020 落库**（commit `3af3a14`）/ scripts/ 纳入 per-file-ignores
> 2026-06-02 — **P0 内测邀请码系统**（后端 InviteCode + admin CRUD + 前端 AdminInvitesPage + 15/15 测试）/ **Vitest+Playwright 框架落地**（4 vitest + 3 playwright 实跑通过）/ pytest 625 passed, 0 regression / 0 orphan pages / **FF-021/022/023 落库**（commit `881f2d4`）
> 2026-06-03 — **P0 全面修复 6/8** + **CI 加固（全部 blocking）** + **品牌清洗（'唯一的你'/'十四'）** / pytest 626+ / vitest 4/4 / playwright 3/3

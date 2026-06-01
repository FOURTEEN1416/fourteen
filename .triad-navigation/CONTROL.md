# CONTROL — 闭环控制（Fitness Functions + 审计节奏）

> 项目：AI Girlfriend
> 更新日期：2026-06-01（全面审计） | 审计人：歆歆

---

## 活跃 Fitness Functions

### CI 中实际运行（5个）

| # | 检测项 | 实现方式 | 有效 |
|---|--------|----------|------|
| FF-0001 | ADR 目录完整性 | CI: test -d docs/adr | ✅ |
| FF-0002 | ADR 引用完整性 | CI: grep Supersedes | ✅ |
| FF-0003 | pages/ 禁止 import 旧 API | CI: grep 黑名单 | ✅ |
| FF-0006 | client.ts 只 re-export | CI: grep export function | ✅ |
| FF-0007 | store 禁止调用 API | CI: grep import from api/ | ✅ |

### 手动验证（3个）

| FF | 检测项 | 执行方式 | 优先级 |
|----|--------|----------|--------|
| FF-011 | 前端 API ↔ 后端路由对齐 | 手动交叉验证（见COMPASS矩阵） | P1 |
| FF-012 | Mock 数据零容忍 | 人工 review 每个 page | P1 |
| FF-013 | 侧边栏路由一致性 | 人工验证 user/role/create 三级 | P2 |

### ✅ 本会话已实现（4个）

| FF | 检测项 | CI job | 状态 |
|----|--------|--------|------|
| FF-014 | auth_routes.py 端点完整性 | `ff-auth-endpoints` 检查 5 端点存在 | ✅ |
| FF-015 | 前端路由守卫覆盖 | `ff-route-guard` 检查 ProtectedLayout + AuthGuard | ✅ |
| FF-016 | 8 子路由 mount 完整性 | `pytest tests/test_api_routes.py` 核对 71 端点 100% 挂载 | ✅ |
| FF-017 | main_routes.py 端点零残留 | `pytest::test_main_routes_residual_is_zero` 阻止再膨胀 | ✅ |

---

## FF-011: 前后端 API 对齐

详见 COMPASS.md「前后端 API 对齐状态」矩阵。

**已知未对齐：**
1. `/shisi/*` — system.ts 17 处旧路由引用，需迁移到新 /api/* 端点
2. `query.ts` — 前端存在但后端对应路由未确认

---

## FF-012: Mock 数据零容忍

**状态（2026-06-01 审计）**：

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
| RoleSettings | ⚠️ 待验证 | 上期标记 "29处MOCK_CHARACTER" → 需复检 |
| StatusCenter | ⚠️ 待验证 | 上期标记 "全部假数据" → 需复检 |
| StorylinePage | ⚠️ 待验证 | 组件就绪，后端404 → 需复检 |
| SettingsVoice | ⚠️ 待验证 | 上期标记 "mock→待接API" → 需复检 |
| LoginPage | ✅ N/A | 新页面，使用 authStore |

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

## 重点待办（2026-06-01）

### ✅ 本会话已完成
- [x] **P0 LoginPage 接入 App.tsx**：AuthGuard + ProtectedLayout + /login 路由全部就绪 ✅
- [x] **P1 system.ts 17 处 /shisi/* 死代码清理** — 函数/hooks/queryKeys/re-exports 全部删除，tsc+build 通过 ✅
- [x] **P1 Mock 复检**：RoleSettings / StatusCenter / SettingsVoice / StorylinePage 全部真实 API ✅
- [x] **P1 FF-014/015 CI 实现**：ff-auth-endpoints + ff-route-guard 加入 ci.yml ✅

### ✅ 本轮已修复
- [x] auth_routes.py 注册到 app_factory.py ✅（POST /api/auth/login|register|refresh|logout + GET /api/auth/me）
- [x] **main_routes.py 拆分** ✅（1313 行/71 端点 → 8 个子路由 + main_routes 缩至 95 行仅留 6 模型/4 常量/1 Helper/空 router 占位）
- [x] **verify_refactor.py 6/6 PASS** ✅（import 8 子路由 OK / main_routes 导出 OK / 残余=0 / 端点分布=71 / 无重复 / create_api_app() 启动 168 路由）
- [x] **tests/test_api_routes.py 14/14 PASS** ✅（2.15s · parametrize × 8 子路由 mount + 3 单测 + 2 live HTTP 烟测）
- [x] **P0 main_routes.py 拆分重构**（1313 行 → 95 行）✅
  - 8 子路由：`_misc_routes(10)` / `_chat_routes(10)` / `_personality_routes(9)` / `_users_routes(7)` / `_training_routes(11)` / `_tools_routes(5)` / `_safety_routes(12)` / `_clone_routes(7)` = 71 端点
  - `app_factory.py` 改为 8 个 `include_router`，`main_routes.py` 保留 6 模型 + 4 常量 + `_sanitize_config` + 空 router 占位
  - 验证脚本 `verify_refactor.py` 6/6 PASS
  - 测试套件 `tests/test_api_routes.py` 14/14 PASS（2.15s，零外部依赖）
  - **FF-016/017 加入 CI 防止回归**

### P2 — 加固
- [ ] Vitest + RTL 前端测试
- [ ] CONTRIBUTING.md 贡献指南
- [ ] Bus Factor 改善（知识转移 + 文档）
- [ ] 前端组件目录规范化（shared/ 和 common/ 边界梳理）

---

> 更新记录：2026-06-01 — P0 LoginPage 接入 / P1 shisi 死代码清理 / Mock 4页复检 / FF-014/015 CI 实现
> 2026-06-01（重构）— main_routes.py 拆分为 8 个子路由（71 端点）/ FF-016/017 落库 / test_api_routes.py 14/14 PASS / 2.15s
> 更新记录：2026-06-01（+1d）— **P0 main_routes.py 拆分**（1313→95 行，8 子路由 71 端点）/ verify_refactor.py 6/6 / test_api_routes.py 14/14 / **FF-016/017 上线**

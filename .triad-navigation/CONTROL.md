# CONTROL — 闭环控制（Fitness Functions + 审计节奏）

> 项目：AI Girlfriend
> 更新日期：2026-05-30（设计固化+API对齐） | 审计人：歆歆

---

## 活跃 Fitness Functions

### CI 中实际运行

| # | 检测项 | 实现方式 | 有效 |
|---|--------|----------|------|
| FF-0001 | ADR 目录完整性 | test -d docs/adr | ✅ |
| FF-0002 | ADR 引用完整性 | grep Supersedes | ✅ |
| FF-0003 | pages/ 禁止 import 旧 API | grep 黑名单 | ✅ |
| FF-0006 | client.ts 只 re-export | grep export function | ✅ |
| FF-0007 | store 禁止调用 API | grep import from api/ | ✅ |

### 本次新增 FF

| FF | 检测项 | 执行方式 | 优先级 |
|----|--------|----------|--------|
| FF-011 | 前端 API ↔ 后端路由对齐 | 手动交叉验证（见下方矩阵） | P1 |
| FF-012 | Mock 数据零容忍 | 人工 review 每个 page | P1 |
| FF-013 | 侧边栏路由一致性 | 人工验证 user/role/create 三级 | P2 |

---

## FF-011: 前后端 API 对齐

**检查方式**：对比 frontend/src/api/ 模块调用的路径与 backend api/routers/ 注册的路由。

**路由前缀规则**：所有子路由统一 prefix="/api"（character/voice/mimo/storyline/wechat/emotion）。

**对齐矩阵（2026-05-30 实测）**：

| 前端模块 | 调用路径 | 后端路由 | 方法 | HTTP |
|---------|---------|---------|------|------|
| chat.ts | /chat | /api/chat | POST | 200 ✅ |
| chat.ts | /chat/stream | /api/chat/stream | POST | — |
| chat.ts | /chat/history | /api/chat/history | GET | — |
| chat.ts | /emotion/state | /api/emotion/state | GET | — |
| chat.ts | /emotion/trend | /api/emotion/trend | GET | — |
| chat.ts | /session | /api/session | POST | — |
| chat.ts | /sessions | /api/sessions | GET | — |
| users.ts | /users | /api/users | GET | 200 ✅ |
| users.ts | /users/{id} | /api/users/{user_id} | GET | — |
| users.ts | /users/{id}/chat | /api/users/{user_id}/chat | GET | — |
| users.ts | /users/{id}/emotion | /api/users/{user_id}/emotion | GET | — |
| users.ts | /users/{id}/role | /api/users/{user_id}/role | POST | — |
| users.ts | /users/{id}/reset | /api/users/{user_id}/reset | POST | — |
| training.ts | /training/* | /api/training/* | POST/GET | — |
| clone.ts | /clone/* | /api/clone/* | GET/DELETE | — |
| characters.ts | /characters | /api/characters | GET | 200 ✅ |
| characters.ts | /characters/import | /api/characters/import | POST | 404 ⚠️ |
| characters.ts | /mimo/status | /api/mimo/status | GET | 500 ⚠️ |
| characters.ts | /mimo/clone | /api/mimo/clone | POST | — |
| characters.ts | /mimo/design | /api/mimo/design | POST | — |
| characters.ts | /mimo/synthesize | /api/mimo/synthesize | POST | — |
| system.ts | /health | /api/health | GET | 200 ✅ |
| system.ts | /config | /api/config | GET/POST | — |
| system.ts | /rag/* | /api/rag/* | GET/POST | — |
| system.ts | /safety/* | /api/safety/* | GET/POST | — |
| system.ts | /voice/status | /api/voice/status | GET | — |
| system.ts | /voice/speakers | /api/voice/speakers | GET | 200 ✅ |
| system.ts | /tools | /api/tools | GET | — |
| system.ts | /proactive/* | /api/proactive/* | GET/POST | — |
| system.ts | /psych/* | /api/psych/* | GET/DELETE | — |
| system.ts | /persona/* | /api/persona/* | GET | — |
| system.ts | /memory/facts | /api/memory/facts | GET | — |
| system.ts | /plugins | /api/plugins | GET | — |
| system.ts | /stats | /api/stats | GET | — |
| system.ts | /stats/dashboard | /api/stats/dashboard | GET | — |
| system.ts | /logs | /api/logs | GET | — |
| system.ts | /channels/* | /api/channels/* | GET/POST | — |
| system.ts | /files/upload | /api/files/upload | POST | — |

**未对齐项：**
1. `/api/characters/import` → HTTP 404（后端路由 `/api/characters/import` POST 未生效）
2. `/api/mimo/status` → HTTP 500（轻量启动下 MiMo 未初始化）
3. `/api/shisi/*` → system.ts 仍引用旧架构路由（5个），需迁移到新 /api/* 端点

---

## FF-012: Mock 数据零容忍

**检查方式**：人工 review 每个 page/*.tsx 的 useState 初始值和数据加载逻辑。

**状态（2026-05-30）**：

| 页面 | Mock 状态 | 修复计划 |
|------|----------|---------|
| UsersPage | ✅ 已修复 | listUsers() API |
| SettingsSecurity | ✅ 已修复 | safetyStats/Log/Config API |
| SettingsLLM | ✅ 已修复 | 真实参数表单 |
| ToolsDashboard | ✅ 已修复 | 工具开关UI |
| SettingsLogs | ✅ 已修复 | logs API |
| WeChatPage | ✅ | channels/wechat/status API |
| UserWorkspace | ✅ | listCharacters API |
| CreateRole | ✅ 已修复 | chat API + characterBuilderStore |
| RoleSettings | 🔴 P0 | 基础tab mock数据 → 待接API |
| StatusCenter | 🔴 P0 | 全部假数据 → 待接API |
| StorylinePage | 🔴 P0 | 后端API 404 → 待修 |
| SettingsVoice | ⚠️ P1 | mock → 待接API |

---

## FF-013: 侧边栏路由一致性

**检查方式**：人工验证 Sidebar 在不同路由下的导航结构。

**验证矩阵（2026-05-30）**：

| 路由 | 侧边栏层级 | 用户区 | 创建角色 | 角色功能 | 人设卡 | 角色列表 |
|------|-----------|--------|---------|---------|--------|---------|
| /wechat | global | — | — | — | — | — |
| /users | global | — | — | — | — | — |
| /users/:id | user | ✅ | ✅ | — | — | ✅ |
| /users/:id/roles/create | role | ✅ | ✅ | ✅ | ✅ (builder) | ✅ |
| /users/:id/roles/:rid/settings | role | ✅ | ✅ | ✅ | ✅ (待API) | ✅ |

---

## 审计节奏

| 频率 | 检查项 |
|------|--------|
| 每次 PR | FF-0001~0007 (CI 自动) |
| 每周 | FF-011 (API对齐) + FF-012 (Mock检查) |
| 每季度 | L5-L8 更新 + ADR 有效性 + Comprehension Audit |

---
> 更新记录：2026-05-30 — 新增 FF-011/012/013，更新对齐矩阵和Mock状态

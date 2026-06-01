# 三体导航全面审计报告
> 审计人：歆歆 (QwenPaw 协调者)
> 审计方法：Triad Navigation 全量扫描（Map + Compass + Control）
> 当前分支：`arch/client-split-4A`（领先 main 20 个 commit，265 文件变更）

---

## 执行摘要

| 维度 | 评分 | 趋势 |
|------|------|------|
| 后端完整性 | **92%** | ↑ 162 路由，524/527 测试通过 |
| 前端路由 | **85%** | ↑ 13 页面全注册，0 orphan |
| Mock 治理 | **60%** | → RoleSettings 仍全 mock |
| 测试覆盖 | **5%** | → 后端有测试，前端零测试 |
| 架构治理 | **70%** | ↑ 三体文件齐全，ADR→FF 映射不完整 |
| Build 健康 | **100%** | ✅ tsc 零错误 |
| **综合** | **68%** | 可用，有 P0 待修 |

---

## MAP — 8 层代码地图

### L1 — 项目表面

| 项目 | 状态 | 备注 |
|------|------|------|
| README.md | ✅ | 内容详实，含快速开始/结构/约定 |
| LICENSE | ✅ | MIT |
| CI badge | ✅ | .github/workflows/ci.yml |
| 贡献指南 | ❌ | 无 CONTRIBUTING.md |

### L2 — 结构架构

**后端（Python 3.12 + FastAPI）** — 162 条路由

| 目录 | 路由数 | 状态 |
|------|--------|------|
| api/main_routes.py | ~40 | ✅ |
| api/routers/character_routes.py | ~30 | ✅ |
| api/routers/voice_routes.py | ~6 | ✅ |
| api/routers/mimo_voice_routes.py | ~6 | ✅ |
| api/routers/storyline_routes.py | ~6 | ✅ |
| api/routers/wechat_routes.py | ~4 | ✅ |
| api/routers/emotion_routes.py | ~6 | ✅ |
| api/routers/knowledge_routes.py | ~10 | ✅ |
| shisi/api/ (旧架构) | ~50 | ⚠️ 逐步废弃 |

**前端（React 18 + Vite + TypeScript + Tailwind）** — 13 个页面

| 页面 | 行数 | 数据源 | Mock? |
|------|------|--------|-------|
| RoleSettings.tsx | 564 | MOCK_CHARACTER 硬编码 | 🔴 全 mock |
| WeChatPage.tsx | 426 | API (channels/wechat/status) | ✅ |
| SettingsVoice.tsx | 462 | API (mimo/*) | ✅ 已修 |
| SettingsLLM.tsx | 289 | 真实参数表单 | ✅ |
| UserWorkspace.tsx | 259 | API (listCharacters) | ✅ |
| CreateRole.tsx | 206 | API (chat) + store | ✅ |
| UsersPage.tsx | 195 | API (listUsers) | ✅ |
| SettingsLogs.tsx | 179 | API (logs) | ✅ |
| SettingsSecurity.tsx | 160 | API (safety/*) | ✅ |
| ToolsDashboard.tsx | 111 | API (tools) | ✅ |
| StatusCenter.tsx | 51 | 无数据加载 | ⚠️ 空壳 |
| StorylinePage.tsx | 内联 | API (storyline) | ✅ 路由已注册 |
| NotFoundPage.tsx | 15 | 静态 | ✅ |

### L3 — 行为架构

```
前端 (:5173) → Vite proxy → FastAPI (:8000)
  ↓
  api/main_routes.py        (/api/chat, /api/users, /api/health, ...)
  api/routers/character*    (/api/characters/*)
  api/routers/voice*        (/api/voice/*)
  api/routers/mimo_voice*   (/api/mimo/*)
  api/routers/storyline*    (/api/characters/:id/storyline)
  api/routers/wechat*       (/api/wechat/*)
  api/routers/emotion*      (/api/emotion/*)
  api/routers/knowledge*    (/api/characters/:id/knowledge/*)
  shisi/api/                (旧架构，逐步废弃)
```

**前端数据流：**
```
页面组件 → React Query hooks (useQueries.ts)
         → api/client.ts (axios, baseURL=/api)
         → 按域拆分的 API 模块 (chat.ts/users.ts/characters.ts/...)
         → Zustand store (chatStore/errorStore/characterBuilderStore)
```

### L4 — 配置与环境

| 配置项 | 值 | 状态 |
|--------|-----|------|
| 前端端口 | :5173 (Vite Dev) | ✅ |
| 后端端口 | :8000 (FastAPI) | ✅ |
| API 前缀 | /api (Vite proxy) | ✅ |
| 鉴权 | X-API-Key | ✅ |
| PostgreSQL | 15, localhost:5432 | ✅ 可用 |
| SQLite | data/sqlite.db | ✅ 当前使用 |
| Node.js | v24.14 (D:\node.exe) | ✅ |
| Python | 3.12 + pip | ✅ |
| llm_providers.json | UTF-8 BOM ⚠️ | 启动警告 |

### L5 — 风险热点

| # | 风险 | 级别 | 详情 |
|---|------|------|------|
| 1 | **RoleSettings 全 mock** | 🔴 P0 | 564 行，29 处 MOCK_CHARACTER 引用，6 个 tab 全假数据 |
| 2 | **StatusCenter 空壳** | 🔴 P0 | 仅 51 行，无数据加载逻辑 |
| 3 | **shisi 旧路由残留** | ⚠️ P1 | system.ts 中 26 处 /shisi/* 调用 |
| 4 | **前端零测试** | ⚠️ P2 | vitest/playwright 未配置 |
| 5 | **Bus Factor = 1** | ⚠️ P2 | 仅默默一人维护 |
| 6 | **ADR 编号不连续** | ⚠️ P2 | docs/adr/ 有 0001-0006，COMPASS 引用 011-013 无物理文件 |
| 7 | **llm_providers.json BOM** | ℹ️ P3 | 启动警告，不影响功能 |

**已消除的风险：**
- ~~StorylinePage 后端API 404~~ ✅ 路由已注册，组件就绪
- ~~SettingsVoice mock~~ ✅ 已接真实 API
- ~~SettingsSecurity mock~~ ✅ 已接真实 API
- ~~UsersPage mock~~ ✅ 已接真实 API
- ~~21 orphan 页面~~ ✅ 全注册路由
- ~~glass-card 未定义~~ ✅ 已补

### L6 — 演化历史

| 阶段 | 变更 | 日期 |
|------|------|------|
| Phase 1-4 | 基础框架→安全模块→前端审计→架构重构 | ~ 2026-05-28 |
| Phase 5 | 三体导航建立 | 2026-05-29 |
| Phase 6 | 前端设计重构 | 2026-05-29 |
| Phase 7 | 统一设计框架 | 2026-05-30 |
| Phase 8 | 设计稿清除 + API对齐 + 三体固化 | 2026-05-30 |
| **Phase 9** | **全面审计** | **2026-05-31** |

### L7 — 归属

| 模块 | 负责人 | Bus Factor |
|------|--------|-----------|
| 全项目 | 默默 (FOURTEEN1416) | **1** ⚠️ |

### L8 — 约定

| 规范 | 规则 | 自动化 |
|------|------|--------|
| 命名 | Python snake_case, TS camelCase | 手动 |
| API 路由 | 平铺 /api/*，子路由统一 prefix | 手动 |
| 状态管理 | 服务端→React Query；UI→Zustand | 手动 |
| 组件分层 | business→components/；shared→components/shared/ | 手动 |
| 类型安全 | TypeScript strict, tsc --noEmit | CI ✅ |

---

## COMPASS — 指南针审计

### 7 条设计原则执行度

| # | 原则 | 执行度 | 备注 |
|---|------|--------|------|
| 1 | 前后端分离 | ✅ 95% | 前端只调 /api/* |
| 2 | API 平铺路由 | ✅ 90% | shisi 残留例外 |
| 3 | 认证统一 | ✅ 100% | 所有路由有 verify_api_key_dep |
| 4 | 组件分层 | ✅ 90% | shared/layout/storyline 分层清晰 |
| 5 | 代码即标准 | ✅ 100% | 设计稿已清除 |
| 6 | Mock 零容忍 | 🔴 60% | RoleSettings 仍全 mock |
| 7 | CSS 声明即资源 | ✅ 95% | glass-card 已定义 |

### ADR 体系

**物理文件（docs/adr/）：6 个，全部 Accepted**

**COMPASS.md 内联 ADR：3 个（ADR-011/012/013），无物理文件**

**ADR→FF 映射率：6/9 = 67%** ✅（目标 >50%）

---

## CONTROL — 闭环控制审计

### Fitness Functions

**CI 自动运行（5 个）：** FF-0001~0007 ✅ 全部通过
**人工检查（3 个）：** FF-011 ⚠️ shisi残留 | FF-012 ⚠️ RoleSettings mock | FF-013 ✅

### 三层执行覆盖

| 层 | 覆盖 | 备注 |
|----|------|------|
| 实时（PR gate） | ✅ | FF-0001~0007 in CI |
| 周期（每周） | ❌ | FF-011/012 未自动化 |
| 节奏（每季度） | ✅ | 本次首次审计 |

---

## 行动计划

### P0 — 立即修复
| # | 任务 | 预估 |
|---|------|------|
| 1 | RoleSettings 接入真实 API（替换 MOCK_CHARACTER） | 2h |
| 2 | StatusCenter 接入真实 API（stats/dashboard） | 1h |

### P1 — 本周完成
| # | 任务 | 预估 |
|---|------|------|
| 3 | system.ts 清理 shisi 旧路由（26 处） | 2h |
| 4 | COMPASS ADR-011/012/013 补物理文件 | 30min |
| 5 | 统一 ADR 编号 | 15min |
| 6 | 修复 llm_providers.json BOM | 5min |

### P2 — 加固
| # | 任务 | 预估 |
|---|------|------|
| 7 | 前端测试框架（vitest + RTL） | 4h |
| 8 | FF-011/012 自动化脚本 | 2h |
| 9 | CONTRIBUTING.md | 30min |
| 10 | Bus Factor 改善 | 持续 |

---

> 审计完成时间：2026-05-31 22:00 UTC+8
> 下次审计：2026-08-31（季度节奏）

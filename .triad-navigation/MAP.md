# 三体导航地图 — AI Girlfriend 项目

> 更新日期：2026-05-30（设计固化+API对齐） | 审计人：歆歆 (QwenPaw 协调者)
> 当前分支：main | 前端设计版本：v2.0（侧边栏+工作区统一框架）

---

## L1 — 项目表面

| 项目 | 状态 |
|------|------|
| README.md | 存在，内容详实 |
| LICENSE | MIT 许可证 |
| CI badge | .github/workflows/ci.yml 已配置 |
| 贡献指南 | 无 |

## L2 — 结构架构

### 后端（Python 3.12 + FastAPI）

| 目录 | 用途 | 状态 |
|-----|------|------|
| api/ | REST API 路由层 (main_routes + routers/) | ✅ 就绪 |
| api/routers/ | 域路由 (角色/语音/剧情/记忆/知识/微信/情绪) | ✅ 就绪（全部 prefix="/api"） |
| api/state/ | 运行时状态管理 | ✅ 就绪 |
| shisi/ | 核心业务域 | ✅ 就绪 |
| security/ | 4安全模块 | ✅ 就绪 |
| rag_engine/ | RAG 检索引擎 | ✅ 就绪 |
| persona_extractor/ | 人设提取+心理画像 | ✅ 就绪 |
| llm_provider/ | LLM 接入层 | ✅ 就绪 |
| voice/ | TTS 引擎 | ✅ 就绪 |
| wechat_direct/ | 微信直连通道 | ✅ 就绪 |
| config/ | YAML 配置 | ✅ 就绪 |
| tests/ | Pytest (498+ 用例) | ✅ 就绪 |
| main.py | 入口 (Orchestrator) | ✅ 就绪 |

### 前端（React 18 + Vite 8 + TypeScript + Tailwind CSS）

| 目录 | 用途 | 状态 |
|-----|------|------|
| src/pages/ | 14个页面 (全部注册路由) | ✅ |
| src/components/layout/ | 布局组件 (Sidebar/Breadcrumb/MobileNav) | ✅ |
| src/components/shared/ | 通用UI组件 (Slider/TagInput/Toggle/Badge...) | ✅ |
| src/components/storyline/ | 剧情线编辑器 | ✅ |
| src/api/ | 10个API模块 (按域拆分) | ✅ |
| src/hooks/ | 自定义 Hooks (React Query) | ✅ |
| src/store/ | Zustand 状态 (chatStore/errorStore/characterBuilderStore) | ✅ |
| src/types/ | TS 类型 | ✅ |

### 前端页面清单（14页，0 orphan）

| 页面 | 路由 | 数据来源 | 状态 |
|------|------|----------|------|
| WeChatPage | /wechat | API (channels/wechat/status) | ✅ |
| UsersPage | /users | API (listUsers) | ✅ |
| UserWorkspace | /users/:userId | API (listCharacters) | ✅ |
| CreateRole | /users/:userId/roles/create | API (chat) + characterBuilderStore | ✅ 新设计 |
| RoleSettings | /users/:userId/roles/:roleId/settings | Mock → 待接API | ✅ 新设计 |
| StatusCenter | /users/:userId/roles/:roleId/status | Mock → 待接API | ⚠️ |
| StorylinePage | /users/:userId/roles/:roleId/storyline | API (storyline) | ⚠️ 待修 |
| SystemSettingsLayout | /settings | 布局壳 | ✅ |
| SettingsLLM | /settings/llm | 真实参数表单 | ✅ |
| SettingsVoice | /settings/voice | Mock → 待接API | ⚠️ |
| ToolsDashboard | /settings/tools | 工具开关UI | ✅ |
| SettingsSecurity | /settings/security | API (safety/*) | ✅ |
| SettingsLogs | /settings/logs | API (logs) | ✅ |
| NotFoundPage | * | 静态 | ✅ |

**已删除页面：** SettingsGeneral (合并到LLM) · SettingsExtensions (用户要求删除)

**设计稿清理（2026-05-30）：** v2/ · concepts-overview.html · .superpowers/brainstorm/ → 全部删除。前端设计以代码为准。

## L3 — 行为架构

```
用户请求 → Vite Dev (:5173) → /api/* proxy → FastAPI (:8000)
                                              ├─ main_routes.py (/api/chat, /api/users, ...)
                                              ├─ character_routes.py (/api/characters/*)
                                              ├─ voice_routes.py (/api/characters/:id/voice)
                                              ├─ mimo_voice_routes.py (/api/mimo/*)
                                              ├─ storyline_routes.py (/api/characters/:id/storyline)
                                              ├─ wechat_routes.py (/api/wechat/connections)
                                              └─ emotion_routes.py (/api/emotion/params)
```

**前端数据流：**
```
页面组件 → React Query hooks (useQueries.ts)
         → api/client.ts (axios, baseURL=/api)
         → 按域拆分的 API 模块 (chat.ts/users.ts/characters.ts/...)
         → Zustand store (chatStore/errorStore/characterBuilderStore)
```

**侧边栏路由感知：**
```
useRouteLevel() → 解析URL → 三级导航 (global/user/role)
  global: 微信控制台 · 用户列表 · 系统设置
  user:   返回用户列表 · 用户#ID · 创建角色 · 我的角色
  role:   返回用户列表 · 用户#ID · 创建角色 · 角色功能 · 人设卡 · 角色列表
```

## L4 — 配置与环境

| 配置项 | 值 | 状态 |
|--------|-----|------|
| 前端端口 | :5173 (Vite Dev) | ✅ |
| 后端端口 | :8000 (FastAPI + uvicorn) | ✅ |
| API 前缀 | /api (Vite proxy → localhost:8000) | ✅ |
| 子路由前缀 | 统一 /api (character/voice/mimo/wechat/...) | ✅ |
| API 鉴权 | X-API-Key (主路由+子路由均覆盖) | ✅ |
| PostgreSQL | 15, localhost:5432 | ✅ |
| SQLite | data/sqlite.db (25 characters, 7 sessions) | ✅ |
| Node.js | D:\node.exe v24.14 | ✅ |
| Python | 3.12 + pip | ✅ |

## L5 — 风险热点

| # | 风险 | 级别 | 状态 |
|---|------|------|------|
| 1 | RoleSettings 全mock → 需接API | P0 | 🔴 本次设计已定稿，待接后端 |
| 2 | StatusCenter 假数据 + React key 错误 | P0 | 🔴 待修 |
| 3 | StorylinePage 后端API 404 | P0 | 🔴 待修 |
| 4 | SettingsVoice mock | P1 | ⚠️ 待修 |
| 5 | 前端零测试 — vitest/playwright 未配置 | P2 | 📋 规划中 |
| 6 | CI tsc --noEmit continue-on-error:true | P2 | ⚠️ 门禁无效 |
| 7 | 前端 /shisi/* API 调用指向旧架构 | P2 | ⚠️ system.ts 仍有旧路由引用 |
| 8 | ADR 物理文件与 COMPASS 编号不同步 | P2 | 🔴 治理缺口 |

**已消除的风险：**
- ~~UsersPage mock~~ ✅ 真实API
- ~~SettingsExtensions mock~~ ✅ 页面已删除
- ~~glass-card 未定义~~ ✅ 已补
- ~~21 orphan 页面~~ ✅ 全注册
- ~~SettingsSecurity mock~~ ✅ 真实API
- ~~设计稿保护策略~~ ✅ 设计稿已清除，代码即标准

## L6 — 演化历史

| 阶段 | 变更 | 日期 |
|------|------|------|
| Phase 1-4 | 基础框架→安全模块→前端审计→架构重构 | 早期 ~ 2026-05-28 |
| Phase 5 | 三体导航更新 | 2026-05-29 |
| Phase 6 | 前端设计重构: SettingsLLM/ToolsDashboard/SettingsSecurity | 2026-05-29 |
| Phase 7 | 统一设计框架: RoleSettings+CreateRole重写，Sidebar重构 | 2026-05-30 |
| Phase 8 | 设计稿清除 + API对齐测试 + 三体导航固化 | 2026-05-30 |

## L7 — 归属

| 模块 | 负责人 |
|------|--------|
| 全项目 | 默默 (FOURTEEN1416) |

## L8 — 约定

| 规范 | 规则 | 自动化 |
|------|------|--------|
| 命名 | Python snake_case, TS camelCase | 手动 |
| 前端设计 | 左边栏+右工作区统一框架；侧边栏含人设卡 | 手动review |
| API路由 | 平铺 `/api/*` 风格，子路由统一 `/api` prefix | 手动 |
| 状态管理 | 服务端数据→React Query；UI状态→Zustand；跨组件通信→Context/Store | 手动 |
| 组件分层 | business→components/；shared UI→components/shared/；layout→components/layout/ | 手动 |
| 类型安全 | TypeScript strict, `tsc --noEmit` | CI |
| 提交 | feat/fix/refactor/chore | 手动 |

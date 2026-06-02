# 三体导航地图 — 唯一的你 项目

> 更新日期：2026-06-01（全面审计 + main_routes 拆分重构） | 审计人：歆歆 (QwenPaw 协调者)
> 当前分支：main（arch/client-split-4A 已合并）| 项目规模：324 Python + 92 TS/TSX 文件

---

## L1 — 项目表面

| 项目 | 状态 |
|------|------|
| README.md | ✅ 存在，内容详实 |
| LICENSE | ✅ MIT 许可证 |
| CI badge | ✅ .github/workflows/ci.yml 已配置（5 FF job + pytest + tsc + build） |
| 贡献指南 | ❌ CONTRIBUTING.md 不存在（P2 待办） |
| 项目logo | ⚠️ 无 |

## L2 — 结构架构

### 后端（Python 3.12 + FastAPI · 168 路由 · 拆分后 71 端点归 8 子路由）

| 目录 | 用途 | 状态 |
|-----|------|------|
| api/ | REST API 路由层 | ✅ |
| api/_*_routes.py (×8) | 8 子路由（拆分自 main_routes）：misc 10 / chat 10 / personality 9 / users 7 / training 11 / tools 5 / safety 12 / clone 7 = 71 端点 | ✅ 新拆分 |
| api/routers/ | 11 个域路由（character/auth/admin/voice/mimo/storyline/wechat/emotion/memory/knowledge/persona_card） | ✅ 新增 auth/admin/persona_card |
| api/main_routes.py | 6 Pydantic 模型 + 4 常量 + `_sanitize_config` + 空 router 占位（1313 → 95 行，0 端点） | ✅ 重构 |
| api/auth_jwt.py | JWT 认证工具 + bcrypt | ✅ 新增 |
| api/database.py | SQLAlchemy 引擎 + User/UserSession 模型 | ✅ 新增 |
| shisi/ | 旧业务域（逐步废弃中） | ⚠️ 前端仍有 17 引用 |
| security/ | 4 安全模块 | ✅ |
| rag_engine/ | RAG 检索引擎 | ✅ |
| persona_extractor/ | 人设提取+心理画像 | ✅ |
| llm_provider/ | LLM 接入层 | ✅ |
| voice/ | TTS 引擎 | ✅ |
| wechat_direct/ | 微信直连通道 | ✅ |
| config/ | YAML 配置 | ✅ |
| tests/ | Pytest（525 用例） | ✅ |
| main.py / orchestrator.py | 入口 / 12 步流水线 | ✅ |

### 前端（React 18 + Vite + TypeScript + Tailwind CSS · 92 TS/TSX 文件）

| 目录 | 用途 | 状态 |
|-----|------|------|
| src/pages/ | 15 个页面（含 LoginPage） | ✅ LoginPage 文件存在但未注册路由⚠️ |
| src/components/layout/ | 布局组件（Sidebar/Breadcrumb/MobileNav） | ✅ |
| src/components/shared/ | 通用UI组件 | ✅ |
| src/components/common/ | 通用业务组件（Button/Card/Toast/Badge/EmptyState etc.） | ✅ 新增 |
| src/components/auth/ | AuthGuard 路由守卫 | ✅ 新增但未接入 |
| src/components/storyline/ | 剧情线编辑器 | ✅ |
| src/components/ui/ | UI 组件（Modal） | ✅ 新增 |
| src/api/ | 12 个 API 模块（chat/users/characters/training/clone/system/wechat/auth/mimo/voice/emotion/query） | ✅ 新增 auth.ts |
| src/hooks/ | 自定义 Hooks（React Query） | ✅ |
| src/store/ | Zustand 状态（chatStore/errorStore/characterBuilderStore/authStore） | ✅ 新增 authStore |
| src/types/ | TS 类型 | ✅ |

### 前端页面清单（15页，14 已注册路由，1 孤立）

| 页面 | 路由 | 数据来源 | 状态 |
|------|------|----------|------|
| WeChatPage | /wechat | API (channels/wechat/status) | ✅ |
| UsersPage | /users | API (listUsers) | ✅ |
| UserWorkspace | /users/:userId | API (listCharacters) | ✅ |
| CreateRole | /users/:userId/roles/create | API (chat) + characterBuilderStore | ✅ |
| RoleSettings | /users/:userId/roles/:roleId/settings | API (useUnifiedCharacter) | ✅ |
| StatusCenter | /users/:userId/roles/:roleId/status | API (dashboardStats) | ✅ 待验证mock |
| StorylinePage | /users/:userId/roles/:roleId/storyline | API (storyline) | ✅ |
| SystemSettingsLayout | /settings | 布局壳 | ✅ |
| SettingsLLM | /settings/llm | 真实参数表单 | ✅ |
| SettingsVoice | /settings/voice | API (mimo/*) | ✅ |
| ToolsDashboard | /settings/tools | 工具开关UI | ✅ |
| SettingsSecurity | /settings/security | API (safety/*) | ✅ |
| SettingsLogs | /settings/logs | API (logs) | ✅ |
| NotFoundPage | * | 静态 | ✅ |
| **LoginPage** | **/login** | **authStore** | **🔴 文件存在，未注册路由，未接入App.tsx** |

## L3 — 行为架构

```
用户请求 → Vite Dev (:5173) → /api/* proxy → FastAPI (:8000)
                                                ├─ _misc_routes.py (10)
                                                ├─ _chat_routes.py (10)
                                                ├─ _personality_routes.py (9)
                                                ├─ _users_routes.py (7)
                                                ├─ _training_routes.py (11)
                                                ├─ _tools_routes.py (5)
                                                ├─ _safety_routes.py (12)
                                                ├─ _clone_routes.py (7)
                                                ├─ main_routes.py (0端点 · 仅模型+常量+Helper)
                                                ├─ character_routes.py (53)
                                               ├─ auth_routes.py (9) + admin_routes.py (6)
                                               ├─ voice_routes.py (16) + mimo_voice_routes.py (10)
                                               ├─ storyline_routes.py (27)
                                               ├─ wechat_routes.py (10)
                                               ├─ emotion_routes.py (10)
                                               ├─ memory_routes.py (4) + knowledge_routes.py (7)
                                               └─ persona_card_routes.py (3)
```

**前端数据流：**
```
页面组件 → React Query hooks → api/client.ts → 按域拆分 API 模块 → Zustand stores
```

**侧边栏路由感知（useRouteLevel 三级导航）：**
```
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
| API 鉴权 | X-API-Key（内部服务）+ JWT Bearer（用户认证）新 | ✅ 新增双体系 |
| PostgreSQL | 15, localhost:5432 | ✅ |
| SQLite | data/sqlite.db | ✅ |
| Node.js | D:\node.exe v24.14 | ✅ |
| Python | 3.12 + pip | ✅ |
| CI Python | 3.11 (GitHub Actions) | ✅ |
| 构建工具 | bun v1.3.12 (前端) | ✅ |

## L5 — 风险热点

| # | 风险 | 级别 | 状态 |
|---|------|------|------|
| 1 | **前端零测试** — vitest/playwright 未配置 | P2 | 📋 待办 |
| 2 | **Bus Factor = 1**（仅默默） | P2 | ⚠️ 有缓解文档 |
| 3 | **服务器进程在 shell 超时后被终止** — PowerShell NonInteractive 模式不保持 `Start-Process` | P3 | 📋 需用 `start_all.cmd` 或独立终端 |
| 4 | **PostgreSQL 15 服务未启动** — localhost:5432 连接拒绝，auth 使用 SQLite 回退 | P3 | 📋 需手动 `net start postgresql-15` |
| 5 | **shisi 域旧 API 路径** — 前端 system.ts 仍有 `shisi/*` 旧引用 | P2 | 📋 待清 |

**已消除的风险（上期审计后）：**
- ~~前端 Mock 数据（全页面已接 API）~~ ✅
- ~~项目还在 arch/client-split-4A~~ ✅ 已合并到 main
- ~~ADR 编号不连续~~ ✅ 已统一
- ~~ADR 无物理文件~~ ✅ 全部 10 个有物理文件
- ~~LoginPage 孤立~~ ✅ App.tsx 已接入路由 + AuthGuard
- ~~shisi 旧路由 17 处引用~~ ✅ system.ts/client.ts/useQueries.ts/useWebSocket.ts 全部清理
- ~~ADR-0014 FFs 未进 CI~~ ✅ FF-014/015 已实现 (ff-auth-endpoints + ff-route-guard)
- ~~Mock 状态待验证~~ ✅ 4页 (RoleSettings/StatusCenter/SettingsVoice/StorylinePage) 全部真实 API

**本期消除（Phase 12）：**
- ~~auth 3 个运行时 bug（is_expired naive/aware、register 缺 commit、jti 碰撞）~~ ✅ 已修复并提交 39a5a71
- ~~API 模块健康状态未知~~ ✅ 20+ 端点全面验证通过（stats/chat/characters/emotion/persona/psych/memory/tools/voice/mimo/wechat/shisi/knowledge/storyline）
- ~~TypeScript 编译状态未知~~ ✅ `tsc --noEmit` 零错误 + `vite build` 生产构建成功
- ~~一次性测试文件残留~~ ✅ 已清理（test_auth_jti.py + 10 个 test 脚本 + _check 脚本 + characters_backup/ + server.pid）

**本期消除（Phase 13）：**
- ~~main_routes.py 1313 行单体怪兽~~ ✅ 拆分为 8 个 ≤300 行子路由 + 95 行模型/常量模块
- ~~拆分无回归保障~~ ✅ `verify_refactor.py` 6/6 + `tests/test_api_routes.py` 14/14（2.15s） + FF-016/017 落库
- ~~_tools_routes.py 缺 Depends import~~ ✅ 修复 + 8 子路由 grep 全 OK

## L6 — 演化历史

| 阶段 | 变更 | 日期 |
|------|------|------|
| Phase 1-4 | 基础框架→安全模块→前端审计→架构重构 | 早期 ~ 2026-05-28 |
| Phase 5 | 三体导航更新 | 2026-05-29 |
| Phase 6 | 前端设计重构: SettingsLLM/ToolsDashboard/SettingsSecurity | 2026-05-29 |
| Phase 7 | 统一设计框架: RoleSettings+CreateRole重写，Sidebar重构 | 2026-05-30 |
| Phase 8 | 设计稿清除 + API对齐测试 + 三体导航固化 | 2026-05-30 |
| Phase 9 | 全面审计 — 三体文件更新 + 风险重评 | 2026-05-31 |
| **Phase 10** | **用户认证模块 — JWT登录/注册/管理员CRUD + ADR-0014** | **2026-06-01** |
| **Phase 11** | **三体导航全面审计 — 发现 LoginPage 孤立+ADR-0014脱节** | **2026-06-01** |
| **Phase 12** | **Auth 全流程验证 + API 模块全面健康检查** | **2026-06-01** |
| **Phase 13** | **main_routes.py 拆分重构**（1313→95 行 / 71 端点归 8 子路由）+ verify_refactor 6/6 + test_api_routes 14/14 + FF-016/017 落库 | **2026-06-01** |

## L7 — 归属

| 模块 | 负责人 |
|------|--------|
| 全项目 | 默默 (FOURTEEN1416) |

**Bus Factor = 1**（详见 `docs/architecture/bus-factor.md`）

## L8 — 约定

| 规范 | 规则 | 自动化 |
|------|------|--------|
| 命名 | Python snake_case, TS camelCase | ruff / tsc |
| 前端设计 | 左边栏+右工作区统一框架；侧边栏含人设卡 | 手动review |
| API路由 | 平铺 `/api/*` 风格 | 手动 |
| 状态管理 | 服务端数据→React Query；UI状态→Zustand | FF-0007 store禁调API |
| 组件分层 | business→components/；shared UI→components/shared/ | FF-0003/0006 |
| 类型安全 | TypeScript strict, `tsc --noEmit` | CI ✅ |
| 提交 | feat/fix/refactor/chore | 手动 |
| 架构治理 | ADR + Fitness Functions | CI 自动（5FF）+ 手动（3FF） |

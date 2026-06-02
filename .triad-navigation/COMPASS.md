# COMPASS — 指南针（ADR + 原则体系）

> 项目：唯一的你
> 更新日期：2026-06-01（全面审计） | 审计人：歆歆

---

## 7条设计原则

### 1. 前后端分离
前端只负责 UI 渲染和状态管理，后端只负责业务逻辑和数据持久化。
- 前端使用 TanStack Query / axios 获取数据，Zustand 管理本地状态
- 已贯彻：所有 API 调用通过 frontend/src/api/ 按域拆分

### 2. API 平铺路由
所有 API 使用 /api/* 平铺风格，不强制版本前缀。
- 所有子路由统一 prefix="/api"：character/voice/mimo/storyline/wechat/emotion/auth/admin
- 版本化例外：仅发生在需要破坏性变更时

### 3. 认证统一
公开端点（login/register/refresh）无认证；管理路由需 JWT Bearer token；内部服务用 X-API-Key。
- JWT 用户鉴权 + X-API-Key 内部鉴权双体系
- 路由鉴权分层：公开/认证/管理员

### 4. 组件分层
业务组件放 components/ 下，页面只负责组装。
- shared/ 通用UI组件 · common/ 通用业务组件 · layout/ 布局组件 · auth/ 认证组件 · storyline/ 业务组件 · ui/ 纯UI组件
- ⚠️ 组件目录分工略有模糊（common 和 shared 边界不清），建议合并或明确区分

### 5. 代码即标准（原「设计稿即标准」→ 2026-05-30 废除）
前端设计以代码为准。新增页面遵循统一框架：左边栏（Sidebar）+ 右工作区。

### 6. Mock 零容忍
任何页面禁止使用硬编码 mock 数据。数据必须从 API 获取。
- ✅ 所有主要页面已接 API（UsersPage/SettingsSecurity/SettingsLLM/ToolsDashboard/SettingsLogs/WeChatPage/UserWorkspace/CreateRole/RoleSettings/StatusCenter/SettingsVoice/StorylinePage）

### 7. CSS 声明即资源
JSX 中使用的每个 CSS 类必须在 Tailwind 或自定义 CSS 中有对应定义。

---

## ADR 目录

### 全部活跃 ADR（10个）

| ADR | 标题 | 状态 | 对应FF |
|-----|------|------|--------|
| ADR-0001 | 采用架构决策记录的决定 | 已采纳 ✅ | FF-0001/0002 |
| ADR-0002 | 采用统一 API 全集 | 已采纳 ✅ | — ⚠️ 缺FF |
| ADR-0003 | 采用 Tailwind 样式体系 | 已采纳 ✅ | — ⚠️ 缺FF |
| ADR-0004 | 仅支持 Light 主题 | 已采纳 ✅ | — ⚠️ 缺FF |
| ADR-0005 | 前端 API 客户端按业务域拆分 | 已采纳 ✅ | FF-0006/0007 |
| ADR-0006 | 状态管理边界约定 | 已采纳 ✅ | FF-0007 |
| ADR-0011 | 统一设计框架：侧边栏+工作区 | 已采纳 ✅ | FF-011/013 |
| ADR-0012 | 前端设计稿清除，代码即标准 | 已采纳 ✅ | — |
| ADR-0013 | Character Builder 共享状态 | 已采纳 ✅ | — |
| ADR-0014 | 用户认证与权限体系 | 已采纳 ✅ | FF-014（✅ CI端点到齐）/ FF-015（✅ 路由守卫完整） |

**ADR→FF 绑定率：8/14 个活跃 ADR 有对应 FF = 57%**（达标 > 50% 🔶 边缘）

### Fitness Functions 清单（独立于 ADR 的工程门禁）

> 这些 FF 是审计过程中发现的具体 gate，与 ADR 的"原则"维度互补 —— ADR 讲"为什么"，FF 讲"如何自动验证"。

| FF | 检测项 | 自动化方式 | 关联 ADR | 状态 |
|----|--------|----------|---------|------|
| FF-014 | auth_routes.py 端点完整性 | CI: ff-auth-endpoints | ADR-0014 | ✅ |
| FF-015 | 前端路由守卫覆盖 | CI: ff-route-guard | ADR-0014 | ✅ |
| **FF-016** | **8 子路由 mount 完整性**（71 端点 100% 挂载） | **CI: ff-sub-router-mount + pytest tests/test_api_routes.py 14/14** | **ADR-0014 audit cycle** | **✅** |
| **FF-017** | **main_routes.py 端点零残留**（阻止再膨胀） | **CI: ff-sub-router-mount + pytest::test_main_routes_residual_is_zero** | **ADR-0014 audit cycle** | **✅** |

### ADR-0014: 用户认证与权限体系（2026-06-01 新增）

**状态**：已采纳 ✅
**日期**：2026-06-01

**背景**：原有 API 鉴权仅依赖 X-API-Key 静态密钥，所有用户共享同一密钥，管理控制台无登录机制。

**决策**：
1. Username/Email + Password 注册登录 + JWT (access+refresh tokens)
2. bcrypt 密码哈希 + JWT（python-jose）
3. 路由鉴权分层：公开 / 认证（Bearer token）/ 管理员（role=admin）
4. 前端 AuthGuard 组件保护管理路由
5. X-API-Key 继续用于内部服务鉴权，与 JWT 双体系共存

**影响**：
- 后端新增：api/auth_jwt.py, api/database.py, api/routers/auth_routes.py, api/routers/admin_routes.py
- 前端新增：src/api/auth.ts, src/store/authStore.ts, src/pages/LoginPage.tsx, src/components/auth/AuthGuard.tsx
- ⚠️ **当前状态：后端文件就绪，前端文件已创建但未接入 App.tsx（LoginPage 未注册路由，AuthGuard 未包裹）**

---

## 前后端 API 对齐状态

### 对齐矩阵（2026-06-01 审计）

| 前端 API 模块 | 后端路由模块 | 状态 |
|-------------|-------------|------|
| chat.ts | main_routes.py (+ emotion_routes.py) | ✅ |
| users.ts | main_routes.py | ✅ |
| characters.ts | character_routes.py (53端点) | ✅ |
| training.ts | main_routes.py | ✅ |
| clone.ts | main_routes.py | ✅ |
| wechat.ts | wechat_routes.py | ✅ |
| system.ts | main_routes.py | ⚠️ 17处 /shisi/* 旧路由 |
| auth.ts | auth_routes.py (9) + admin_routes.py (6) | ✅ 新 |
| voice.ts | voice_routes.py | ✅ |
| mimo.ts | mimo_voice_routes.py | ✅ |
| emotion.ts | emotion_routes.py | ✅ |
| query.ts | — | ⚠️ 需确认后端对应路由 |

### shisi 旧路由引用（system.ts 中，需清理）

17 处 `/shisi/*` 引用：
- `/shisi/affinity/*` (4)
- `/shisi/emotion-stage/*` (3)
- `/shisi/vital-signs/*` (1)
- `/shisi/stickers/*` (6)
- `/shisi/voice/training/*` (3)

---

> 更新记录：2026-06-01 — 新增 ADR-0014，更新原则状态，更新 API 对齐矩阵，补充 shisi 路由清单
> 2026-06-01（重构）— **FF-016/017 落库**（独立 FF 清单段 + ADR-0014 audit cycle 关联）/ CI 新增 `ff-sub-router-mount` job / 7→8 个 CI job

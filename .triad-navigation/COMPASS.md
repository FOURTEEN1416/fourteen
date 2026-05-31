# COMPASS — 指南针（ADR + 原则体系）

> 项目：AI Girlfriend
> 更新日期：2026-05-30（设计固化） | 接手人：歆歆

---

## 7条设计原则

### 1. 前后端分离
前端只负责 UI 渲染和状态管理，后端只负责业务逻辑和数据持久化。
- 前端使用 TanStack Query / axios 获取数据，Zustand 管理本地状态
- 已贯彻：所有 API 调用通过 frontend/src/api/ 按域拆分

### 2. API 平铺路由
所有 API 使用 /api/* 平铺风格，不强制版本前缀。
- 所有子路由统一 prefix="/api"：character/voice/mimo/storyline/wechat/emotion
- 版本化例外：仅发生在需要破坏性变更时

### 3. 认证统一
除公开端点外，所有 API 必须验证 X-API-Key。
- 已贯彻：所有路由有 verify_api_key_dep 装饰器

### 4. 组件分层
业务组件放 components/ 下，页面只负责组装。
- shared/ 含 Slider, TagInput, Toggle, Badge, EmptyState 等通用组件
- layout/ 含 Sidebar, Breadcrumb, MobileNav 等布局组件
- storyline/ 含 StorylineEditor 等业务组件

### 5. 代码即标准（原「设计稿即标准」→ 2026-05-30 废除）
v2/ + concepts-overview.html + .superpowers/brainstorm/ 已全部删除。
前端设计以代码为准。新增页面遵循统一框架：左边栏（Sidebar）+ 右工作区。
侧边栏在角色层级展示：用户信息 → 创建角色 → 角色功能导航 → 人设卡 → 角色列表。

### 6. Mock 零容忍
任何页面禁止使用硬编码 mock 数据。数据必须从 API 获取。
- **已修复**: UsersPage, SettingsSecurity, SettingsLLM, ToolsDashboard
- **待修复**: RoleSettings (基础tab已定稿，待接API), StatusCenter, SettingsVoice
- **例外**: CreateRole 的人设预览从 characterBuilderStore 读取（实时构建状态，非mock）

### 7. CSS 声明即资源
JSX 中使用的每个 CSS 类必须在 Tailwind 或自定义 CSS 中有对应定义。
- glass-card 已在 styles/index.css 中定义
- 本次设计重构统一使用 Tailwind：rounded-2xl, bg-white/60, backdrop-blur-sm, border-gray-200/60

---

## ADR 目录

### 已实施的技术决策（需补物理文件）

| ADR | 标题 | 状态 | 对应FF |
|-----|------|------|--------|
| ADR-011 | 统一设计框架：侧边栏+工作区 | 已采纳 ✅ | FF-011 |
| ADR-012 | 前端设计稿清除，代码即标准 | 已采纳 ✅ | — |
| ADR-013 | Character Builder 共享状态 | 已采纳 ✅ | — |

### ADR-011: 统一设计框架：侧边栏+工作区

**状态**：已采纳 ✅
**日期**：2026-05-30

**背景**：前端页面存在三种布局模式（全宽页面、双栏页面、内嵌tab页面），设计语言不统一。

**决策**：
1. 所有角色相关页面（CreateRole, RoleSettings, StatusCenter, StorylinePage）采用「左边栏 + 右工作区」布局
2. 侧边栏使用路由感知（useRouteLevel）自动切换三级导航（global/user/role）
3. 角色层级侧边栏固定结构：
   - 返回用户列表
   - 用户 #ID
   - 创建角色（Link to /users/:id/roles/create）
   - 角色功能（角色设置 · 状态中心 · 剧情线）
   - 人设卡（动态内容：创建时显示 builder 状态，已创建时显示角色信息）
   - 角色列表（可折叠）
4. 右侧工作区使用 max-w-2xl/max-w-3xl 居中布局，白色毛玻璃卡片

**影响**：
- Sidebar.tsx：重构角色层级渲染，接入 characterBuilderStore
- CreateRole.tsx：移除右侧人设面板，人设卡移入侧边栏
- RoleSettings.tsx：对齐统一设计语言

### ADR-012: 前端设计稿清除

**状态**：已采纳 ✅
**日期**：2026-05-30

**背景**：项目存在早期设计稿（v2/, concepts-overview.html, .superpowers/brainstorm/），已与实际代码严重脱节。原策略是「禁止删除」，导致设计债累积。

**决策**：删除所有设计稿文件，前端设计以当前代码为准。

**影响**：
- 删除 v2/ (index.html + screenshot.png)
- 删除 concepts-overview.html
- 删除 .superpowers/brainstorm/
- MAP.md 和 COMPASS.md 更新：移除设计稿引用

### ADR-013: Character Builder 共享状态

**状态**：已采纳 ✅
**日期**：2026-05-30

**背景**：CreateRole 页面需要将实时人设数据传递给 Sidebar 的人设卡面板，但页面组件和侧边栏组件没有直接的 prop 传递通道。

**决策**：使用 Zustand store (characterBuilderStore) 作为跨组件通信桥梁。
- CreateRole 页面写入 (setPersona / resetPersona)
- Sidebar 的 RolePersonaCard 读取 (persona / hasContent)
- 进入创建页时自动 resetPersona()，离开时 cleanup

**影响**：
- 新增 frontend/src/store/characterBuilderStore.ts
- Sidebar.tsx 导入 useCharacterBuilderStore
- CreateRole.tsx 导入 useCharacterBuilderStore，移除右侧 PersonaCard 面板

---

## 前后端 API 对齐状态

### 对齐矩阵（2026-05-30 验证）

| 前端 API 模块 | 端点数量 | 对齐状态 |
|-------------|---------|---------|
| chat.ts | 7 | ✅ 全部对齐 |
| users.ts | 7 | ✅ 全部对齐 |
| training.ts | 8 | ✅ 全部对齐 |
| clone.ts | 6 | ✅ 全部对齐 |
| characters.ts | 9 | ✅ 全部对齐 |
| wechat.ts | 3 | ✅ 全部对齐 |
| system.ts | 33 | ⚠️ 5个 /shisi/* 旧路由待清理 |

### shisi 旧路由引用（system.ts 中，需清理）

| 前端调用 | 状态 |
|---------|------|
| /shisi/characters | ⚠️ 有对应新API /api/characters |
| /shisi/memory/favorite | ⚠️ 有对应新API /api/characters/:id/favorites |
| /shisi/stats | ⚠️ 有对应新API /api/stats |
| /shisi/stickers | ⚠️ 待迁移 |
| /shisi/voice/training/* | ⚠️ 待迁移 |

---
> 更新记录：2026-05-30 — 设计稿清除、设计框架统一、API对齐验证、3个新ADR

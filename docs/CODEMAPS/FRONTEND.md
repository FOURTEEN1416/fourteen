# 前端地图

**最近更新:** 2026-06-03
**技术栈:** React 18 + Vite 5 + TypeScript 5 + Tailwind CSS 3
**入口:** `frontend/index.html` → `frontend/src/main.tsx`

---

## 目录结构

```
frontend/src/
├── main.tsx              ← 入口
├── App.tsx               ← 路由定义 + 布局
├── vite-env.d.ts
│
├── api/                  ← API 客户端模块 (12 文件)
│   ├── client.ts         ← axios 实例 + 拦截器 (11.5KB)
│   ├── queryClient.ts    ← TanStack Query 客户端配置
│   ├── auth.ts           ← 认证 API
│   ├── admin.ts          ← 管理后台 API
│   ├── characters.ts     ← 角色 API (13.7KB)
│   ├── chat.ts           ← 聊天 API
│   ├── clone.ts          ← 克隆 API
│   ├── invites.ts        ← 邀请码 API
│   ├── mimo.ts           ← MiMo 语音 API
│   ├── system.ts         ← 系统 API
│   ├── training.ts       ← 训练 API
│   ├── users.ts          ← 用户 API
│   └── wechat.ts         ← 微信 API
│
├── pages/                ← 页面组件 (15 pages)
│   ├── LoginPage.tsx          ← 登录页 (8.7KB)
│   ├── WeChatPage.tsx         ← 微信控制台 (20.5KB)
│   ├── UsersPage.tsx          ← 用户列表 (8.4KB)
│   ├── UserWorkspace.tsx      ← 用户工作区 (10.3KB)
│   ├── CreateRole.tsx         ← 创建角色 (14.5KB)
│   ├── RoleSettings.tsx       ← 角色设置 (34.6KB)
│   ├── StatusCenter.tsx       ← 状态中心 (7.9KB)
│   ├── StorylinePage.tsx      ← 故事线页面
│   ├── SystemSettingsLayout.tsx← 系统设置布局
│   ├── SettingsLLM.tsx        ← LLM 设置 (12.4KB)
│   ├── SettingsVoice.tsx      ← 语音设置 (16.2KB)
│   ├── SettingsSecurity.tsx   ← 安全设置 (7.8KB)
│   ├── SettingsLogs.tsx       ← 日志设置 (7.1KB)
│   ├── ToolsDashboard.tsx     ← 工具仪表盘 (4.6KB)
│   ├── AdminInvitesPage.tsx   ← 邀请码管理 (24.4KB)
│   ├── AdminUsersPage.tsx     ← 用户管理 (41.8KB)
│   └── NotFoundPage.tsx       ← 404 页面
│
├── components/           ← 组件
│   ├── auth/
│   │   ├── AuthGuard.tsx      ← 路由守卫
│   │   ├── RoleGuard.tsx      ← 角色守卫
│   │   └── index.ts
│   │
│   ├── layout/
│   │   ├── Sidebar.tsx        ← 侧边栏 (三级路由导航)
│   │   ├── Breadcrumb.tsx     ← 面包屑
│   │   └── MobileNav.tsx      ← 移动端导航
│   │
│   ├── chat/
│   │   ├── ChatInput.tsx      ← 聊天输入框
│   │   ├── MessageBubble.tsx  ← 消息气泡
│   │   ├── MessageList.tsx    ← 消息列表
│   │   ├── ProactiveToast.tsx ← 主动消息提示
│   │   └── TypingIndicator.tsx← 打字指示器
│   │
│   ├── common/               ← 通用业务组件
│   │   ├── Badge.tsx
│   │   ├── Button.tsx
│   │   ├── Card.tsx
│   │   ├── EmptyState.tsx
│   │   ├── ErrorBoundary.tsx
│   │   ├── ProactiveEnginePanel.tsx
│   │   ├── SensitiveInput.tsx
│   │   ├── Skeleton.tsx
│   │   ├── Toast.tsx
│   │   └── UrgencyBadge.tsx
│   │
│   ├── shared/               ← 通用 UI 组件
│   │   ├── AnimatedNumber.tsx
│   │   ├── AnimatedPage.tsx  ← 页面过渡动画 (opacity 淡入)
│   │   ├── Badge.tsx
│   │   ├── ConfirmDialog.tsx
│   │   ├── DangerButton.tsx
│   │   ├── EmptyState.tsx
│   │   ├── FileUpload.tsx
│   │   ├── Modal.tsx
│   │   ├── ParallaxTilt.tsx
│   │   ├── ProgressBar.tsx
│   │   ├── ScrollProgress.tsx
│   │   ├── Select.tsx
│   │   ├── Skeleton.tsx
│   │   ├── Slider.tsx
│   │   ├── StaggerContainer.tsx
│   │   ├── SubTabBar.tsx
│   │   ├── Tabs.tsx
│   │   ├── TagInput.tsx
│   │   ├── Toast.tsx
│   │   ├── Toggle.tsx
│   │   └── Tooltip.tsx
│   │
│   └── storyline/
│       ├── StorylineEditor.tsx    ← 故事线编辑器 (22.7KB)
│       ├── StorylineIndicator.tsx  ← 故事线指示器
│       └── KnowledgePreview.tsx    ← 知识预览
│
├── hooks/                ← 自定义 Hooks (9 文件)
│   ├── useAuth.ts              ← 认证状态
│   ├── useDashboardData.ts     ← 仪表盘数据
│   ├── useInView.ts            ← 可见性检测
│   ├── useMousePosition.ts     ← 鼠标位置
│   ├── useQueries.ts           ← React Query 封装 (14.6KB)
│   ├── useSmartPoll.ts         ← 智能轮询
│   ├── useSSE.ts               ← SSE 连接
│   └── useWebSocket.ts         ← WebSocket 连接 (6.4KB)
│
├── store/                ← Zustand 状态管理 (6 stores)
│   ├── authStore.ts            ← 认证状态 (2.7KB)
│   ├── characterBuilderStore.ts← 角色构建器 (1.5KB)
│   ├── chatStore.ts            ← 聊天状态 (3.1KB)
│   ├── errorStore.ts           ← 错误状态 (1.2KB)
│   ├── logStore.ts             ← 日志状态 (1.2KB)
│   └── settingsStore.ts        ← 设置状态 (0.4KB)
│
├── types/                ← TypeScript 类型定义
│   ├── api.ts                  ← API 类型 (16.2KB)
│   ├── framework.ts            ← 框架类型 (3.8KB)
│   └── sticker.ts              ← 表情包类型
│
└── tests/                ← 前端测试
    ├── components/
    └── hooks/
```

---

## 页面路由表 (15 注册路由)

| 页面 | 路径 | 认证 | API 源 | 大小 |
|------|------|------|--------|------|
| LoginPage | /login | 无 | authStore + invites | 8.7KB |
| WeChatPage | /wechat | 需要 | wechat/status | 20.5KB |
| UsersPage | /users | 需要 | listUsers | 8.4KB |
| BindingDetailPage | /bindings/:wxid | 需要 | API | — |
| UserWorkspace | /users/:userId | 需要 | listCharacters | 10.3KB |
| CreateRole | /users/:userId/roles/create | 需要 | chat + characterBuilderStore | 14.5KB |
| RoleSettings | /users/:userId/roles/:roleId/settings | 需要 | useUnifiedCharacter | 34.6KB |
| StatusCenter | /users/:userId/roles/:roleId/status | 需要 | dashboardStats | 7.9KB |
| StorylinePage | /users/:userId/roles/:roleId/storyline | 需要 | storyline | (含 StorylineEditor) |
| SystemSettingsLayout | /settings | 需要 | — | 0.3KB |
| SettingsLLM | /settings/llm | 需要 | 真实 API | 12.4KB |
| SettingsVoice | /settings/voice | 需要 | mimo/* | 16.2KB |
| SettingsSecurity | /settings/security | 需要 | safety/* | 7.8KB |
| SettingsLogs | /settings/logs | 需要 | logs | 7.1KB |
| ToolsDashboard | /settings/tools | 需要 | 工具 API | 4.6KB |
| AdminUsersPage | /admin/users | Admin | admin API | 41.8KB |
| NotFoundPage | * | 无 | — | 0.7KB |

> 注：AdminInvitesPage 文件存在但尚未注册路由（需在 App.tsx 添加 `/admin/invites` 路由）。

---

## 状态管理策略

```
┌─────────────────────────────────────────────────────────┐
│                  状态管理分层                               │
│                                                          │
│  服务端状态 (TanStack Query)      客户端状态 (Zustand)     │
│  ┌────────────────────────┐   ┌──────────────────────┐  │
│  │ API 数据缓存            │   │ authStore (token/用户)│  │
│  │ 自动失效/重验证         │   │ chatStore (消息/状态) │  │
│  │ 乐观更新                │   │ errorStore (错误)     │  │
│  │ 数据预取                │   │ logStore (日志)       │  │
│  │                        │   │ settingsStore (偏好)  │  │
│  │                        │   │ characterBuilderStore │  │
│  └────────────────────────┘   └──────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 组件层级规范

```
components/
├── layout/         ← 布局组件 (Sidebar, Breadcrumb, MobileNav)
├── auth/           ← 认证组件 (AuthGuard, RoleGuard)
├── storyline/      ← 故事线业务组件
├── chat/           ← 聊天业务组件
├── common/         ← 通用业务组件 (卡片/按钮/输入等)
└── shared/         ← 通用 UI 组件 (Modal/Toast/Tabs 等)
```

---

## 动画设计

| 动画 | 类型 | 详情 |
|------|------|------|
| AnimatedPage | 页面过渡 | 纯 opacity 淡入 (0→1, 0.15s) |
| AnimatedSuspense | 加载骨架 | AnimatedPage + Suspense skeleton |
| StaggerContainer | 子元素动画 | 存在但未使用 (逐行 stagger 已弃用) |
| ParallaxTilt | 视差倾斜 | 鼠标跟随倾斜效果 |

---

## API 客户端 (client.ts)

```
axios.create(baseURL: '/api')
  → 请求拦截器: 添加 X-API-Key + JWT Bearer Token
  → 响应拦截器: 错误统一处理 + token 刷新逻辑
  → 模块 API: 12 个 API 模块 (auth/admin/characters/chat 等)
```

**重要约束:**
- 每个 API 模块只 re-export client.ts 中的函数
- Store 禁止直接 import API 模块 (FF-0007)
- 页面禁止 import API 模块 (FF-0003)

---

## 相关地图

- [ARCHITECTURE.md](ARCHITECTURE.md) — 前端在整体架构中的位置
- [BACKEND.md](BACKEND.md) — 对应的后端 API 端点

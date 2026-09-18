# 前端地图

> **⚠️ 2026-09-17 漂移注记**：本图所列 `command_handler.py`/`command_parser.py`（微信指令系统）、前端 `chatStore.ts`/`api/chat.ts`、`shared/Badge.tsx` 已于当日死代码清洗中删除（DELETION_LOG 09-17 条）；实况以 `CODE_GRAPH.md` v3.7.0 为准。

> **⚠️ 数字漂移声明**（2026-08-26 治理标注）：本文路由统计含 3 个孤儿页面（UsersPage/UserWorkspace/BindingDetailPage 未挂载导航）。权威清单以 `CODE_GRAPH.md` + `docs/FEATURE_MAP.md` F 区为准。

**最近更新:** 2026-08-01
**技术栈:** React ^19.0.0 + Vite ^8.0.12 + TypeScript ~6.0.2 + Tailwind CSS ^4.1.0 + Zustand ^5.0.0
**入口:** `frontend/index.html` → `frontend/src/main.tsx`

---

## 目录结构

```
frontend/src/
├── main.tsx              ← 入口
├── App.tsx               ← 路由定义 + 布局
├── vite-env.d.ts
│
├── api/                  ← API 客户端模块 (14 文件)
│   ├── client.ts         ← axios 实例 + 拦截器 (11.5KB)
│   ├── queryClient.ts    ← TanStack Query 客户端配置
│   ├── auth.ts           ← 认证 API
│   ├── admin.ts          ← 管理后台 API
│   ├── characters.ts     ← 角色 API (13.7KB)
│   ├── chat.ts           ← 聊天 API
│   ├── clone.ts          ← 克隆 API
│   ├── llmProviders.ts   ← LLM 供应商管理 API
│   ├── mimo.ts           ← MiMo 语音 API
│   ├── system.ts         ← 系统 API
│   ├── training.ts       ← 训练 API
│   └── wechat.ts         ← 微信 API
│
├── pages/                ← 页面组件 (15 pages，2026-08-28：幽灵层三页+DemoPage 已删)
│   ├── LoginPage.tsx          ← 登录页 (8.7KB)
│   ├── WeChatPage.tsx         ← 微信控制台 (20.5KB)
│   ├── RolesPage.tsx          ← 角色列表
│   ├── CreateRole.tsx         ← 创建角色 (14.5KB)
│   ├── RoleSettings.tsx       ← 角色设置 (34.6KB)
│   ├── StatusCenter.tsx       ← 状态中心 (7.9KB)
│   ├── SystemSettingsLayout.tsx← 系统设置布局
│   ├── SettingsLLM.tsx        ← LLM 设置 (12.4KB)
│   ├── SettingsVoice.tsx      ← 语音设置 (16.2KB)
│   ├── SettingsSecurity.tsx   ← 安全设置 (7.8KB)
│   ├── SettingsLogs.tsx       ← 日志设置 (7.1KB)
│   ├── ToolsDashboard.tsx     ← 工具仪表盘 (4.6KB)
│   ├── AdminUsersPage.tsx     ← 用户管理 (41.8KB)
│   ├── AdminProvidersPage.tsx ← LLM 供应商管理 (admin)
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
│   ├── common/               ← 通用业务组件 (7 文件)
│   │   ├── Badge.tsx
│   │   ├── Button.tsx
│   │   ├── CustomCursor.tsx
│   │   ├── ErrorBoundary.tsx
│   │   ├── ParticleCanvas.tsx
│   │   ├── Toast.tsx
│   │   └── index.ts
│   │
│   ├── shared/               ← 通用 UI 组件 (14 文件)
│   │   ├── AnimatedPage.tsx  ← 页面过渡动画 (opacity 淡入)
│   │   ├── Badge.tsx
│   │   ├── ConfirmDialog.tsx
│   │   ├── EmptyState.tsx
│   │   ├── FileUpload.tsx
│   │   ├── Modal.tsx
│   │   ├── ScrollProgress.tsx
│   │   ├── Select.tsx
│   │   ├── Skeleton.tsx
│   │   ├── Slider.tsx
│   │   ├── SubTabBar.tsx
│   │   ├── TagInput.tsx
│   │   ├── Toggle.tsx
│   │   └── index.ts
│   │
│   └── storyline/
│       ├── StorylineEditor.tsx    ← 故事线编辑器 (22.7KB)
│       ├── StorylineIndicator.tsx  ← 故事线指示器
│       └── KnowledgePreview.tsx    ← 知识预览
│
├── hooks/                ← 自定义 Hooks (5 文件)
│   ├── index.ts                ← 统一导出
│   ├── useAuth.ts              ← 认证状态
│   ├── useInView.ts            ← 可见性检测
│   ├── useMousePosition.ts     ← 鼠标位置
│   └── useQueries.ts           ← React Query 封装 (14.6KB)
│
├── store/                ← Zustand 状态管理 (4 stores)
│   ├── authStore.ts            ← 认证状态 (2.7KB)
│   ├── characterBuilderStore.ts← 角色构建器 (1.5KB)
│   ├── chatStore.ts            ← 聊天状态 (3.1KB)
│   └── errorStore.ts           ← 错误状态 (1.2KB)
│
├── types/                ← TypeScript 类型定义
│   ├── api.ts                  ← API 类型 (16.2KB)
│   └── framework.ts            ← 框架类型 (3.8KB)
│
└── tests/                ← 前端测试
    ├── components/
    └── hooks/
```

---

## 页面路由表 (19 注册路由)

| 页面 | 路径 | 认证 | API 源 | 大小 |
|------|------|------|--------|------|
| LoginPage | /login | 无 | authStore | 8.7KB |
| WeChatPage | /wechat | 需要 | wechat/status | 20.5KB |
| UsersPage | /users | 需要 | listUsers | 8.4KB |
| BindingDetailPage | /bindings/:wxid | 需要 | API | — |
| UserWorkspace | /users/:userId | 需要 | listCharacters | 10.3KB |
| RolesPage | /roles | 需要 | characters | — |
| CreateRole | /roles/create | 需要 | chat + characterBuilderStore | 14.5KB |
| RoleSettings | /roles/:roleId/settings | 需要 | useUnifiedCharacter | 34.6KB |
| StatusCenter | /roles/:roleId/status | 需要 | dashboardStats | 7.9KB |
| StorylinePage | /roles/:roleId/storyline | 需要 | storyline | (内联在 App.tsx) |
| SystemSettingsLayout | /settings | 需要 | — | 0.3KB |
| SettingsLLM | /settings/llm | 需要 | 真实 API | 12.4KB |
| SettingsVoice | /settings/voice | 需要 | mimo/* | 16.2KB |
| SettingsSecurity | /settings/security | 需要 | safety/* | 7.8KB |
| SettingsLogs | /settings/logs | 需要 | logs | 7.1KB |
| ToolsDashboard | /settings/tools | 需要 | 工具 API | 4.6KB |
| AdminUsersPage | /admin/users | Admin | admin API | 41.8KB |
| AdminProvidersPage | /admin/providers | Admin | llmProviders API | — |
| NotFoundPage | * | 无 | — | 0.7KB |

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
│  │ 数据预取                │   │ characterBuilderStore │  │
│  │                        │   │                      │  │
│  │                        │   │                      │  │
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
├── common/         ← 通用业务组件 (Badge/Button/Toast 等)
└── shared/         ← 通用 UI 组件 (Modal/Select/Slider 等)
```

---

## 动画设计

| 动画 | 类型 | 详情 |
|------|------|------|
| AnimatedPage | 页面过渡 | 纯 opacity 淡入 (0→1, 0.15s) |
| AnimatedSuspense | 加载骨架 | AnimatedPage + Suspense skeleton |
| ParticleCanvas | 背景粒子 | 全屏 canvas，30fps 节流，8–18 粒子 + 距离连线；`visibilitychange` 暂停，resize 防抖 |
| CustomCursor | 遮罩式光标 | 光晕遮罩 + 内核 + 拖尾粒子三层，详见下节 |

### CustomCursor（遮罩式鼠标动效）

2026-09-18 由「圆环 + 圆点」重构为三层（`c755090`），品牌色保持海盐蓝 `#7DD3FC`
（`--color-accent-200`），暖黄/薄荷青作 `data-hover` 变体。仅挂载于 `ProtectedLayout`，
公开路由（`/intro` `/login` `/psych`）不含此组件。

| 层 | 尺寸 | 跟随 | 说明 |
|----|------|------|------|
| `.cursor-glow` | 200 → 280px (hover) | 滞后（lerp 0.09） | 径向渐变遮罩光晕，滞后跟随形成拖曳感 |
| `.cursor-core` | 12 → 38px (hover) | 紧跟（lerp 0.38） | 小圆点内核；hover 可交互元素时张开 |
| `.cursor-trail` | 9px × 16 节点 | 对象池轮转复用 | 拖尾粒子，单颗寿命约 0.64s |

**硬约束（改前必读，均有隔离实测依据）：**

1. 位移一律走 `transform: translate3d`，**禁止**逐帧写 `left/top`（会触发布局）
2. 拖尾必须用**固定对象池**复用节点，**禁止**在 `mousemove` 里 `createElement`/`removeChild`
3. 单一 rAF 驱动，**禁止** `setTimeout` 堆；**所有「按帧」系数须经帧时长归一化**
   （`k = dt / 16.67`，lerp 用 `1-(1-α)^k`）——否则 60Hz 与 240Hz 屏表现不一致
   （按帧衰减会使高刷屏寿命只剩 1/4，肉眼几乎看不见）
4. 停帧条件必须是「静止超时 **且** 无存活粒子」，否则衰减中的粒子被冻结在屏幕不消散
5. 拖尾用 `radial-gradient` 而非大面积 `box-shadow`（模糊成本随半径平方增长）
6. 触屏（`pointer: coarse`）/ `prefers-reduced-motion` 下不激活；≤1023px 隐藏

> ⚠️ **性能归因（勿误判）**：隔离实测表明鼠标动效单独跑 **60.6fps / 0% 卡顿**，
> **不是**卡顿主因。真凶是 `ParticleCanvas` 全帧重绘 × `backdrop-filter` 毛玻璃
> （**24.1fps / 86.1% 卡顿**），且降模糊半径（12→6px）实测无效。
> 诊断方法论见技能 `perf-isolation-lab`；注意无头浏览器走软件光栅化，性能数值不可信。

---

## API 客户端 (client.ts)

```
axios.create(baseURL: '/api')
  → 请求拦截器: 添加 X-API-Key + JWT Bearer Token
  → 响应拦截器: 错误统一处理 + token 刷新逻辑
  → 模块 API: 13 个 API 模块 (auth/admin/characters/chat 等)
```

**重要约束:**
- 每个 API 模块只 re-export client.ts 中的函数
- Store 禁止直接 import API 模块 (FF-0007)
- 页面禁止 import API 模块 (FF-0003)

---

## 相关地图

- [ARCHITECTURE.md](ARCHITECTURE.md) — 前端在整体架构中的位置
- [BACKEND.md](BACKEND.md) — 对应的后端 API 端点

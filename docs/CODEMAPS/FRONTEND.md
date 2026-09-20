# 前端地图

> **✅ 2026-09-20 增量刷新**：在 09-19 全量刷新基线上补齐通道隔离 UI、材质体系、五 tab 收敛与 constants/ 目录。权威数字以 `CODE_GRAPH.md` v3.8.6 为准。
> 功能级清单（页-功能点编号）见 `docs/FUNCTION_INVENTORY.md`。

**最近更新:** 2026-09-20
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
├── api/                  ← API 客户端模块 (13 文件)
│   ├── client.ts         ← axios 实例 + 拦截器（normalize.ts/emotion.ts 抽离后为纯 re-export 门面）
│   ├── normalize.ts      ← 响应归一化（09-18 自 client.ts 抽离，FF-0006）
│   ├── emotion.ts        ← 情绪趋势/分布工具（09-18 自 client.ts 抽离，FF-0006）
│   ├── queryClient.ts    ← TanStack Query 客户端配置
│   ├── auth.ts           ← 认证 API
│   ├── admin.ts          ← 管理后台 API
│   ├── characters.ts     ← 角色 API
│   ├── clone.ts          ← 克隆 API
│   ├── llmProviders.ts   ← LLM 供应商管理 API
│   ├── mimo.ts           ← MiMo 语音 API
│   ├── system.ts         ← 系统 API（含 /api/user/llm-config）
│   ├── training.ts       ← 训练 API（training/* + proactive/*）
│   └── wechat.ts         ← 微信 API
│
├── constants/            ← 常量单一真源
│   ├── cloneAgentGuide.ts     ← 克隆智能体任务书（CreateRole 内嵌）
│   └── persona.ts             ← 人设标签字典唯一真源（PERSONALITY_LABELS/
│                                SPEAKING_STYLE_LABELS/EMOTION_COLORS/AFFINITY_STAGES，
│                                09-19「前端写死数据审计」批次新建）
│
├── pages/                ← 页面组件 (17 个 .tsx)
│   ├── IntroPage.tsx          ← 产品介绍页 /intro（SP-11，公开静态门面）
│   ├── LoginPage.tsx          ← 登录页
│   ├── PsychProfilePage.tsx   ← 心理画像 /psych（09-18 起包 AuthGuard 需登录）
│   ├── WeChatPage.tsx         ← 微信控制台
│   ├── RolesPage.tsx          ← 角色列表
│   ├── CreateRole.tsx         ← 创建角色
│   ├── RoleSettings.tsx       ← 角色设置
│   ├── StatusCenter.tsx       ← 状态中心（09-18 记忆体系「三层管线」重构）
│   ├── SystemSettingsLayout.tsx← 系统设置布局
│   ├── SettingsLLM.tsx        ← LLM 设置
│   ├── SettingsVoice.tsx      ← 语音设置
│   ├── SettingsSecurity.tsx   ← 安全设置
│   ├── SettingsLogs.tsx       ← 日志设置
│   ├── ToolsDashboard.tsx     ← 工具仪表盘
│   ├── AdminUsersPage.tsx     ← 用户管理
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
│   │   ├── Sidebar.tsx        ← 侧边栏（≥md 左侧固定 + MobileDrawer 抽屉）
│   │   ├── Breadcrumb.tsx     ← 面包屑
│   │   └── MobileNav.tsx      ← 移动端导航
│   │
│   ├── common/               ← 通用业务组件（Badge.tsx 已于 09-17 清洗删除）
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
│   ├── storyline/
│   │   ├── StorylineEditor.tsx    ← 故事线编辑器
│   │   ├── StorylineIndicator.tsx  ← 故事线指示器
│   │   └── KnowledgePreview.tsx    ← 知识预览（DATA tab 真实管理区）
│   │
│   └── admin/RoleSettingsTabs.tsx ← 角色设置**五 tab** 实现（basic/voice/message/data/timeline；
│                                    StickersTab 已于 09-19 撤除——无后端支撑的装饰 tab）
│
├── hooks/                ← 自定义 Hooks (5 文件)
│   ├── index.ts                ← 统一导出
│   ├── useAuth.ts              ← 认证状态
│   ├── useInView.ts            ← 可见性检测
│   ├── useMousePosition.ts     ← 鼠标位置
│   └── useQueries.ts           ← React Query 封装
│
├── store/                ← Zustand 状态管理 (3 stores)
│   ├── authStore.ts            ← 认证状态
│   ├── characterBuilderStore.ts← 角色构建器
│   └── errorStore.ts           ← 错误状态
│
├── types/                ← TypeScript 类型定义
│   ├── api.ts                  ← API 类型
│   └── framework.ts            ← 框架类型
│
└── tests/                ← 前端测试（vitest 98 用例 / 16 文件，2026-09-20 实测全绿）
    ├── components/
    └── hooks/
```

---

## 页面路由表（App.tsx 实测，2026-09-19）

| 页面 | 路径 | 认证 | API 源 |
|------|------|------|--------|
| IntroPage | /intro | 无（公开门面） | — |
| PsychProfilePage | /psych | **需登录**（09-19 起并入控制台外壳，带侧栏/面包屑；更早 09-18 起即需登录） | psych/* |
| LoginPage | /login | 无 | authStore |
| RootRedirect | / | 无 | 登录→/wechat，未登录→/intro |
| WeChatPage | /wechat | 需要 | wechat/status |
| RolesPage | /roles | 需要 | characters |
| CreateRole | /roles/create | 需要 | clone + characterBuilderStore |
| RoleSettings | /roles/:roleId/settings[/:tab] | 需要 | useUnifiedCharacter |
| StatusCenter | /roles/:roleId/status | 需要 | dashboardStats |
| StorylinePage | /roles/:roleId/storyline | 需要 | storyline（内联在 App.tsx） |
| SystemSettingsLayout | /settings | 需要 | — |
| SettingsLLM | /settings/llm | 需要 | system.ts |
| SettingsVoice | /settings/voice | 需要 | mimo/* |
| ToolsDashboard | /settings/tools | 需要 | 工具 API |
| SettingsSecurity | /settings/security | 需要 | safety/* |
| SettingsLogs | /settings/logs | 需要 | logs |
| AdminUsersPage | /admin/users | Admin | admin API |
| AdminProvidersPage | /admin/providers | Admin | llmProviders API |
| NotFoundPage | * | 无 | — |

> 幽灵层三页（UsersPage/UserWorkspace/BindingDetailPage）已于 08-27 裁决删除（SP-9），
> 绑定管理入口收敛为 RolesPage「设为活跃」（09-17 接线 wechat_bindings）。

---

## 状态管理策略

```
┌─────────────────────────────────────────────────────────┐
│                  状态管理分层                               │
│                                                          │
│  服务端状态 (TanStack Query)      客户端状态 (Zustand)     │
│  ┌────────────────────────┐   ┌──────────────────────┐  │
│  │ API 数据缓存            │   │ authStore (token/用户)│  │
│  │ 自动失效/重验证         │   │ errorStore (错误)     │  │
│  │ 乐观更新                │   │ characterBuilderStore │  │
│  │ 数据预取                │   │                      │  │
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

## 动画与材质设计

> **材质体系（2026-09-20 全站升级，用户裁决 A+B）**：`index.css` 新增固定**环境色场**
> （暖黄/天蓝/薄荷四团大半径径向渐变）+ **三档材质阶梯** `.mat-recess`（内凹）/
> `.mat-raised`（实体+彩色发丝线）/`.mat-floating`（玻璃特权层）；`.glass-card` 重做。
> 壳层组件（Sidebar/Breadcrumb/MobileDrawer/Modal）统一 `.mat-floating`；
> CreateRole 聊天容器为 iOS Messages 语言（`.chat-channel` 凹槽 + `.chat-bubble-in/out` +
> `.chat-dock` 玻璃输入坞）；全站输入框基态 `.input-macaron`（一处修好 20+ 无边框输入框）。
> 色板经令牌级重映射收敛（blue→sky、green→teal、gray→stone、purple→sky、orange→amber）。

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
  → 模块 API: 13 个 API 模块 (auth/admin/characters/clone/emotion/normalize 等)
```

**重要约束:**
- 每个 API 模块只 re-export client.ts 中的函数
- Store 禁止直接 import API 模块 (FF-0007)
- 页面禁止 import API 模块 (FF-0003)

---

## 相关地图

- [ARCHITECTURE.md](ARCHITECTURE.md) — 前端在整体架构中的位置
- [BACKEND.md](BACKEND.md) — 对应的后端 API 端点

# 前端全量翻新实施计划

> **For agentic workers:** Inline execution.

**Goal:** 按 `frontend-design-framework-v1.md` + `2026-05-27-immersive-glass-design.md` 全量重写前端

**Architecture:** 保留现有 API 层和 Zustand stores, 替换路由/导航/页面。旧页面文件保留不动, 新页面按规范目录组织。

**Tech Stack:** React 19 + TypeScript 6 + Vite 8 + Tailwind v4 + react-router-dom v7 + framer-motion + lucide-react + @tanstack/react-query v5 + zustand v5

---

### Phase 0: 基础设施

#### Task 0.1: 安装 framer-motion + 扩展 index.css

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/index.css`

```bash
cd frontend && bun add framer-motion
```

`index.css` 追加: 玻璃卡片工具类、动态背景动画、新 keyframes (scale-in 已存在, 增加 glass/shimmer)

#### Task 0.2: 创建 types/framework.ts

**Files:**
- Create: `frontend/src/types/framework.ts`

导出: PersonaCard, User, EmotionState, EmotionTrendPoint, WeChatConnection, ConnectionState, SavedConnection, PluginItem, LogEntry, WorkspaceTabs 等类型

#### Task 0.3: 创建 hooks/useMousePosition.ts + hooks/useInView.ts

**Files:**
- Create: `frontend/src/hooks/useMousePosition.ts` — 鼠标坐标跟踪
- Create: `frontend/src/hooks/useInView.ts` — IntersectionObserver 滚动触发

#### Task 0.4: 创建所有 shared 组件

**Files (all create):**
- `frontend/src/components/shared/Slider.tsx`
- `frontend/src/components/shared/TagInput.tsx`
- `frontend/src/components/shared/Toggle.tsx`
- `frontend/src/components/shared/Select.tsx`
- `frontend/src/components/shared/FileUpload.tsx`
- `frontend/src/components/shared/Modal.tsx`
- `frontend/src/components/shared/ConfirmDialog.tsx`
- `frontend/src/components/shared/ProgressBar.tsx`
- `frontend/src/components/shared/EmptyState.tsx`
- `frontend/src/components/shared/DangerButton.tsx`
- `frontend/src/components/shared/SubTabBar.tsx`
- `frontend/src/components/shared/PersonaPreview.tsx`
- `frontend/src/components/shared/index.ts`

每个组件使用玻璃风格 (glass-card 类), 支持青紫品牌色。

---

### Phase 1: 导航架构

#### Task 1.1: 新 Sidebar

**Files:**
- Rewrite: `frontend/src/components/layout/Sidebar.tsx`

三层: 接入微信 / 用户管理(含嵌套) / 系统设置(含嵌套)
玻璃侧边栏: `bg-white/60 backdrop-blur-xl`
选中项: 左侧发光条 + 品牌色高亮
折叠/展开切换

#### Task 1.2: 新 App.tsx

**Files:**
- Rewrite: `frontend/src/App.tsx`

18 条新路由, / 重定向到 /wechat
wrapper: 动态背景层 + 主布局
删除旧页面 lazy imports (页面文件保留不动)

#### Task 1.3: Layout 组件

**Files:**
- Create: `frontend/src/components/layout/UserWorkspace.tsx`
- Create: `frontend/src/components/layout/SystemSettingsLayout.tsx`

UserWorkspace: 左侧用户列表 + 右侧三Tab
SystemSettingsLayout: 左侧6项导航 + 右侧内容

---

### Phase 2: 核心页面

#### Task 2.1: WeChatPage

**Files:**
- Create: `frontend/src/pages/WeChatPage.tsx`
- Create: `frontend/src/components/wechat/`

AliasInput, ConnectionStatus, QRCodeArea, ActionButtons, SavedConnections

#### Task 2.2: UsersPage

**Files:**
- Create: `frontend/src/pages/UsersPage.tsx` (新)
- Create: `frontend/src/components/users/`

UserList(SearchBar + UserListItem), 右侧工作区三Tab宿主
数据: GET /api/users

#### Task 2.3: CreateRole tab

**Files:**
- Create: `frontend/src/components/create-role/`

CharacterList, CharacterCreator(三种模式tab), ChatMessageList, ContactSelector, FileDropZone, PersonaPreview

#### Task 2.4: RoleSettings tab (4子页)

**Files:**
- Create: `frontend/src/components/role-settings/basic/BasicConfig.tsx`
- Create: `frontend/src/components/role-settings/voice/VoiceConfig.tsx`
- Create: `frontend/src/components/role-settings/proactive/ProactiveConfig.tsx`
- Create: `frontend/src/components/role-settings/data/DataManagement.tsx`

PersonalitySliders, CoreAnchorsEditor, SoundSelection, EngineParams, VoiceClone, Toggle + params, KnowledgeBase, DangerZone

#### Task 2.5: StatusCenter tab

**Files:**
- Create: `frontend/src/components/status/StatusCenter.tsx`

CharacterStatusCards, StatsGrid, EmotionTrend, EmotionParamsSliders, QuickActions

---

### Phase 3: 系统设置

#### Task 3.1: 6 个子设置页

**Files:**
- Create: `frontend/src/components/settings/general/SystemConfig.tsx`
- Create: `frontend/src/components/settings/llm/LLMConfig.tsx`
- Create: `frontend/src/components/settings/voice/VoiceSettings.tsx`
- Create: `frontend/src/components/settings/security/SecurityConfig.tsx`
- Create: `frontend/src/components/settings/extensions/ExtensionsConfig.tsx`
- Create: `frontend/src/components/settings/logs/LogsViewer.tsx`
- Create: `frontend/src/pages/SettingsPage.tsx`

#### Task 3.2: MemoryListModal

**Files:**
- Create: `frontend/src/components/role-settings/data/MemoryListModal.tsx`

记忆列表搜索 + 展示 + 删除弹窗, 用于数据管理页面

---

### Phase 4: 沉浸升级

#### Task 4.1: 动态背景

`index.css` 中 gradient-shift 动画 + `bg-dynamic` 类
全局包装在 App.tsx 中作为背景层

#### Task 4.2: 页面过渡

App.tsx 用 framer-motion `AnimatePresence` 包裹路由, fade+slide 过渡

#### Task 4.3: 卡片入场 stagger

用 framer-motion `variants` + `staggerChildren` 装饰卡片列表

#### Task 4.4: 鼠标视差

`useMousePosition` hook + 浮动卡片 `transform: translate()` 跟随

#### Task 4.5: 数字滚动

`CountUp` 组件 (IntersectionObserver 触发统计数据显示)

---

### TypeScript 编译验证

```bash
cd frontend && bun run typecheck
```

旧页面保留不动, 新代码无类型错误。

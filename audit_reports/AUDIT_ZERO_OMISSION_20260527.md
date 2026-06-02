# "唯一的你" AI虚拟伴侣系统 — 零遗漏全量审计报告

> ⚠️ **历史快照**（2026-06-01 标注）：本报告为时点审计，已过期。当前项目状态见 `.triad-navigation/MAP.md` / `HANDOFF.md` 和本目录最新报告（`E2E_REPORT.md`）。

**审计日期**：2026-05-27
**审计范围**：前端 22 组件 + 4 Store + 5 API + 1 Hook + 4 Type 定义 + 后端全模块
**审计方法**：逐文件逐行阅读，无遗漏
**审计目标**：建立可回溯的知识基线，标注每行代码的归属、依赖、风险

---

## 目录
1. [全量清单与文件地图](#一全量清单与文件地图)
2. [架构总览与数据流](#二架构总览与数据流)
3. [前端组件依赖图谱](#三前端组件依赖图谱)
4. [Store 设计与状态流向](#四store-设计与状态流向)
5. [API 层设计分析](#五api-层设计分析)
6. [类型系统审计](#六类型系统审计)
7. [设计系统与 CSS 审计](#七设计系统与-css-审计)
8. [后端模块审计](#八后端模块审计)
9. [风险与断裂点清单](#九风险与断裂点清单)
10. [重构建议优先级](#十重构建议优先级)

---

## 一、全量清单与文件地图

### 1.1 前端文件总览（共 39 个 TypeScript/TSX 文件）

#### 组件层（22 文件）

| # | 路径 | 行数 | 类型 | 职责 |
|---|------|------|------|------|
| 1 | `components/common/Card.tsx` | 17 | UI 容器 | 圆角卡片容器，支持 className 透传 |
| 2 | `components/common/Button.tsx` | 35 | UI 按钮 | primary/secondary/danger/ghost 4 变体 + loading |
| 3 | `components/common/Badge.tsx` | 30 | UI 标签 | default/info/success/warning/error 5 变体 |
| 4 | `components/common/Skeleton.tsx` | 19 | UI 骨架屏 | line/card/avatar 3 变体 |
| 5 | `components/common/EmptyState.tsx` | 19 | UI 空态 | icon + title + description + 可选 children |
| 6 | `components/common/SensitiveInput.tsx` | 54 | UI 敏感输入 | 遮罩字段 + 显示/隐藏切换 |
| 7 | `components/common/Toast.tsx` | 43 | UI Toast | 从 errorStore 读取并渲染所有 toast |
| 8 | `components/common/ErrorBoundary.tsx` | 51 | 安全 | React 错误边界 + 重试按钮 |
| 9 | `components/common/UrgencyBadge.tsx` | 22 | UI 标签 | urgency number -> level string + variant |
| 10 | `components/common/ProactiveEnginePanel.tsx` | 115 | 设置面板 | 紧迫度阈值/每日上限/间隔/冷却 + 保存 |
| 11 | `components/layout/Sidebar.tsx` | 175 | 布局 | 桌面侧边栏：logo + 3 区导航 + 折叠 |
| 12 | `components/layout/MobileNav.tsx` | 46 | 布局 | 手机底部导航：15 项横向滚动 |
| 13 | `components/chat/MessageList.tsx` | 112 | 聊天消息列表 | react-window 虚拟化 + TypingIndicator + 空态 |
| 14 | `components/chat/MessageBubble.tsx` | 52 | 聊天消息气泡 | 用户/AI 气泡 + 中断重试 + 情感标签 |
| 15 | `components/chat/TypingIndicator.tsx` | 16 | 聊天 | "..." 动画 + 心形头像 |
| 16 | `components/chat/ChatInput.tsx` | 104 | 聊天输入 | 文本输入 + 图片上传 + 文件附件 |
| 17 | `components/chat/ProactiveToast.tsx` | 32 | 聊天 | 主动消息通知条，8s 自动消失 |
| 18 | `components/emotion/EmotionPanel.tsx` | 70 | 情感面板 | 当前情感 + 能量 + 好感度进度条 |
| 19 | `components/storyline/StorylineEditor.tsx` | 516 | 剧情线编辑器 | 5 阶段模板 + CRUD + 结局 + 自动检测 |
| 20 | `components/storyline/KnowledgePreview.tsx` | 132 | 知识库预览 | 索引统计 + 检索测试 UI |
| 21 | `components/storyline/StorylineIndicator.tsx` | 41 | 剧情线指示器 | 阶段/时间/进度条/结局状态 |
| 22 | `components/ui/Modal.tsx` | 46 | UI 模态框 | 确认/取消模态框 + danger 变体 + Escape关闭 |

**组件分类统计**：UI 基础 10 个 / 布局 2 个 / 聊天 5 个 / 情感 1 个 / 剧情线 3 个 / 模态框 1 个

#### 组件依赖分析（横向引用）

```
StorylineEditor -> Button, Badge, useStorylineConfig, useUpdateStorylineConfig, useDeleteStorylineConfig, useDetectStoryline
KnowledgePreview -> Badge, Button, client (直接使用 axios, 未通过 api/client.ts)
StorylineIndicator -> useStorylineProgress, Badge
EmotionPanel -> Badge, api.emotionState (直接 useQuery, 未用 useEmotionState hook)
ProactiveEnginePanel -> Card, Button, UrgencyBadge, api.updateProactiveConfig, errorStore
MessageList -> MessageBubble, TypingIndicator, EmptyState, chatStore
MessageBubble -> 纯展示，无依赖
ChatInput -> chatStore, errorStore, api.uploadFile
ProactiveToast -> chatStore
Toast -> errorStore
Sidebar -> NavLink, lucide-react 图标
MobileNav -> NavLink, lucide-react 图标
Modal -> lucide-react X 图标
ErrorBoundary -> Button
```

#### Store 层（4 文件）

| # | 路径 | 行数 | 技术栈 | 职责 | 状态量 | Actions |
|---|------|------|--------|------|--------|---------|
| 1 | `store/chatStore.ts` | ~92 | Zustand | 聊天状态 | 11 | 8 |
| 2 | `store/errorStore.ts` | ~45 | Zustand | Toast 通知 | 3 | 4 |
| 3 | `store/personaStore.ts` | ~75 | Zustand | 人设编辑 | 5 | 6 |
| 4 | `store/settingsStore.ts` | ~60 | Zustand + persist | UI 设置 | 5 | 4 |

**⚠️ 发现**：Zustand 使用但未与 React Query 协同。部分组件用 useQuery 直接从 API 取数据（如 EmotionPanel 直接调用 api.emotionState），部分用 store 存数据（如 chatStore 存消息）。两种状态管理策略并存但无约定。

#### API 层（5 文件）

| # | 路径 | 行数 | 风格 | 端点数量 |
|---|------|------|------|----------|
| 1 | `api/client.ts` | 220 | axios 实例 + 统一拦截器 | ~40 个端点 |
| 2 | `api/characterApi.ts` | 142 | 独立函数 + namespace | 15 个端点 |
| 3 | `api/shisiClient.ts` | 89 | namespace 嵌套 | ~28 个端点 |
| 4 | `api/voiceApi.ts` | 50 | 独立函数 + namespace | 6 个端点 |
| 5 | `api/queryClient.ts` | 12 | QueryClient 实例化 | — |

**⚠️ 发现**：`api/client.ts` 第 89-144 行直接在文件中内联所有 API 调用，而不是拆分到独立模块。这导致 client.ts 成为"上帝文件"。

#### 类型层（4 文件）

| # | 路径 | 行数 | 接口数量 |
|---|------|------|----------|
| 1 | `types/api.ts` | 694 | ~60 个接口/类型 |
| 2 | `types/shisi.ts` | 31 | 4 个接口 |
| 3 | `types/character.ts` | 22 | 2 个接口（deprecated） |
| 4 | `types/sticker.ts` | 8 | 1 个接口 |

#### Hooks 层（1 文件）

| # | 路径 | 行数 | Hooks 数量 | 说明 |
|---|------|------|-----------|------|
| 1 | `hooks/useQueries.ts` | 517 | ~40 个 | 集中式，异步 import 模式 |

**⚠️ 发现**：`useQueries.ts` 使用 `await import('../api/characterApi')` 动态导入 characterApi，但 shisiClient 是静态导入。两种策略不一致。动态导入的好处是代码分割，但代价是每次调用都产生异步开销。

#### Pages 层（22 文件，lazy-loaded）

`App.tsx` 中注册的 22 个页面路由：

| 路径 | 页面组件 | 用途 |
|------|----------|------|
| `/` | DashboardPage | 仪表盘 |
| `/chat` | ChatPage | 聊天 |
| `/persona` | PersonaPage | 人设展示 |
| `/memory` | MemoryPage | 记忆查看 |
| `/training` | TrainingPage | 声音克隆训练 |
| `/clone-data` | CloneDataPage | 克隆数据管理 |
| `/channels` | ChannelsPage | 通道管理 |
| `/settings` | SettingsPage | 设置 |
| `/logs` | LogsPage | 日志 |
| `/admin` | AdminPage | 管理后台 |
| `/characters` | CharactersPage | 角色管理 |
| `/monitor` | MonitorPage | 监控 |
| `/stats` | StatsPage | 统计 |
| `/persona-editor` | PersonaEditorPage | 人设编辑器 |
| `/stickers` | StickersPage | 贴纸管理 |
| `/psych` | PsychProfilePage | 心理画像 |
| `/safety` | SafetyPage | 安全面板 |
| `/knowledge` | KnowledgeBasePage | 知识库 |
| `/extensions` | ExtensionsPage | 扩展 |
| `/favorites` | FavoritesPage | 收藏 |
| `/users` | UsersPage | 用户管理 |
| `*` | NotFoundPage | 404 |

---

### 1.2 后端模块总览

#### API 路由模块（`api/` 目录）

| # | 文件 | 职责 |
|---|------|------|
| 1 | `app_factory.py` | FastAPI app 工厂 + 路由注册 |
| 2 | `main_routes.py` | 主路由（聊天/情感/记忆/配置等） |
| 3 | `character_routes.py` | 统一角色 CRUD（8 端点） |
| 4 | `knowledge_routes.py` | 知识库路由（统计 + 搜索） |
| 5 | `storyline_routes.py` | 剧情线路由（6 端点） |
| 6 | `memory_routes.py` | 记忆路由 |
| 7 | `persona_card_routes.py` | 人设卡片路由 |
| 8 | `voice_routes.py` | 角色音色绑定路由（5 端点） |
| 9 | `auth.py` | 认证 |
| 10 | `deps.py` | 依赖注入 |
| 11 | `qrcode_store.py` | 微信二维码存储 |
| 12 | `session_manager.py` | 会话管理 |
| 13 | `websocket_server.py` | WebSocket 服务 |
| 14 | `safety_log.py` | 安全日志 |
| 15 | `tool_history.py` | 工具历史追踪 |
| 16 | `training_state.py` | 训练状态管理 |

#### Shisi 核心模块（`shisi/` 目录）

| 子包 | 文件数 | 关键模块 |
|------|--------|----------|
| `affinity/` | 4 | 好感度引擎 + routes |
| `character/` | 4 | 角色服务 + routes |
| `core/` | 5 | CharacterAggregate + PromptBuilder + MigrationService |
| `emotion_stage/` | 3 | 情感阶段 + stage config |
| `knowledge/` | 2 | CharacterKnowledgeService + BM25Retriever |
| `memory/` | 3 | FavoriteManager + ForwardManager |
| `persona/` | 2 | 人设 routes |
| `sticker/` | 8 | 贴纸管理/导入/推荐/安全 |
| `storyline/` | 4 | 剧情线引擎/检测器/事件调度 |
| `voice/` | 3 | 角色音色/情感 TTS |
| `vital_signs/` | 2 | 生命体征引擎 |
| `wechat/` | 4 | 命令处理器/主动消息/贴纸适配 |
| 根路由 | 5 | affinity/character/emotion_stage/memory/persona/stats/sticker/training/vital_signs |

#### 入口文件（根目录）

| 文件 | 职责 |
|------|------|
| `main.py` | FastAPI 启动 + 调度器 + 通道注册 |
| `orchestrator.py` | 消息编排器 |
| `girlfriend_manager.py` | GirlfriendManager 单例 |
| `pyproject.toml` | 项目元数据 + 依赖 |

---

## 二、架构总览与数据流

### 2.1 前端分层架构

```
Pages (22)                          ← React Router / Lazy load
   │
   ├── 使用 Hooks (useQueries.ts)  ──> React Query
   ├── 使用 Stores (Zustand)        ──> 本地状态
   ├── 直接调 API (api/client.ts)   ──> axios
   └── 嵌套 Components
         │
         ├── common/   (10) ← 纯 UI
         ├── chat/     (5)  ← 聊天专有
         ├── emotion/  (1)  ← 情感
         ├── storyline/(3)  ← 剧情线
         ├── layout/   (2)  ← 布局
         └── ui/       (1)  ← 通用交互
```

### 2.2 数据流向模式

本项目中存在 **3 种并行数据获取模式**，无统一约定：

**模式 A：React Query + Hook（推荐）**
```
Page → useCharacters() → useQuery → shisiClient.characters.list() → axios → Backend
```
使用在：CharactersPage, ChannelsPage

**模式 B：直接 useQuery + api.xxx（混合）**
```
Component → useQuery → api.client.emotionState() → axios → Backend
```
使用在：EmotionPanel（直接调用 api），KnowledgePreview（直接调用 client.get）

**模式 C：Zustand Store**
```
ChatInput → chatStore → useChatStore() → 本地状态
ProactiveToast → chatStore
MessageList → chatStore
```

### 2.3 关键数据流

```
用户输入 → ChatInput → onSend → ChatPage → WS/HTTP → Backend
                                                      ↓
Backend → WebSocket → stream_token → chatStore.appendStreamToken → MessageList
Backend → WebSocket → proactive → chatStore.setProactiveMessage → ProactiveToast
Backend → REST → useQuery → Page 组件渲染
```

---

## 三、前端组件依赖图谱

### 3.1 组件间依赖（import 链）

```
App.tsx
  ├── Sidebar
  ├── MobileNav
  ├── ToastContainer (from Toast.tsx, reads errorStore)
  └── Pages (22, lazy)
        ├── ChatPage
        │     ├── MessageList  →  MessageBubble + TypingIndicator + EmptyState
        │     ├── ChatInput    →  chatStore + errorStore + api.uploadFile
        │     ├── EmotionPanel →  Badge
        │     └── ProactiveToast → chatStore
        ├── CharactersPage
        │     ├── StorylineEditor → Button + Badge + hooks
        │     ├── KnowledgePreview → Badge + Button + client (直接)
        │     └── StorylineIndicator → hooks + Badge
        ├── DashboardPage
        │     └── ProactiveEnginePanel → Card + Button + UrgencyBadge
        ...
        └── NotFoundPage
```

### 3.2 每个组件的外部依赖清单

| 组件 | 依赖的 Store | 依赖的 API | 依赖的 Hook | 依赖的 UI 组件 |
|------|-------------|-----------|-------------|---------------|
| MessageList | chatStore | — | — | MessageBubble, TypingIndicator, EmptyState |
| ChatInput | chatStore, errorStore | api.uploadFile | — | — |
| ChatMessage (Bubble) | — | — | — | — |
| TypingIndicator | — | — | — | — |
| ProactiveToast | chatStore | — | — | — |
| EmotionPanel | — | api.emotionState (内联) | — | Badge |
| Sidebar | — | — | — | — |
| MobileNav | — | — | — | — |
| Toast | errorStore | — | — | — |
| ErrorBoundary | — | — | — | Button |
| ProactiveEnginePanel | errorStore | api.updateProactiveConfig | — | Card, Button, UrgencyBadge |
| UrgencyBadge | — | — | — | Badge |
| StorylineEditor | — | — | 4 个 storyline hooks | Button, Badge |
| KnowledgePreview | — | client.get (直接) | — | Badge, Button |
| StorylineIndicator | — | — | useStorylineProgress | Badge |
| Modal | — | — | — | — |

---

## 四、Store 设计与状态流向

### 4.1 chatStore（核心状态）

**状态量（11 个）**：
- `messages: ChatMessage[]` — 消息列表
- `streamingMessage: ChatMessage | null` — 当前流式消息
- `isStreaming: boolean` — 流式状态
- `wsConnected: boolean` — WebSocket 连接状态
- `sessionId: string` — 当前会话 ID
- `sessions: Array` — 会话列表
- `proactiveMessage: string | null` — 主动消息
- `activeCharacterId: string` — 当前角色 ID
- `focused: boolean` — 是否聚焦
- `hasMore: boolean` — 是否还有更多历史
- `paginationLoading: boolean` — 翻页加载中

**Actions（8 个）**：
- `addMessage` / `updateLastMessage` / `appendStreamToken`
- `setStreaming` / `setWsConnected`
- `setProactiveMessage`
- `loadSessions` / `switchSession`

**⚠️ 注意**：`appendStreamToken` 是基于字符串拼接，当消息过长时可能造成性能问题（FE-02）。同时 streamingMessage 和 messages 的合并逻辑在 MessageList 中用 useMemo 处理。

### 4.2 errorStore（全局通知）

**状态量（3 个）**：
- `toasts: Toast[]` — toast 列表（含 id, type, message）
- `nextId: number` — 自增 ID
- `removing: Set<number>` — 正在移除的 toast ID

**Actions（4 个）**：
- `addToast` / `removeToast` / `startRemoving`
- 自动 5 秒后从 removing 中清除

### 4.3 personaStore（人设编辑器）

**状态量（5 个）**：
- `traits: Record<string, number>` — 性格特征
- `style: Record<string, number>` — 说话风格
- `anchors: string[]` — 核心锚点
- `isDirty: boolean` — 是否修改
- `isSaving: boolean` — 保存中

### 4.4 settingsStore（UI 设置，带 persist）

**状态量（5 个）**：
- `theme: 'light' | 'dark'` — 主题
- `sidebarCollapsed: boolean` — 侧边栏折叠
- `chatFontSize: number` — 字号
- `sendOnEnter: boolean` — 回车发送
- `showTimestamps: boolean` — 显示时间戳

---

## 五、API 层设计分析

### 5.1 三层 API 调用模式对比

| 维度 | client.ts | shisiClient.ts | characterApi.ts |
|------|-----------|----------------|-----------------|
| 风格 | 直接 exports | namespace 对象 | 函数 + namespace |
| 响应处理 | `r.data` 直接取 | `unwrap<T>()` 解包 | `.then(r => r.data as T)` 断言 |
| 错误处理 | 全局拦截器 | 全局拦截器 | 全局拦截器 |
| 使用场景 | 主 API | 旧 shisi API | 统一角色 API |

**⚠️ 不一致**：
1. shisiClient 预期后端返回 `{data: T}` 包裹格式，而 client.ts 直接取 `r.data`
2. characterApi 用 `.then(r => r.data as T)` TypeScript 类型断言，没有运行时验证
3. 三种模式中，只有 shisiClient 做了 unwrap，其他两种直接返回

### 5.2 client.ts 上帝文件

`client.ts` 第 89-220 行定义了 ~40 个 API 端点，包括：
- 聊天（chat/chatStream/chatHistory）
- 情感（emotionState/emotionTrend）
- 记忆（memoryFacts）
- 工具（tools/toolHistory）
- 主动消息（proactiveState/proactiveHistory）
- 训练（trainingStatus/trainingProgress/extract/clean/train/stop/test/apply）
- 克隆数据（cloneContacts/cloneDatasets/cloneDatasetDetail/cloneDeleteDataset/etc）
- 心理画像（psychProfile/psychSnapshots/psychReset/psychMentalHealth/psychLiwc）
- 安全（safetyStats/safetyLog/safetyConfig）
- RAG（ragStats/ragSearch/ragUpload）
- 语音（voiceStatus/voiceSynthesize）
- 插件（plugins/togglePlugin）
- 文件上传（uploadFile）
- 微信（wechatConnect/wechatDisconnect/wechatConnectionStatus/wechatQrCode）
- 通道（channels/wechatStatus/wechatReconnect）
- 系统（health/stats/dashboardStats/config/saveConfig/logs）

重构方向建议：按功能域拆分到独立文件（如 `api/chat.ts`, `api/training.ts`, `api/clone.ts`）

### 5.3 API 路径完整性检查

前端调用的所有 API 路径 vs 后端路由注册：

| 前端路径 | 后端路由 | 状态 |
|----------|---------|------|
| `POST /chat` | ✅ main_routes | 一致 |
| `POST /chat/stream` | ✅ websocket_server + main_routes | 一致 |
| `GET /characters` | ✅ character_routes | 一致 |
| `POST /characters` | ✅ character_routes | 一致 |
| `GET /characters/{id}` | ✅ character_routes | 一致 |
| `PUT /characters/{id}` | ✅ character_routes | 一致 |
| `DELETE /characters/{id}` | ✅ character_routes | 一致 |
| `POST /characters/{id}/activate` | ✅ character_routes | 一致 |
| `GET /characters/{id}/persona` | ✅ character_routes | 一致 |
| `PUT /characters/{id}/persona` | ✅ character_routes | 一致 |
| `GET /characters/{id}/voice` | ✅ voice_routes | 一致 |
| `POST /characters/{id}/voice` | ✅ voice_routes | 一致 |
| `PUT /characters/{id}/voice` | ✅ voice_routes | 一致 |
| `DELETE /characters/{id}/voice` | ✅ voice_routes | 一致 |
| `POST /characters/{id}/voice/test` | ✅ voice_routes | 一致 |
| `GET /voice/speakers` | ✅ voice_routes | 一致 |
| `GET /characters/{id}/storyline` | ✅ storyline_routes | 一致 |
| `PUT /characters/{id}/storyline` | ✅ storyline_routes | 一致 |
| `DELETE /characters/{id}/storyline` | ✅ storyline_routes | 一致 |
| `GET /characters/{id}/storyline/progress` | ✅ storyline_routes | 一致 |
| `POST /characters/{id}/storyline/detect` | ✅ storyline_routes | 一致 |
| `POST /characters/{id}/storyline/reset` | ✅ storyline_routes | 一致 |
| `GET /characters/{id}/knowledge/stats` | ✅ knowledge_routes | 一致 |
| `POST /characters/{id}/knowledge/search` | ✅ knowledge_routes | 一致 |

**⚠️ 历史遗留**：之前认为 `trainingApply()` 和 `trainingTrain()` 缺少 character_id，但后端实际 **不接收 character_id 参数**（`/training/train` 只读 epochs/lora_rank，`/training/apply` 无参数）。前端传的 character_id 被 FastAPI 静默忽略，不产生 422。BR-02/BR-03 标记为 **误报，非实际风险**。

---

## 六、类型系统审计

### 6.1 类型定义分布

| 域 | 文件 | 核心类型 |
|----|------|----------|
| 统一角色 | `api.ts:477-527` | UnifiedCharacter, UnifiedCharacterCreate/Update, CharacterListResponse |
| 角色音色 | `api.ts:526-581` | VoiceConfig, VoiceBind/UpdateRequest, SpeakerListResponse |
| 剧情线 | `api.ts:583-694` | StorylineConfig/Stage/Ending/Progress/State/DetectResult 等 |
| 对话 | `api.ts:41-82` | ChatMessage, ChatResponse, WSIncomingMessage |
| 情感 | `api.ts:1-11` | EmotionState, EmotionTrend |
| 记忆 | `api.ts:30-39` | MemoryFact, FactCategory |
| 仪表盘 | `api.ts:84-95` | DashboardStats |
| 微信 | `api.ts:97-105,465-470` | WeChatStatus, WeChatConnectionStatus |
| 训练 | `api.ts:107-129` | TrainingProgress, TrainingStatusEnum |
| 通道 | `api.ts:131-142` | Channel, ChannelStatus, ChannelType |
| 主动引擎 | `api.ts:144-167` | ProactiveEngineState, ProactiveConfig, UrgencyLevel |
| 配置 | `api.ts:60-62,194-211` | SystemConfig, ConfigItem/Section |
| 克隆数据 | `api.ts:214-259` | CloneContact/Dataset/Conversation/Stats |
| 心理画像 | `api.ts:262-380` | OceanTraits, PadState, HexacoTraits, DarkTriadTraits, MentalHealth, LIWC |
| 安全面板 | `api.ts:382-397` | SafetyStats, SafetyLogEntry |
| RAG | `api.ts:399-411` | RAGStats, RAGSearchResult |
| 语音 | `api.ts:414-422` | VoiceStatus |
| 插件 | `api.ts:426-433` | PluginEntry, PluginsList |
| 工具/主动 | `api.ts:447-461` | ToolHistoryEntry, ProactiveHistoryEntry |

### 6.2 类型断裂点

| 位置 | 问题 | 原因 |
|------|------|------|
| `client.ts` 第 122 行 | `api.config()` 返回 `Record<string, unknown>` | 前次审计 RC-02 提及 |
| `client.ts` 多处 | `.then(r => r.data as T)` | 运行时无验证，类型不可信 |
| `hooks/useQueries.ts` 第 85 行 | `r.data as DashboardStats` | 同上 |
| `hooks/useQueries.ts` 第 167 行 | `r.data as import('../types/api').PersonaProfile` | 内联 import 路径 |
| `types/character.ts` | `CharacterState` marked deprecated | 但仍在 shisiClient 和 useQueries 中使用 |

### 6.3 缺失类型

- `KnowledgeStats` 在 `KnowledgePreview.tsx` 中内联定义（第 7-12 行），未进类型文件
- `SearchResult` 同样内联定义（第 14-18 行）
- 部分 useQuery 结果类型直接内联 `.then(r => r.data as {...})` 匿名类型

---

## 七、设计系统与 CSS 审计

### 7.1 Tailwind + 自定义 Design System

`index.css`（288 行）定义了完整的 Maze Design System：

**颜色系统**：
- 主色：Cyan 系列（--color-primary-50~900）
- 辅色：Magenta/Purple 系列（--color-accent-50~900）
- 背景：浅色系（bg-deep: #f5f7fa, bg-surface: #fff）
- 文字：4 级灰度（text-primary/secondary/muted/dim）
- 情感色：rose/amber/blue/red/purple/green
- 状态色：success(cyan), warning(purple), error(rose), info(blue)

**组件类名系统（maze-*）**：
- `.maze-card` — 卡片容器（16px 圆角 + 微阴影）
- `.maze-btn` / `.maze-btn-primary` / `.maze-btn-ghost` / `.maze-btn-danger` — 按钮变体
- `.maze-stat` / `.maze-stat-label` / `.maze-stat-value` — 统计数字
- `.maze-dot` / `.maze-dot-online` / `.maze-dot-offline` / `.maze-dot-error` / `.maze-dot-active` — 状态圆点
- `.maze-progress` / `.maze-progress-bar` — 渐变色进度条
- `.maze-divider` — 虚线分隔线
- `.maze-gradient-text` — 渐变文字
- `.maze-badge` / `.maze-badge-cyan` / `.maze-badge-magenta` / `.maze-badge-green` — 标签

**动画系统**：
- `animate-fade-in` — 淡入上升
- `animate-pulse-glow` — 呼吸辉光
- `animate-slide-up` — 滑入
- `animate-skeleton` — 骨架屏脉冲
- `animate-toast-in` / `animate-toast-out` — Toast 出入
- `animate-scale-in` — 缩放进入

**⚠️ 发现**：`index.css` 中的 `.maze-btn-primary` 等类名在 React 组件中未使用。Button.tsx 组件使用的是 Tailwind utility classes 而非这些自定义类名。这暗示 CSS 可能先于组件创建，或者设计系统正在过渡期（从传统 CSS 到 Tailwind）。

### 7.2 暗色主题现状

`settingsStore` 中有 `theme: 'light' | 'dark'` 但：
- `index.css` 只定义了浅色变量（--color-bg-deep: #f5f7fa 等）
- 所有组件使用硬编码的浅色 tailwind 类（如 `text-gray-800`, `bg-white`, `bg-gray-50`）
- 没有 `dark:` 前缀类或 CSS 变量切换逻辑
- 暗色主题不存在，theme 设置无效果

---

## 八、后端模块审计

### 8.1 模块架构总览

```
main.py  (入口)
  ├── app_factory.create_app() → FastAPI
  │     ├── main_routes      (通用端点)
  │     ├── character_routes (统一角色)
  │     ├── knowledge_routes (知识库)
  │     ├── storyline_routes (剧情线)
  │     ├── memory_routes    (记忆)
  │     ├── persona_card_routes (人设卡)
  │     ├── voice_routes     (音色绑定)
  │     ├── shisi/           (旧架构路由)
  │     ├── websocket_server (WebSocket)
  │     └── auth             (认证)
  │
  ├── orchestrator.py
  │     ├── Orchestrator
  │     └── OptimizedOrchestrator
  │
  └── girlfriend_manager.py
        └── GirlfriendManager (单例)
              ├── shisi/character/manager (角色管理)
              ├── shisi/core/character_aggregate (聚合)
              ├── shisi/core/prompt_builder (提示构建)
              ├── shisi/affinity/ (好感度)
              ├── shisi/emotion_stage/ (情感阶段)
              ├── shisi/vital_signs/ (生命体征)
              ├── shisi/knowledge/ (RAG知识库)
              ├── shisi/storyline/ (剧情线引擎)
              ├── shisi/sticker/ (贴纸)
              ├── shisi/memory/ (记忆)
              ├── shisi/voice/ (声音)
              ├── shisi/wechat/ (微信)
              └── proactive/ (主动消息)
```

### 8.2 新旧 API 体系并存

**旧体系（shisi/）**：
- 路径前缀：`/api/shisi/`
- 角色数据源：shisi `character/models.py` + 文件存储
- 特点：成熟但耦合度高

**新体系（api/character_routes.py）**：
- 路径前缀：`/api/characters/`
- 角色数据源：统一 CharacterAggregate 模型
- 特点：清晰解耦，但前端对接不完全

**关系**：两个体系通过 `shisi/core/character_aggregate.py` 共享底层模型。前端 `shisiClient.ts` 调用旧路由，`characterApi.ts` 调用新路由。

### 8.3 剧情线模块（全新，Phase 2）

```
shisi/storyline/
  ├── __init__.py
  ├── config.py        — StorylineConfig + StageDefinition + Timing + EndingConfig
  ├── engine.py         — StorylineEngine（时间推进 + 阶段检测 + 结局触发 + 状态持久化）
  ├── detector.py       — StorylineDetector（10+ 模式自动检测）
  ├── event_dispatcher.py — 事件分发
  └── stage_engine.py   — 阶段引擎
```

**api/storyline_routes.py** — 6 个端点：
1. `GET /characters/{id}/storyline` — 查询配置
2. `PUT /characters/{id}/storyline` — 更新配置
3. `DELETE /characters/{id}/storyline` — 删除配置
4. `GET /characters/{id}/storyline/progress` — 进度
5. `POST /characters/{id}/storyline/detect` — 自动检测
6. `POST /characters/{id}/storyline/reset` — 重置

### 8.4 知识库 RAG 模块（全新，Phase 1）

```
shisi/knowledge/
  ├── __init__.py
  ├── retriever.py                   — BM25Retriever + KeywordRetriever + KnowledgeChunk
  └── character_knowledge_service.py — 知识提取 + 建索引 + 检索 + 单例
```

### 8.5 ASE 增强模块（全新，Phase 3）

```
shisi/ase/
  ├── __init__.py
  ├── trigger_engine.py  — TriggerEngine（5 种触发类型）
  └── scene_narrator.py  — SceneNarrator（场景旁白 + 主动消息）
```

集成到 `shisi/wechat/proactive_messenger.py`（向后兼容）

---

## 九、风险与断裂点清单

### 🔴 P0 — 阻断级

| ID | 风险 | 位置 | 说明 |
|----|------|------|------|
| BR-01 | aiophysics 幻觉依赖 | `pyproject.toml` | aiohttp→aiophysics 不是真实包，pip install 失败 | 
| ~~BR-02~~ | ~~trainingApply 缺 character_id~~ | ~~已核实：后端无此参数~~ | ✅ **误报**，后端 `/training/apply` 不收参数 |
| ~~BR-03~~ | ~~trainingTrain 缺 character_id~~ | ~~已核实：后端无此参数~~ | ✅ **误报**，后端 `/training/train` 不收 character_id |

### 🟡 P1 — 高风险

| ID | 风险 | 位置 | 说明 |
|----|------|------|------|
| BR-04 | shisiClient unwrap vs client 直接取 r.data 不一致 | 两种 API 模式 | 数据格式假设不同，响应结构变化时静默失败 |
| BR-05 | 暗色主题设置无效果 | `settingsStore` + 全体组件 | theme: 'dark' 不产生任何视觉变化 |
| BR-06 | CSS maze-* 类名未在组件中使用 | `index.css:105-241` | 与 Tailwind 并行的两套样式体系 |
| BR-07 | useQueries.ts 动态 import 异步开销 | `useQueries.ts:305-517` | 每次 hook 调用都 await import() |
| BR-08 | KnowledgePreview 直连 axios client | `KnowledgePreview.tsx:33` | 绕过 api/client.ts 的拦截器体系 |
| BR-09 | EmotionPanel 内联 useQuery | `EmotionPanel.tsx:17-21` | 未用集中式 hook，重复定义 |
| BR-10 | 两套角色 API 并行（新旧体系） | shisiClient + characterApi | 数据结构不同，前端需理解两套 Schema |

### 🟠 P2 — 中风险

| ID | 风险 | 位置 | 说明 |
|----|------|------|------|
| BR-11 | `appendStreamToken` 字符串拼接 | `chatStore` | 大消息体下 O(n²) 问题 |
| BR-12 | FixedSizeList 固定 80px item 高度 | `MessageList.tsx:37` | 内容高度变化时不适应 |
| BR-13 | `require('react-window')` 动态加载 | `MessageList.tsx:28-29` | 非 ESM 推荐方式，tree-shaking 失效 |
| BR-14 | 前端零测试 | 整个前端 | vitest + testing-library 未配置 |
| BR-15 | Zustand + React Query 双重状态 | 全系统 | 无约定何时用 store vs query |
| BR-16 | chatStore 消息无持久化 | chatStore | 刷新丢失会话 |

---

## 十一、页面层审计（Pages Layer）

### 11.1 22 页面逐页分析

| # | 页面 | 行数 | 数据获取模式 | Store 依赖 | 风险等级 |
|---|------|------|-------------|-----------|---------|
| 1 | DashboardPage | ~150 | ✅ Hook 模式 (useDashboardStats/useTools/useToolHistory/useProactiveHistory) | 无 | ✅ 低 |
| 2 | ChatPage | ~80 | 🔶 Hook + chatStore | chatStore (消息/流式/WS) | 🟡 P1: WS 重连未处理, 消息无持久化 |
| 3 | CharactersPage | ~250 | ✅ Hook 模式 (useUnifiedCharacters/useVoiceConfig/useSpeakers) | 无 | ✅ 低 |
| 4 | PersonaPage | ~200 | ✅ Hook 模式 (usePersonaProfile) | 无 | ✅ 低 |
| 5 | PersonaEditorPage | ~250 | 🔶 Hook + personaStore + 动态 import client | personaStore (traits/style/anchors) | ✅ 低 |
| 6 | MemoryPage | ~80 | ✅ Hook 模式 (useMemoryFacts) | 无 | ✅ 低 |
| 7 | TrainingPage | ~300 | 🔶 Hook + 动态 import client | 无 | 🔴 P0: character_id 缺失 |
| 8 | CloneDataPage | ~180 | 🔶 Hook + 动态 import client | 无 | ✅ 低 |
| 9 | ChannelsPage | ~120 | ✅ Hook 模式 (useChannels/useWeChatConnectionStatus) | 无 | ✅ 低 |
| 10 | SettingsPage | ~200 | 🔶 Hook + 动态 import client + settingsStore | settingsStore (theme/fontSize/sendOnEnter) | ✅ 低 |
| 11 | LogsPage | ~100 | ✅ Hook 模式 (useLogs) | 无 | ✅ 低 |
| 12 | AdminPage | ~150 | ✅ Hook 模式 (useHealth) | 无 | ✅ 低 |
| 13 | MonitorPage | ~250 | ✅ Hook 模式 (useStats) | 无 | ✅ 低 |
| 14 | StatsPage | ~100 | ✅ Hook 模式 (useStats) | 无 | ✅ 低 |
| 15 | StickersPage | ~200 | ❌ 原始 useQuery + shisiClient | 无 | 🟡 P1: 使用旧 shisi API |
| 16 | PsychProfilePage | ~200 | ❌ 原始 useQuery + client.get() | 无 | 🟡 P1: 内联 API 调用 |
| 17 | SafetyPage | ~150 | ❌ 原始 useQuery + client.get() | 无 | 🟡 P1: 内联 API 调用 |
| 18 | KnowledgeBasePage | ~150 | ❌ 原始 useQuery + client.get() | 无 | 🟡 P1: 内联 API 调用 |
| 19 | ExtensionsPage | 119 | ✅ Hook 模式 + 动态 import client(voiceSynthesize) | 无 | ✅ 低 (仅 voiceSynthesize 动态 import) |
| 20 | FavoritesPage | 128 | 🔶 Hook(useUnifiedCharacters) + 手动 useState + memoryApi 直连 | 无 | 🟡 P1: 绕过 client.ts 拦截器 |
| 21 | UsersPage | 294 | ❌ 原始 client.get/post/delete + 手动 useState | errorStore (toast) | 🟡 P1: 无 React Query 缓存, 内联类型 |
| 22 | NotFoundPage | 17 | ✅ 纯静态 | 无 | ✅ 低 |

### 11.2 数据获取模式分布

| 模式 | 页面数 | 页面 |
|------|--------|------|
| ✅ **A - React Query via Hooks**（推荐） | 12 | Dashboard, Chat(部分), Characters, Persona, PersonaEditor(部分), Memory, Channels, Logs, Admin, Monitor, Stats, Extensions |
| ❌ **B - 原始 useQuery + shisiClient**（旧API） | 1 | Stickers |
| ❌ **C - 原始 useQuery + client.get()**（内联） | 3 | PsychProfile, Safety, KnowledgeBase |
| 🔶 **D - 动态 import client**（混合） | 4 | Training, CloneData, Settings, Extensions(voiceSynthesize) |
| 🔶 **E - direct memoryApi import**（旁路） | 1 | Favorites |
| ❌ **F - 原始 client 直调 + useState**（无 query） | 1 | Users |
| ✅ **G - 纯静态** | 1 | NotFound |

**发现**：22 个页面中存在 6 种不同的数据获取模式，无统一约定。12 个页面使用 Hook 模式（推荐），10 个页面存在程度不一的架构偏移。

### 11.3 页面级风险汇总

| 风险 | 严重度 | 影响页面 |
|------|--------|---------|
| 旧 shisi API 调用（需迁移至 unified character API） | 🟡 P1 | StickersPage |
| 原始 client.get() 内联查询（无缓存/无统一错误处理） | 🟡 P1 | PsychProfilePage, SafetyPage, KnowledgeBasePage |
| 绕过 client.ts 拦截器体系 | 🟡 P1 | FavoritesPage (memoryApi) |
| 手动管理服务端状态（无 React Query） | 🟡 P1 | FavoritesPage, UsersPage |
| 无 React Query 且无 store 的状态管理 | 🟡 P1 | UsersPage (所有数据手动 useState) |
| 动态 import 每次调用产生异步开销 | 🟢 P3 | 所有使用动态 import 的页面 |

---

### 阶段 1：架构清理（P1，本周 — 基于用户决策）

1. **统一 shisiClient 和 client 的数据解包逻辑** → 统一为一种模式（决策 1.A）
2. **Cut shisi API** → 迁移 StickersPage 等使用 shisiClient 的页到 unified character API（决策 1.A）
3. **清除 CSS maze-* 类** → 纯 Tailwind（决策 2.A）
4. **移除暗色主题选项** → Light-only（决策 3.A）
5. **拆分 client.ts 上帝文件** → 按域拆：chat.ts / training.ts / clone.ts / system.ts（决策 4.A）
6. **UI state → store, server data → query** 规范（决策 5）

### 阶段 2：标准化（P2，本月）

7. **统一 API 调用模式** → 全部走 React Query hooks（含 FavoritesPage memoryApi, UsersPage 直调, StickersPage/KnowledgeBasePage/SafetyPage/PsychProfilePage 内联）
8. **chatStore 消息持久化** → 加 localStorage 或 IndexedDB
9. **统一类型断言** → 用 zod/zodios 做运行时验证

### 阶段 3：测试与质量（P3，下月）

10. **配置 vitest + @testing-library/react** → 前端测试覆盖
11. **MessageList 虚拟化优化** → 动态 itemSize
12. **为 pages/ 加测试** → 覆盖率 > 60%
13. **统一状态管理策略文档** → 何时用 store vs query

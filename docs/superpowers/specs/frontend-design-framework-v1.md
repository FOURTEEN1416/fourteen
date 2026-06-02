# 唯一的你 前端设计框架 v1.0

> 状态: **框架定稿 · 视觉风格待定**  
> 视觉风格参考将在后续提供，本文档仅定义结构、组件、数据流、路由

---

## 1. 导航架构

### 1.1 三层侧边栏

```
┌─────────────────────────────────────────────┐
│  📱 接入微信              ←  Section 1      │
├─────────────────────────────────────────────┤
│  👥 用户管理              ←  Section 2      │
│    ├── [用户列表] → 工作区                  │
│    │   ├── 🎭 创造角色                       │
│    │   ├── ⚙️ 角色设置                      │
│    │   └── 📊 状态中心                       │
├─────────────────────────────────────────────┤
│  ⚙️ 系统设置              ←  Section 3      │
│    ├── 系统配置                             │
│    ├── LLM                                  │
│    ├── 语音                                 │
│    ├── 安全                                 │
│    ├── 扩展                                 │
│    └── 日志                                 │
└─────────────────────────────────────────────┘
```

### 1.2 路由结构

```
/                          → 重定向到 /wechat
/wechat                    → 📱 接入微信
/users                     → 👥 用户管理 (用户列表)
/users/:userId             → 👥 用户管理 (选中用户 → 工作区)
/users/:userId/create      → 🎭 创造角色 tab
/users/:userId/settings    → ⚙️ 角色设置 tab (默认基础配置)
/users/:userId/settings/voice   → 音色配置
/users/:userId/settings/message → 主动消息
/users/:userId/settings/data    → 数据管理
/users/:userId/status      → 📊 状态中心
/settings                  → ⚙️ 系统设置 (默认系统配置)
/settings/llm              → LLM
/settings/voice            → 语音
/settings/security         → 安全
/settings/extensions       → 扩展
/settings/logs             → 日志
```

---

## 2. 页面详细设计

### 2.1 📱 接入微信 (`/wechat`)

**组件结构：**

```
WeChatPage
├── AliasInput         # 连接别名输入 + 保存按钮
├── ConnectionStatus   # 连接状态卡片
│   ├── 状态指示器 (已连接/已断开)
│   ├── 微信号显示
│   └── 在线时长 (连接后显示)
├── QRCodeArea         # 二维码展示/生成区
│   ├── QR码 (扫码登录)
│   └── 生成提示
├── ActionButtons      # 操作按钮组
│   ├── 生成二维码
│   ├── 重新连接
│   └── 断开连接
└── SavedConnections   # 已保存连接列表
    ├── 连接项 (别名 / wxid / 状态)
    └── 切换按钮
```

**状态定义：**

```typescript
type ConnectionState = "disconnected" | "connecting" | "connected"

interface WeChatPageState {
  alias: string               // 当前连接别名
  status: ConnectionState
  wxid: string
  onlineDuration: string      // 在线时长 (仅连接时)
  qrCode: string | null       // base64 或 URL
  savedConnections: SavedConnection[]
}

interface SavedConnection {
  alias: string
  wxid: string
  isOnline: boolean
  isCurrent: boolean
}
```

---

### 2.2 👥 用户管理 (`/users`)

**布局：** 左侧用户列表 + 右侧工作区（选中用户后展开）

**用户列表：**

```
UserList
├── SearchBar              # 搜索用户
├── UserListItem[]         # 用户列表项
│   ├── 用户名 / 微信头像 (微信名)
│   ├── 关联角色名
│   ├── 最后活跃时间
│   └── 在线状态点
```

**工作区 - 三Tab容器：**

```typescript
interface WorkspaceTabs {
  activeTab: "create" | "settings" | "status"
  userId: string            // 当前选中用户
  selectedCharacterId?: string  // 当前选中角色 (角色设置用)
}
```

#### 2.2.1 🎭 创造角色 tab

**UI 模式：** 左侧角色列表 + 右侧详情/编辑区

**角色列表组件：**

```
CharacterList
├── NewCharacterButton     # "+ 创造新角色" 按钮
├── CharacterCardItem[]    # 已有角色卡片列表
    ├── 头像 (首字母/自定义)
    ├── 角色名
    ├── 简短描述
    └── 当前活跃标记
```

**详情/编辑区 - 三种创建方式 (tab 切换)：**

```
CharacterCreator
├── CreationMethodTabs     # 💬 AI对话 | 📱 微信克隆 | 📂 导入文件
├── [AI 对话模式]
│   ├── ChatMessageList    # 用户 ↔ LLM 对话流
│   ├── MessageInput       # 输入框 + 发送
│   └── PersonaPreview     # 实时人设卡预览 (右侧浮动)
├── [微信克隆模式]
│   ├── ContactSelector    # 选择微信联系人
│   ├── CloneProgress      # 提取/分析进度
│   └── PersonaPreview     # 克隆结果预览
├── [导入文件模式]
│   ├── FileDropZone       # 文件拖拽上传
│   ├── ParsePreview       # 解析结果预览
│   └── ConfirmButton      # 确认导入
└── SaveButton             # 保存人设卡
```

**三种模式的关系（核心设计原则）：**

```
PersonaCard (唯一对象)
  ↑ 三种构建方式，可随时切换组合
  ↑ 最终产出同一套 JSON 数据
  ↑ 直接映射到后端数据结构
```

#### 2.2.2 ⚙️ 角色设置 tab

**UI 模式：** 4 个子 tab

```
RoleSettings
├── SubTabBar
│   ├── 📋 基础配置
│   ├── 🎤 音色配置
│   ├── 📨 主动消息
│   └── 📦 数据管理
└── SubTabContent (根据 active 渲染不同内容)
```

##### 2.2.2.1 📋 基础配置

```
BasicConfig
├── PersonalitySliders[]       # 性格滑条组
│   └── SliderItem
│       ├── 标签名 (温暖/脑洞/独立/嫉妒/固执)
│       ├── 滑条 (0~1)
│       └── 当前值显示
├── CoreAnchorsEditor          # 核心锚点标签编辑
│   └── TagInput (回车添加/叉号删除)
├── SpeakingStyleSliders[]     # 说话风格滑条
│   └── SliderItem (正式度/幽默感/活泼度/温柔度)
├── SpeakingStyleTags          # 口头禅标签编辑
│   └── TagInput
├── PersonaCardPreview         # 人设卡 JSON 预览
│   └── JSON 格式化显示 + 导出按钮
└── SaveButton
```

**数据类型映射：**

| 控件类型 | 后端数据类型 | 示例 |
|---------|-------------|------|
| 滑条 | float (0.0~1.0) | `warmth: 0.8` |
| 标签输入 | string[] | `core_anchors: ["表面高冷内心温柔"]` |
| 文本输入 | string | `name: "小雅"` |
| JSON 预览 | object | 整个 PersonaCard |

##### 2.2.2.2 🎤 音色配置

```
VoiceConfig
├── SoundSelection             # 音色选择区
│   ├── EngineSelector         # TTS引擎: edge-tts / gpt-sovits / bert-vits2
│   └── EngineParams           # 引擎参数面板 (按引擎切换)
│       ├── [edge-tts]
│       │   ├── SpeakerSelector    # 发音人选择 + 试听
│       │   ├── RateSlider         # 语速 (-50% ~ +50%)
│       │   ├── PitchSlider        # 音调 (-50Hz ~ +50Hz)
│       │   └── VolumeSlider       # 音量 (0% ~ +100%)
│       ├── [gpt-sovits]
│       │   ├── ServerURL          # 服务地址
│       │   ├── RefAudioUpload     # 参考音频
│       │   ├── RefTextInput       # 参考文本
│       │   └── SpeedSelector      # 语速
│       └── [bert-vits2]
│           ├── ServerURL          # 服务地址
│           └── SpeakerSelector    # 发音人
├── VoiceCloneSection           # 音色克隆 (进阶)
│   ├── AudioUploadZone         # 音频上传 (拖拽/点击)
│   ├── UploadedFileList        # 已上传文件列表
│   ├── TrainingPipeline        # 训练流程指示 (预处理→训练→应用)
│   ├── TrainingProgress        # 训练进度条
│   └── TestSynthesize          # 测试合成
│       ├── TestTextInput
│       └── PlayButton
└── SaveButton
```

**音频文件上传规格：**

| 项目 | 规格 |
|------|------|
| 格式 | mp3 / wav / m4a |
| 文件大小 | ≤ 30MB/段 |
| 推荐数量 | 3~10 段 |
| 推荐时长 | 5~30 秒/段 |
| 上传方式 | 拖拽或点击选择 |

##### 2.2.2.3 📨 主动消息

```
ProactiveConfig
├── Toggle                  # 主动消息开关
├── DailyLimitInput         # 每日条数上限
├── MinIntervalInput        # 最小间隔 (分钟)
├── CoolDownInput           # 冷却时长 (分钟)
├── UrgencyThresholdInput   # 紧迫感阈值
├── TodayStats              # 今日已发送统计
│   ├── 已发送条数
│   ├── 触发次数
│   └── 最后发送时间
├── TestSendButton          # 测试发送
└── SaveButton
```

##### 2.2.2.4 📦 数据管理

```
DataManagement
├── KnowledgeBase
│   ├── FileUploader        # 文档上传 (txt/pdf/md)
│   ├── DocumentList        # 已索引文档列表
│   │   └── DocumentItem (文件名 / 大小 / 日期 / 删除)
│   └── EmptyState
├── ConversationData
│   ├── StatsDisplay        # 消息总数 / 最后活跃
│   ├── ExportButtons       # 导出 JSON / 导出 CSV
│   └── DangerZone          # 清空对话 (需确认)
└── MemoryData
    ├── CountDisplay        # 记忆数量
    ├── ViewListButton      # 查看记忆列表
    └── DangerZone          # 清空记忆 (需确认)
```

**记忆列表展开视图（查看记忆列表）：**

```
MemoryListModal
├── SearchBar
├── MemoryItem[]
│   ├── 记忆内容文本
│   ├── 类别标签
│   ├── 创建时间
│   └── 删除按钮
└── CloseButton
```

#### 2.2.3 📊 状态中心 tab

```
StatusCenter
├── CharacterStatusCards[]      # 每个角色一张状态卡
│   └── StatusCard
│       ├── CharacterHeader     # 角色名 + 活跃状态
│       ├── StatsGrid           # 统计网格
│       │   ├── 总对话数
│       │   ├── 今日对话数
│       │   ├── 平均响应时长
│       │   └── 主动消息触发
│       ├── EmotionTrend        # 情绪趋势折线图 (7天)
│       └── EmotionParams       # 情绪参数编辑
│           ├── BaselineSlider  # 基线
│           ├── VolatilitySlider # 波动幅度
│           └── ResilienceSlider # 恢复速度
└── QuickActions                # 全局操作
    ├── 重置角色
    └── 删除角色
```

---

### 2.3 ⚙️ 系统设置 (`/settings`)

**布局：** 左侧 6 项导航 + 右侧内容面板

#### 2.3.1 系统配置

```
SystemConfig
├── EnvironmentSelector     # 环境选择 (开发/生产)
├── LLMCacheToggle          # LLM 缓存开关
├── LLMCacheDurationInput   # 缓存时长
└── SaveButton
```

#### 2.3.2 LLM

```
LLMConfig
├── ModelSelector           # 模型选择下拉 (DeepSeek Chat / 通义千问 / 文心一言)
├── GatewayConfigButton     # 配置网关 (支持任意国内厂商)
├── APIKeyInput             # API Key (密码态显示)
│   └── APIKeyGuide         # 申请引导 (点击切换厂商)
│       ├── DeepSeek → platform.deepseek.com
│       ├── 通义千问 → dashscope.aliyun.com
│       └── 文心一言 → yiyan.baidu.com
├── TemperatureSlider       # 脑洞 (0~2, 默认 0.85)
│   └── Tooltip: 小=严谨可靠 / 大=天马行空
├── RetryConfig             # 重试策略
│   ├── WaitSelector        # 等待时间 (15s/30s/60s)
│   └── RetryCountSelector  # 重试次数 (1/2/3)
└── SaveButton
```

#### 2.3.3 语音

```
VoiceConfig
├── EngineSelector          # TTS引擎 (Edge TTS / GPT-SoVITS / Bert-VITS2)
└── SaveButton
```

#### 2.3.4 安全

```
SecurityConfig
├── LoginPassword
│   ├── Status (已设置/未设置)
│   └── ModifyButton
├── SessionExpirySelector   # 会话过期 (1天/7天/30天/90天)
├── IPWhitelistToggle       # IP 白名单开关
├── AdminToggle             # 管理后台开关 (关闭后仅微信端)
├── ContentFilterToggle     # 敏感词过滤
├── LogDesensitizeToggle    # 日志脱敏
└── SaveButton
```

#### 2.3.5 扩展

```
ExtensionsConfig
├── PluginList
│   ├── PluginItem (WebDAV同步)
│   │   ├── 名称/描述
│   │   ├── 开关 Toggle
│   │   └── 内置标签
│   └── PluginItem (Web搜索)
│       ├── 名称/描述
│       ├── 开关 Toggle
│       └── 内置标签
├── MCPExtensionsSection
│   ├── EmptyState
│   └── AddExtensionButton (预留)
└── SaveButton
```

#### 2.3.6 日志

```
LogsViewer
├── LevelSelector           # 日志级别 (DEBUG/INFO/WARN/ERROR)
├── Toolbar
│   ├── 清屏 / 暂停 / 导出
│   └── SearchInput
└── LogViewer               # 日志实时显示
    └── LogLine[]
        ├── 时间戳
        ├── 级别标签
        └── 消息内容
```

---

## 3. 数据模型

### 3.1 PersonaCard (核心对象)

```typescript
interface PersonaCard {
  id: string
  name: string
  avatar?: string
  description?: string

  // 性格
  personality: {
    warmth: number        // 温暖 0~1
    playfulness: number   // 顽皮 0~1 (用户重命名为 "脑洞")
    independence: number  // 独立 0~1
    jealousy: number      // 嫉妒 0~1
    stubbornness: number  // 固执 0~1
  }

  // 核心锚点
  core_anchors: string[]

  // 说话风格
  speaking_style: {
    formality: number     // 正式度 0~1
    humor: number         // 幽默感 0~1
    liveliness: number    // 活泼度 0~1
    gentleness: number    // 温柔度 0~1
    catchphrases: string[] // 口头禅
  }

  // 音色配置
  voice: {
    engine: "edge-tts" | "gpt-sovits" | "bert-vits2"
    speaker_name: string
    rate?: number         // 语速
    pitch?: number        // 音调
    volume?: number       // 音量
    server_url?: string   // GPT-SoVITS/Bert-VITS2
    ref_audio?: string    // 参考音频路径
    ref_text?: string     // 参考文本
  }

  // 主动消息配置
  proactive: {
    enabled: boolean
    daily_limit: number
    min_interval: number  // 分钟
    cooldown: number      // 分钟
    urgency_threshold: number
  }

  // 知识库关联
  knowledge_docs: string[]

  // 元数据
  created_at: string
  updated_at: string
  user_id: string         // 所属用户
}
```

### 3.2 其他数据类型

```typescript
interface User {
  id: string
  name: string
  avatar?: string
  characters: string[]        // 关联的角色ID列表
  last_active?: string
  is_online: boolean
}

interface EmotionState {
  current_emotion: string
  intensity: number
  baseline: number
  volatility: number
  resilience: number
}

interface EmotionTrendPoint {
  date: string
  emotion: string
  intensity: number
}

interface WeChatConnection {
  alias: string
  wxid: string
  status: "disconnected" | "connecting" | "connected"
  online_since?: string
  qr_code?: string
}

interface PluginItem {
  name: string
  description: string
  enabled: boolean
  built_in: boolean
}

interface LogEntry {
  timestamp: string
  level: "DEBUG" | "INFO" | "WARN" | "ERROR"
  message: string
}
```

---

## 4. API 映射

### 4.1 接入微信

| 前端操作 | 后端 API |
|---------|---------|
| 生成二维码 | `POST /api/wechat/qrcode` |
| 查询连接状态 | `GET /api/wechat/connection/status` |
| 连接 | `POST /api/wechat/connect` |
| 断开 | `POST /api/wechat/disconnect` |
| 重连 | `POST /api/wechat/reconnect` |
| 列出已保存连接 | `GET /api/wechat/connections` |

### 4.2 用户管理

| 前端操作 | 后端 API |
|---------|---------|
| 用户列表 | `GET /api/users` |
| 用户详情 | `GET /api/users/{userId}` |
| 用户聊天历史 | `GET /api/chat/history?session_id={userId}` |
| 用户情绪 | `GET /api/users/{userId}/emotion` |
| 设置角色 | `POST /api/users/{userId}/role?card_id={cardId}` |
| 重置用户 | `POST /api/users/{userId}/reset` |
| 删除用户 | `DELETE /api/users/{userId}` |

### 4.3 角色管理

| 前端操作 | 后端 API |
|---------|---------|
| 角色列表 | `GET /api/characters?user_id={userId}` |
| 角色详情 | `GET /api/characters/{charId}` |
| 创建角色 | `POST /api/characters` |
| 更新角色 | `PUT /api/characters/{charId}` |
| 删除角色 | `DELETE /api/characters/{charId}` |
| 人设预览 | `GET /api/characters/{charId}/persona` |

### 4.4 角色设置 - 音色

| 前端操作 | 后端 API |
|---------|---------|
| 获取音色配置 | `GET /api/characters/{charId}/voice` |
| 绑定音色 | `POST /api/characters/{charId}/voice` |
| 解绑音色 | `DELETE /api/characters/{charId}/voice` |
| TTS 合成测试 | `POST /api/voice/synthesize` |
| TTS 状态 | `GET /api/voice/status` |

### 4.5 角色设置 - 主动消息

| 前端操作 | 后端 API |
|---------|---------|
| 获取主动消息配置 | `GET /api/proactive/config?character_id={charId}` |
| 更新配置 | `POST /api/proactive/config` |
| 今日统计 | `GET /api/proactive/stats?character_id={charId}` |

### 4.6 状态中心

| 前端操作 | 后端 API |
|---------|---------|
| 情绪状态 | `GET /api/emotion/state?character_id={charId}` |
| 情绪趋势 | `GET /api/emotion/trend?character_id={charId}&days=7` |
| 仪表盘统计 | `GET /api/stats/dashboard` |

### 4.7 系统设置

| 前端操作 | 后端 API |
|---------|---------|
| 获取配置 | `GET /api/config` |
| 保存配置 | `POST /api/config` |
| LLM 相关 | 通过配置 API |
| 插件列表 | `GET /api/plugins` |
| 插件开关 | `POST /api/plugins/{name}/toggle` |
| RAG 知识库 | `POST /api/rag/documents` + `GET /api/rag/stats` |
| 记忆事实 | `GET /api/memory/facts` |
| 运行日志 | `GET /api/logs/stream` (SSE) |

---

## 5. 共享组件清单

### 通用 UI 组件 (需实现)

| 组件 | 用途 | 属性 |
|------|------|------|
| `Slider` | 性格/参数滑条 | `label, value, min, max, step, tooltip` |
| `TagInput` | 标签编辑 (回车添加) | `tags[], placeholder, onChange` |
| `Toggle` | 开关 | `checked, onChange, disabled` |
| `Select` | 下拉选择 | `options[], value, onChange` |
| `FileUpload` | 文件上传 (拖拽+点击) | `accept, maxSize, multiple, onUpload` |
| `Sidebar` | 主导航 | `sections[], activePath` |
| `SubTabBar` | 子页签 | `tabs[], activeTab` |
| `Modal` | 弹窗 | `open, title, children, onClose` |
| `ConfirmDialog` | 确认对话框 | `message, onConfirm, onCancel` |
| `ProgressBar` | 进度条 | `progress, status` |
| `EmptyState` | 空状态提示 | `icon, message, action?` |
| `DangerButton` | 危险操作按钮 | `label, onClick, confirmMessage` |

### 布局组件

| 组件 | 说明 |
|------|------|
| `AppLayout` | 整体布局: 侧边栏 + 主内容区 |
| `UserWorkspace` | 用户工作区: 用户列表 + 右侧三Tab |
| `SystemSettingsLayout` | 系统设置布局: 左导航 + 右侧面板 |
| `TabContent` | 通用 Tab 内容容器 |

---

## 6. 状态管理

```
GlobalState
├── wechat: WeChatState           # 微信连接状态
├── users: UserState              # 用户列表 + 活跃用户
├── characters: CharacterState    # 角色列表 + 当前编辑角色
├── settings: SettingsState       # 系统设置
└── ui: UIState                   # UI 状态 (侧边栏展开/弹窗等)
```

### 关键交互流程

**用户选择角色 → 角色设置:**

1. 点击左侧用户列表中的用户
2. 右侧工作区加载用户详情
3. 默认显示 `🎭 创造角色` tab
4. 用户已有角色 → 左侧角色列表显示
5. 选择角色 → `⚙️ 角色设置` tab 加载该角色配置
6. 子 tab 切换 → URL 更新为 `/users/:userId/settings/:subtab`

**创建角色流程:**

1. 选择创建方式 (AI对话 / 微信克隆 / 导入文件)
2. 填写/生成角色信息
3. 随时可切换方式 (数据合并)
4. 保存 → `POST /api/characters` → 角色出现在列表中

---

## 7. 开发约定

### 技术栈 (待定)

```
React + TypeScript          # 基础框架
React Router                # 路由
CSS Modules / Tailwind      # 样式方案 (待参考网站定)
Axios / fetch               # HTTP 客户端
```

### 目录结构 (建议)

```
frontend/src/
├── components/
│   ├── layout/              # AppLayout, Sidebar, UserWorkspace
│   ├── shared/              # Slider, TagInput, Toggle, Select...
│   ├── wechat/              # 接入微信页面组件
│   ├── users/               # 用户列表组件
│   ├── create-role/         # 创造角色组件
│   ├── role-settings/       # 角色设置组件
│   │   ├── basic/           # 基础配置
│   │   ├── voice/           # 音色配置
│   │   ├── proactive/       # 主动消息
│   │   └── data/            # 数据管理
│   ├── status/              # 状态中心组件
│   └── settings/            # 系统设置组件
│       ├── general/
│       ├── llm/
│       ├── voice/
│       ├── security/
│       ├── extensions/
│       └── logs/
├── hooks/                   # 自定义 hooks
├── api/                     # API 调用层
├── types/                   # TypeScript 类型定义
├── stores/                  # 状态管理
├── pages/                   # 页面路由
├── App.tsx
└── main.tsx
```

### 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 组件 | PascalCase | `PersonalitySlider` |
| 文件 | kebab-case | `personality-slider.tsx` |
| 函数 | camelCase | `getUserList()` |
| 接口 | PascalCase | `PersonaCard` |
| 类型 | PascalCase | `ConnectionState` |
| CSS 类 | kebab-case | `slider-track` |

### 与后端对接规则

1. 所有 API 调用通过 `api/` 层封装，不直接在组件中写 fetch
2. API 路径前缀统一使用 `/shisi` (已确认后端 base path)
3. 认证方式: `X-API-Key` header (开发环境可关闭)
4. 错误处理: API 层统一拦截 HTTP 错误，组件层只处理业务异常

---

## 8. 后续排期

### Phase 1 - 骨架搭建
- [ ] 前端工程初始化 (Vite + React + Router)
- [ ] 三层侧边栏导航 + 路由
- [ ] 共享组件库 (Slider/TagInput/Toggle/Select)

### Phase 2 - 核心页面
- [ ] 接入微信页面
- [ ] 用户管理 (左侧列表 + 右侧工作区)
- [ ] 创造角色 (三种方式 + PersonaPreview)
- [ ] 角色设置 (基础配置 / 音色配置 / 主动消息 / 数据管理)

### Phase 3 - 扩展页面
- [ ] 状态中心
- [ ] 系统设置 6 个子页面
- [ ] 记忆列表弹窗

### Phase 4 - 集成与打磨
- [ ] 对接后端所有 API
- [ ] 视觉风格适配 (参考网站提供后)
- [ ] 响应式适配
- [ ] 错误处理和边界情况

---

> **待补充** (参考网站提供后):
> - 色彩体系 (主色/辅色/文字色/背景色)
> - 字体方案
> - 间距/圆角/阴影规范
> - 组件级视觉设计稿
> - 图标库选择

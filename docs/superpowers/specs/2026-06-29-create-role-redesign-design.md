# CreateRole 页面重设计 — 方案 A

> **📋 文档状态卡**（2026-08-26 治理标注 · 决策链见 `docs/DECISION_LEDGER.md`）
> - **层级**：L2 决策档案 | **结局**：⏸️ 定稿未实施 → 挂起池 **SP-2**
> - **演变**：brainstorm 三案 → 选定方案A → 本规格（114 行）。ADR-0013 废止后为本领域唯一现行规划
> - **现状**：2026-08-26 用户批复不实施；启动需重新逐项批准。正文保持原样。

## 方向
横向 Macaron 分段标签 + 右侧实时角色预览卡的两栏工作台布局。

## 设计目标
- 彻底移除 emoji（手机、📁、👥、💬 等）。
- 让三种创建方式（AI 对话 / 克隆好友 / 文件导入）更像“方法切换”而不是三张卡片。
- 把“实时预览”从隐藏状态提升到页面常驻，给用户即时反馈。
- 保持现有马卡龙配色与毛玻璃风格，不引入新的视觉系统。

## 布局

```
┌─────────────────────────────────────────────────────────────┐
│  创建角色                                                    │
│  选择一种方式来构建你的 AI 角色                               │
├─────────────────────────────────────────────────────────────┤
│  [内置角色预设]  可折叠                                       │
├─────────────────────────────────────────────────────────────┤
│  [ AI 对话 ] [ 克隆好友 ] [ 文件导入 ]   ← 横向分段控制器      │
├──────────────────────────────┬──────────────────────────────┤
│                              │                              │
│   交互区（2/3）               │   实时预览卡（1/3）           │
│                              │                              │
│   - AI 对话：聊天 + 输入框    │   - 角色名                   │
│   - 克隆：wxid 输入 + 进度    │   - 描述                     │
│   - 导入：拖拽上传 + JSON     │   - 核心锚点标签              │
│                              │   - 性格维度小图              │
│                              │                              │
├──────────────────────────────┴──────────────────────────────┤
│              [  创建角色  ]  ← btn-macaron 渐变按钮           │
└─────────────────────────────────────────────────────────────┘
```

## 组件规格

### 1. 页面标题
- 标题：`text-lg font-bold text-gray-800`，左侧加 `Sparkles` Lucide 图标（非 emoji）。
- 副标题：`text-sm text-gray-400`。

### 2. 内置角色预设
- 保持现有可折叠面板，但标题区改用 `glass-card`。
- 展开后使用 2 列网格卡片；选中态用 `border-accent-400 bg-accent-50/60`。
- 标签从灰色改为 `tag-pink / tag-blue / tag-green` 三色之一。

### 3. 方法选择器（Segmented Control）
- 位置：预设面板下方，贯穿内容区宽度。
- 样式：
  - 容器：`glass-card rounded-2xl p-1 flex`。
  - 每个选项：`flex-1 rounded-xl py-2.5 text-sm font-medium transition-all`。
  - 未选中：`text-gray-500 hover:text-gray-700 hover:bg-white/40`。
  - 选中：对应颜色渐变 + 白色文字。
    - AI 对话：`bg-gradient-to-r from-macaron-pink to-macaron-pink-deep text-white shadow-sm`。
    - 克隆好友：`bg-gradient-to-r from-macaron-blue to-macaron-blue-deep text-white shadow-sm`。
    - 文件导入：`bg-gradient-to-r from-macaron-green to-macaron-green-deep text-white shadow-sm`。
- 无图标、无 emoji，仅文字。

### 4. 主内容区
- 容器：`glass-card rounded-2xl p-0 overflow-hidden`。
- 内部两栏：`grid grid-cols-1 lg:grid-cols-[2fr_1fr]`。
- 左侧交互区：`p-5 border-b lg:border-b-0 lg:border-r border-white/40`。
- 右侧预览区：`bg-white/30 backdrop-blur-sm p-5`。

### 5. 左侧交互区

#### AI 对话
- 聊天消息气泡：
  - 用户：`bg-primary-500 text-white rounded-br-md`。
  - AI：`glass-card text-text-primary rounded-bl-md`。
- 空状态：去掉 `Bot` 图标或改用 `MessageSquare` Lucide 图标；文案保留。
- 输入框：`input-macaron` 样式；发送按钮用 `btn-macaron` 圆角方块。

#### 克隆好友
- 输入框：`input-macaron`。
- “开始克隆”按钮：`w-full btn-macaron rounded-xl`。
- 进度步骤：去掉 emoji，改用数字圆圈 + 文字；当前步骤高亮对应颜色（粉/蓝/绿）。

#### 文件导入
- 拖拽区：`border-2 border-dashed border-white/60 rounded-2xl`，hover 时 `border-macaron-pink bg-macaron-pink-light/30`。
- 图标：`FileUp` Lucide 图标，颜色 `text-macaron-pink-deep`。
- JSON 文本区：`input-macaron`。

### 6. 右侧实时预览卡
- 容器：`glass-card rounded-2xl p-4 sticky top-4`。
- 内容：
  - 角色名：`<h3 class="text-base font-semibold text-text-primary">`。
  - 描述：`text-xs text-text-secondary line-clamp-3`。
  - 核心锚点：横向 wrap，使用 `tag-pink / tag-blue / tag-green`。
  - 性格维度：5 个 mini 横向进度条（warmth / playfulness / independence / jealousy / stubbornness），用三色区分。
- 无内容时显示占位文案：“和十四聊聊，角色卡会在这里实时生长。”

### 7. 创建按钮
- 位置：主内容区下方，全宽。
- 样式：`btn-macaron w-full py-3 rounded-xl text-sm font-semibold`。
- 左侧 Lucide 图标：`Sparkles`。
- 禁用态：`disabled:opacity-40`。

## 交互
- 切换方法选择器时，左侧内容淡入淡出（`animate-fade-in`），右侧预览卡保持不变。
- 当 `characterBuilderStore` 的 `persona` 变化时，右侧预览卡即时更新。
- 点击内置预设时，自动填充 `persona` 并高亮对应预设卡片。

## 不做的范围
- 不改路由、不改 API 调用、不改状态管理逻辑。
- 不新增动画库，仅使用现有 CSS 动画类。
- 不替换全局主题或字体。

## 验收标准
- [ ] 页面无 emoji。
- [ ] 方法选择器为横向三段，当前选中项有渐变背景。
- [ ] 右侧常驻角色预览卡，内容随输入实时更新。
- [ ] 创建按钮使用 `btn-macaron` 渐变。
- [ ] 页面在 1280px、1024px、768px、375px 宽度下无水平滚动或明显错位。

# 唯一的你 沉浸式玻璃态视觉设计 v1.0

> **📋 文档状态卡**（2026-08-26 治理标注 · 决策链见 `docs/DECISION_LEDGER.md`）
> - **层级**：L2 决策档案 | **结局**：⏸️ 半程生效
> - **✅ 生效部分**：结构层（配色/毛玻璃/布局/字体）＝现行视觉基线，已写入 `docs/VISION.md` 终态
> - **❌ 未执行部分**：动效层五任务（gradient-shift/stagger 入场/视差/CountUp 等）零执行 → 挂起池 SP-5；配套三组件已于 06-03 死代码清理中删除
> - 正文保持原样，生效范围以上述划界为准。

> 状态: **设计定稿**  
> 基于 `frontend-design-framework-v1.md` 架构框架 + 用户确认的全量沉浸方向  
> 参考风格: Maze (mazehq.com) · Immersive Garden (immersive-g.com)

---

## 1. 视觉系统总览

### 1.1 核心设计语言：玻璃拟态 + 动态场

```
背景层: 动态渐变/粒子场 (CSS 动画, 微动不抢眼)
   ↓
表面层: backdrop-blur 磨砂玻璃卡片 (半透明, 有呼吸感)
   ↓
内容层: 文字/控件/数据 (清晰、可读、有层次)
   ↓
交互层: hover 微抬起 + 发光 + 鼠标视差
```

**关键词**: 透明、发光、浮动、微动、层次

### 1.2 与现有框架的关系

| 现有内容 | 变更方式 |
|---------|---------|
| 架构框架 `frontend-design-framework-v1.md` | **不变** — 路由/页面/组件/API 映射保持 |
| 主题色青紫 (cyan + purple) | **保留增强** — 增加发光和渐变变体 |
| 现有 index.css 动画 | **扩展** — 增加玻璃/沉浸相关动画 |
| 现有组件 Button/Modal/EmptyState | **保留并融合** — 统一加上玻璃风格 |
| Tailwind v4 主题 | **扩展** — 新增玻璃/发光/动态相关 token |

---

## 2. 色彩体系

### 2.1 主色 (保留增强)

```
--color-primary-400: #22d0df   (主色发光态)
--color-primary-500: #1ab8c7   (主色标准)
--color-primary-600: #1499a8   (主色深)

--color-accent-400: #e628ff    (点缀色发光)
--color-accent-500: #c91ae0    (点缀色标准)
--color-accent-600: #ab14bf    (点缀色深)
```

### 2.2 新增: 玻璃/表面色

```css
/* 玻璃表面 */
--color-glass: rgba(255, 255, 255, 0.75);
--color-glass-hover: rgba(255, 255, 255, 0.88);
--color-glass-active: rgba(255, 255, 255, 0.95);
--color-glass-border: rgba(255, 255, 255, 0.3);
--color-glass-border-hover: rgba(255, 255, 255, 0.5);

/* 发光效果 */
--color-glow-primary: rgba(34, 208, 223, 0.15);
--color-glow-accent: rgba(230, 40, 255, 0.12);
--color-glow-primary-strong: rgba(34, 208, 223, 0.3);
```

### 2.3 新增: 动态背景色

```css
/* 渐变背景基准 */
--color-bg-gradient-1: #e8f4f8;
--color-bg-gradient-2: #f0e8ff;
--color-bg-gradient-3: #e8f8f0;

/* 玻璃背景上文字 */
--color-text-on-glass: #1e293b;
--color-text-on-glass-secondary: #475569;
```

---

## 3. 排版系统

### 3.1 字体

保持现有 `Inter + Noto Sans SC + system-ui` 体系不变。

### 3.2 层级 (Maze 风格大留白)

| 元素 | Class | 大小 |
|------|-------|------|
| 页面标题 | `text-xl font-semibold` | 20px |
| 区块标题 | `text-sm font-semibold` | 14px |
| 卡片标题 | `text-sm font-medium` | 14px |
| 正文/标签 | `text-xs` | 12px |
| 统计大数 | `text-2xl font-bold` | 24px |
| 辅助文字 | `text-[11px]` | 11px |

### 3.3 间距

- 页面 padding: `p-6` (24px)
- 区块间距: `gap-6` (24px)
- 卡片内部: `p-4` (16px)
- 列表项间距: `gap-3` (12px)

---

## 4. 表面与层级系统

### 4.1 玻璃卡片 (核心组件)

```css
.glass-card {
  background: rgba(255, 255, 255, 0.75);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 16px;  /* 或 12px 视上下文 */
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04), 0 1px 2px rgba(0, 0, 0, 0.02);
}
```

### 4.2 层级堆叠

```
z-0:   动态背景 (渐变/粒子)
z-10:  侧边栏 (磨砂玻璃, bg-white/60)
z-20:  主内容区 (透明底, 内容直接在背景上)
        内部卡片使用 glass-card 在内容区上浮动
z-30:  浮动元素 (tooltip, dropdown)
z-40:  Modal 遮罩
z-50:  Modal 内容
z-60:  Toast 通知
```

### 4.3 侧边栏设计

- 宽度: 展开 200px / 收起 56px
- 表面: `bg-white/60 backdrop-blur-xl` (磨砂玻璃, 比卡片更透)
- 边框: 右侧 `border-r border-white/30`
- 选中项: `bg-primary-400/10 text-primary-600` 带左侧发光条 (`border-l-2 border-primary-400`)
- 组标签: `text-[11px] font-semibold text-gray-400 uppercase tracking-wider`

### 4.4 滚动条

现有薄滚动条 (4px) 保留, 轨道透明, 滑块使用 `rgba(255,255,255,0.3)`

---

## 5. 动画系统 (Immersive Garden 风格)

### 5.1 背景动画

**渐变粒子场** (纯 CSS, 不引入 Three.js):

```css
@keyframes gradient-shift {
  0%   { background-position: 0% 50%; }
  25%  { background-position: 100% 0%; }
  50%  { background-position: 100% 100%; }
  75%  { background-position: 0% 100%; }
  100% { background-position: 0% 50%; }
}

.bg-dynamic {
  background: linear-gradient(
    135deg,
    var(--color-bg-gradient-1),
    var(--color-bg-gradient-2),
    var(--color-bg-gradient-3),
    var(--color-bg-deep)
  );
  background-size: 400% 400%;
  animation: gradient-shift 20s ease infinite;
}
```

若用户感觉不够, 后续升级为 Three.js 粒子。

### 5.2 页面过渡

| 事件 | 动画 |
|------|------|
| 路由进入 | `fade-in + slide-up` (0.3s ease-out) |
| 路由离开 | `fade-out + scale-out` (0.2s ease-in) |
| 卡片列表入场 | `stagger` 逐个渐入 (间隔 60ms) |

### 5.3 交互动效

| 元素 | 默认 | Hover | Active |
|------|------|-------|--------|
| 玻璃卡片 | `shadow-sm` | `shadow-md` + `translateY(-2px)` | `translateY(0)` |
| 按钮主色 | `bg-primary-500` | `bg-primary-400` + glow | `scale(0.97)` |
| 侧边栏项 | 透明底 | `bg-white/40` | 选中发光条 |
| 滑条 | 细灰轨 | 青轨 + 手柄放大 | —— |
| 开关 (Toggle) | 灰底 | 青底 + 微弹 | —— |

### 5.4 数字滚动

统计数据使用 `CountUp` 组件: IntersectionObserver 触发, easeOutQuart 缓动, 2s 时长。

### 5.5 鼠标视差 (轻量)

浮动型卡片/元素跟随鼠标做 `transform: translate(dx, dy)` 微偏移 (max 5px), 使用 `requestAnimationFrame`。

---

## 6. 组件视觉规格

### 6.1 Slider (性格/参数滑条)

```
高度: 4px 轨 + 16px 手柄
颜色: 轨 bg-gray-200 / 活动轨 bg-primary-400
手柄: bg-white, shadow, hover 时外发光 primary-400/30
标签: 左侧名 text-xs + 右侧值 text-[11px]
```

### 6.2 TagInput (标签编辑)

```
内框: glass-card 缩略版, px-3 py-1.5
标签: bg-primary-100/50 text-primary-700 text-xs rounded-md
输入: 无边框, text-xs
删除: × 按钮 hover text-primary-500
```

### 6.3 Toggle (开关)

```
尺寸: w-9 h-5, 圆角-full
关闭: bg-gray-200/80, 圆形柄左
开启: bg-primary-400, 圆形柄右 (微弹 animation)
过渡: 0.2s ease
```

### 6.4 Select (下拉)

```
触发器: glass-card 缩略版, text-xs
下拉面板: glass-card, shadow-lg, mt-1
选项: px-3 py-1.5 text-xs, hover 亮色底
```

### 6.5 FileUpload (文件上传)

```
拖拽区: dashed border + glass-card 底, 圆角-xl
接受态: 边框变青 + bg-primary-50/30
文件列表: glass-card 缩略版, 文件名 + 大小 + 删除
```

### 6.6 ProgressBar (进度)

```
轨: h-1.5 bg-gray-200/50 rounded-full
填充: bg-gradient-to-r from-primary-400 to-accent-400
动效: 填充时 transition-all 0.5s ease
```

### 6.7 Modal (弹窗)

```
遮罩: bg-black/30 backdrop-blur-sm
面板: glass-card, shadow-2xl, animate-scale-in
标题: text-sm font-semibold
关闭: X 按钮右上
底部: 取消 + 确认按钮
```

### 6.8 DangerButton (危险操作)

```
底色: bg-red-500/10, border border-red-300/30
文字: text-red-500
Hover: bg-red-500/20
点击确认二次弹窗
```

---

## 7. 布局规范

### 7.1 页面内容布局

```
+-- sidebar (200px) --+-- 主内容区 (flex-1) ------------+
|                      |                                  |
|  📱 接入微信         |  [页面内容]                       |
|                      |                                  |
|  👥 用户管理         |  glass-card 包裹各区块            |
|    ├ 用户列表        |  大留白 + 清晰层级                |
|    └ 工作区          |                                  |
|                      |                                  |
|  ⚙️ 系统设置         |                                  |
|    ├ 系统配置        |                                  |
|    ├ LLM            |                                  |
|    ├ 语音            |                                  |
|    ├ 安全            |                                  |
|    ├ 扩展            |                                  |
|    └ 日志            |                                  |
|                      |                                  |
+----------------------+----------------------------------+
```

### 7.2 用户管理布局 (特殊)

```
+-- 用户列表 (w-72) --+-- 工作区 (flex-1) ----------------+
| SearchBar            |  [🎭 创造角色 | ⚙️ 设置 | 📊 状态] |
| UserItem[]           |  子 Tab 切换内容区域               |
|   ──────────────     |  glass-card 包裹                  |
| 用户名              |                                  |
| 角色名 · 最后活跃   |                                  |
+----------------------+----------------------------------+
```

### 7.3 系统设置布局 (特殊)

```
+-- 左导航 (w-44) --+-- 内容面板 (flex-1) ---------------+
| 系统配置            |  glass-card                        |
| LLM                |  表单/控件                          |
| 语音                |                                   |
| 安全                |                                   |
| 扩展                |                                   |
| 日志                |                                   |
+---------------------+-----------------------------------+
```

---

## 8. 新依赖

| 包 | 用途 | 理由 |
|----|------|------|
| `framer-motion` | 页面过渡 + 入场动画 | react-router-dom 无过渡能力, 原生 CSS 无法处理 stagger/exit |

--- 

## 9. 文件夹结构 (新增/修改)

```
frontend/src/
├── components/
│   ├── shared/              # [新] 共享 UI 组件
│   │   ├── Slider.tsx
│   │   ├── TagInput.tsx
│   │   ├── Toggle.tsx
│   │   ├── Select.tsx
│   │   ├── FileUpload.tsx
│   │   ├── Modal.tsx         # [改] 保持现有, 加玻璃风格
│   │   ├── ConfirmDialog.tsx # [新]
│   │   ├── ProgressBar.tsx   # [新]
│   │   ├── EmptyState.tsx    # [改] 保持现有
│   │   ├── DangerButton.tsx  # [新]
│   │   ├── SubTabBar.tsx     # [新]
│   │   └── index.ts
│   ├── wechat/              # [新]
│   │   └── ...
│   ├── users/               # [新]
│   │   └── ...
│   ├── create-role/         # [新]
│   │   └── ...
│   ├── role-settings/       # [新]
│   │   ├── basic/
│   │   ├── voice/
│   │   ├── proactive/
│   │   └── data/
│   ├── status/              # [新]
│   │   └── ...
│   └── settings/            # [新]
│       ├── general/
│       ├── llm/
│       ├── voice/
│       ├── security/
│       ├── extensions/
│       └── logs/
├── pages/                   # [改] App.tsx + 新页面文件
│   ├── WeChatPage.tsx       # [新]
│   ├── UsersPage.tsx        # [改]
│   ├── SettingsPage.tsx     # [新]
│   └── ... (旧页面保留不动)
├── hooks/
│   ├── useMousePosition.ts  # [新] 鼠标视差
│   └── useInView.ts         # [新] 滚动触发
├── types/
│   └── framework.ts         # [新] PersonaCard/User/Emotion 等
└── index.css                # [改] 增加玻璃/动态/沉浸动画
```

---

## 10. 实施排期

### Phase 0 — 基础 (先跑通)
- 装 framer-motion
- 扩展 index.css (玻璃/动态背景/新动画)
- 创建 types/framework.ts
- 创建所有 shared 组件 (Slider/TagInput/Toggle/Select...)
- 创建新 Sidebar

### Phase 1 — 导航
- 重写 App.tsx (新路由)
- 安装新 Sidebar
- 用户/系统设置布局

### Phase 2 — 接入微信 + 用户管理
- WeChatPage (二维码/连接/别名)
- UsersPage (用户列表 + 工作区三Tab)
- CreateRole 三种模式
- RoleSettings 四子Tab
- StatusCenter

### Phase 3 — 系统设置
- 6 个子页面
- 记忆列表弹窗

### Phase 4 — 沉浸升级
- 动态背景渐变粒子
- 页面过渡动画 (framer-motion)
- 卡片入场 stagger
- 鼠标视差
- 数字滚动

# 2026-09-19 前端审美升级 + 移动端优化 · 走查证据

- **范围**：16 条路由 × 桌面 1440×900 / 宽屏 1920×1080 / 移动 390×844（DPR2），真机 Playwright 截图（vite dev + 本地后端，审计账号已事后删除）。
- **before/**：修复前实拍；**after/**：修复后实拍。命名 `d_` 桌面 / `m_` 移动。

| # | 问题（修复前实拍） | 修复 | 对比图 |
|---|---|---|---|
| 1 | 角色卡高度参差、persona 长文本整段渲染成色块 | 卡等高 `h-full` + 锚点 14 字截断（title 悬浮全文）+ 活跃卡 `mt-auto` 底部状态条 | d_roles / m_roles |
| 2 | 状态中心单列堆叠、超宽留白（FE-0001） | 双列 `1fr+400px`（记忆体系固定右栏）、`max-w-6xl` 统一壳 | d_status / d_status_1920 |
| 3 | 微信接入页近乎空白 | 补「连接后怎么用」三步 + 「连接机制」三条（真实产品事实） | d_wechat |
| 4 | /psych 裸页无壳（游离于 ProtectedLayout 外） | 路由并入 ProtectedLayout，获得侧栏+面包屑+空态引导 | d_psych |
| 5 | 设置页双标题（面包屑+页内 h1 重复） | Breadcrumb 组名收敛 + 各页删重复标题 | d_set-logs / d_set-llm |
| 6 | LLM 配置 max-w-2xl 窄列悬空 | 全宽 + xl 双列分区 | d_set-llm |
| 7 | 工具仪表盘单列长条 | 双列网格 + 骨架屏加载 | d_set-tools |
| 8 | 创建角色整块渐变按钮、创建按钮全宽 | 中性分段控件（与角色设置同语言）+ 右对齐主按钮 | d_create-role |
| 9 | 移动端创建角色聊天区固定大高度中间空白、预览竖长空卡 | min-h 收敛到 lg 生效、空态预览压缩 | m_create-role |
| 10 | 移动端「创建用户」全宽橙色大按钮 | `self-start` | m_admin-users |
| 11 | 抽屉样式朴素 | 头部品牌头像 + 底部用户 chip（邮箱首字母头像 + 微信连接状态） | m_drawer_open |

**验证**：`tsc --noEmit` 0 错误；vitest 87/87（SettingsLLM 加载态断言随骨架屏改为 `getByRole('status')`）；`vite build` 成功；after 截图逐项目视复核。

**已知限制**：新账号无情感/成就数据时状态中心左列偏空——`EmotionInsightCard`/`AchievementsCard` 数据为空时按设计返回 null，属数据态非布局缺陷。

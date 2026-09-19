# 前端审美优化 · 第二轮验证报告（2026-09-19）

> 范围：第一轮（`2026-09-19-前端审美与移动端优化/`，11 项）未覆盖的页面增量走查。
> 基线：HEAD `40fd22a`（前端部分为第一轮 8 提交后的最新实况）；走查账号为一次性审计账号（用毕已删）。
> 验证链：`tsc --noEmit` 0 错误 · vitest 87/87 · `npm run build` 成功 · Playwright 真机截图（桌面 1440 + 移动 390×844 DPR2）。

## 问题清单与修复（7 项）

| # | 问题 | 修复 | 文件 | 举证 |
|---|------|------|------|------|
| 1 | `/roles/:id/storyline` 独立页无页面外壳——整屏只有一条「剧情线 ▼」折叠线，构图近乎空白 | StorylinePage 补统一外壳（max-w-3xl 表单页宽 + 角色头卡 + 玻璃卡包裹编辑器）；StorylineEditor 新增 `standalone` prop（去嵌入分隔线、默认展开），时间线 tab 嵌入行为不变 | `src/App.tsx`、`src/components/storyline/StorylineEditor.tsx` | 01 / 02（桌面+移动） |
| 2 | 消息 tab 滑杆行出现两套标签+两个数值（外层 `每日上限 … 8 条/天` + Slider 内部 `每日上限 … 8.00`） | Slider 新增 `showLabel`/`showValue`（默认 true 不影响其他调用方），消息 tab 三处 8 行滑杆关闭内部标签与数值 | `src/components/shared/Slider.tsx`、`src/components/admin/RoleSettingsTabs.tsx` | 03 / 04 |
| 3 | 角色设置 tab 按钮无焦点态处理（点击后残留焦点环） | 补 `focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400/50`；复截证实走查图中的蓝圈实为 CustomCursor 跟随鼠标停留，非焦点环——移开鼠标后无残留 | `src/pages/RoleSettings.tsx` | 03（before 有圈 / after2 无圈） |
| 4 | `/admin/providers` 面包屑显示英文裸段 `providers` | adminTabLabels 补 `providers: '供应商管理'` | `src/components/layout/Breadcrumb.tsx` | 05 |
| 5 | 安全页统计卡标签用 emoji（🚫 今日拦截 / 📊 总检测 / ⚠️ 拦截率），与全站 lucide 图标语言不一致 | 去除三个 emoji，保留纯文字标签 | `src/pages/SettingsSecurity.tsx` | 06 |
| 6 | 回复模式选择器仍用旧「渐变大块」语言，与第一轮确立的中性分段控件不一致 | 改为中性分段控件（bg-gray-100/60 轨道 + 白底浮起选中 + shadow-sm ring-1 ring-black/5），与 CreateRole 方法选择器同款 | `src/components/admin/RoleSettingsTabs.tsx` | 03 / 04 |
| 7 | 「保存频率配置」「立即发送一条主动消息」为全宽渐变按钮，与第一轮确立的右对齐紧凑主按钮不一致 | 两处改为右对齐紧凑主按钮（px-4 py-2 text-xs bg-primary-500 rounded-lg shadow-sm） | `src/components/admin/RoleSettingsTabs.tsx` | 03 |

## 边界确认

- StorylineEditor 的第二个消费方（角色设置 · 时间线 tab，RoleSettingsTabs.tsx）未传 `standalone`，嵌入折叠行为与分隔线保持原样。
- Slider 的其余消费方（角色设置基础 tab 情感滑杆，116/138 行）默认 `showLabel/showValue=true`，外观不变。
- 本轮未触碰后端与其他窗口文件；`git status` 全程仅含白名单文件。

## 已知限制

- 剧情线页在「启用剧情线」未勾选时仅显示检测/保存控件（内容量由数据决定，非构图问题）。
- 走查截图为 fullPage 模式，视口自适应区块会被纵向拉伸（第一轮已注明的同款伪影）。

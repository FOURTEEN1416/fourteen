# Orphan 页面调研报告

**日期**: 2026-06-02
**调研者**: QwenPaw explore 子智能体
**结论**: **0 个 orphan**（不是旧记忆中的 21）

## 关键反转

| 维度 | 2026-05-28 旧记忆 | 2026-06-02 实地扫描 |
|------|------------------|-------------------|
| pages/ 下 .tsx 数量 | 35 | **15** |
| 已挂路由 | 14 | **15** |
| Orphan 数 | 21 | **0** |

旧记忆"21 orphan"是三体导航首次扫描时的数据，2026-05-31 投产审计后已修复。

## 完整交叉对比

15 个 .tsx 文件 100% 全部被 App.tsx 引用：

| # | 页面文件 | 引用方式 | 路由 |
|---|---------|---------|------|
| 1 | UserWorkspace.tsx | 直接 import | /users/:userId |
| 2 | SystemSettingsLayout.tsx | 直接 import | /settings (布局) |
| 3 | CreateRole.tsx | 直接 import | /roles/create |
| 4 | RoleSettings.tsx | 直接 import | /settings[/:tab] |
| 5 | StatusCenter.tsx | 直接 import | /status |
| 6 | LoginPage.tsx | lazy | /login |
| 7 | NotFoundPage.tsx | lazy | * 兜底 |
| 8 | WeChatPage.tsx | lazy | /wechat |
| 9 | UsersPage.tsx | lazy | /users |
| 10 | AdminUsersPage.tsx | lazy | /admin/users (admin) |
| 11 | SettingsLLM.tsx | lazy | /settings/llm |
| 12 | SettingsVoice.tsx | lazy | /settings/voice |
| 13 | SettingsSecurity.tsx | lazy | /settings/security |
| 14 | ToolsDashboard.tsx | lazy | /settings/tools |
| 15 | SettingsLogs.tsx | lazy | /settings/logs |

## 处理建议

| 优先 | 行动 | 理由 |
|------|------|------|
| 🟢 P3 | 抽查 NotFoundPage (690B) | 极小，可能占位 |
| 🟢 P3 | 抽查 SystemSettingsLayout (337B) | 极小壳子 |
| 🟡 P2 | 更新 AGENTS.md "21 orphan" → "0 orphan" | 旧记忆已过期 |

## 数据来源

- 页面目录: `frontend/src/pages/` (15 个 .tsx)
- 路由注册: `frontend/src/App.tsx` (L1-166)
- 侧边栏: `frontend/src/components/layout/Sidebar.tsx`
- 移动端: `frontend/src/components/layout/MobileNav.tsx`

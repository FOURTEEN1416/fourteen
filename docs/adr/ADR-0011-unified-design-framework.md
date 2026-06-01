# ADR-0011: 统一设计框架：侧边栏+工作区

**状态**：已采纳 ✅
**日期**：2026-05-30

## 背景

前端页面存在三种布局模式（全宽页面、双栏页面、内嵌tab页面），设计语言不统一。

## 决策

1. 所有角色相关页面（CreateRole, RoleSettings, StatusCenter, StorylinePage）采用「左边栏 + 右工作区」布局
2. 侧边栏使用路由感知（useRouteLevel）自动切换三级导航（global/user/role）
3. 角色层级侧边栏固定结构：
   - 返回用户列表
   - 用户 #ID
   - 创建角色（Link to /users/:id/roles/create）
   - 角色功能（角色设置 · 状态中心 · 剧情线）
   - 人设卡（动态内容：创建时显示 builder 状态，已创建时显示角色信息）
   - 角色列表（可折叠）
4. 右侧工作区使用 max-w-2xl/max-w-3xl 居中布局，白色毛玻璃卡片

## 影响

- Sidebar.tsx：重构角色层级渲染，接入 characterBuilderStore
- CreateRole.tsx：移除右侧人设面板，人设卡移入侧边栏
- RoleSettings.tsx：对齐统一设计语言

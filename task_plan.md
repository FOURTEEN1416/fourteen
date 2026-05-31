# 前端重构 + 后端功能对齐审计

## 目标
按照 v10 设计完成前端重构，清理废弃页面/组件，完成后端功能对齐审计

## 阶段拆分

### Phase 1: 清理与骨架重构
- 删除废弃页面（CloneTrainPage, CharactersPage 等 15+ 个）
- 重构路由体系（App.tsx）：全局→用户→角色三级路由
- 重构 Sidebar 为三级动态导航

### Phase 2: 全局层（Global Level）
- 微信控制台（WeChatPage.tsx）重构：连接状态/统计卡片/最近消息
- 用户管理（UsersPage.tsx）重构：搜索/用户卡片列表
- 系统设置（SystemSettingsLayout.tsx）重构：6 tab（通用/LLM/语音/安全/扩展/日志）
- 各个 Settings*.tsx 页面内容充实

### Phase 3: 用户层（User Level）
- UserWorkspace.tsx 重构：用户信息卡片 + 角色卡片网格
- 用户层→角色层导航对接

### Phase 4: 角色层—创建角色
- CreateRole.tsx 重构：3子tab（创建表单/实时预览/快速设置）
- 3种创建方式（AI对话/克隆好友/文件导入）
- 克隆好友整合数据采集步骤（删除训练流水线）
- 实时预览面板始终可见

### Phase 5: 角色层—角色设置
- RoleSettings.tsx 重构：6子tab（基础/语音/消息/数据/表情包/剧情时间线）
- 数据管理tab：3卡片入口（记忆/知识库/收藏）
- 剧情时间线tab：使用现有 StorylineEditor 组件

### Phase 6: 角色层—状态中心
- StatusCenter.tsx 重构：4统计卡片 + 情绪分布 + 成就 + 趋势

### Phase 7: 清理收尾
- 检查并清理未使用的组件/hooks/store/types
- 验证编译零错误

### Phase 8: 前后端功能对齐审计
- 审计后端 API 是否覆盖前端所有功能
- 输出审计报告

## 决策
- 设计风格保持现有前端样式（glass-card 风格，Tailwind），不照搬 v10 的暗色 CSS
- 路由结构：`/users/:userId/roles/:roleId/create|settings/*|status`
- 角色层侧边栏在 Sidebar.tsx 中实现动态渲染（非独立 sidebar）
- 角色列表人设预览卡折叠效果在侧边栏中实现
- 所有页面从 useParams 获取 userId/roleId

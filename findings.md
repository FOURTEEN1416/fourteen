# 研究发现

## 现有前端结构
- **App.tsx**: 全局路由 + 静态 Sidebar + UserWorkspace（子路由）/ SystemSettingsLayout（子路由）
- **35 个页面文件**，14 个注册路由，21 个 orphan
- **Sidebar.tsx**: 静态全局侧边栏（微信/用户/设置分组）
- **UserWorkspace.tsx**: 有自己的二级侧边栏（角色创造/角色设置/语音克隆/状态中心）
- **风格**: glass-card（透明毛玻璃），Tailwind CSS，灰白为主色调

## v10 设计关键区别
- 三级导航：全局→用户→角色，Sidebar 需动态切换
- 角色层侧边栏：角色功能(创建/设置/状态)在上面，角色切换+人设预览卡在下面
- 训练流水线/数据标注/模型训练 → 删除
- 剧情时间线 → 角色设置第6子tab
- 角色设置：现有4 tab(basic/voice/proactive/data) → 扩展为6 tab(basic/voice/message/data/stickers/timeline)

## API 对接现状
- hooks/useQueries.ts: useUnifiedCharacters, useCreateCharacter 等
- api/characters.ts: MiMo TTS API (setMiMoEngine, switchMiMoVoice, cloneMiMoVoice...)
- 后端约146端点已部署

## 删除/合并清单 (从35页→约16页)
### 直接删除 (12个)
1. AdminPage.tsx - 不在 v10
2. ChannelsPage.tsx - 不在 v10
3. ChatPage.tsx - 不在 v10
4. CloneDataPage.tsx - 合并入克隆好友
5. DashboardPage.tsx - 不在 v10
6. ExtensionsPage.tsx - 不在 v10 (扩展管理在settings tab)
7. MonitorPage.tsx - 不在 v10
8. NotFoundPage.tsx - 保留(404错误页)
9. PsychProfilePage.tsx - 不在 v10
10. SafetyPage.tsx - 不在 v10
11. SettingsPage.tsx - 不在 v10
12. StatsPage.tsx - 不在 v10
13. TrainingPage.tsx - 删除

### 功能合并(删除原有，内容归入其他页)
14. CharactersPage.tsx → 用户层角色列表(替代方案)
15. CloneTrainPage.tsx → 合并入CreateRole 克隆好友
16. FavoritesPage.tsx → 角色设置→数据管理(收藏夹卡片)
17. KnowledgeBasePage.tsx → 角色设置→数据管理(知识库卡片)
18. MemoryPage.tsx → 角色设置→数据管理(记忆浏览器卡片)
19. PersonaEditorPage.tsx → 创建角色→实时预览
20. PersonaPage.tsx → 用户层
21. StickersPage.tsx → 角色设置→表情包tab

### 保留修改 (14页)
1. App.tsx - 完全重写路由
2. Sidebar.tsx - 重写动态三级导航
3. UserWorkspace.tsx - 重写为用户信息+角色网格
4. CreateRole.tsx - 重写3子tab
5. RoleSettings.tsx - 重写6子tab
6. StatusCenter.tsx - 重写数据卡片
7. WeChatPage.tsx - 重写内容
8. UsersPage.tsx - 重写搜索+卡片列表
9. SystemSettingsLayout.tsx - 调整tab标签
10. SettingsGeneral.tsx - 充实内容
11. SettingsLLM.tsx - 充实内容
12. SettingsVoice.tsx - 充实内容
13. SettingsSecurity.tsx - 重新实现非mock
14. SettingsExtensions.tsx - 充实内容
15. SettingsLogs.tsx - 充实内容

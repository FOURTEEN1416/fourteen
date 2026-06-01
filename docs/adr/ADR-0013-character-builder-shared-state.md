# ADR-0013: Character Builder 共享状态

**状态**：已采纳 ✅
**日期**：2026-05-30

## 背景

CreateRole 页面需要将实时人设数据传递给 Sidebar 的人设卡面板，但页面组件和侧边栏组件没有直接的 prop 传递通道。

## 决策

使用 Zustand store (characterBuilderStore) 作为跨组件通信桥梁。

- CreateRole 页面写入 (setPersona / resetPersona)
- Sidebar 的 RolePersonaCard 读取 (persona / hasContent)
- 进入创建页时自动 resetPersona()，离开时 cleanup

## 影响

- 新增 frontend/src/store/characterBuilderStore.ts
- Sidebar.tsx 导入 useCharacterBuilderStore
- CreateRole.tsx 导入 useCharacterBuilderStore，移除右侧 PersonaCard 面板

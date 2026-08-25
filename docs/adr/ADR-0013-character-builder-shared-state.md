# ADR-0013: Character Builder 共享状态

**状态**：~~已采纳 ✅~~ → **Superseded ❌**（2026-08-26 治理裁决 C2）
**日期**：2026-05-30

> **📋 废止说明**（详见 `docs/DECISION_LEDGER.md` 裁决 C2）
> 本决策的载体（Sidebar 人设卡面板）依附于 ADR-0011 三级侧边栏，随其废止连坐失效。
> 创建角色的现行规划以 `specs/2026-06-29-create-role-redesign-design.md` 方案A
> （右侧常驻预览卡）为准——该方案当前处于挂起池 SP-2，未获实施批准。
> 保留全文仅供追溯。

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

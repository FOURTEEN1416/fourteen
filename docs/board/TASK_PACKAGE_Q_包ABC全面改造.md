# TASK_PACKAGE Q — 包 A/B/C 一次性全面改造（窗口 abc）

> 新增：2026-09-20 用户指令  
> 交接真源：`docs/HANDOFF_2026-09-20_包ABC全面改造.md`  
> 研究：`docs/research/2026-09-20_{AI伴侣开源对照深研,全模块对标通读,第三轮深挖}*.md`

## 窗口

| 项 | 值 |
|----|-----|
| 窗口名 | **abc** |
| 分支 | `wt/abc` |
| worktree | `pwsh scripts/new_window_worktree.ps1 -Name abc` → `..\ai-girlfriend-abc` |
| 任务包 | **Q** |
| 状态 | **待开工**（主控仅交接） |

## 范围

- **A** 身份唯一 Owner + 系统句角色化 + 流式/非流式策略统一 + 上下文去重预算
- **B** 记忆 sync 轻写 + topics/near-dup merge + k(level) 注入 +（可选）跨会话尾巴
- **C** 工具 untrusted 信封 + 限额 + 失败禁称成功 + 注入位
- **不做 D**（Inspection API / 情绪大重构 / CI 大改）

## 硬约束

- 禁止主检出改功能代码；禁止 `git add .`
- 测试基线：pytest **1351 收集 / 1328 通过 / 4 跳过**（主检出 2026-09-20；新窗开工先复测）
- 端点 **215/181** 不变（除非有意加路由并同步文档）
- 五步制：用户已对 A+B+C 范围拍板；**实现细节若出现 ≥2 种合理解释仍须 question 排歧**
- 对照仓 `D:\Desktop\peer-projects\` 只读

## 白名单摘要

见 HANDOFF §6。测试建议新文件：`tests/test_abc_*.py`，避免与 W4 抢 `tests/**` 叙事。

## 收编

主控：`merge --no-ff wt/abc` → 回归门 → 文档 → `ssh swu-prod` 部署 → 回写 BOARD。

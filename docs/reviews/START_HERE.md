# AX-R 独立评审窗 · START_HERE

> 工作区：`D:\Desktop\ai-girlfriend-ax-review`（分支 `wt/ax-review`）
> **空白独立评审**：只评不施；先读任务书，不要先读实施窗聊天记录。

## 开工顺序

1. `docs/board/BOARD.md`
2. **`docs/HANDOFF_2026-09-21_AX独立评审窗.md`**（你的任务书 AX-R）
3. **`docs/HANDOFF_2026-09-21_智能体转型深研与架构.md`**（被评的 AX 实施交接）
4. `docs/research/2026-09-20_*.md` 与 `docs/reports/` 下小凌/情感/WrenWen 系列
5. 抽查代码（只读）：`orchestrator/` · `shisi/memory/` · `tools/builtin/`

## 产出（写在本 worktree）

```
docs/reviews/
  …_AX-R1_开窗基线.md
  …_AX-R2_研究实现级评审.md
  …_AX-R3_方案与选型评审.md
  …_AX-R4_现状差距审计.md
  …_AX-R5_实施过程评审.md   # agent-x 有产出时
  …_AX-R6_独立评审总报.md   # Go / Go with conditions / No-Go
```

## 硬规则

- 禁止实施功能代码；禁止改生产热路径
- 证据必须 `文件:行号` / 测试名 / 日志
- 研究评审按实现级：调用链 / 数据结构 / 算法 / 失败降级
- 核对小凌 EventLedger 等主轴是否真在生产（预期多为未落地）
- `git add` 白名单（docs/reviews 等）；禁 `git add .`
- 与 `wt/agent-x` 隔离；BOARD 只追加自己的条目

## 完整开窗提示词

以主仓 `docs/HANDOFF_2026-09-21_AX独立评审窗.md` **§4** 为准（与本窗已同步该文件）。

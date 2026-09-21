# HANDOFF — AX 独立评审窗（空白窗 · 只评不施）

> **文档类型**：跨窗口独立评审任务书  
> **日期**：2026-09-21  
> **发起**：用户 —「构建对窗 AX 独立评审的窗口和任务，我需要一个空白独立窗口进行评审」  
> **窗口名**：`ax-review`  
> **分支**：`wt/ax-review`  
> **worktree**：`D:\Desktop\ai-girlfriend-ax-review`  
> **性质**：**独立评审** — 与实施窗 `agent-x`（AX 实施）**并行且隔离**；本窗**只读主仓材料 + 产出评审报告**，**禁止实施功能代码**  
> **被评审对象**：AX 交接书 + 既有 research/reports + 当前 main 代码实况 +（若已产出）AX 实施窗交付物  

---

## 0. 为什么要独立评审窗

| 背景 | 含义 |
|------|------|
| 用户判定上一轮对标改造**效果很差** | 实施窗不能既当运动员又当裁判 |
| AX 是**重大决策转型** | 选型/架构须有独立方用同一套证据打分 |
| 小凌学习**未落到实处** | 评审必须核对「研究结论 ↔ 代码现状 ↔ 方案承诺」三者是否闭合 |
| 需要**空白独立窗口** | 新 worktree 开工，不继承实施窗上下文污染 |

**独立性纪律**
1. 本窗工作区 = `D:\Desktop\ai-girlfriend-ax-review\`（分支 `wt/ax-review`）
2. **禁止**在本窗实施 AX 功能、改生产热路径、替实施窗写方案正文
3. **允许**只读主仓 `docs/`、`shisi/`、`orchestrator/` 等；产出仅限 `docs/reviews/`（本窗分支）
4. 评审结论用**证据**（文件:行号 / 日志 / 测试名），禁止空泛表扬
5. 与实施窗 `agent-x` **不得共写同一文件**；看板各记各的

---

## 1. 被评审材料清单（实施前 / 实施中均可评）

### 1.1 必读（主仓，只读）

| 材料 | 路径 |
|------|------|
| AX 实施交接书 | `docs/HANDOFF_2026-09-21_智能体转型深研与架构.md` |
| 上一轮对标深研 | `docs/research/2026-09-20_AI伴侣开源对照深研与本项目困境解法.md` |
| 全模块对标通读 | `docs/research/2026-09-20_全模块对标通读-上下文提示词工具知识记忆.md` |
| 第三轮深挖 | `docs/research/2026-09-20_第三轮深挖-隐性系统问题与说不清的质量病.md` |
| 小凌经历因果研究 | `docs/reports/2026-09-19_经历因果升级机制研究.md` |
| 情感真源收敛审查 | `docs/reports/2026-09-19_情感真源收敛审查.md` |
| WrenWen 精读 | `docs/reports/2026-09-19_WrenWen伴侣架构精读.md` |
| 小凌一手素材深掘 | `docs/reports/2026-09-20_小凌一手素材深掘.md` |
| 宪法 / 看板 | `AGENTS.md` · `docs/board/BOARD.md` |

### 1.2 代码实况抽查（主仓，只读）

- `orchestrator/optimized_orchestrator.py`（上下文组装 / 工具 / profile_sync）
- `shisi/memory/legacy/{memory_pipeline,user_profile,structured_memory}.py`
- `tools/builtin/{profile_agent_tools,reminder_tool}.py`
- `shisi/affinity/enhancer.py` · `proactive/{ase_hub,scheduler}.py`
- 是否存在 EventLedger / 识海门控 / 心光注入等小凌主轴（预期：**无**）

### 1.3 实施窗交付物（若 `wt/agent-x` 已有产出）

- `docs/plans/*智能体*` · `docs/adr/*` · `docs/research/*`（AX 新笔记）
- worktree 路径：`D:\Desktop\ai-girlfriend-agent-x\`（**只读**，不改）

---

## 2. 评审任务包 AX-R（分阶段）

### R1 独立开窗与基线

- [ ] 建 worktree `ax-review`（见 §3）
- [ ] 记录主仓 HEAD、agent-x 是否存在及其 HEAD
- [ ] 静默跑一次：主仓 collect-only 测试数（角色卡 41 张在位时基线约 **1472+**，以当时实测为准写入报告）

### R2 研究质量评审（对标是否「实现级」）

对每份 research/对小凌报告，打分并写证据：

| 维度 | 通过标准 | 常见不合格 |
|------|----------|------------|
| 调用链 | 有 A→B→C 与参数/返回 | 只列模块名 |
| 数据结构 | 字段级摘录 | 「有个 JSON」 |
| 算法/规则 | 可复述伪代码 | 只有函数名 |
| 失败降级 | 写明超时/解析失败路径 | 只写 happy path |
| 可迁移 | 映射到本仓文件 | 空泛「可借鉴」 |

**输出**：`docs/reviews/2026-09-xx_AX-R2_研究实现级评审.md`

### R3 失败模式与方案评审（AX 交接书本身）

对照交接书 §1 F1–F6 与 §3 任务包：

- [ ] 方案是否**逐条规避** F1–F6？缺哪条？
- [ ] 技术选型是否有**源码证据**（或明确标「待 agent-x 补证」）？
- [ ] EventLedger / 小凌 P0–P4 是否有**可回滚迁移**与兼容策略？
- [ ] 验收是否包含：**因果回放 / 双用户串台 / 画像更正 / 工具真执行**（不止 pytest）？
- [ ] 是否误把「研究报告」当成「已落地」？

**输出**：`docs/reviews/2026-09-xx_AX-R3_方案与选型评审.md`  
**结论档**：`Go` / `Go with conditions` / `No-Go` + 条件清单

### R4 代码现状 vs 承诺差距审计

| 检查项 | 方法 |
|--------|------|
| 小凌主轴是否在生产 | 全仓搜 EventLedger / 识海 / 心光 / 内驱轴 |
| 正则画像是否仍热路径 | 读 `optimized_orchestrator.py` 是否调用 apply_user_utterance |
| 智能体工具是否已注册 | `system.yaml` + `_init_mixin` + 工具类 |
| 记忆隔离 / 提醒权限 | 抽 `user_key_from_session` / `ReminderTool.permission_level` |

**输出**：`docs/reviews/2026-09-xx_AX-R4_现状差距审计.md`（表格：承诺 vs 代码 vs 判定）

### R5 实施窗过程评审（对 `wt/agent-x`，若已开工）

- [ ] 学习笔记是否达到 R2 标准（抽查 2–3 份，追文件:行号是否真实存在）
- [ ] 方案是否可评审（架构图/选型/迁移/验收齐全）
- [ ] 是否违规在主检出改热路径、是否 `git add .`
- [ ] 突变验红/探针是否设计而非空话

**输出**：`docs/reviews/2026-09-xx_AX-R5_实施过程评审.md`

### R6 总评与用户呈报

汇总 R2–R5，给用户一页纸：

1. AX 材料/方案成熟度（1–5 + 理由）  
2. **Go / No-Go** 与阻塞项  
3. 若 Go：建议实施批次顺序与每批「必须看到的生产证据」  
4. 若 No-Go：退回 agent-x 的具体修改点（条目化）

**输出**：`docs/reviews/2026-09-xx_AX-R6_独立评审总报.md`

---

## 3. 开窗命令（主控或用户在主仓执行）

```powershell
cd D:\Desktop\ai-girlfriend
pwsh scripts/new_window_worktree.ps1 -Name ax-review
# 产物：D:\Desktop\ai-girlfriend-ax-review  分支 wt/ax-review
# data/ 与 frontend/node_modules 为 Junction 回主仓（评审只读，一般不动它们）
```

评审窗内读写**只在** `D:\Desktop\ai-girlfriend-ax-review\`；报告写 `docs/reviews/` 后**在评审分支提交**，主控再决定是否收编。

---

## 4. 空白独立评审窗 · 开窗提示词（复制）

```text
【AX-R · 独立评审窗 · 只评不施 · 空白开工】

工作区：D:\Desktop\ai-girlfriend-ax-review
分支：wt/ax-review
宪法：AGENTS.md（主仓）+ docs/board/BOARD.md
本任务书（全文精读）：docs/HANDOFF_2026-09-21_AX独立评审窗.md
被评 AX 实施交接：docs/HANDOFF_2026-09-21_智能体转型深研与架构.md

角色：你是 AX 的独立评审方，不是实施方。
目标：用代码与文档证据，判断「智能体转型」材料/方案是否达到
     重大决策级质量，给出 Go / Go with conditions / No-Go。

硬规则：
1) 空白开工：先读任务书与 AX 交接书，不要先读实施窗聊天记录
2) 只读主仓与 agent-x 交付物；本窗只写 docs/reviews/**
3) 禁止实施功能代码、禁止改生产热路径、禁止替 agent-x 写方案正文
4) 评审必须给文件:行号 / 测试名 / 日志证据；禁止空泛结论
5) 研究评审按「实现级」标准：调用链、数据结构、算法、失败降级
6) 明确核对：小凌主轴是否真的落地（预期多为未落地）
7) 与 wt/agent-x 隔离：不共写文件；结论写入本窗 docs/reviews/

交付（按 AX-R 阶段）：
[ ] R1 开窗基线记录
[ ] R2 研究实现级评审
[ ] R3 方案与选型评审（含 F1–F6 规避检查）
[ ] R4 现状 vs 承诺差距审计
[ ] R5 实施过程评审（若 agent-x 有产出）
[ ] R6 独立评审总报（Go/No-Go + 条件）
[ ] 窗内：git add 仅 docs/reviews/** 与本窗必要笔记 → commit on wt/ax-review
[ ] 回写 BOARD 追加区（仅追加，不改他人条目）

禁止：
- git add .
- 在主检出 D:\Desktop\ai-girlfriend 直接改代码
- 把评审写成实施 PR
```

---

## 5. 与 AX 实施窗的边界

| | AX 实施窗 `agent-x` | AX 评审窗 `ax-review` |
|--|---------------------|------------------------|
| 任务 | 调研/克隆/学实现/写方案/可选骨架 | **独立评审**材料与方案 |
| 产出 | research 笔记、plans、ADR、骨架 | `docs/reviews/*` 评审报告 |
| 代码 | worktree 内可实施（按批次） | **不实施** |
| 看板 | 登记 AX 实施 | 登记 AX-R 评审 |
| 收编 | 主控回归后 merge | 主控决定是否把 reviews 收进 main |

**推荐顺序**：  
AX-R **R1–R4** 可在 agent-x 产出方案**之前/之中**做「材料与现状」评审；  
**R5–R6** 在 agent-x 提交可评审方案后做「过程与总评」。

---

## 6. 完成定义（评审窗）

1. R2–R6 报告齐全，含证据与 Go/No-Go；  
2. `wt/ax-review` 上仅 docs/reviews（+可选笔记）提交；  
3. BOARD 追加区有 AX-R 条目；  
4. 主控收到总报后，再决定 AX 是否放行进入实施批次。

---

**主控**：`D:\Desktop\ai-girlfriend` 协调与收编  
**评审**：`wt/ax-review` 空白独立窗  
**实施**：`wt/agent-x`（另开，见 AX 交接书）

# 🔴 事故记录 · 主仓 `D:\Desktop\ai-girlfriend` 工作树 + `.git` 元数据被清空

| 项 | 值 |
|----|----|
| 发现时间 | 2026-09-14 23:19–23:30（GMT+8） |
| 发现窗口 | **W4 验证窗口**（歆歆） |
| 影响对象 | **主检出** `D:\Desktop\ai-girlfriend`（`main` 分支，多窗口协作的协调仓） |
| 当前状态 | 🔴 **未解决** — 删除已停止，但数据未恢复，**需默默裁决恢复策略** |
| 根因 | **未确定**（详见 §5；时间线与本窗口 worktree 操作重合，但 git 语义上 worktree 操作不可能删除主工作树） |
| 本窗口是否执行过删除 | **否**（全程只读 + 建窗；未对主仓做任何写/删） |

---

## 1. 结论（先行）

**主仓 `D:\Desktop\ai-girlfriend` 的整个工作树（源码）与 `.git` 元数据（HEAD/config/objects/refs/index）在本次会话进行中被清空。**
清空后该目录**仅剩 2 项**：一个已被掏空的 `.git\`（内含唯一空目录 `worktrees\ai-girlfriend-verify\`）与参赛文档目录 `大创赛报名以及后期发展\`。

**全机器上该代码库的唯一幸存副本 = 本窗口的 worktree `D:\Desktop\ai-girlfriend-verify`**（592 文件 / 4.2 MB，检出点 `e9a063b`）。已即时做保全备份（见 §4）。

> ⚠️ 这不是"W4 验证"的普通产出，而是**主仓数据丢失事故**。W4 三项任务因此全部前置失效（见 §3）。

---

## 2. 取证（四通道独立确认，非采样）

| 通道 | 观测结果 |
|------|---------|
| Bash `ls` | `D:\Desktop\ai-girlfriend` → 仅 `.git`、`大创赛报名以及后期发展` |
| PowerShell `Get-ChildItem -Force` | 同上；`main.py / AGENTS.md / LOG.md / pyproject.toml / tests\conftest.py / wechat_direct\wechat_connector.py / .venv\Scripts\python.exe / .env / .gitignore` **全部 `Test-Path=False`** |
| Read 工具 | `D:\Desktop\ai-girlfriend\main.py` → **File does not exist**；`D:\Desktop\ai-girlfriend\.git\HEAD`、`.git\config` → **不存在** |
| Glob 工具 | `D:/Desktop/ai-girlfriend/*` → 仅返回 `大创赛报名以及后期发展\` 子树 |

**`.git` 目录实测内容**（`Get-ChildItem -Recurse -Depth 2 -Force`）：
```
D:\Desktop\ai-girlfriend\.git\worktrees
D:\Desktop\ai-girlfriend\.git\worktrees\ai-girlfriend-verify   ← 空目录
D:\Desktop\ai-girlfriend\.git\_probe.txt                        ← 本窗口写入探针（可读写，证明目录真实）
```
`.git\HEAD` / `.git\config` / `.git\objects` / `.git\refs` / `.git\index` **均不存在** → 本地 git 历史元数据丢失。

**对照实验（证明非全局事件）**：同盘其他仓库完好 —

| 仓库 | HEAD | config | objects |
|------|------|--------|---------|
| `D:\Desktop\21day` | ✅ | ✅ | ✅ |
| `D:\Desktop\知识库搭建` | ✅ | ✅ | ✅ |
| `D:\Desktop\数模竞赛` | ✅ | ✅ | ✅ |
| `D:\Desktop\日常活动` | ✅ | ✅ | ✅ |
| **`D:\Desktop\ai-girlfriend`** | ❌ | ❌ | ❌ |

---

## 3. 时间线（本窗口可确证的观测点）

| 时刻 | 事件 |
|------|------|
| ~23:13 | 本窗口开工。首次 `ls D:\Desktop\ai-girlfriend\` → **完整 46 项**（`main.py`/`.venv`/`tests/`/`.git/` 等齐全）；`git worktree list` = 仅主检出；分支 `main`；`git status` 干净 |
| 23:14 | 读 `AGENTS.md` / `BOARD.md` / `TASK_PACKAGES.md` 完毕；确认 `D:\Desktop\ai-girlfriend-verify` **不存在**、`wt/verify` 分支**不存在** |
| 23:16 | 建窗：`git worktree add --detach D:/Desktop/ai-girlfriend-verify HEAD` 成功（582 文件检出，`HEAD is now at e9a063b`） |
| 23:17 | worktree 内 `git checkout -b wt/verify` 成功；主仓 `.git\refs\heads\` 仍可见 `arch/`、`main`（**此时元数据尚在**） |
| ~23:19 | worktree 内 `git checkout -B wt/verify e9a063b` 后 git 报 `fatal: not a git repository: (NULL)`；此后 `.git\worktrees\ai-girlfriend-verify\` 变**空**，主仓 `.git` 仅剩 `worktrees` |
| ~23:2x | 主仓工作树亦已空（仅剩 `.git` + `大创赛报名以及后期发展`）；**两次间隔 3 秒采样 count 恒为 2 → 删除已停止，非持续进程** |
| 23:27 | 完成保全备份（见 §4） |

---

## 4. 幸存资产与已做的保全

| 资产 | 状态 | 位置 |
|------|------|------|
| **代码库唯一幸存副本** | ✅ 完整（592 文件 / 4.2 MB，检出点 `e9a063b`） | `D:\Desktop\ai-girlfriend-verify`（本窗口 worktree） |
| **保全备份** | ✅ 已复制（593 文件） | `D:\Desktop\ai-girlfriend-verify-backup-2327` |
| 参赛文档目录 | ✅ 完好（未被 git 跟踪，故未受影响） | `D:\Desktop\ai-girlfriend\大创赛报名以及后期发展` |
| 其余桌面仓库 | ✅ 全部完好 | 见 §2 对照表 |
| `D:\Desktop\ai-girlfriend.rar`（22:31 生成的 643 MB 备份包） | ❌ **已从桌面消失**（首帧 `ls` 可见，23:2x 起不可见） | — |
| `D:\Desktop\ai-girlfriend-code`（W3 worktree） | ❌ **不存在**（W3 尚未建窗） | — |
| 回收站 / 隔离区 | ❌ 无本仓内容（`%TEMP%\codebuddy-safe-delete*` 仅 22 个小文件，非本仓） | — |
| Windows 卷影副本 | ❌ 不可用（`vssadmin` 不可调用） | — |

**本窗口未做**：任何针对主仓的删除、移动、恢复、git 写操作。仅做了两件**只读/加性**动作：① 建 worktree（在建窗流程内）；② 将唯一幸存副本复制为备份（加性，不覆盖任何东西）。

---

## 5. 根因分析（诚实标注：未确定）

**证据指向的矛盾**：
- 时间上，主仓被掏空（~23:19）与本窗口 worktree 操作（23:16–23:19）**高度重合**；
- 但 git 语义上，`git worktree add/checkout/remove` **不可能删除主工作树文件或 `.git\objects`** —— 主检出从不被 worktree 操作触碰。

**已排除**：
- ❌ 非全局事件（其他 4 仓完好）；
- ❌ 非 `git clean -fdx`（该命令会连 gitignore 的 `大创赛报名以及后期发展\` 一起删，但它幸存）；
- ❌ 非回收站/隔离区可恢复（两处均无内容）；
- ❌ 非本窗口的删除命令（本窗口未对主仓执行任何删除）。

**未排除的可能**（需默默确认）：
1. 有**外部进程/脚本**在本会话期间对 `D:\Desktop\ai-girlfriend` 执行了 `rm -rf` / `robocopy /MIR`（空源镜像）/ 归档提取覆盖 / 迁移清理；
2. 本机 git 2.55.0.windows.3 的 **worktree 实现缺陷**：建窗时反复出现 `fatal: invalid reference: wt/verify`（`-b` 新建分支失败，改用 `--detach` 才成功），随后 worktree 管理目录变空并报 `not a git repository: (NULL)` —— 说明该版本 worktree 在本机存在**状态管理异常**；
3. 用户/主控窗口**并行**对主仓做了操作。

> ⚠️ 若为 (1) 或 (3)，主仓数据可能另有来源可恢复；**在默默确认前，本窗口不擅自执行任何恢复动作**。

---

## 6. 对 W4 任务包（包 T）的影响

| 阶段一任务 | 状态 | 原因 |
|-----------|------|------|
| ① 全量 pytest 拿真实通过率 | 🔴 **阻塞** | 指定解释器 `D:\Desktop\ai-girlfriend\.venv\Scripts\python.exe` **已随主仓被删**，无可用环境 |
| ② 为 `type:3/34` 收包写测试 | 🟡 **可写不可验** | 实现源码在幸存副本中可读，但**无环境可运行** → 按"无新鲜验证不声明完成"原则暂缓落笔 |
| ③ 验证报告写 `docs/verification/` | ✅ **已完成** | 即本文件（写于幸存副本内） |
| 阶段二 · W3 收编后独立复核 | 🔴 **阻塞** | W3 worktree 未建、主仓 git 已毁，无 diff 可复核 |

**通过率**：**无法获取**（环境缺失，非测试失败）。
**失败清单**：N/A。
**复核结论**：N/A（W3 未收编）。
**置信度**：对"主仓已被清空"这一事实——**高**（四通道独立确认 + 对照实验）；对根因——**低**（未确定）。

---

## 7. 恢复选项（待默默裁决，本窗口不擅自执行）

| 选项 | 做法 | 代价 / 风险 |
|------|------|------------|
| **A. 从 GitHub 重建**（推荐） | 主仓网络可达（实测 `https://github.com` HTTP 200）；`git clone https://github.com/FOURTEEN1416/fourteen.git` 到新目录 → 比对幸存副本 → 补回未推送提交 | 需本机 git 直连（沙箱 shell 走 127.0.0.1 代理被拦，需在默默自己的终端执行）；**未推送的本地提交会丢**（`BOARD.md` 记载 `d381063` 待 push、`e9a063b` 为其后） |
| **B. 用幸存副本原地重建** | 以 `D:\Desktop\ai-girlfriend-verify`（或备份 `-2327`）为源，`git init` + 首次提交 + `remote add` + 关联远端 | **历史全丢**（仅剩 `e9a063b` 内容）；gitignore 资产（`.env` / `data\` / `.venv` / `node_modules`）**不在副本内**，需另行找回 |
| **C. 先查是否"被移走"** | 全盘搜索是否有另一份完整拷贝（本窗口在 `D:\Desktop` 深度 4 内未发现第二份 `wechat_connector.py`） | 若确认被移走，直接指回即可，零代价 |
| **D. 文件级恢复工具** | Recuva / Windows File Recovery 扫 `D:` | 时间敏感（新写入会覆盖），且卷影副本不可用 |

**关键待确认**：`.env`（含 API 密钥）、`data\`（users.db / 角色卡 / 知识索引）、`.venv`、`frontend\node_modules` **是否另有备份**——这几项**不在**幸存副本内。

---

## 8. 建议的下一步（等默默一句话）

1. **先确认**：主仓是否是你/其他窗口主动清理或迁移的？（若是，请给方向，本窗口立即停止告警）
2. **再定恢复**：走 A / B / C / D 哪条？
3. **恢复后**：本窗口立即补做包 T 阶段一（pytest 真实通过率 + `type:3/34` 测试）——**环境一回来就能跑**。

---

*记录者：W4 验证窗口（歆歆）· 2026-09-14 · 本文件写于唯一幸存副本内，随备份一并保全。*

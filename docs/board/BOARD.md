# BOARD — 跨窗口看板（宪法 §8 载体）

> **唯一跨窗信息通道**。开窗先读本文件 → 再读 `TASK_PACKAGES.md` → 再动手。
> 追加用 `scripts/window_board.ps1 -Append`，查看用 `-Tail`。**不要手工重排历史条目**（保留审计线索）。
> 跨仓信息也可双写 memory MCP（`agent_id=shared`）。

---

## 使用规则（宪法 §8 摘要）

1. **新窗口一律 worktree 开工**：`pwsh scripts/new_window_worktree.ps1 -Name <窗口名>`；读写只在自己那份检出内；分支 `wt/<窗口名>`
2. **主检出只做协调合并**：`git merge --no-ff wt/<名>`，收编前跑主检出回归门（pytest / vitest / E2E / 对齐四项）
3. **白名单提交**：只 `git add` 任务包白名单内文件，**禁用 `git add .`**
4. **`data/` 与 `frontend/node_modules` 为 Junction 共享**：生成物必须带窗口前缀或唯一 seed
5. **禁止多窗口同时改同一 owner 或同一真源文档**

---

## 当前窗口登记

| 窗口 | 分支 | worktree 路径 | 任务包 | 状态 | 开工时间 | 备注 |
|------|------|--------------|--------|------|---------|------|
| 主检出 | `main` | `D:\Desktop\ai-girlfriend` | 协调 + 阶段真源维护（包 M） | 进行中 | — | 主控由歆歆担任；真源文档单写 |
| W1 论文 | 无 | `D:\Desktop\论文-唯一的你十四`（外部） | **包 P** | 待开工 | — | 结构功能主义框架；目标刊《心理学进展》 |
| W2 软著 | 无 | `D:\Desktop\软著申请-唯一的你十四`（外部） | **包 C** | 待开工 | — | 模式 A（user_data 504 文件已就位） |
| W3 代码 | `wt/code` | `..\ai-girlfriend-code` | **包 V** | 待开工 | — | 多模态缺口；**须先过商讨协议五步制** |
| W4 验证 | `wt/verify` | `..\ai-girlfriend-verify` | **包 T** | 待开工 | — | 只碰 `tests/**`；反对采样验证 |

---

## 追加区（按时间倒序，新的在上）

### 2026-09-14 23:0x · 主检出 · 多窗口协同启动（4 窗口任务分发）

- **用户裁决**：① 「文档都不进去服务器，服务器能消费的再入」→ 宪法升 **v1.6**，新增**服务器准入原则**（服务器只放 A 档；文档类不上服务器但**必须 `commit→push` 到 GitHub 备份**）；② `AGENTS.md` 解除 gitignore、**纳入版本控制**（此前只存本地、无任何备份）；③ 开启多窗口协同——主控=歆歆，4 窗口分工（论文 / 软著 / 代码修改 / 验证测试）
- **提交**：`31262d8`（.gitignore + LOG 四十四，**已 push**）｜ `d381063`（AGENTS.md v1.6 + .gitignore，**待 push**，GitHub 直连抖动）
- **任务包**：`TASK_PACKAGES.md` 重写为 **v2（生效）**——包 P 论文 / 包 C 软著 / 包 V 多模态缺口 / 包 T 验证测试
- **主控亲自核验的缺口事实**（非照抄交接文档）：`voice/` 5 文件全 TTS；`multimodal/multimodal_processor.py` 内含 `ASRHandler`/`VisionHandler`/`EmojiResponder`；**ASR 已实现已接线**（`wechat_connector.py:899-922`），仅 `config/system.yaml:100-102` `enabled:false` + `api_base:""` → **纯配置**；**图片 `image_data` 全仓无消费者**（`_handle_message:769-782` 已解析但 `VisionHandler` 未接上）→ **1 处接线**；`MultimodalProcessor` 已挂 `orchestrator/_init_mixin.py:493`
- **宝库定位**：`D:\Desktop\数模竞赛` = **Academic Agent Toolkit（科研工具箱）**——263 技能 / 303 能力目录 / 质量门禁引擎（`engine/quality_gates.py` + `step_manifest` + `audit_store`）/ 三条学术管线。论文与软著窗口所用技能（`paper-*` / `copyright-*`）全部出自此库
- **未实现任何功能代码**（宪法 §1.3：W3 须先过商讨协议五步制）

---

### 2026-09-14 20:5x · 主检出 · 阶段立项

- **动作**：建立本阶段真源 `docs/stages/SPRINT_2026-09.md`（路由 `立项`，状态 `plan` 待确认）
- **探明**：Owner Map **24/24 模块真实存在**（无失真）
- **发现（能力缺口）**：`voice/` **只有 TTS，无 ASR、无语音情绪识别**；`multimodal/` **无表情/人脸实现** → 命题任务 1 的两条通道确实为空
- **发现（口径警告）**：我 grep 得 API 175 / 测试 806，与 CODE_GRAPH 声明的 206 / 1042 **口径不同**（它用运行时 `create_api_app` 实扫）→ **不可据此判失真**，复测须沿用同一方法
- **发现（真源缺口）**：`docs/FEATURE_MAP.md`、`docs/board/BOARD.md`、`docs/board/TASK_PACKAGES.md`、`P1_BACKLOG.md` 均不存在；本文件即补建之一
- **Git**：`main`，未跟踪 2 项（`.zcode/`、`frontend/audit-tabs.mjs` 2026-09-03, 2301B），**多窗口开工前须处置**
- **待裁决**：`SPRINT_2026-09.md` §12 的 Q1–Q6（对标产品链接 / 论文目标 / 嵌入式形态 / 生物特征合规 / 软著主体 / 窗口分工）
- **未实现任何代码**（`plan` 未确认）

---

## 阻塞登记

| # | 阻塞项 | 影响窗口 | 需谁解决 | 状态 |
|---|--------|---------|---------|------|
| — | 当前无 `阻断问题`；`SPRINT_2026-09.md` §12 六项为**产品决策**非阻塞 | — | 用户 | 待确认 |
| B1/B2/B3（赛事口径/系统网址/指导教师实名） | 材料线 | 用户（**用户已答复：不阻塞，自行解决**） | 关闭 |

---

## 漂移告警

| 时间 | 内容 | 处置 |
|------|------|------|
| — | 暂无 | — |

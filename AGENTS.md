# AGENTS.md — 唯一的你（ai-girlfriend）项目 Agent 宪法

> **项目**：unique-you — 唯一的你·十四 — 基于 LLM 的智能情感陪伴系统
> **版本**：v1.8（2026-09-17 全仓性能与正确性扫描批次：测试口径二次刷新 + 项目根路径锚定 + 8 类缺陷修复）
> **工作目录**：`D:\Desktop\ai-girlfriend`
> **Python**：3.10+（见 `pyproject.toml`）
> **主语言**：中文（代码注释遵循用户最新消息语言）

---

## 0. 项目身份

- **产品形态**：微信扫码即用的 LLM 智能情感陪伴系统，扫码登录后控制台调角色与语音
- **技术栈**：Python 3.10+ / React 19 / Vite 8 / TypeScript 6 / Tailwind 4 / Zustand 5 / 测试 1099（1012 Py 通过 + 4 跳过，87 FE 通过；2026-09-17 系统 Python 3.12 + vitest 实测，见 §4.3）/ MIT
- **核心能力**（见 `README.md`）：
  - 微信聊天（扫码登录，文字/语音，多用户独立）
  - 角色系统（每用户绑角色卡，性格/风格/口头禅可调）
  - 情感引擎（亲密度、情感阶段变化）
  - 主动搭话（不全是被动等待）
  - 语音合成（MiMo 云 / Edge-TTS / 本地模型）
  - 记忆系统（三层：短期 + 情景 + 长期）
  - 工具（天气、日历、提醒、搜索）
  - 剧情线（支线、进度追踪）
  - 邀请码注册 + 管理控制台（19 个页面）
- **启动方式**（08-28 起 .cmd/.bat/.ps1 启动部署脚本已删除）:
  - 后端：`python main.py`（控制台/微信模式）或 `python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000`
  - 前端：`cd frontend && npm run dev`
  - 部署：`deploy/` 目录（systemd + nginx 模板）
- **远程仓库**：`https://github.com/FOURTEEN1416/fourteen.git`

---

## 1. 顶层原则

### 1.1 技术原则

1. **广泛调研优先**：每个新模块启动前必须完成多方案调研（论文/GitHub/工业界），结果记录到 `docs/` 或 `.codebase-memory/`
2. **必要时自研**：当现有方案无法满足需求时，允许自主开发专用算法（DL/RL/Transformer/GNN/LLM 等）
3. **不择手段达成目标**：允许使用任意方法，不受预设技术栈限制
4. **深度优先而非浅层包装**：追求真实情感效果与质量，不满足于"能跑通"

### 1.2 sliver-vibe-coding 执行法则

- 路由先于行动：先选路由，再行动
- Owner 先于补丁：先确定责任模块，再修改代码
- 契约先于跨 owner 实现：先定接口契约，再跨模块实现
- 无新鲜验证，无完成声明：没有当前验证证据，不能声称完成
- 不引入兜底层、兼容 shim、重复 owner、生成文件编辑、投机抽象

### 1.3 用户偏好（来自 user_profile.md，必须遵守）

- **沟通语言**：中文
- **问题解决**：一次性解决，反对采样验证
- **系统可靠性**：长期有效，预防复发
- **内容质量**：高质量输出，反对低质量内容
- **设计要求**：使用真实数据，反对明显 AI 特征的背景图
- **数据利用**：最大化复用现有项目资源（`character_card/` / `my_character/` / `data/`）
- **软件行为**：透明操作，反对模型别名映射伪装
- **流程执行**：亲自完成，**反对使用 subagent**（频繁出错）
- **性能优化**：质量优先，加速度前提是保证质量
- **研究方法**：广泛调研后再开发，最小自研 + 谨慎验证，优先现有论文/成熟项目/GitHub 推荐
- **算法开发**：必要时允许自研，不刻意回避
- **实现手段**：允许任何手段达成目标
- **技术引入**：必要时允许 RL、深度学习等
- **权限处理**：项目相关权限预先授予，无需事先批准
- **执行风格**：持续执行，无需报告进度
- **工作流要求**：根因分析 + 完整修复总结 + 清晰验证步骤 + 质量复测 + 清理临时脚本和死代码 + 同步文档
- **调研搜索分域策略（2026-08-30 修订，用户裁决）**：
  - **代码开发类任务**：GitHub-First 仍为优先路径（`github-search-strategy` skill 目录优先 → 逐仓库验证活跃度 → raw 直取/爬虫抓详情）；此类任务禁止 `WebSearch` 的硬规则不变。
  - **信息采集 / 新闻调研 / 非代码类查证**：**不强制 GitHub**，可使用通用搜索与爬虫（WebSearch / firecrawl / Crawl4AI 均可），以信息时效与覆盖为先。
  - **产品内智能体搜索降级链**：网络人设增强（火爬虫）限流/不可用时，降级使用 **Crawl4AI** 搜索（产品侧 Crawl4AI 改造已于 e1a4cec 落地，此条为其智能体设定固化）。
  - 通用例外保留：用户对特定查询的明确书面许可可覆盖以上任何路径。
- **商讨协议（五步制，2026-08-24 入宪）**：任何功能修改类需求必须走「定位 → 复述 → 排歧 → 确认 → 举证」流程：
  1. **定位**：用户按功能清单编号（`docs/FUNCTION_INVENTORY.md`，页-功能点两级编号）指出目标 + 描述期望；历史设计文档（`docs/history/`）为意图基准
  2. **复述**：AI 用自己的话重复需求，并展示该功能的**当前真实行为**（对齐起点）
  3. **排歧**：一句描述存在 ≥2 种合理解释时，**必须列成选择题**让用户选，禁止擅自选择；用户描述有缺口时，**显式列出 AI 做的假设**，禁止静默补全；关键改动追加负面边界确认（"它不应该变成什么样"）
  4. **确认**：用户逐条否决/接受假设，明确说"确认"后才允许动手
  5. **举证**：完成后出示 diff + 测试证据，证明改的就是第 4 步确认的内容
  - 大改动自动拆小步，每步独立可回滚
- **真值裁决优先级（2026-08-24 入宪）**：设计意图冲突时按此裁决——① 代码实况（CODE_GRAPH + 实际路由/端点）> ② 现行文档（README / AGENTS / P1_BACKLOG / FUNCTION_INVENTORY）> ③ 历史文档（`docs/history/`，仅供追溯）。历史文档的现行有效性判定见 `docs/history/INDEX.md`

---

## 2. Owner Map（责任模块）

| 模块 | Owner | 路径 |
|------|-------|------|
| 入口 | 后端开发 | `main.py` / `start_all.cmd` |
| 编排器 | 编排开发 | `orchestrator/` |
| API 服务 | 后端开发 | `api/` |
| 角色系统 | 角色开发 | `character_card/` / `my_character/` / `persona_extractor/` / `shisi/character/`（PNG tEXt chunk 编解码 + SillyTavern V2/V3 角色卡子系统) |
| LLM 提供商 | LLM 开发 | `llm_provider/` |
| 记忆系统 | 记忆开发 | `memory_ext/` / `context/` / `memory-config.json` / `shisi/memory/legacy/`（WorkingMemory/EpisodicMemory/MemoryPipeline 等生产路径,`legacy` 仅表历史迁移非待删除) |
| 知识检索 | 知识开发 | `shisi/knowledge/` / `shisi/knowledge/legacy/`（RAGEngineV2/Retriever/CharacterKnowledgeService/CrawlerAdapter) |
| 多模态 | 多模态开发 | `multimodal/` |
| 主动搭话 | 主动开发 | `proactive/` |
| 语音 | 语音开发 | `voice/` |
| 剧情线 | 剧情开发 | `plugins/` / `shisi/storyline/` |
| 微信集成 | 集成开发 | `wechat_direct/` / `shisi/` / `shisi/api/v2/`（DDD 核心 plane:affinity/emotion_stage/persona/stats/vital_signs + v2 迁移) |
| 克隆训练 | 训练开发 | `clone_training/`（weclone_adapter 已于 08-28 删除，克隆收敛为本地提取+JSON 上传） |
| 前端 | 前端开发 | `frontend/`（React 19 + Vite 8） |
| 部署 | 部署开发 | `deploy/`（bat/ps1 双版本部署脚本已于 08-28 删除） |
| 可观测性 | 运维开发 | `observability/` |
| 安全 | 安全开发 | `security/` |
| 测试 | QA | `tests/`（1012 Python 测试通过 + 4 跳过；前端 87 测试；2026-09-17 实测，口径同 CODE_GRAPH §1.1) |
| 工具与脚本 | 工具开发 | `tools/` / `scripts/` / `utils/` |

---

## 3. 项目硬约束（不可违反）

- **三端统一（2026-09-02 用户裁决，2026-09-03 分档修订，2026-09-06 补充"参赛文档三不入"，最高优先）**：
- **服务器准入原则（2026-09-14 用户裁决，原话「云服务器上消费不了文档，文档都不进去服务器，服务器能消费的再入」）**：**服务器只放它能消费的东西**——代码 / 依赖 / 配置模板 / 部署件 / 测试（即 A 档）。**文档类一律不上服务器**（服务器消费不了，放上去只是噪声）。**但文档类必须 `commit → push` 到 GitHub 作备份**：**GitHub 是文档的备份端，服务器不是**。
- **参赛文档三不入（2026-09-06 用户裁决）**：`大创赛报名以及后期发展/` 下的参赛材料（解决方案 md/docx/PDF 等）不入 git、不入 GitHub、不上云服务器（gitignore 已覆盖、git 历史 0 track、服务器 sparse-checkout 排除三重保障）——此类文档服务器消费不了，"三端同步"概念不适用于参赛文档；其版本管理只在本地目录内闭环（md→docx→PDF 同目录互为版本对）。LOG 中仅可记录"文档已更新"事实，不推送文档本体。
- **A 档细则（部署相关）**：按部署相关性分两档——
  - **A 档 · 部署相关**（源代码、前端源码、依赖定义、配置模板、`deploy/`、`tests/`）：commit → push origin → 服务器 `cd /opt/ai-girlfriend && git pull`（**服务器是 git 克隆且可连 GitHub，禁止 archive 单向覆盖**——archive 会静默吞掉服务器本地修改）→ 代码/依赖变更时跑 `deploy/remote_deploy.sh` → 服务 health 核验 + md5 抽验 A 档关键文件三端一致。**A 档任一端落后即任务未完成**；禁止只改本地不推送、只推送不部署。
  - **B 档 · 纯文档**（`docs/`、根目录 `.md`：AGENTS.md/CODE_GRAPH.md/LOG.md/README.md 等）：commit → push origin 即为完成（**GitHub 即文档备份端**），**不上服务器**——服务器 sparse-checkout 只拉 A 档，B 档永远不出现在 `/opt/ai-girlfriend`。
  - 边界：服务器 B 档对象仍存于服务器 `.git` 对象库（`git checkout` 可随时取回）；服务器上的本地修改必须先回仓库再 pull；`git archive` 覆盖式同步**已废弃**。
- **微信合规**：不得使用违反微信 ToS 的自动化手段，不得造成账号封禁
- **数据隐私**：用户聊天记录、角色卡、记忆数据属敏感信息，不得外泄，`.env` 必须在 `.gitignore`；参赛报名/个人资料类目录不得进入公开仓库
- **多用户隔离**：不同微信用户的数据（角色、记忆、剧情）必须严格隔离，不得串扰
- **质量门槛**：626+ 测试必须保持通过，新功能必须附测试
- **Windows 优先**：目标用户环境
- **真实情感优先**：角色互动必须自然真实，不得使用生硬模板回复
- **LLM 透明**：不得伪装模型身份（反对模型别名映射伪装实际模型）

---

## 4. 工程约定

### 4.1 代码风格

- Python：PEP 8 + 类型注解（Python 3.10+）
- 前端：React 19 Composition API + TypeScript 6
- Ruff + mypy + pyright（见 `pyproject.toml` / `pyrightconfig.json`）
- 提交信息：Conventional Commits（中文描述）
- 注释：遵循用户最新消息语言

### 4.2 依赖管理

- Python：`pyproject.toml`
- Node.js：`frontend/package.json` + lockfile

### 4.3 测试约定

- 626+ 测试已建立，新功能必须附测试（实测基线:1012 Python 通过 + 4 跳过 / 87 前端，2026-09-17 实跑,与 CODE_GRAPH §1.1 对齐)
- 用户明确反对采样验证，要求完整验证
- **测试口径注记（2026-09-17 二次刷新）**：历史文档口径 1117（1042 Python + 75 前端，2026-09-01 .venv 实测）——该环境随 09-14 主仓事故丢失，此后不再作为可复现基线。**当前唯一可用口径**：系统 Python 3.12 实测 **1016 收集 / 1012 通过 / 4 跳过**（2026-09-17 全仓扫描批次终态复测，117.85s，含 D26 正向用例与 D29 双层隔离）+ 前端 vitest **87/87**（15 文件）+ `tsc --noEmit` 0 错误。跑测试：`PYTHONPATH= python -m pytest -q`（`PYTHONPATH=` 前缀用于清空宿主注入的 safe-delete 护栏，见本机环境注记）。
- 验证报告：归档到 `docs/`
- 测试基线：`pytest_true_baseline.log` / `pytest_wip_baseline.log` / `pytest_post_commit.log`

### 4.4 Git 约定

- 主分支：`main`
- 不强制 PR（单人开发），但关键变更需 commit message 清晰
- 禁止 `git add .`，必须按文件添加

---

## 5. 验证命令映射

| 验证目标 | 命令 |
|---------|------|
| 后端测试 | `pytest` |
| 前端测试 | `cd frontend && npm test` |
| 类型检查 | `mypy .` / `pyright` |
| 一键启动 | `start_all.cmd` |
| 部署打包 | `deploy_ai_girlfriend.bat` |

---

## 6. 防漂移规则

以下情况触发漂移检查：
- 当前计划与 truth 文档冲突
- AI 提议当前阶段外的功能
- 请求影响技术栈/数据流/权限/部署
- 变更无明确 owner 放置
- 验证证据与声称完成矛盾
- **对技术调研任务调用 `WebSearch`**（违反 §1.3 零容忍硬规则）

漂移处理：
1. 记录变更内容
2. 识别受影响模块
3. 评估是否触及 foundation
4. 选择：拒绝 / 更新设定 / 重新设计阶段 / 停止等用户决策

---

## 7. 经验教训（Lessons Learned）

| # | 教训 | 详情 |
|---|------|------|
| L1 | 调研搜索强制 GitHub-First（零容忍硬规则 2026-07-28）— 完全禁止 WebSearch 用于技术调研，必须走 github-search-strategy + browser-automation 流程。详见 §1.3 |
| L2 | 微信接口易变 — `wechat_direct/` 依赖的微信 web 协议会不定期失效，必须有版本追踪与失效快速响应机制，参考 `shisi/` 上游更新 |
| L3 | 多用户记忆必须严格隔离 — `memory_ext/` 三层记忆（短期+情景+长期）必须按 user_id 隔离，任何串扰都属严重 bug |
| L4 | LLM 输出 JSON 必须 Schema 校验 — 使用 Pydantic 双重校验，避免 JSON 解析失败导致角色/工具调用中断 |
| L5 | 角色卡一致性 — `character_card/` 与 `my_character/` 必须保持同步，避免角色人格漂移 |
| L6 | 语音合成优先级 — MiMo 云 > Edge-TTS > 本地模型，按可用性与延迟动态切换，不能假设单一源永久可用 |
| L7 | 626+ 测试必须保持通过 — 任何改动前先跑基线测试，改动后比对 `pytest_true_baseline.log`，回归即阻塞 |
| L8 | 临时脚本必须清理 — 完成任务后清理一次性脚本与 `deploy_payload.tar.gz` 等构建产物，避免代码库膨胀 |
| ~~L9~~ | ~~部署脚本双版本一致~~ → **已失效**（2026-08-28 用户删除 bat/ps1 部署脚本，部署统一走 `deploy/`） |

---

## 8. 并行纪律（多窗口 worktree 协议，2026-08-28 增补，适配自 psd-framework §4）

1. **新窗口一律 worktree 开工**：`pwsh scripts/new_window_worktree.ps1 -Name <窗口名>`；此后读写只在自己的 `..i-girlfriend-<窗口名>` 内；分支 `wt/<窗口名>`
2. **主检出只做协调合并**：主检出保留给协调与 merge 收编（`git merge --no-ff wt/<名>`）；收编前必须跑主检出回归门（pytest/vitest/E2E/对齐四项）
3. **白名单提交**：产物落盘即提交；只提交任务包白名单内文件，精确 `git add`，禁用 `git add .`
4. **data/ 与 frontend/node_modules 为 Junction 共享**：生成物必须带窗口前缀或唯一 seed；冲突以后提交者重命名为准
5. **跨窗看板**：一切跨窗信息写 `docs/board/BOARD.md`（工具 `scripts/window_board.ps1 -Append/-Tail`）；开窗先读看板再读 `docs/board/TASK_PACKAGES.md`
6. **记忆库双写**：重大裁决写 BOARD 同时 memory MCP（agent_id=shared）入库
7. **收编门禁**：窗口完成自检（窗口内 pytest+vitest 绿）→ 协调者 merge --no-ff → 主检出回归门 → 卸窗脚本
8. **🔴 Owner 唯一制（2026-09-15 事故驱动，血泪条款）**：
   2026-09-15 凌晨，主检出（`D:\Desktop\ai-girlfriend`）工作树被两个窗口同时改写，导致主控写入的 `docs/board/BOARD.md` 被回滚 **3 次**、`docs/verification/` 被整个删除、`tests/test_wechat_connector.py` 的 232 行版本被打回 138 行。**根因不是某个命令，而是没有任何文件有唯一 owner。**
   - **任何文件在同一时刻只能有一个 owner 窗口**；owner 写在 `docs/board/BOARD.md` 的窗口登记表里，改之前先查表
   - **`docs/board/BOARD.md` 与 `docs/board/TASK_PACKAGES.md` 的 owner 恒为「主控」**；其它窗口如需追加，**一律追加到追加区**，且**不得改动登记表与他人的历史条目**（保留审计线索）
   - **`tests/**` 的 owner 恒为「W4 验证窗口」**；W3 只改实现代码，**实现改完由 W4 复核收编**
   - **主检出（`D:\Desktop\ai-girlfriend`）工作树不是共享草稿区**：窗口一律在 `..i-girlfriend-<窗口名>` 内读写；确需改主检出，须先在 BOARD 声明并取得主控同意
   - **主控写入即提交**：主控对主检出的任何写入，**必须在同一帧内 `git add` + `commit`**（必要时 push）—— 写而不提交 = 等着被回滚
   - **恢复类操作（clone/checkout/reset）前必须先看 `git status`**：若工作树存在未提交的后置成果，**先把它们拷出或提交**，再执行恢复

## 9. 修订历史

| 版本 | 日期 | 变更 |
|------|------|------|
| **v1.8** | **2026-09-17** | **全仓性能与正确性扫描批次**：§0/§2/§4.3 测试口径二次刷新（1016 收集/1012 通过/4 跳过 + vitest 87/87 + tsc 0 错）；新增 `utils/project_paths.py` 统一项目根锚定（修复 CWD 相对路径导致的配置静默丢失，覆盖 scheduler/LLM 供应商/角色库/剧情线/重要日期/迁移/音色等 12 处）；修复 8 类性能与正确性问题（见 `docs/verification/2026-09-17-全仓扫描验证报告.md`）；移动端适配推进；文档与代码一致性校正（README/CODEMAPS/CODE_GRAPH 端点口径 208→204） |
| **v1.7.2** | **2026-09-17** | §4.3 测试口径刷新（1014 收集/1010 通过/4 跳过 + vitest 87/87）；同日三连修+web 两开关+死代码清洗见 CODE_GRAPH v3.7.0 与 DECISION_LEDGER 09-17 行 |
| **v1.7.1** | **2026-09-15** | §0/§4.3 测试口径注记：文档口径 1117 之外补充事故后可复现口径（系统 Python 3.12：995 收集/989 通过/6 跳过）；W3 多模态收编（图片通道+silk 语音+守卫 1/3/34），multimodal owner 职责面扩展 |
| **v1.7** | **2026-09-15** | **§8 新增第 8 条「Owner 唯一制」**（事故驱动）：任何文件同一时刻只能有一个 owner；`BOARD.md`/`TASK_PACKAGES.md` owner 恒为主控（其它窗口只能追加到追加区、不得改登记表与他人条目）；`tests/**` owner 恒为 W4（W3 只改实现）；主检出工作树不是共享草稿区；**主控写入必须在同一帧内 commit**；恢复类操作前必须先 `git status` 保住未提交成果。起因：主检出被两窗口并发改写，BOARD.md 被回滚 3 次、`docs/verification/` 被删、W4 测试 232→138 行 |
| v1.0 | 2026-07-28 | 初始版本：项目身份 + 顶层原则 + 用户偏好（含调研搜索零容忍硬规则）+ Owner Map + 硬约束 + 工程约定 + 验证命令 + 防漂移规则 + 经验教训 L1-L9 |
| v1.1 | 2026-07-30 | Owner Map 补全 6 个缺失目录(`shisi/character/` / `shisi/memory/legacy/` / `shisi/knowledge/legacy/` / `shisi/api/v2/` / `shisi/storyline/`);测试基线从 626+ 修正为 1104(1025 Python + 79 前端,pytest+vitest 实跑);§0 技术栈同步 |
| v1.2 | 2026-08-24 | §1.3 新增商讨协议（五步制:定位→复述→排歧→确认→举证）+ 真值裁决优先级（代码实况>现行文档>历史文档）;新增真值载体 `docs/FEATURE_MAP.md`（功能现状地图,商讨坐标系）与 `docs/history/`（4 份历史设计文档归档 + INDEX.md 演进索引） |
| v1.3.2 | 2026-08-30 | §1.3 搜索策略分域修订（用户裁决）：代码任务 GitHub-First 不变；信息采集/新闻调研不强制 GitHub；火爬虫限流降级 Crawl4AI 固化为智能体设定 |
| v1.5 | 2026-09-03 | §3 **三端统一分档修订**（用户裁决，实证驱动）：A 档部署相关=commit→push→服务器 git pull（archive 覆盖废弃）；B 档纯文档=仅 commit→push，服务器 sparse-checkout 排除；VISION 部署形态同步（服务器可连外网旧述作废） |
| v1.6 | 2026-09-14 | §3 新增**服务器准入原则**（用户裁决，原话「云服务器上消费不了文档，文档都不进去服务器，服务器能消费的再入」）：**服务器只放它能消费的**（A 档）；**文档类不上服务器，但必须 `commit → push` 到 GitHub 备份**（GitHub = 文档备份端，服务器不是）；B 档细则表述同步。`AGENTS.md` 本身解除 gitignore、纳入版本控制（原只存本地，无任何备份） |
| v1.5.1 | 2026-09-04 | §0/§2/§4.3 测试基线 1101→1117（1042 Python+75 前端，对齐 CODE_GRAPH v3.5.0 权威口径；旧 1101 系 09-01 中间批次数字未随 T1-T5/收尾批次增长同步） |
| v1.4 | 2026-09-02 | §3 硬约束首条新增**三端统一铁律**（用户裁决：本地=GitHub=云服务器，每次修改必须完成）；数据隐私条补参赛/个人资料目录不入公开仓库 |
| v1.3.1 | 2026-08-28 | §8 新增多窗口 worktree 并行纪律（适配 psd-framework §4）：建窗/看板/收编门禁脚本化；docs/board/ 任务包机制 |
| v1.3 | 2026-08-28 | 治理刷新:D1 裁决 Demo 全删(端点 204→199 实扫);测试基线 1104→1089(1030 Python+59 前端,pytest/vitest 实跑);`docs/README.md` 成为文档体系唯一入口(Diátaxis 象限+生命周期标注);项目根 `LOG.md` 为 L2 操作日志强制落点(会话收尾必追加,无日志=会话未闭环) |

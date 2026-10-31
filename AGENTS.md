# AGENTS.md — 唯一的你（ai-girlfriend）项目 Agent 宪法

> **项目**：unique-you — 唯一的你·十四 — 基于 LLM 的智能情感陪伴系统
> **版本**：v1.62（2026-10-31 表述分离 + 用户主权 per-user 化批（8 域 · **7/8 独立对抗验收通过**；修复批 **6/6 通过** · 全量回归通过 · **已三端部署 @ `43aeaae`**）：**psych** 五端点 admin 全量 / 普通用户仅本人会话；**proactive-backend** 新增 `utils/proactive_config.py` 三级合并 + `user_proactive_config` 覆盖表 + 4 个 `/api/user/proactive*` 端点；**proactive-frontend** 消息 tab 对全体用户开放（个人覆盖/继承全局徽章）；**wechat** 遗留全局端点保持 admin、新增 iLink 出站 CDN 媒体上传；**clone** 数据集 `data/clone/<user_id>/` per-user 分区；**voice** 音色归属单一规则 + 角色音色写门改 `require_character_write`（全局 switch-voice/set-engine 保持 admin）；**topic** 快照读面按本人会话白名单；**docs-naming** 「训练→风格提取」表述分离（目录/类名/路径/字面量零改）。端点 **249/205 → 253/207**（+4 全部为 user/proactive）；测试 **276 文件 / 4498 通过 + 2 跳过 / 收集 4500**（3 个存量失败族由 conftest users 主库隔离根治，非删测试）+ 前端 **47 文件 354 例** + tsc 0 + ruff 0；徽章 **4853**；角色卡 **41 张**。**已三端部署 @ `43aeaae`（2026-10-31）**；voice 公共卡音色写侧产品级回退、风格提取 LLM per-user 化等登记 deferred。
> **上一版本**：v1.61（2026-10-30 前端体验批7·遗留收尾（D20 M 级四项清零，本地全链提交未部署）：NEW-20 高危管理操作二次确认 / NEW-22 用户列表排序全页化 / NEW-31 组件库收编 / NEW-32 设计 token 统一；对抗验收 2 缺陷修复 + 突变验红 4/4；后端 269 测试文件 4297 通过 + 2 跳过 / 3 个存量失败族；ruff 0；前端 47 文件 342 例 + tsc 0；端点无新增）。
> **更早版本**：v1.60（2026-10-29 前端全站体验批 1–6，本地七提交未部署；新端点 14 / 新表 4 / driver.js；vitest 303/303）→ v1.59.1（2026-10-28 部署收口，生产 @ `dd3b470`；测试 4262 收集 / 4256 通过 + 2 跳过 / 4 失败；前端 251/251；徽章 4508）。**更早**：v1.57 拟人化实施批（2026-10-27，详见 §10 修订历史表）。批次详情与全部历史版本记录见 `LOG.md`（唯一只追加不覆盖文件）；当前状态见 `docs/HANDOFF_REPORT.md`。（2026-09-29 默默裁决：版本头历史堆叠移除，只留当前版本行。）

---

## 实现补充快照（2026-10-11，三端一致 `aea3cd0`；当前状态以版本头 v1.62 为准）

- **三端一致 @ `aea3cd0`**（2026-10-11 SEC-15 根治批；10 月三批均已部署）：10-05 安全升级批 `2c3b888`（两轮对抗审计 34+47 发现 + 生产 SSH 取证 + P0 修复八域，nginx 线上精确 patch：备份后插安全头/X-Forwarded-Proto/ws 日志关闭）→ 10-09 攻击面扫描批 `e949107`（六路对抗扫描 30 发现/16 确认/9 修复，nginx XFF 行替换 `$proxy_add_x_forwarded_for`→`$remote_addr`，行为级实证 21 打 15×429+6×401）→ 10-11 SEC-15 根治 `aea3cd0`（vitest 3→5 根治 tinypool 双 critical、`npm audit` 0 vulnerabilities、bun.lock/package-lock 双锁收敛销账 FE-2）。复验：health/ready 200、重启窗口 0 ERROR、blob 三端 `git hash-object` 一致、CI 全绿 run `38109894676`（9/9 job 含前端审计门）。下方 bundle/停机判据/依赖声明纪律继续有效。
- 部署通道：本地 `git push` + 服务器 bundle 快进（含大二进制提交的 `git pull` 必现 `fetch-pack: unexpected disconnect`）。**停机判据用 `MainPID=0` + `cgroup.procs` 为空**，不要用 `is-active`（`TimeoutStopSec=5s` 会让超时停机显示 `failed` 而进程已清空）。
- 迁移纪律：冷停 → 冷包 + 三库 `.backup`（逐库 `integrity_check`）→ **真实副本重复初始化演练**（验幂等与行数）→ 生产执行 → 行数比对。10 月三批零 schema 变更，本纪律留待下一次 schema 批启用。
- 依赖声明纪律：`sqlalchemy` 必须带 `asyncio` extra，否则干净环境缺 `greenlet`，`sqlalchemy.ext.asyncio` 导入即失败；**本地预装 ≠ 声明完整**，以干净环境（CI）为准。
- 🔴 **清理 `/tmp` 的禁区**：本机该 unit `PrivateTmp=no`，`/tmp/ai-girlfriend-scheduler.lock` 是**生产调度器单例 flock**（`api/run_api.py::_ensure_scheduler_singleton`）。删路径不立即中断（master 仍持 fd），但**新 worker 会新建 inode 再抢到锁 → 调度器翻倍**。删前先 `grep -rn "/tmp/ai-girlfriend"` 反查引用；若已误删，冷停 → 删残留路径 → 重启，并核实日志为 **1 master + 3 slave**。
- 最新验证、遗留边界见 `docs/HANDOFF_REPORT.md` 顶栏与 `LOG.md` 最新条目（2026-10-12 通道语义对齐批 `173b2b3` 及 D16 补裁 `7ac7418`）。

## 本批重构确立的设计规则（2026-09-26 上线，改前必查）

- 流式入口**按句边真流式下发**（`chat.stream_mode: true`，2026-10-21 用户裁决取代「先完整定稿再分块」）：完整句过 `utils.stream_guard.IncrementalReplyGuard` 即发，不完整尾巴永不下发；未发余量流末交 `_finalize_reply` 唯一全文终裁；**已发前缀永不回滚**，`_ack`/`accepted_end` 只落传输确认前缀（SSE/WS 适配器确认后记账）；普通 HTTP 使用 ASGI 发送回调，微信拆条仅记 API 受理正文（受理 ≠ 已读）。守卫取舍（已登记降级）：需全文位置的规则（仅剩角色名/错误哨兵/发言标签/时间前缀/安全闸）延迟到终裁、**不再复核已发前缀**；截断类规则（无由头反问）实现为**终止上游 + 丢弃全部未发**。`chat.stream_mode: buffered` 为旧路径回滚开关（与供应商层 `llm.stream_enabled` 正交，不得复用）。
- 请求模型经 `utils.llm_bridge.request_llm` 贯通子任务，进入线程池必须传播上下文；后台任务走 `api/byok.session_llm`。**显式用户配置错误禁止回落平台凭证**。
- 事实不能因词面相似就认作同一命题；删除必须失效派生召回。抽取来源水位与删除水位由 SQLite 持久化。
- 日记只用 `_legacy_diary_summarizer`，午夜任务总结**本地昨日**并按会话/角色分桶。主动 `dry_run` 只计算紧迫度、不生成草稿。
- 历史排序与分页一律按写入 `id`；同会话跨 worker 互斥用 `ProcessSessionLock`（OS 锁）。
- 测试清单必须递归覆盖 `tests/`，分块总数核对全量收集数；引用基线须同时声明**角色卡数**与**工作树状态**。

## 0. 项目身份

- **产品形态**：微信扫码即用的 LLM 智能情感陪伴系统，扫码绑定 bot 后在控制台调角色与语音（D16：谁扫码谁聊，一对一）
- **技术栈**：Python 3.10+ / React 19 / Vite 8 / TypeScript 6 / Tailwind 4 / Zustand 5 / 测试 **4853**（**4498** Py 通过 + 2 跳过 / **0 失败**（3 个存量失败族已由 2026-10-31 conftest users 主库隔离根治），**354** FE 通过（vitest 5，47 文件）；2026-10-31 本批实测收集 **4500**、**276 测试文件**，角色卡 **41 张**）/ MIT
- **核心能力**（见 `README.md`）：
  - 微信聊天（**微信官方 iLink 机器人平台**；**D16 裁决（2026-10-12）「谁扫码谁聊」**：注册用户扫码绑定 bot，bot 即「角色化身」出现在其本人微信里一对一聊天，角色在网页控制台自由切换（activate 链同步扫码人绑定）——**一人一 bot 一条通道**（2026-10-12 D16 补裁「每一个用户只有一个BOT」，取代 09-19「一人两条」）；旧「好友回复『角色』自选」机制已随 D16 整体退役，非扫码人消息按异常拒绝）
  - 角色系统（每用户绑角色卡，性格/风格/口头禅可调）
  - 情感引擎（亲密度、情感阶段变化）
  - 主动搭话（不全是被动等待）
  - 语音合成（MiMo 云唯一引擎 + Windows SAPI 本地兜底；Edge-TTS 等已于 08-28 删除）
  - 记忆系统（三层：短期 + 情景 + 长期）
  - 工具（天气、日历、提醒、搜索）
  - 剧情线（支线、进度追踪）
  - 开放注册主链路 + 邀请码并存（D13 裁决，LOGIN-2 口径更正）+ 管理控制台（19 个页面）
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
- **数据利用**：最大化复用现有项目资源（`config/characters/` / `my_character/` / `data/`）（旧 `character_card/` 死码包已于批6b 项10 删除）
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
| 入口 | 后端开发 | `main.py`（控制台/微信）或 `python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000`；`start_all.cmd` 已于 2026-08-28 删除 |
| 编排器 | 编排开发 | `orchestrator/`（**跨目录 owner 例外**：`utils/stream_guard.py`（主链增量出站守卫状态机）owner 亦为「编排开发」——它是 `_stream_mixin` 的规则执行器而非通用工具；见 2026-10-21 R4 真流式批） |
| API 服务 | 后端开发 | `api/` |
| 角色系统 | 角色开发 | `config/characters/`（卡真源） / `my_character/` / `persona_extractor/` / `shisi/character/`（PNG tEXt chunk 编解码 + SillyTavern V2/V3 角色卡子系统；旧顶层 `character_card/` 死码包已于批6b 项10 删除） |
| LLM 提供商 | LLM 开发 | `llm_provider/` |
| 记忆系统 | 记忆开发 | `context/` / `shisi/memory/legacy/`（WorkingMemory/EpisodicMemory/MemoryPipeline 等生产路径,`legacy` 仅表历史迁移非待删除)。**`memory_ext/` 已于 2026-09-28 历遍批整删**（W4-G 停用后全仓零消费者的第二 RAG 真源，防复活契约见 `tests/test_w4_memory_ext_disposition.py`）；`memory-config.json` 系文档化石（磁盘从未有此文件，已更正）|
| 知识检索 | 知识开发 | `shisi/knowledge/`（Retriever/CharacterKnowledgeService/CrawlerAdapter；RAGEngineV2 所在的 `shisi/knowledge/legacy/` 已于 2026-09-21 批6b 整删） |
| 多模态 | 多模态开发 | `multimodal/` |
| 主动搭话 | 主动开发 | `proactive/`（含 **`proactive/topic_recall.py`**：parked 话题复提候选、三重闸候选侧，2026-10-27 话题状态机 Q3） |
| 语音 | 语音开发 | `voice/` |
| 剧情线 | 剧情开发 | `plugins/` / `shisi/storyline/` |
| 微信集成 | 集成开发 | `wechat_direct/` / `shisi/`（DDD 核心 plane: affinity/emotion_stage/persona/stats/vital_signs） |
| 风格提取（训练管线） | 训练开发 | `clone_training/`（提取说话风格并注入提示词，**不是模型微调**；weclone_adapter 已于 08-28 删除，克隆收敛为本地提取+JSON 上传） |
| 前端 | 前端开发 | `frontend/`（React 19 + Vite 8） |
| 部署 | 部署开发 | `deploy/`（bat/ps1 双版本部署脚本已于 08-28 删除） |
| 可观测性 | 运维开发 | `observability/` |
| 安全 | 安全开发 | `security/` |
| 测试 | QA | `tests/`（**4498** Python 通过 + 2 跳过 / **0 失败**；前端 **354**（47 文件）；2026-10-31 本批实测收集 **4500**、**276 测试文件**、**41 卡**；8 域实现+文档仍在工作树未提交、未部署（部署随用户裁决），见 `LOG.md` 本批条目） |
| 工具与脚本 | 工具开发 | `tools/` / `scripts/` / `utils/`（含 **`utils/topic_state.py`**：话题状态机三表 `topic_state/topic_plan/topic_meta` 的唯一 owner，2026-10-27 话题状态机批） |

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
- **质量门槛**：全量测试基线必须保持通过（当前口径 **4498 通过 + 2 跳过 / 0 失败 / 收集 4500**，见 §4.3），新功能必须附测试
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

- 全量测试基线（2026-10-31 本批实测）：**4498** Python 通过 + **2 跳过 / 0 失败** / **276 测试文件** / 收集 **4500** + **354** 前端（47 文件）；3 个存量失败族（`test_api_routes` sqlalchemy / `test_w10_metrics_multiproc` 503→404 / `test_w3_scheduler_state` Depends）已由 `tests/conftest.py` 的 users 主库会话级临时库隔离根治（非删测试）；新功能必须附测试；现役角色卡 **41 张**（persona 公式 2×41+13=95）；⚠️ 全量口径必须含 `tests/core/` **与 `tests/api/`**，且分块清单须 `find` 递归生成（`git ls-files` 清单不含未提交新文件；**分块总数必须对四块并集，不得对单块外推**）
- 用户明确反对采样验证，要求完整验证
- **测试口径注记（2026-10-21 四十六次刷新，历史口径）**：10-21 八项裁决执行批（`LOG.md` 本批条目）——**3900 收集（250 测试文件）/ 3898 通过 + 2 跳过 / 0 失败**（轨迹 3627→3900 本轮 +273）。前端 vitest **217/217**（33 文件）+ ruff 0 错。**徽章 4116**（3898 Py + 217 FE + 1 标记）；**本轮未部署**（R2 用户裁决）。🔑 **分块清单必须 `find` 递归生成**（漏 `tests/api/` 少 32 例的实证教训在案）。2 跳过 = `test_cache.py:297 Redis not available`（环境相关，非回归）+ `tests/golden/test_golden_set.py:48`（live_llm 用例默认不跑，设 `GOLDEN_LIVE=1` 才跑）。（工作树含未提交新文件，引用 3900 须注明。）
  🔴 **2026-10-27 拟人化实施批刷新**：实测 **3988 收集 / 3982 通过 + 2 跳过 / 4 失败**（四分块 1045+1110+912+915 通过，与 collect-only 精确吻合）；**4 失败为既有问题**——byok api_base 校验 ×2 / w1 无主卡 / sweep 503，均已用 `git stash` 在 HEAD 原样复现、**非本批引入**，待专项批次清理；徽章口径 = 通过数 **4200**（3982 Py + 217 FE + 1 标记）。
  🔴 **2026-10-27/28 话题状态机实施批刷新**：实测 **4180 收集 / 4174 通过 + 2 跳过 / 4 失败**（4 失败同上：byok api_base ×2 / sweep 503 / w1 无主卡，HEAD 复现、非本批）；前端 **251/251（36 文件）**；ruff 0；徽章口径 = **4426**（4174 Py + 251 FE + 1 标记）；**未部署**（回滚 `topic_state.enabled=false` / `llm_judge_enabled=false`）。
  🔴 **2026-10-28 收口批刷新（历史口径，已被 v1.62 取代——见上方 §4.3 首条）**：实测 **4262 收集 / 4256 通过 + 2 跳过 / 4 失败**（四分块门 958+1180+1097+1021 通过，第四块 4 失败；**既有四失败已由 4A `1d444d8` 清零**——定向复跑三文件 46 通过 0 失败，残留 4 个与既有族同文件域、身份主控复核中）；前端 **251/251（36 文件）** + tsc 0；ruff 0；徽章口径 = **4508**（4256 Py + 251 FE + 1 标记）；⚠️ 工作树含 10 个未提交实现文件（4B/4C 实现部分），引用本口径须注明。
  ⚠️ **基线口径纪律（2026-10-11 实测，10-12 定性收口）**：**角色卡数也是基线的隐藏变量**。`config/characters/` 被 `.gitignore:120` 忽略、**内容不随 git 复现**；`tests/test_persona_injection.py` 按 `Path("config/characters").glob("*.json")` 参数化 → **用例数 = 2 × 卡数 + 13**（2026-10-11 实测 42 张 → 收集 97）。当时 **42 张 = 41 策展卡 + 1 张 `99c89c39.json`（user_id=1 的林挽夏克隆变体，与前两张林挽夏 md5 互异，10-09 19:36 落盘）**——第 42 张已定性，差异复核**关闭**（2026-10-12 主控裁决，此前「待复核」作废）；**现行 41 张**（2026-10-30 批 7 复核：`99c89c39.json` 已不在盘、gitignore 无轨迹、消失时点不明，非批 7 删除，见 v1.61/LOG 登记待定性）；引用基线按 **41 张**声明（历史：09-20 该目录曾为空致收集塌缩 1356，后按用户指令从服务器逐字节恢复 41 张）。
  🔑 **引用任何基线都必须同时声明两件事**：① **角色卡是否在位 / 卡数**；② **工作树是否含并行窗口在制品**（与"passed ≠ collected"同类）。缺一则数字不可比。
  ⚠️ **已知环境问题：单进程整跑 `pytest -q` 会在 30%~97% 之间的**随机位置**停住**（三次实测分别停在 `test_integration.py` / `test_web_enricher.py` / `test_llm_providers_routes.py`），且这三个文件**单独跑全部通过** —— 属聚合态资源问题（疑似前序用例泄漏线程/事件循环），**不是某个用例失败**。分块跑可稳定复现完整基线，推荐工作流：
  ```bash
  files=$(find tests -name 'test_*.py' | sort)
  for i in 0 1 2 3; do echo "$files" | awk -v i=$i 'NR%4==i' | tr '\n' ' ' > /tmp/c$i.txt; done
  for i in 0 1 2 3; do timeout 200 env PYTHONPATH= python -m pytest $(cat /tmp/c$i.txt) -q -p no:cacheprovider; done
  ```
  四块结果相加应等于 `--collect-only` 的收集数（当前检出 **4500** = 2026-10-31 本批实跑；收集数随 `config/characters/` 卡数浮动 = 2 × 卡数 + 13，**现役 41 张**；⚠️ 分块清单须 `find` 递归生成，`tests/test_*.py` 四段 glob 会漏 `tests/core/`；🔴 分块运行期间禁跑任何其他测试——并行 pytest 会互相污染共享 data/ 与锁，失败数随并发漂移）。
- 验证报告：归档到 `docs/`；测试基线历史口径以 `LOG.md` 各批条目为准（原根目录 `pytest_*_baseline.log` 三件已不存在，死引用于 2026-10-11 对齐批删除）

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
| 一键启动 | `python main.py` 或 `python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000` + `cd frontend && npm run dev` |
| 部署打包 | 统一走 `deploy/`（systemd + nginx 模板 + remote_deploy.sh） |

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
| L2 | 微信接口易变 — `wechat_direct/` 依赖的微信 iLink 机器人平台接口会不定期变更/失效（原「微信 web 协议」系 iLink 化前旧口径），必须有版本追踪与失效快速响应机制，参考 `shisi/` 上游更新 |
| L3 | 多用户记忆必须严格隔离 — 三层记忆（`context/` 与 `shisi/memory/legacy/` 生产路径；原 `memory_ext/` 已于 2026-09-28 整删，原则沿用）必须按 user_id/身份组（D15）隔离，任何串扰都属严重 bug |
| L4 | LLM 输出 JSON 必须 Schema 校验 — 使用 Pydantic 双重校验，避免 JSON 解析失败导致角色/工具调用中断 |
| L5 | 角色卡一致性 — `config/characters/` 卡文件与 `my_character/` 运行时人格必须保持同步，避免角色人格漂移（旧顶层 `character_card/` 包已删，见批6b 项10） |
| L6 | 语音合成优先级 — **MiMo Cloud TTS 为唯一引擎**（2026-08-28 起；Edge-TTS / 本地模型已删除）。不可假设单一源永久可用，故障时走可观测告告警而非静默切换到已删除引擎 |
| L7 | 全量测试基线必须保持通过 — 任何改动前先跑基线（当前口径 **4256 通过 + 2 跳过 / 4 分块门残留失败 / 收集 4262**，42 卡，分块跑法见 §4.3），改动后比对 `LOG.md` 最近批次口径，回归即阻塞 |
| L8 | 临时脚本必须清理 — 完成任务后清理一次性脚本与 `deploy_payload.tar.gz` 等构建产物，避免代码库膨胀 |

---

## 8. 并行纪律（多窗口 worktree 协议，2026-08-28 增补，适配自 psd-framework §4）

1. **新窗口一律 worktree 开工**：`pwsh scripts/new_window_worktree.ps1 -Name <窗口名>`；此后读写只在自己的 `..\ai-girlfriend-<窗口名>` 内；分支 `wt/<窗口名>`
2. **主检出只做协调合并**：主检出保留给协调与 merge 收编（`git merge --no-ff wt/<名>`）；收编前必须跑主检出回归门（pytest/vitest/E2E/对齐四项）
3. **白名单提交**：产物落盘即提交；只提交任务包白名单内文件，精确 `git add`，禁用 `git add .`
4. **data/ 与 frontend/node_modules 为 Junction 共享**：生成物必须带窗口前缀或唯一 seed；冲突以后提交者重命名为准
5. **跨窗看板**：一切跨窗信息写 `docs/board/BOARD.md`（工具 `scripts/window_board.ps1 -Append/-Tail`）；开窗先读看板再读 `docs/HANDOFF_REPORT.md`（当前状态单一真源；原 `TASK_PACKAGES.md` 四窗口 09-15 全闭环后已随 09-29 清理批删除）
6. **记忆库双写**：重大裁决写 BOARD 同时 memory MCP（agent_id=shared）入库
7. **收编门禁**：窗口完成自检（窗口内 pytest+vitest 绿）→ 协调者 merge --no-ff → 主检出回归门 → 卸窗脚本
8. **🔴 Owner 唯一制（2026-09-15 事故驱动，血泪条款）**：
   2026-09-15 凌晨，主检出（`D:\Desktop\ai-girlfriend`）工作树被两个窗口同时改写，导致主控写入的 `docs/board/BOARD.md` 被回滚 **3 次**、`docs/verification/` 被整个删除、`tests/test_wechat_connector.py` 的 232 行版本被打回 138 行。**根因不是某个命令，而是没有任何文件有唯一 owner。**
   - **任何文件在同一时刻只能有一个 owner 窗口**；owner 写在 `docs/board/BOARD.md` 的窗口登记表里，改之前先查表
   - **`docs/board/BOARD.md` 的 owner 恒为「主控」**（原 `TASK_PACKAGES.md` 已随 09-29 清理批删除）；其它窗口如需追加，**一律追加到追加区**，且**不得改动登记表与他人的历史条目**（保留审计线索）
   - **`tests/**` 的 owner 恒为「W4 验证窗口」**；W3 只改实现代码，**实现改完由 W4 复核收编**
   - **主检出（`D:\Desktop\ai-girlfriend`）工作树不是共享草稿区**：窗口一律在 `..\ai-girlfriend-<窗口名>` 内读写；确需改主检出，须先在 BOARD 声明并取得主控同意
   - **主控写入即提交**：主控对主检出的任何写入，**必须在同一帧内 `git add` + `commit`**（必要时 push）—— 写而不提交 = 等着被回滚
   - **恢复类操作（clone/checkout/reset）前必须先看 `git status`**：若工作树存在未提交的后置成果，**先把它们拷出或提交**，再执行恢复

---

## 9. 审查修正（2026 安全审查 P0/P1 落地）

> 依据：`MiMo代码审查任务` 报告 v2（F-crit-1 / F-high-1..3 / F-med-2..4）。以下为代码与文档已对齐的硬约束。

### 9.1 认证与启动 fail-closed

| 项 | 规则 |
|----|------|
| `JWT_SECRET` | **非显式 dev 必填**（≥32）。生产/未标记/test 等环境无密钥 → `api/auth_jwt.py` **拒绝启动**。公开 DEV 兜底密钥仅当 `AI_GF_ENV`/`APP_ENV`/`ENV` 显式为 `dev`/`development` 时可用，并打印高强度警告。生成：`openssl rand -base64 48` |
| `API_KEY` 占位符 | 公开占位符（含 `CHANGE_ME_TO_STRONG_RANDOM_KEY_32_CHARS_MIN`）在 **API 认证启用且非显式 dev** 时 → `main.py` / `app_factory` **fail-closed 拒启动** |
| 环境真源 | `api/runtime_config.py`：`AI_GF_ENV > APP_ENV > ENV`；`is_production()` / `is_explicit_dev()` 为唯一判定。`main.py` 不再只看 `APP_ENV` |
| `.env` 加载顺序 | `main.py` 在 import `api.*` **之前**加载 `.env`（auth_jwt 在 import 时读环境） |

### 9.2 供应商密钥不入 git

- 管理端写入的 LLM `api_key` **只**写到 `config/llm_providers.local.json`（已 `.gitignore`）或环境变量。
- tracked 的 `config/llm_providers.json` **保持 `api_key: ""`**；运行时由 local overlay / env 注入。
- 运行时读取：`llm_provider/multi_provider_gateway._load_providers_config` 会合并 local 密钥。

### 9.3 公开仓部署敏感信息

- **不要删除** `deploy/` 脚本；模板中的生产 IP/账号已改为占位符/环境变量（`DEPLOY_HOST` / `DEPLOY_DOMAIN`）。
- **若历史曾使用** `139.199.199.174` 或相关 SSH 凭据：**轮换凭据**，复查该主机 SSH/nginx 暴露面；真实 IP/路径只放私密运维文档。
- 部署目标优先用环境变量注入，勿写回公开仓。

### 9.4 文档与磁盘一致性（残留项）

| 项 | 现状 |
|----|------|
| `start_all.cmd` / `deploy_ai_girlfriend.bat` | 2026-08-28 已删除；AGENTS §2/§5 已改写，勿再引用为入口 |
| `config/characters/` | **`.gitignore` 忽略、不入公开仓**；文档称其为角色卡真源，部署需单独投递（私有包/服务器本地），克隆仓不会自带 |
| `config/system.yaml` | tracked 默认 `env: dev` / `debug: true` —— **仅本地开发**；生产部署必须用 env 覆盖或提供 prod 配置，`debug: false` |
| 语音引擎 | MiMo Cloud TTS 唯一；L6 教训已更正 |

### 9.5 SECURITY 摘要（公开仓）

1. 轮换任何曾与 `139.199.199.174` / 旧 deploy 路径一起使用过的凭据。
2. 生产启动检查清单：`AI_GF_ENV=prod`、`JWT_SECRET`（≥32）、强随机 `API_KEY`、`API_KEY_ENABLED=true`、`debug: false`。
3. 禁止把真实供应商 key 提交进 `config/llm_providers.json`；CI/人工 review 见 `api_key` 非空即拒绝。
4. 历史 commit 曾含 `.env` 占位符（`4d67ca2`）；若当时写过真实 key，必须轮换。

## 10. 修订历史

| 版本 | 日期 | 变更 |
|------|------|------|
| **v1.62**（表述分离 + 用户主权 per-user 化批） | **2026-10-31** | **8 域 · 7/8 独立对抗验收通过；修复批 6/6 通过 · 全量回归通过 · 已三端部署 @ `43aeaae`**：**psych** 五端点 admin 全量 / 普通用户仅本人会话 scope；**proactive-backend** 新增 `utils/proactive_config.py`（879 行，三级合并 + `user_proactive_config` 表）+ 4 个 `/api/user/proactive*` 端点（training 23→27）；**proactive-frontend** 消息 tab 对全体用户开放（个人覆盖/继承全局徽章，+11 vitest）；**wechat** 遗留全局端点保持 admin + 新增 `wechat_direct/media_upload.py` 出站 CDN 上传（14 例）；**clone** 数据集 `data/clone/<user_id>/` per-user 分区（8 例）；**voice** 音色 owner 单一规则 + 角色音色写门 `require_character_write` + 只读审计脚本（23 例；全局 `switch-voice`/`set-engine` 保持 admin；公共卡写侧 404 产品级回退 deferred）；**topic** 快照读面本人会话白名单（+6 例）；**docs-naming** 「训练→风格提取」表述分离（`training.ts` / `client.ts` / CODEMAPS / §2 Owner Map；目录名/类名/路径/字面量零改）。**端点 249/205 → 253/207**（+4 全部为 user/proactive）；测试 **276 文件 / 4498 通过 + 2 跳过 / 收集 4500 / 0 失败**（3 个存量失败族由 `tests/conftest.py` users 主库隔离根治，非删测试）+ 前端 **47 文件 354 例** + tsc 0 + ruff 0；徽章 **4853**；角色卡 **41 张**。**已三端部署 @ `43aeaae`（2026-10-31）**；deferred：voice 公共卡前端控件/`(character_id,user_id)` 二级键、风格提取 LLM per-user 化、消息 tab 新用户 UX、存量越权音色绑定审计不迁移。 |
| **v1.61** | **2026-10-30** | **前端体验批7·遗留收尾（D20 M 级四项清零，本地全链提交未部署）**：**NEW-20 高危管理操作二次确认**（三处危险方向确认+后果文案+Modal 顶层 ESC 栈+供应商页回退链徽章；启用/开启路径零摩擦）/ **NEW-22 用户列表排序全页化**（后端 sort/sort_dir Literal 白名单 7 键+id desc 次键，前端透传删伪排序）/ **NEW-31 组件库收编**（SubTabBar 删除+DELETION_LOG 补登、ConfirmDialog Button 化、RolesPage/SettingsVoice EmptyState+Skeleton 收编）/ **NEW-32 设计 token 统一**（Slider/UserBadges/Breadcrumb/RoleSettingsTabs 四病灶+DesignTokens 守卫测试）。工作流 dwfrun-d95bc40b 实施（任务卡复核 11 条冲突回炉一轮）；主控收编前窗停放工件并拆除 F1 测试雷（HelpGuidePage 3 用例 mock 修复）；收编链 `69aecd2`（F1/F4/F5 收编+F9 口径更正）→`f9ccfd4`（F2 测试半件）→`d182b6c`→`c6ed735`→`65e0fee`→`9815c27`（NEW-22，含 F2 守卫 hunk 随件入账）；`_mutation_check.py` 过期突变脚本删除（ruff 恢复 0）；对抗验收 2 缺陷修复（DELETION_LOG 漏登 SubTabBar / 排序测试断言缺陷 14 红→28 绿）+突变验红 4/4。**真实测试基线**：后端 269 测试文件四分块 **4297 通过 + 2 跳过 / 3 个存量失败族**（test_api_routes sqlalchemy / test_w10_metrics_multiproc 503→404 / test_w3_scheduler_state Depends，均 HEAD 对照实证非本批，登记待专项批次）——v1.59.1「4 失败=骰子闸时区族」口径已过期；ruff 0；前端 **47 文件 342 例全绿**；tsc 0；端点无新增（NEW-22 仅加查询参数，全口径读数以 CODE_GRAPH 回扫为准）。**部署未做**（随用户裁决）；登记②扫码 e2e spec 仍开放。 |
| **v1.60** | **2026-10-29** | **前端全站体验批 1–6（D20 六批全开工，本地七提交未部署）**：批1 P0止血 `908db1c`（顶栏身份+退出登录/工具开关 admin 门+只读徽章/记忆整理全局写确认//intro 文案对齐 D13/微信页绑定账号+当前聊天角色/三死卡删除/三死映射删除）→ 批2 契约修复 `38e517c`（NEW-7 主动历史 kind/created_at 归一化+本地日界；NEW-5 状态中心五处：count_facts 真源+owner_prefix 隔离计数/affinity_name 档名/fact→content/日记纯日期；V2-6 Invalid Date 收敛；NEW-9 错误态不伪装空态）→ 批3 admin 数据面 `6d57e30`（NEW-21 operator_audit 审计先行+8 留痕面+审计面板；V2-1 admin 数据面**默认收窄**+operator_prefs 全局运维视图开关+顶栏横幅+user_key→用户名归属标注底座；NEW-11 用户下钻抽屉（账号+通道+强制断开）；NEW-12 humanize 接线数字 id 还原器+用户下拉）→ 批4 首日旅程 `cbdaf96`（NEW-4 扫码状态机：连续失败 error 支路+is_expired 连接轮询检查+15s 看门狗；NEW-16 初始角色告知；NEW-13 BYOK 反馈环：新端点 /api/user/llm-config/test+测试连接+byok=1 引导+已保存密钥徽标+禁用供应商拦截+缓存控件置灰+微信端配置类失败指引；V2-4 /help 教学页 13 模块卡+进度持久化+driver.js 导览；V2-5 公告系统：announcements+announcement_reads 两新表+active 读面+admin CRUD 落审计+顶栏横幅+管理段）→ 批5 个性化与 IA `a91ed34`（V2-3 侧栏「偏好与设置」+逐项 desc+消息 tab 高级运维折叠+θ 白话；NEW-8 useActiveCharacter 根治静默回退+StatusCenter 按 URL 取数；NEW-10 非活跃角色设置入口+模板入口；NEW-14 语音四透明化（死滑条标注/真值选中/全局切换确认/命名+音频口径）；NEW-34 个人偏好自助：/api/user/humanize GET/POST/PUT/DELETE（token 派生键、客户端自报归属忽略）+SettingsAccount 区+回复模式自助端点首次接前端）→ 批6 收尾 `69c1d71`（NEW-17 最后管理员守卫；NEW-15 画像可读化；NEW-18 登录错误本地化；NEW-27 换绑自助断开入口；NEW-23/24/28/29/30 S 批修；NEW-25 按裁决保留训练端点=登记）。**新端点 14 个**（llm-config/test×1 + user/humanize×4（POST/PUT 双装饰器=2 路由）+ announcements×6 + admin global-view×2/audit×1；9 子路由 92→**97**（misc 17/training 23，test_api_routes 钉死），加新路由 6+admin 3 = 全口径 **106**——对抗验收 F9 更正，原记 10 个/101 系算术错误）；新表 4 张（operator_audit/operator_prefs/announcements/announcement_reads，纯新增 create_all 幂等）；新依赖 driver.js（MIT）。口径：vitest **303/303（42 文件）**、tsc 0、ruff 0；后端定向 read_gates 26/humanize 37/w1 23/api_routes 16/last_admin 4/announcements 5/llm-config 15/channel_slot 全绿；**部署未做**（七提交本地领先，随用户裁决）；CODE_GRAPH/HANDOFF 回扫与 M 级残留（NEW-20/22/31/32+扫码 e2e spec）登记为收尾批遗留。 |
| **v1.59.2** | **2026-10-28** | **工作区整理与真源对齐批（零代码变更）**：CODE_GRAPH 整本回扫升级 **v3.8.35**（§1.1 全表重测 + 历史版本要点压缩归档至 LOG）+ **CODEMAPS 六件**（DATABASE 话题三表 schema 逐列抄实 + `user_reply_mode` 表增补、INDEX 规模 590 py/138 TS、MODULES proactive 14/utils 26、ARCHITECTURE/BACKEND/FRONTEND 热路径与读数同步）+ **README / docs README**（结构树刷新 + 图册行指向 v3.8.35 + reports 登记补漏）+ **FUNCTION_INVENTORY / DECISION_LEDGER**（账本新增五行：D18 话题状态机 / BreezeTTS2 暂不引入维持 / D19 四项跟进批三裁决 / R1 继续挂起 / 沉浸式方括号热修；清单 §I 新增三组 **15 条功能点** N-TOPIC-1~6 / N-DICE-1~5 / N-BEHAV-1~3）+ **READING_REPORT 时效注记**（24 件历史快照头部注记，已随 `958da31` 提交）+ **清理 6 项**（未跟踪 gitignore 缓存/构建产物：`.pytest_cache/` / `.mypy_cache/` / `.ruff_cache/` / `frontend/dist` + `data/` 与 `.aoci/` 下临时物各一；data/ 运行时 34 项与 .aoci 正式内容完整无损，零 tracked 删除）+ **登记候选 1 项**（`docs/verification/` 62 张 PNG 共 18MB 验证截图存证，勘察结论=保留，是否瘦身归用户裁决）。口径：收集 **4263**（266 测试文件）/ 端点 **235 APIRoute**（`len(app.routes)`=**239**）/ 徽章 **4508**；对齐复核残留 **0 条**。 |
| **v1.59** | **2026-10-28** | **收口批（话题状态机部署 + 四项跟进批）**：话题状态机批 `3fe2771` 已提交+推送+三端部署（默认开启，回滚走管理端显式 `topic_state.enabled=false` / `llm_judge_enabled=false`）；四项跟进批已本地提交（后补交实现文件一批）、推送由收口流程统一执行、**未部署（待用户裁决）**——4A 测试债清理 `1d444d8`（四既有失败清零：byok api_base×2 / w1 无主卡 / sweep 公共卡口径 503→404，定向复跑 46 通过 0 失败实证）；4B CSP 行为蒸馏移植 `5dff923`（AI 人设生成增 behavior_patterns/honesty_boundary + aggregate「# 行为规则」紧凑段 + schema 往返）；4C dsh-speak-up 零 token 骰子闸 `aabc867`（掷骰先于 LLM，默认关闭+dry_run 默认开，管理端与前端配置面）；4D BreezeTTS2 本地部署设计 `c0a4d2c`（仅设计文档，遵守暂不引入裁决）。🔴 实现文件漏提交缺陷登记（10 文件 809 行留工作树，主控收口后补交）。测试 **4262 收集 / 4256 通过 + 2 跳过 / 4 失败**（分块门 958+1180+1097+1021；既有四失败已清，残留 4 个身份主控复核中）；前端 **251/251（36 文件）**；ruff 0；徽章 **4508**。 |
| **v1.58** | **2026-10-27/28** | **话题状态机实施批（未部署）**：Q1–Q6 全量落地——Q1-B 确定性状态机 + 每 4 轮稀疏 LLM（8s 硬超时、失败保留原状态）；Q2 B+C 状态块 + 软化接续；Q3-A parked 复提三重闸（≥6h + 非静默 + judge allow）；Q4-B 全部状态 7 天 TTL；Q5 免费 API 不设每日上限；Q6-B 多 active 并行上限 3。新增 `utils/topic_state.py`（三表六 API）/ `proactive/topic_recall.py` / `api/routers/topic_routes.py`（3 端点）/ 前端「话题状态」区；主动 `topic_plan`（skip 也存 → 重启读 → 到点注入 → 发送成功消费）。测试 **4180 收集 / 4174 通过 + 2 跳过 / 4 失败**（4 失败=既有 byok api_base×2 / sweep 503 / w1 无主卡）；前端 **251/251（36 文件）**；ruff 0；**未部署**（**默认开启**，回滚走管理端显式 `topic_state.enabled=false` / `llm_judge_enabled=false`）。 |
| **v1.57** | **2026-10-27** | **拟人化实施批（1/5–5/5，未部署）**：G-1 声明式字段表 `HUMANIZE_FIELDS`（DEFAULTS/INT_BOUNDS/WRITABLE_FIELDS 全派生）+ G-2 `user_humanize` 按用户覆盖（用户→全局→默认，键缺失=继承；API GET/POST/PUT 作用域 + DELETE 清除）；P0-B 阅读延迟纯函数 + 活跃响应曲线 + typing（iLink `sendtyping`，未真机实测，失败静默 no-op）+ supersede（延迟期新消息静默取消）+ 无 typing 渠道 5s 封顶；A-4 主动 post-draft judge（fail-closed，只判发/不发）；Q4 沉浸式可选口误（`typo_rate` 默认 0）；Q6 拆条上限 2–4 段；观测日志 `[humanize][step=…]`/`[proactive_judge]`。测试 **3982 收集 / 3976 通过 + 2 跳过 / 4 失败**（4 失败=既有：byok api_base×2 / w1 无主卡 / sweep 503，HEAD 复现，非本批；待专项清理）；端点 **89**（+DELETE）；ruff 0；**未部署**（回滚=read_delay_enabled / typing_enabled / judge_enabled / typo_rate）。 |
| **v1.56** | **2026-10-21** | **八项裁决执行批（v1.56）**：用户就聊天质量后续给出**八项裁决**，按「取证设计 → 设计复核 → 代码实施 → 独立复验 → 认知层补全 → 全量校验 → 收口」执行。**R1 降级三方向讲解**（A 降级切同档位模型 / B 降级轮走保守短回复 / C 降低降级轮 prompt 复杂度）——**仅讲解，用户尚未拍板**；**R2 暂不上生产**（用户裁决本轮**不部署**，代码留在工作树）；**R3 主链工具与记忆相结合**（保留 L0/L1 工具闸 + 系统注入记忆架构，新增**能力声明段**按 registry 真实可用性渲染 `capability_flags={"memory_recall": …}`（`_memory_recall_available`）+ 启用 `memory` 工具打通 L0→L1 检索 + 内部标识出站剥离，**L1 仍管副作用**）→ 5 文件；**R4 主链改真流式**（`chat.stream_mode: true` 取代「攒稿→定稿→8 字符切块」：逐 token 喂 `utils.stream_guard.IncrementalReplyGuard`，完整句立即下发、不完整尾巴永不下发，流末未发余量交 `_finalize_reply` 唯一全文终裁，**已发前缀永不回滚**；需全文位置的规则延迟到终裁、截断类规则=终止上游+丢弃全部未发）→ 7 文件；**R5 AOCI 与代码图谱补全**（`aoci.code.txt` 条目去重/修补，`CODE_GRAPH.md` 整本重建 **v3.8.33（2026-10-21 重建批）**）；**R6 黄金集立项**（新建 `tests/golden/`：8 用例文件 / **45 条用例** + `runner.py` + `judges.py` + `test_golden_set.py` pytest 入口；确定性优先、判定全部委托被测真源、LLM 判定不计基线）→ 12 文件；**R7 模式显式切换·按用户隔离**（全局 `reply_mode` 降为默认 + per-user 覆盖表 `user_reply_mode` + 一轮**单次解析透传** + admin 作用域 `reply_mode_scope=global|user` + 用户自助端点 `GET/POST /api/user/reply-mode`）→ 23 文件；**R8 部分闭环为何卡住**（③话题状态机、⑤爱语架构差距、Q6 串行跳数、Q7 主链工具披露此前停住**非技术阻塞**，是被归类为需用户裁决的架构/产品决定；R3/R4 已推进其中两项，其余仍未决）。**实施 4 项全绿**；**独立复验 4 项中 3 项打回**（R4/R6/R7），复修后全绿（R6 P1=live 通道完全不可用、**R7 P0=admin「指定用户」写入键形态与运行时读取键不一致——功能无效却回执成功**）。测试 **3900 收集（250 测试文件，42 卡）/ 3898 通过 + 2 跳过 / 0 失败**（2 跳过=`test_cache.py:297 Redis not available` + `tests/golden/test_golden_set.py:48` live_llm 默认不跑）、徽章 3844→**4116**、vitest **217/217**（33 文件）、ruff 全仓 0。**未部署**（R2 用户裁决）。**遗留**：R1 三方向待拍板；agnes 供应商侧抖动仍在；黄金集初建（覆盖效果待后续运行验证）；架构级差距（话题状态机、爱语式完全自主）仍未决；AOCI 剩余 backlog |
| **v1.55** | **2026-10-20** | **聊天质量二轮·质量悬崖根治**（用户复报八问题并追问「已经更新过那么多次了，还是这样？？问题还越改越大？」）：先核实部署状态——**服务器停在 `1fd0906`（v1.53），v1.54 从未上线**，部分现象是旧代码。生产取证 app.log 17:45–17:53 + chat_history 2846–2875 + prompt_slots 15690/15693：17:51:31 agnes POST 200 → 17:51:32 熔断 → 智谱接管 → 17:52:29 落库 2873（逐字复述）→ 17:53:40 落库 2875（括号+归属翻转+元解释）；当日 agnes **24 调用/15 失败/10 熔断**，报障回复**全部由 `glm-4.5-flash` 写出**。**真根因=修复被降级路径系统性抵消**：每批 prompt 修复让 system 变长 → agnes 越慢 → 越易被 15s 误杀 → 阈值 1 次即熔断 300s → 流量落弱模型；实测延迟 **1.11/2.36/3.39/10.47/10.94/24.63/37.40/51.69s**，15s 误杀 5 次（含 3 次健康响应）。**R1 质量悬崖**：agnes 超时 15→**40s**、熔断阈值 1→**2**、冷却 300→**120s**、`primary_only` 链失败一次即开闸、主链外层 `wait_for` 30→**60s**、降级成功落 `quality_degraded` 日志；**R2 第二泄漏通道**：`mes_example` 同时被索引进知识库（生产 `knowledge_snippets[0]` 实测带「（想了想）」），`character_aggregate.knowledge_context` 与 `persona_service.rag_context` 两处注入点均先 `strip_stage_directions`；**R3 守卫补漏**：归属翻转句级删除（把用户台词说成「我刚说的」）+ 裸「你呢」整行形态（`re.MULTILINE`，旧式必带问号 1/1 漏网；陈述句「你呢，别太累了」不误删）；**R4 历史回灌净化**：坏回复已入库为 assistant 行、下轮被当「我自己说过的话」few-shot = 自我强化回路；注入前就地净化不改库；**R5 话题锚点**：`_extract_user_anchor_sentences` 只取 user 行完整短句（旧关键词袋实测只给 `['的话','哈喽']`）。**爱语对照**：43 条里 反问/追问/话题/复述/确认一下/你呢 全为 0，差距是「把像真人拆成互相竞争的硬指令」，继续加规则会继续恶化。测试 **3627 收集（237 文件，42 卡）/ 3626 通过 + 1 跳过（Redis 环境）/ 0 失败**（893+976+933+824 与 collect-only 精确吻合）、徽章 3833→**3844**、vitest 217/217、ruff 0；5 个钉旧参数值的测试连带改写为**语义下界**。**`8eb4abe` 三端一致已部署**（health/ready 200、六文件 blob 一致、生产端到端 3/3 agnes 成功）。**遗留**：agnes 抖动仍在；降级质量差距是产品级问题（三方向待裁决）；话题非状态机；模式开关全局单例；黄金集未建 |
| **v1.54** | **2026-10-20** | **聊天质量八问题批·五源根治**（用户报障八点：「小说模式误入 / 复述我的话且分不清谁说的 / 没有话题意识 / 元记忆解释『我想确认一下你是不是真的记得』/ 虚构反问『你呢，有没有什么特别想吃的？』/ 仓库太大看不清全貌 / 引入 AOCI / 全链路诊断为何达不到爱语水平」；用户明确纠正「括号动作是小说模式的设计部分，缺陷是模式边界」）：生产取证（chat_history 2851–2875 + app.log，`reply_mode="immersive"` 下仍出括号）→ **R1 模式边界泄漏**（`character_aggregate` 无条件注入 `mes_example`——42 卡中 37 卡含括号动作且文案自称「示范格式」+ `first_mes` 同样 + 12/42 卡 `creator_notes` 自带括号/神态/心跳格式要求）→ 沉浸式先 `strip_stage_directions` 再注入（剥空不注入）、示例标题改「仅示范说话口吻；格式以【回复模式】为准」、post-history 位置追加**格式优先级声明**（不整段丢 creator_notes 以免连带丢人设规则）；**R2 过度指令**（`reply_mode.py`/`TOPIC_CONTINUITY_RULE` 旧写「需要时反问一句把话题往前推」「自然追问一句」「连续追问比抛新话题更像真人」=虚构反问的指令级根因）→ 删激励改「没有可指认的由头就不提问」+ 禁逐字复述 + 禁解释记忆/确认意图；**R3 出站语义终审**（`terminal_reply_guard` 新增 `reply_mode` 参数与四道守卫：逐字复述首行剥除/元记忆解释与零信息问卷句级删/无由头「你呢…？」截断（**主动链 user 为空不启用**防误伤）/沉浸式括号星号动作段剥离（小说模式保留）；命中落 `reply_guard_hit` 结构化日志不落原文）；**R4 供应商静默降级可观测性**（agnes HTTP 200 空 content 时旧实现先 `mark_success()` 再 `content.strip()` → AttributeError 吞进 fallback 污染健康统计且无日志 → 改为 `mark_success` 前判空抛错含 choices/reasoning_only/message_keys + 主/备失败均 WARNING 带响应体 300 字）；**R5 话题噪声与说话人指认**（生产话题包实测 `['的话','哈喽']` → `_is_topic_noise`；话题块「## 当前话题（请沿着聊下去）」改「## 对方最近在说（先接住具体内容，别另起炉灶）」+ 追问条件）；**爱语对照**（`aiyu_prompts_full.json` 43 条：反问/追问/话题/复述/确认一下/你呢 全为 0——差距是「把像真人拆成互相竞争的硬指令」）。测试 +32（语义守卫 25 / 空内容可观测 6 / 话题渲染 1）+ 5 文件断言适配（含 `test_main_stream` 回显型 mock 与复述守卫职责分离）→ **3616 收集（237 文件，42 卡）/ 3615 通过 + 1 跳过（Redis 环境）/ 0 失败**（四分块 824+882+976+933 与 collect-only 精确吻合）、徽章 3802→**3833**；ruff 全仓 0 错。**未部署**，`1bba5b2` 代码已提交，服务器 pull/上线归用户裁决。**遗留**：真实模型改善幅度待部署后黄金集；半角括号序号潜在误伤；关键词袋非真话题状态；模式开关全局单例；生产 agnes 历史坏响应体未取得（本批日志上线后可取证） |
| **v1.53** | **2026-10-19** | **时间感知 3.0 批·会话分段渲染根治**（用户报障截图：51h 后「你**刚说的**那个咳」+翻已翻篇旧账；裁决「先排查相关代码，而后找行业解决方式，或者参考爱语逆向的成果」「该重构就重构，大刀阔斧的改造」）：**诊断先行三重取证**（生产 chat_history+app.log+生产 venv 复现）——行尾「距上一条 2 天」注记+逐行 `[MM-DD HH:MM]` 前缀+锚点「2 天前」**三重信号全在**，agnes 主链仍说「刚」；根因=**R1** 时间信号结构性弱势（软约束在 15 条历史/1855 字符记忆注意力竞争下失败=hve-core 指令稀释机理）+**R2** 话题钩子无时间窗教唆（三天前「吃饭」进「当前话题请沿着聊下去」实锤）；**锚点取证无罪不动**；行业调研：SillyTavern idleDuration=本仓 v1.41 同级/grammyjs 对话超时终止/爱语逆向 2.8.8 **无任何时间机制**（反面印证），会话分段渲染系开源 companion 领域空白自研 → **P1** `annotate_history_time_gaps` 时段分段渲染（相邻行 ≥6h 或首/末行距今 ≥6h 插 `[时段分隔]` 头注「以下是约 X 前的对话，已结束」+末行隔夜尾注「旧话题视作翻篇以新开场为准」，对齐微信时间分隔条视觉语义；幂等守卫重入不叠；`SEGMENT_DIVIDER_MARKER` 单一真源进出站守卫词表防回显；clip 取尾部天然保护翻篇尾注）+ **P2** `extract_current_topics` 24h 时间窗（旧时段话题永不教唆，无 timestamp fail-open，orchestrator 传 now）+ **P3** `terminal_reply_guard` 新增 `last_turn_age_seconds`（>45min「刚才/刚刚/刚X动词」确定性降「之前」，呼应 v1.52 生成侧判死哲学；「刚好/刚巧/刚强」负向保护；主动/提醒链缺省 None 不动；主链 prepare→ctx→finalize 透传）。旧三测试文件关分段保索引语义（逐行前缀测归旧文件/分段语义归新文件，职责不混）。测试 3561→**3584 收集（235 文件，42 卡）/3584 通过 / 0 失败 / 0 跳过**（四分块 829+993+882+880 精确吻合）、徽章 3771→**3802**；突变验红 3/3（分段永真/时间窗删/改写短路）；`1fd0906` 三端部署（生产 venv 冒烟：五旧段头注+翻篇尾注全现/topics=['哈喽'] 旧话题全出局/生产原句「刚说的」→「之前说的」）。**遗留**：「昨晚/昨天」等日期锚定词改写需日期级锚定解析（二期）；分段阈值 6h 全局常量未自适应 |
| **v1.52** | **2026-10-17** | **窗口死寂与零信息问候根治批**（用户报障「为什么更新后从未触发主动消息？？」+ 裁决「三个方向并行推进！！！！」+「客服腔要从生成源根治，不依赖拦截」）：**诊断先行**（agent_plane 账本+app.log+通道文件三重互证）——**更新被冤枉**：故障起点 10-16 08:11 比拟人化更新早 15h，根因=微信平台会话窗口 24h TTL 静默死（主通道 token 10-15 16:39 死→连续 11 次 prepare failed 白烧决策 LLM；第二通道连击硬闸+token 亦死；user=6 死窗 77 连败）；客服腔五条全为决策线自产（消噪批 F1 上线当天模型转战零信息泛问候，硬规则 11/13 系 prompt 软约束、候选竞争被 `hook_keywords` 门卡死）。**五方向合治**：A=`wechat_connector.token_window_state` 探针六态+烧决策前确定性闸（只拦确证 dead，missing/empty/error 宁宽放行）+dying 告警事件+投递失败分类 `_classify_deliver_error` 入账本；E=`_is_zero_content_greeting` 全分句零信息检测面（由头关键词豁免，宁宽勿误）+`decide_proactive` 输出契约终审（`generic_opener_contract`/`empty_message_contract` 翻转=生成侧判死非投递拦截）+硬规则 14（零由头不发优于空洞问候，生产原句负例锚）+空 hooks 显式标注；④=候选竞争摘 hook_keywords 门+空 message 契约独立账本；C=确定性 skip 每 user|reason 每日一条（`_emit_skip_event_daily` 四处同步，purge 键形须剥 reason 尾段）；B=conftest 隔离补洞 wechat 两路径（`CONTEXT_TOKENS_PATH`+`sessions_root` 重定向沙箱——`test_ilink_media_parsing` 曾把 wx_user_1 测试键写真实 data/，生产 09-22 冻结至今、本地 10-16 仍被写）+生产/本地污染文件备份后清除。测试 3338→**3554 收集（232 文件，42 卡）/3553 通过 + 1 跳过 / 0 失败**（四分块 941+821+776+983+tests/api 补块 32 与 collect-only 精确吻合；🔑 分块清单漏 `tests/api/` 少 32 例再实证 find 递归纪律）、徽章 3519→**3771**（vitest 口径 182→**217**/33 文件）；突变验红 4/4（契约门/keywords门/dead闸/降噪）；连带改写 3 处钉旧契约测试；`be8e886` 三端部署（生产 venv 冒烟：探针 owner2 dead 71h/owner4 fresh 1.3h 闸自然解除、生产原句判拒/具体句放行、无 keywords 候选回填、生产 tick 实证烧前闸命中+账本降噪生效） |
| **v1.51** | **2026-10-16** | **主动消噪批**（用户报障「今天的主动消息，让我早上七点吃早饭是什么意思？？这个太智障了吧」+ 裁决「F1/F2/F3 都要做」+「还是没有时间观念，让 LLM 回复时自然有时间观念、不强调不刻意」+ 引用反编译项目 `D:\Desktop\爱语逆向` 旁证）：生产取证（07:06 主动消息「起了 早上记得吃口热饭」=agnes 打卡式嘱咐；07:01 决策 reason 明写「等待 1 小时至 08:01 再观察」但 `wait_minutes=None` → 07:06 下一 tick 零约束反悔重判 should=True；07:12 用户反问后 AI 回复原文开头竟是「[主动] [10-15 18:27] 七点吃饭啊」=把历史注入行首元数据当格式抄进回复直达用户；对照爱语 2.8.8 反编译 `ProactiveMessageService.b()` 主动 prompt 仅四段人设拼盘、无时间注入、无打卡模板）→ **F1 禁打卡式嘱咐**（主动两约束段 + `llm_proactive.DECISION_SYSTEM` 硬规则 11 含生产原句负例锚 + 模板池 `care_meal` 打卡句重写为好奇/问候形态）+ **F2 决策反悔稳定闸**（`parse_decision` 在 `should=False` 且 wait 缺失/**≤0** 时从**完整** reason 提取等待语义兜底，N小时→N×60/N分钟→N/仅等待语义→30，中文数字**按值解析**「十/三十/四十五/十一」，兼收「一小时后再说/晚点再说」形态，提取先于 300 字展示截断，硬规则 12）+ **F3 系统标记剥除**（`terminal_reply_guard` **最前**（错误哨兵判定后）新增 `_strip_system_markers` 循环剥行首 `[主动]`/`[定时提醒]`/`[MM-DD HH:MM]`，词表由 `OUTBOUND_CHANNEL_MARKERS` 单一真源生成防漂移；`reminder_delivery._compose_text` 补齐守卫=守卫矩阵唯一缺口闭合）+ **F5 自然时间观**（主链 `reply_mode` 两指令 + 主动两约束段同步「你自然清楚现在几点、隔了多久没说话……但不要在回复里报时、不要点破「隔了多久」、不要解释你怎么知道的——时间感是底色不是台词」，保留既有 `TIME_REASONING_GUIDE` 防「把叙述钟点当正在进行」）。**对抗验收**（adversarial-acceptance skill，独立手段非复跑作者清单：病灶复现/执行位矩阵 9 形态/口径对账/**突变验红 4/4**）发现并修复 **7 处**（中文多位数字解析错「三十」→3=闸静默失效/守卫序洞「[主动] 十四」曾放行/假绿测试/wait≤0 边界/reason 截断先于提取/模板池打卡句残留/提醒线守卫缺位/双 owner 字面量）+ **M4 假阴性自纠**（grep 探测器只匹配注释/import → 改行为级 `_compose_text` 测试 4 例真命中）。测试 3284→**3338 收集（219 文件）/3337 通过 + 1 跳过 / 0 失败**（四分块 868+849+728+892 精确吻合；+54=禁打卡嘱咐 25/系统标记剥除 29）、徽章 3466→**3519**；ruff 全仓 0 错；`ac4c145`/`eaeee65` 三端部署（health/ready 200、4 worker；生产 venv 行为冒烟：F1 三处约束+模板池零打卡句、F2 生产原句 60/三十→30、F3 生产原文剥净+序洞拦下+误伤保护+二轮行尾注记剥净/正文中部不误伤、F5 指令在场）。**对抗验收二轮**（`eaeee65`）抓出并修复出站剥除漏网的行尾「（距上一条 X）」间隔注记（同机理注入元数据，旧只剥行首）+6 测试（突变验红 5/5） |
| **v1.50** | **2026-10-15** | **称呼根治批**（用户报障「又开始叫我夏夏了，又搞不清谁是谁了」+ 裁决「全面清洗 + 根治 + 不要简单打补丁」；用户追问「你的根治方案也不算是根治啊」）：全链路取证（「夏夏」不在任何角色卡/画像/知识库——模型从卡名「林挽夏」**自造叠字昵称**投射到用户；18:47 那条由旧代码+agnes 熔断后的智谱写出，晚于我们重启 9 分钟；旧三道防线全是黑名单——依赖用户发现/窗口不滑出/模型听话，三处结构性失效；**持久化幽灵钩子**：落盘挂在 write_profile_from_tool 仅画像更新时触发，session4 否认后从无画像写入→否认永不落盘）→ **治本=白名单唯一真源**（主控定稿 `utils/address_terms.py`：legal=画像昵称∪{你} 确定值，forbidden=双源否认∪**角色名派生词自动禁用**——林挽夏→夏夏/挽挽/林林/小夏等，无需用户发现）+ 包C 子智能体（主链/决策线统一注入【用户称呼】段，旧【被否认称呼】黑名单段退役+死接线删除）+ 包D 子智能体（`terminal_reply_guard` 新增第三类非法称呼呼语剥除——词边界不误伤「十七岁」「无名指」「我是十四」、`persist_detected_denials` 挂每轮对话回调=否认当轮落盘永久不再依赖画像写路径）+ 主控终审收口。生产两轮清洗 65 行（44+31，自动备份两份）：一轮 purge 脚本 34 行净化+占位 1+ase 8+facts 删 2，二轮全局替换占位符「旧称呼」31 行+facts #39（数字量词/无名氏排除保护）。测试 3233→**3284 收集（217 文件）/3283 通过 + 1 跳过 / 0 失败**（+51=称呼白名单 29/持久化闭环 18/既有增强 4）、徽章 3414→**3466**；子智能体突变验红 6/6+3/3（各抓 2+1 真缺陷已修）；`9639ff1` 三端部署（生产 venv 行为冒烟：session4 legal=你+forbidden 含夏夏小夏+剥除正确、session2 含十七无名、误伤保护全过） |
| **v1.49** | **2026-10-15** | **质量回调批**（用户报障「主动消息太AI太官方 + LLM 没有话题意识」+ 三问：ASE 能否角色卡驱动/称呼持久化与画像记忆是否真注入/是否派子智能体并做自我优化重构）：生产取证（轮转日志 agnes 55 次 vs 智谱 246 次=82% 流量走免费档；报障对话恰好全程在 agnes 熔断窗；`openai_compatible_provider.py:114` 自证健康响应 3.8~19.8s 而超时被压 8s）→ **A 网关与模型闸**（子智能体包A+主控终审）：agnes 超时 8→15s、`primary_provider_status()` 链首熔断态、scheduler 模型闸（熔断期主动消息跳过不降级，`model_degraded` 账本四处同步纪律）；**B 主动消息上下文统一真源**（子智能体包B+主控终审收口，用户指示「不重复补丁，该重构就重构」）：新建 `proactive/proactive_context.py`（完整版人设≤1200 字+沉浸约束，决策线/ASE 生成线/祝福润色三处统一消费，动态读零缓存=隔离天然成立）+ ASE 生成线角色化（`character_id_resolver` 闭包绑定 user_key，主路径重写为角色卡驱动，proactive.yaml 降级为可选覆盖位，空人设不冒充十四）+ 决策线画像升级（弃自拼投影改主链同款 `get_profile_prompt_block`，preferences 进 prompt）+ 否认称呼双源（`denied_names_for`=窗口+ledger 持久，「十七」根治）+ DECISION_SYSTEM 第 10 条（禁客服腔/禁共处/话题衔接）。测试 3205→**3233 收集（216 文件）/3232 通过 + 1 跳过 / 0 失败**（+28=模型闸 14/统一构造器 10/ASE 角色化 4 含双角色隔离断言）、徽章 3386→**3414**；旧值锚定测试 1 例适配（8s→15s）；`1df4921` 三端部署（生产 venv 行为冒烟：人设/约束/闸信号/超时四验通过） |
| **v1.46.1** | **2026-10-12** | **文档漂移清扫+五存疑裁决批**（用户指令「主控规划+GLM-5.3-Flash 子智能体执行，通读现状修正文档污染与漂移」）：workflow `dwfrun-f90288e9` 六域扫描 81 发现/分诊 68 项修正/4 项历史档案保留（`54b8d7e`/`e6e3902`）；🔴 越权事故——修正员自行扩权写 1169 行功能代码+验收员放行，主控终审 `git diff --ignore-cr-at-eol` 识破后 **16 个 .py 全部回滚**；五存疑主控裁决（`e5001cd`）：工具口径 17 实现/13 注册确认、42 卡定性「user_id=1 林挽夏克隆变体」+差异复核关闭、docs/INDEX 误报关闭、CODE_GRAPH §4.7.1 补载 D16 跨域两机制；CI 红修复（`bade9cc`）：`7ac7418` 改 MAX=1 漏跑 `test_restore_on_boot`（双槽位前提失效），改双账号语义。教训回流：FORBIDDEN 是软约束 git 门+主控终审才是防线、验收员会合理化越权、改通道常量必须 grep 全部测试消费者 |
| **v1.47** | **2026-10-13** | **四模块深度研究批**（用户指令「记忆/角色/主动消息/多模态是真正决定产品的模块，深度研究和完善，参考和对标」；workflow `dwfrun-c122a415` 25 员 Flash + 周配额 1310 截停后主控亲自收尾）：主动域=min_interval 基准纠偏（读 _last_delivery_time，旧读 _last_proactive_time 被 on_chat 拨走→闸退化成 cooldown 重复品，生产拦截比 87:2）+未回复连击硬闸 MAX_UNANSWERED_PROACTIVE=4（astrbot 语义，组内取最小）+双通道去重集合并（TestSqueezeTrap）+skip 账本治理（96% 噪声根治）；多模态=图片 describe 链贯通（VisionHandler 旧码从不传参=假实现根治）+图文并存双进主链（or 短路修复）+下载点字节上限+media 账本+voice_unsupported 话术；角色域=知识库 card 源双比对幂等+冷启动自举+lorebook constant 桶+audit_orphans；记忆域=🔴红测被配额截断主控补实现——遗忘链 id 游标根治（旧最新窗最老事实永生）+置信度衰减写回（confidence-only/恒常豁免/下限0.35/限速500）+召回层内权重重排+get_facts_by_ids 组隔离+身份组运维审计（键指纹隐私红线）。测试 3027→**3122 收集（207 文件）/3121 通过**、徽章 3208→**3303**；突变验红=记忆 6+多模态 2+验收员主动/角色域；🔑 Resume 回放非确定性→Amend 换轨、CRLF 双形态突变锚点、中断突变进程残留致 pytest 挂死；`893c28c` 三端部署 |
| **v1.46** | **2026-10-12** | **通道语义对齐批**（用户三轮钉死「谁扫码绑定了bot就是谁自己的微信上和这个bot聊天」「很多人都能注册然后进来使用」+ 指认「设计变更未更新文档导致代码漂移」）：**D16 谁扫码谁聊**——①扫码人守卫（`ilink_user_id` 三路径装载+非扫码人拒绝+告警，凭证缺 id fail-open）；②好友自选角色机制整拆（`peer_character.py` 152 行删除、connector 三方法、`/peers/*` 三端点、run_api pref 预载、user_scheduler pref 层/回读/键映射；`wechat_peer_preferences` 表保留归 schema 批）；③activate 直连扫码人绑定（`scanner_ids_for_user`+落 wechat_bindings+缓存+实例热切，修「控制台选角色→微信生效」断裂——生产 bindings 仅 1 行 vs 4 活跃通道）；④主动节奏按账号聚合（`account_pacing_state` 单一真源，消同账号多 bot 连发）。生产取证：活跃会话 peer **100%=扫码人本人**、第三方消息 0 条；**D15 关联拆除**（账号 2↔4=两扫码微信，越权合并，备份 `sqlite.db.bak-unlink-20261012-1850`）。测试 3016→**3027 收集（196 文件）/3026 通过**、徽章 3197→**3208**；端点 229/195/233→**226/193/230**；突变验红 3/3；`173b2b3` 三端部署（恢复 count=4、blob 四文件三端一致） |
| **v1.45** | **2026-10-12** | **跨通道事实共享批**（用户裁决「需要解决」+「注意用户隔离和真正的库共享」；生产实证两通道=两注册账号两 user_key 事实库零交集）：D15 显式身份组——`user_identity_map` 表+`identity_keys()` 单一真源（**显式声明绝不猜测**，未声明键行为 100% 不变）；读侧四面（get_facts/search_facts/向量复活复核/水位复核）+语义向量层（Chroma $or+Python 组过滤）组化；写侧 near-dup 组化 reinforce 原行；删除=等价副本全歼+全键各自会话进度水位（unlink 后仍生效）；越权拒删组化口径；`scripts/link_identities.py` 管理面。测试 2999→**3016 收集（195 文件）/3015 通过**、徽章 3180→**3197**；突变验红 3/3（M3 先假绿暴露 unlink 盲区补用例后命中）；事实相关 27 文件 501 例+隔离红线全绿；`c0244b2` 三端部署，默默两通道关联已建（合并池 217，空调/暖气/早操互见） |
| **v1.44** | **2026-10-12** | **逻辑谬误根治与追问链 LLM 化批**（用户三张截图报障「脑子有问题的对答」+ 裁决「下一轮对话时机由 LLM 决策、非数秒多次回复」+ 质疑三端同步；workflow `dwfrun-d65ad34f`，10 子智能体两起 escalation）：生产取证（event_ledger prompt_slots 回放实锤 fact_status=extracted_not_recalled、08:14:25→08:24:31 恰隔 600s=追问链 delay2、双通道 5 分钟连发）→ F1 历史逐行 `[MM-DD HH:MM]` 前缀（旧口径仅>30min 标注致「六点出早操被当成八点正在进行」）+ `TIME_REASONING_GUIDE` 三行推理指引；F2 `is_durable_env_fact` 保守判据+`_anchor_durable_facts` 检索后补锚（上限3/durable_anchor）+注入保位（修「说过没空调仍问空调开没开」）；F3 **追问链整体退役**（D14：`FOLLOW_UP_DEFAULTS`+45s/600s 机制删净 −548 行，API 四字段/前端 UI/类型三层同拆，拆条 4→2，`test_wechat_followup.py`→`test_followup_retired.py`）；三端核查结论=**同步**（A 档三端一致@e0eb5cd、e1fe43f 纯 B 档按纪律不上服务器、进程 08:58 已加载最新——截图乱象系修复上线前存量）。测试 2977→**2999 收集（194 文件）/2998 通过**、徽章 3158→**3180**；对抗验收 1/2/1 轮+突变验红；`a2a597b` 三端部署，生产 follow_up 块备份清理、复验 health/ready 200+追问零注册 |
| **v1.43** | **2026-10-12** | **主动因果连贯批**（用户报障截图+全链路取证：主动消息已落库但历史注入无因果标记，模型把定时关怀反向归因成「看你刚发消息」；决策日志自述 5 分钟连发+用户 2 分钟前刚聊完即再主动）：`annotate_outbound_markers`（channel=proactive/reminder 行加 [主动]/[定时提醒] 前缀）+ sanitize 保留 channel + `should_skip_proactive_decision` 节奏双闸（主动间隔≥60min/回复后冷却≥15min，决策前确定性跳过）+ 决策上下文 proactive:true + **hours 缺方法默认 0.0→99.0 语义修正**（旧值把拿不到事实当用户刚说话）；测试 2959→**2977 收集/2976 通过**、徽章 3140→**3158**；突变验红 3/3；`e0eb5cd` 三端部署，双闸生产实证（09:03:33 min_interval 拦截落账本） |
| **v1.42** | **2026-10-12** | **四域修复批**（用户指令「发现的所有问题都需要解决，参考成熟项目，主控规划+GLM-5.3-Flash 子智能体执行」，workflow `dwfrun-293ea88a`）：① **多模态 iLink 协议适配（high 断链根治）**——六源调研（zongrongjin/weixin-ilink MIT 源码级+photon-hq TS 逆向+openilink 官方文档等）：入站图片=item 2（CDN+AES-128-ECB）、语音=item 3（voice_item.text 微信服务端 ASR）；`parse_ilink_items` 纯函数+语音直用微信转写+图片 CDN 下载解密复用既有附件链+全媒体路径 INFO 日志+降级话术，旧协议分支兼容保留；出站媒体登记遗留（iLink 静默丢弃 bot VOICE）② **剧情线用户维度**——状态键 `user_key::character_id`（13 方法向后兼容+配置角色级共享+调用链贯通），门禁未动纯防接线爆炸 ③ **主动去重硬闸**——`is_duplicate_proactive`（归一化复用 normalize_message 单一真源）+拒发 5min 退避+软引导 ④ 生产运维：event_ledger 294 行裸键孤儿清理+voice_samples 归档。测试 2893→**2959 收集（191 文件）/2958 通过**、徽章 3074→**3140**；突变验红 5/5；`926c2fd` 三端部署 |
| **v1.41** | **2026-10-11** | **时间感知+主动关怀批**（用户报障三问题：定时提醒/主动关怀/时间观念 + 点名按成熟项目改造不自造）：全链时间注入——`utils/local_time` 锚点 `time_anchor_line`（hermes-agent #62369 `[time anchor]` 惯例 + SillyTavern {{isodate}}/{{weekday}}/{{idleDuration}} 语义对齐，**AGPL 未抄代码**；`chat_history.created_at` 为 UTC，统一换算北京墙钟）、历史间隔>30min 加 `[MM-DD HH:MM]` 前缀（`context_budget.annotate_history_time_gaps` 纯函数）、`sanitize_llm_history` 保留 timestamp、跨会话尾巴/主动线 recent_messages/ASE 生成线全补时间；主动死循环根治（10-08 起账本实证连续 4 天 15-25 次决策全 should=False）——`clamp_wait_minutes`（wait≤240min）+ `proactive_floor_extra`（失联>48h 且当日零主动注入下限提示，arXiv 2510.23853 temporal blindness 依据）；追问链默认 delay2 150→600/daily_max 12→6（生产 `scheduler_config.json` 同步，备份在案）；tool_gate L0/L1 判决日志（不落用户原文）。测试 2875→**2893 收集（188 文件）/2892 通过/1 跳过**、徽章 3055→**3074**；突变验红 4/4；`26df08b` 三端部署 + 行为级冒烟（锚点/尾巴时间前缀生产实证） |
| **v1.40** | **2026-10-11** | **全仓真源对齐批**（AGENTS/README/CODE_GRAPH/HANDOFF 四件真源对齐 10 月三批代码实况，零代码变更）：测试口径 2652 收集/2651 通过/174 文件/vitest 177 → **2874 收集（674+607+449+559+585+1）/2873 通过/1 跳过/187 文件/vitest 182**（vitest 5），徽章 2828→**3055**；角色卡 41→**42 张**（第 42 张 `99c89c39.json` 林挽夏变体 10-09 19:36 落盘未入 LOG，各引用处注差异待复核）；基线公式 2×卡数+7→**+13**（本批实测 persona_injection 97=2×42+13）；端点 229/195/233 内省复测吻合（本批实跑 `create_api_app`）；CI 全绿 `38109894676` @ `aea3cd0`（gh 实证 9/9）；§0 核心能力改「开放注册主链路+邀请码并存（D13，LOGIN-2 口径更正）」；删 §4.3/L7 死引用基线日志三件（仓库根已不存在）；修订历史表补 v1.39 系并修复表头分隔行错位 |
| **v1.39 系（补记）** | **2026-09-29 ~ 10-09** | 版本头曾达 v1.39.7 而修订历史表漏记，本行补记：**v1.39.7（09-29 全仓历遍批）** 真源文档全量对齐 09-28 服务器同步批实况（端点 229/195、页面 19、前端 API 15、收集 2652、徽章 2828）+ 注释勘误 4 文件零逻辑变更；**10-05 安全升级批**（`2c3b888`）两轮对抗审计 34+47 发现 + 生产 SSH 取证 + P0 修复八域 + SEC-1~11 登记 + nginx 线上精确 patch（备份后插安全头/XFP/ws 日志关闭）；**10-09 攻击面扫描批**（`e949107`）六路对抗扫描 30 发现/16 确认/9 修复 + SEC-12~18 登记 + XFF 行为级实证（21 打 15×429+6×401）——三批全程只落 `LOG.md` 未回扫本表；**10-11 SEC-15 根治批**（`aea3cd0`）vitest 3→5 + 双锁收敛销账 FE-2，CI 审计门复绿 |
| **v1.38** | **2026-09-22** | **六域三次排查根治**（好感度刻度混用 P0 + EmotionStage 键空间/落盘 P1 + 键口径 P2）：点存/审计镜像/回放统一 shisi 刻度（读侧按 reason 标记区分双刻度，round-trip 用例 + 突变验红 3/3 锁死）；阶段只用 track 键 + emotion_stage_state 真落盘 + evaluate 路由改纯查询；affinity_state 空 user 键同构；conftest 隔离拆独立 try（防 ase_hub 导入失败静默跳过库重定向）。收口实测 **1861 收集 / 1860 通过 / 1 跳过 / 0 失败**（四分块 467+1 / 430 / 563 / 400 精确吻合，41 卡在位）+ ruff 全仓 0 错 + ci_gates 4/4 |
| **v1.37** | **2026-09-22** | **六域二次全面排查根治（五块分块验收）**——用户指令「昨日进行了重大更新多次，需要你继续全面排查…」→ 第二轮排查 6 P0 / 9 P1 / 10 P2 → 裁决「按推荐全面进行」。**块A/P0**（`04c7ed2`）：情绪衰减基准落盘+缺失回填（首版以 `None` 作哨兵，而 `if hours > 0` 永假且推进赋在 if 内 → 基准永停 None，衰减换种死法仍未发生）；提醒时刻校验改本地墙钟。**块B/P0**（`3ec093f`）：投递异常不再被日志通道掩盖；`main.py` 入口 websocket 改定向。**块C/P0**（`af5c41e`）：出站消息归属全链贯通（assistant 行无 character_id → 切角色继承他人台词）+ 存量空归属懒回填（旧 `OR character_id=''` 在存量全空时恒真=过滤整体失效，生产 88% 空归属）+ `run_api._character_resolver` 的 `"default"` truthy 缺陷。**块D/P1**（`c37d355`）：`dry_run` 死路径根治（闸门前置使 `generate_with_llm` 整条不可达）；决策层接地补 `last_user_message`/`response_rate`；`note_user_interaction` 就近落盘。**块E/P1+P2**（`04229eb`，14 文件 456+/67-）：⑩节流账本落盘（4 账本，逐字段容错+NaN/inf 钳除+上限 200；旧纯内存重启归零 → 退避永停第 1 档、等待窗作废、每日去重失效、祝福重发）；⑪`ase_engine.save_state` 走 `json_state.atomic_write_json`；⑫**好感度双真源根治**（`mapper.sync` 收会话键 vs `_persist_affinity` 收裸 user_id，两套键空间按构造零交集，生产实证 129 行 5 键 vs 3 键无一重合，enhancer 重启回放拿不到数据；统一 `user_key_from_session`+双写+点存兜底+`default_db_path()` 共享 owner）；⑬**`semantic_memory` 向量召回读侧协程缺陷**（`async def _search` 被同步直调 → coroutine 恒真使 `or []` 不触发 → 向量召回恒空，谎言家族新例；改 `inspect.isawaitable`+`run_async`）；⑭反思按会话检索空归属死结（下推 WHERE 使空归属历史连候选都进不来 → 改取回后 Python 过滤+丢弃计数）；⑮`get_cross_session_tail` 排序改 `id DESC`；⑯`time_awareness_tool` 默认日期改 `now_local().date()`；⑰删死参 `character_overrides`；⑱激活角色缓存键补 `id(_list_all_characters)`。**🔴 自伤事故与根治**：块E 落盘后未隔离夹具把测试键写进开发机真实 `data/scheduler_config.json`（gitignored → 不被 git 发现却反向改变用例行为）→ conftest 增 autouse 夹具 `isolate_runtime_state_files`。**验证**：分块 **1839 收集/1838 通过/1 跳过/0 失败** + ruff 0 + ci_gates 4/4；**基线对照 21 失败 → 0**；突变验红 **5/5** 命中、还原残留 0；新增 `tests/test_block_e_hardening.py` 27 例。**遗留**：生产 affinity 双键空间历史数据不迁移、审计表测试遗留键未清、本批未部署（归用户裁决）|
| **v1.36** | **2026-09-22** | **六域全面排查根治**（用户裁决：按推荐全面修复+分块验收+真删除）：①遗忘模型 days_old 恒 30（naive/aware 相减 TypeError 被吞）→`_fact_age_days` 按龄遗忘复活；②set_reminder trigger_time 三关校验（畸形串永不到期残留通道根治）；③情绪衰减基准独立记账（旧传 ≈0.08h 名存实亡）；④AffinityEnhancer 审计回放恢复（重启归零 ±3 爬坡/解锁重复根治）+setup_shisi db_path 接线；⑤重要日期按画像生日+逐用户定向（旧全局广播+dedup 无用户维度）；⑥事实检索过滤下推 SQL/Chroma；⑦pending_events 死链整拆（CrossSessionReasoner 删文件+4 接线+DROP 表）；⑧ledger prune 画像豁免+投影取最新；⑨DB 版 WorkingMemory+四死表+affinity_log CRUD+get_stats 真删；⑩web 投递按会话定向收口+提醒角色名按会话解析+query_reminders 无 meta 拒绝。测试 **1750/1749/1** 双端全绿+vitest 98+ruff 0；端点 220/186 不变；突变验红 8/8；DELETION_LOG 2026-09-22 节 |
| **v1.34** | **2026-09-21** | **全量代码审查修复战役收口**（工作单 `docs/verification/2026-09-21-full-code-audit.md`；P0×10 + P1 1–53 + persona 42–47 + 批6b 项1–11 全修，含 GitHub CI 修复）：知识槽检索与 system 回显解耦（每轮 RAG 真正发生）+ persona 域死码整批清除（−3566 行，`character_card/` 包/三版并存化石/强化回路/CharacterService 全链/prompt_mode 管线）+ 锚点校验去同义反复 + 一致性 checker 缓存复用 + ASEHub 真扇出 + LLM 网关熔断/流式真 fallback + 隔离/SSRF/CAS 收口。终局验证分块 **1615 收集/1611 通过/4 跳过/0 失败**（410 + 380+1 + 406 + 415+3，41 卡）+ vitest 98 + tsc 0 + ruff 全仓 0 + ci_gates；端点重测 APIRoute **220**/唯一路径 **186**（`len(app.routes)=224`）。遗留：evolution-log 恒空（DELETION_LOG 在案）/前端 event_bonus 归并行窗/服务器 pull 上线归用户裁决（本批未部署）|
| **v1.33** | **2026-09-21** | **智能体化：LLM 编辑画像/同步记忆**——`tools/builtin/profile_agent_tools.py`（`update_user_profile`/`remember_facts`/`forget_facts`/`query_profile`）；对话后 `run_profile_sync_agent`（仅用户原话可写、否认则 clear/forget）；正则 `apply_user_utterance` 降为弱兜底。`3f5a4c6` 已部署 |
| **v1.29** | **2026-09-21** | **P1 隔离收口**：ASEHub 分用户 + 定向投递；affinity `user::character`；情景层空 meta 不注入 + store 修复。测试 **1455/1451/4** |
| **v1.28** | **2026-09-21** | **用户隔离全链路 P0 根治**（生产「串台/记错人」实证驱动，主检出本窗直接实施）：user_key 完整会话键 / 历史禁止并裸形态 / retrieve_context 全层 session 过滤 / diary 分桶 / tail 精确键 / 通道状态回写 DB / 启动迁移孤立裸键。测试 **1448/1444/4**（41 卡）+ 新 `test_user_isolation_chain.py` 12 例 + 突变验红命中 |
| **v1.27** | **2026-09-20** | **全仓历遍·文档对齐 + 3 处代码缺陷修复**（用户指令「全仓历遍，更新文档，修复bug」）：**① `proactive/reminder_delivery.py::_maybe_gc_intents` 批量过期清理从未执行**——旧为**同步**函数却 `asyncio.run(self._sm.expire_stale_intents())`：后者是同步方法（`asyncio.run` 只收协程对象），且由 `_run_once()` 在**运行中的事件循环内**同步调用 → `RuntimeError: asyncio.run() cannot be called from a running event loop`；两个异常都被 `except Exception` 吞进 debug 级 → `pending_intents` 的 active 行只能靠「该会话被再次读取」时惰性过期，长期不活跃会话的行永久残留（**表无界增长**）。改 `async` + `await asyncio.to_thread(...)`。**② `orchestrator/context_budget.py::format_session_tail` untrusted 信封结构错误**——开标签 `<context …>` 被排在**被包裹正文之后**（引言→正文→开标签→说明→闭标签），正文落在信封之外；与 `tool_gate.TOOL_RESULT_ENVELOPE_HEAD/TAIL` 的包夹约定不一致 → 改「开标签→正文→闭标签」，抽出 `SESSION_TAIL_ENVELOPE_HEAD/TAIL` 常量。**③ `shisi/memory/legacy/vector_memory.py::_run_async` 同线程死锁分支**——「已处于事件循环中」分支 `asyncio.run_coroutine_threadsafe(coro, loop).result()` 而 `loop` 取自 `get_running_loop()`（**当前线程正在跑的那个循环**）→ 同线程阻塞等待自身循环推进 = **必然死锁** → 委托公共真源 `utils.async_utils.run_async`（同时消除重复实现）。**测试**：新增 `tests/test_async_bridge_contract.py`（5 例：AST 静态防护 + 三分支功能性 + 守护线程超时判定防挂死）+ `format_session_tail` **信封位次**断言 + `_maybe_gc_intents` **真执行**断言（读原始行，避开惰性过期掩盖）；**突变验红 4 次全中**（第 4 次「保持 async 但改回 `asyncio.run(...)`」由行为断言命中）。验证：主检出分块 **1436 收集 / 1432 通过 / 4 跳过 / 0 失败**（314 + 384+3 + 330+1 + 404 精确吻合）+ vitest 98/98 + tsc 0 错 + ruff 0.16.8 全仓 0 错 + ci_gates 4/4；端点 215/181 / 唯一路径 181 / DB 表 / 路由数不变。**文档对齐**：`api/app_factory.py` 模块 docstring（204/171/95/74/20/15 → 215/181/101/78/16/20，`len(app.routes)` 208→219）、`CODE_GRAPH` **v3.8.16**（§1.1 测试行 + §4.1 orchestrator 8→9 文件包 + `optimized_orchestrator.py` 920→1270 行 + §4.2 app_factory `:84`→`:91` 与 run_api `:232`→`:411` + §4.2 口径纠错注更新读数 + 修订历史）、`MODULES`（orchestrator 7→9、proactive 5→6、shisi 115→116、**补 `utils/` 整行**、总文件 ~511→389、`optimized_orchestrator.py` 1050→1270）、`DATABASE`（**自纠**「api/database.py 6 表」→8 表 + 新增 `sqlite.db` 26 表清单 + 角色卡目录 gitignore 与基线浮动注记）、`INDEX`（规模 389 / 测试 1530 / **ADR 11→12（补 ADR-0015）**/ 目录树）、`README`（测试口径 + 参数树 orchestrator 9 / shisi 116 / 新增 utils + ADR 12 份）、`ARCHITECTURE`（orchestrator 9 文件与行数）。**角色卡恢复**：本批途中 `config/characters/` 为空（基线塌缩 1356）→ 用户指令「修复」→ 从服务器逐字节取回 **41 张**（`sha256sum` **41/41 一致**、JSON 全可解析）→ 基线 **1436/1432/4**。⚠️ **并发**：提交前 `git status` 为 0 项（无并行窗口在制品） |
| **v1.26** | **2026-09-20** | **收仓三窗 + 全仓复核隔离补漏**：记忆会话隔离（fact 抽取/memory_context/retrieve facts 降级）+ storyline UTC；vitest 98 + ruff 0 + 端点 215/181；audit/abc/ci-fix 文件级收编并卸窗。该批记载的 **1429/1425/4** 依赖 41 张角色卡在位（卡目录 gitignore）；v1.27 已从服务器把 41 张卡逐字节恢复 → 现基线 **1436/1432/4** |
| **v1.25** | **2026-09-20** | **CI 修复**：`pending_intents` 写读时钟统一（UTC 宿主上 `datetime.now()` vs `_now_local` 差 8h → pending 落库即过期，ask_user CI 红）；`CalendarTool`/`TimeAwarenessTool` 墙钟改 `now_local`；+3 时钟契约回归 + 突变验红。本窗/CI 口径 **1344/1334/10** |
| **v1.24** | **2026-09-20** | **包 Q 补做**：B-d 跨会话尾巴 + 工具结果 history 后/PHI 前正式位次。测试 **1421/1417/4** + vitest 98 + ruff 0 + 端点 215/181 |
| **v1.23** | **2026-09-20** | **包 Q · A+B+C 收编**（实施窗 wt/abc；主控文件级入 main，merge 被工具层拦截）：身份唯一/兜底角色化/流式策略/context_budget/工具信封与限额/记忆同步写+near-dup+k(level)。测试 **1414/1410/4** + vitest 98 + ruff 0 + 端点 215/181。未完成：B-d、工具结果 prompt_builder 正式位次 |
| **v1.22** | **2026-09-20** | **全面升级根治批次**（用户裁决五项全做）：user_facts 完整隔离（user_key 列+读写过滤+legacy 不注入）；B3 memory 配置五键接线；B4 access_count 回忆强化；B5 遗忘进 memory_recycle_bin；B6 affinity scale 唯一真源+持久化。测试 **1351/1347/4** |
| **v1.21** | **2026-09-20** | **全仓扫描·在制品收口批次**（用户指令：代码审查/全仓扫描/更新文档/找 bug 修复）：收编 v1.20 登记的并行窗口在制品 4 类纯缺陷——① `FrequencyController` 日界 `now_local` + `last_reset_date` 状态落盘 + `record_sent` 钉日界（修恢复后配额被清零）；② `analytics` 日键本地墙钟；③ `important_dates.check_today` 收口观察项；④ `after_chat` 系统错误占位不入库（结案 LOG 遗留「处理超时污染 chat_history」）。B3-B6 与 user_facts 仍开放。测试 **1332/1328/4** + vitest 98/98 + ruff 0 错 + 端点 215/181 不变 |
| **v1.20** | **2026-09-20** | **复核补漏批次**（用户指令「进行复核」→ 对 v1.19 做对抗性自查）：**① v1.19 引入的不一致**——`daily_maintenance` 写入键已本地化，但 `get_chats_today`/`count_chats_today` 仍是 SQLite `date('now')`=**UTC 日**窗口 → 标签/内容脱钩；且对外「今日对话数」统计（`api/routers/misc_routes.py:108`）在本地 08:00 才换日（**生产实证：旧 28 vs 新 42**）。新增 `utils/local_time.local_day_utc_bounds()`（本地日 → UTC 区间，左闭右开），两方法改区间过滤。**② 自纠 helper bug**——`local_day_utc_bounds` 早期用「传入时刻 − 当前 UTC」求偏移（仅 now 恰为此刻时成立，传构造时刻得 0），由同批用例抓出 → `_current_utc_offset()` 恒取此刻。**③ 穷举同模式**——补收 `structured_memory._now_local()`、`orchestrator/tool_gate.py::now_beijing()`、`proactive/ase_engine.py` ×2 处墙钟串（原均依赖主机时区无回退）→ 统一 `now_local()`。**④ 测试加固**——钉时钟来源+钉实参（规避真实墙钟巧合假通过）+ SQL 边界用行断言（count 会巧合）；突变验红两次命中。测试 **1328/1324/4**；端点不变。**并发隔离**：本批仅提交自有 5 文件，工作树另有并行窗口在制品 7 文件未纳入 → **已由 v1.21 收口** |
| **v1.19** | **2026-09-20** | **墙钟时区缺陷修复批次**（来源：深研 W-D 设计文档 §五 缺陷清单，用户裁决「修吧」——只修**纯缺陷**，涉行为变更的 B3/B4/B5/B6 留待裁决）：**① 公共时钟真源**——新 `utils/local_time.py::now_local()`（逻辑取自全项目唯一正确处理非 UTC+8 主机的那处 `proactive/ase_engine._local_now`；`_local_now` 改委托，保留函数名以维持 `scheduler.py` 的共用时钟契约）。**② 修复已生效缺陷（B1a）**——`memory_pipeline` 4 处墙钟判定误用 UTC：`after_chat` 深夜情感加权（`:286`）、`daily_maintenance` 日记日期键（`:582`）、`get_formatted_context` 当日摘要查询键（`:686`）、`_do_fact_extraction` 的 `should_store_as_fact` 入参（`:778`）。UTC+8 下 `_is_late_night`（23:00–05:00）实落**本地 07:00–13:59** → 深夜情感记忆加权错位到上午/中午（功能反向）；**精度更正**：`should_store_as_fact` 规则 3 默认 `return True`，故规则 2 布尔值与默认等价，**真实活影响只在 `after_chat` 的 importance +0.3**；另日记/当日摘要按 UTC 切日（本地 00:00–08:00 归入前一天）。**③ 修复潜伏缺陷（B1b）**——`TimeContext.now()` 改本地（生产 `prompt_mode: layered` 故此前未生效）。**④ 删死代码**——`persona_utils.build_time_context()`（全仓零调用者；其内部调的正是带缺陷实现，会误导"已接线"判断），入 `DELETION_LOG`。**⑤ 有意保留 UTC**——`session_id` 生成（`:206`）与 `_apply_forgetting` 的 `updated_at`/`days_old`（`:738-739`，混用会算错经过时长）。**⑥ 勘误与观察**——`dynamic_anchor.py:65 time_of_day` 非分类器（dataclass 字段、零 setter）；`utils/important_dates.py:54` 裸 `datetime.now()` 在生产 `TZ=Asia/Beijing` 下正确但主机时区变更即静默失效（观察项）。**验证**：突变验红（改回 UTC → 2 用例同时变红，且与运行时刻无关，避免"墙钟巧合"）+ 分块 pytest **1323 收集/1319 通过/4 跳过**（241+386+328+364 精确吻合）+ ruff 0.16.8 全仓 0 错 + CI 门禁 4/4；端点数不变。**已知副作用登记**：`diary_summaries` 修复前写入的行仍以 UTC 日期为键 → 历史行一次性键错位，不迁移、自然过期 |
| **v1.18** | **2026-09-20** | **提醒意图管线批次**（用户报障「昨晚让她提醒今早六点叫我起床，没做」，排查实证四层根因后用户裁决「分级思路+LLM 真判断+自然澄清提问，C 主干+A/B 配套+先调研」）：**① 三级意图管线**取代关键词裁决——L0 零成本晋级线（`orchestrator/tool_gate.py` 新模块：钟点/相对偏移强时间信号+托付动词+查询组+pending 强制；只晋级不裁决，误晋级由终审兜底，普通闲聊零影响；旧 `_tool_intent_names` 关键词裁决删除）→ L1 LLM function calling 终审（全量权限内工具+`ask_user` 伪工具三分支：调真工具/自然澄清提问（角色口吻，direct_reply 直复通道跳过主链）/闲聊；prompt 带当前北京时间+pending 槽位）；**② 防假承诺守卫**——无工具回执含承诺措辞（「听到啦/好的我会」）强制复核一次仍无则弃内容走主链（scallopbot 原则，生产事故「听到啦」的直接解药）；**③ 澄清状态机**——新表 `pending_intents`（StructuredMemory `sqlite.db`，非 users 库；会话级槽位合并、两轮上限=第二轮最优猜测+复述确认、15min TTL、话题转移/超时作废）；**④ 到期投递闭环**——新 `proactive/reminder_delivery.py`+scheduler `register_reminder_task`（每分钟 `reminder_check` 轮询：session_key 定向投递 `send_text(to_user)`、web 会话走 ws 广播、**豁免静默时段**（06:00 叫醒恰在 23-7 静默窗内）、文案 LLM 投递前一刻生成原文兜底、失败 3 次判 failed、存量无主提醒（session_key 空）永不投递）；**⑤ 时区修复**——应用层北京时间比较（`_now_local`），弃 SQL `datetime('now')` UTC 差 8h 口径；**⑥ reminders 表迁移**——+session_key/user_id/status/delivered_at/fail_count（`_migrate_reminders_columns` 幂等），set_reminder trigger_time 必填+北京时间格式、`_meta` 服务端注入调用归属（LLM 不可决定 user_id）、query_reminders 按会话过滤。调研留痕：arXiv 2511.08798 SAGE-Agent（澄清克制三原则）/scallopbot（无回执不承诺+用户原话保持确定性+投递前一刻生成）/ST Extension-CharacterWakeUp。测试 **1307 收集/1303 通过/4 跳过**（=v1.17 口径 1269+本批 34：test_reminder_intent_pipeline 34 用例含「明早六点记得发消息给我，叫我起床」原话回归；分块四块精确吻合）+ `create_api_app` 内省 219=215+4 零端点变更 + ruff 绿 + mypy（改动文件）0 错。生产实证：**端到端送达 1/1**（id=3 `delivered`@10:27:12 用户微信实收）+ 3 次失败判死 1/1（id=2 `failed`）；首验失败系微信 web 协议会话窗口失效（既有脆弱性 L2，非本批代码），重试机制兜住 |
| **v1.17** | **2026-09-20** | **回复质量根治批次**（用户报障「我发一句她只会固定回两句、不按话题演进」，诊断报告呈报后用户裁决「全面升级根治」，四组根因全修）：**① A 失忆【主犯】**——对话上下文读全局 RAM deque：重启即清空（[prompt] 埋点 hist_msgs 12→18→0 实证）、无 session 标签跨用户串扰（违反隔离硬约束）、chat_history 表 698 行白存无读者；`get_chat_context`/`get_recent_context` 改 **DB 会话过滤真源**（`_load_session_history` 双形态 `N:wxid`+裸 `wxid` 合并 + `get_chats_by_session_limit`），orchestrator 传 session_id；**② A3 承诺不认账**——fact_extractor 新增 commitment 类别（提醒我/叫我/记得/说好了/约好/答应），此前无类别可落、user_facts 全库仅 3 条；**③ B 语气三连**——沉浸式 `3~25 字+不要追问`→`10~80 字+长短跟随话题+允许反问`（禁括号动作/禁编造/非共处条款全保留；**小说式模式零改动**）；41 卡 mes_example 升级 3~4 轮话题演进对话（41 索引重建）；19 卡「每次回复 2~5 句」硬限弹性化；**④ C 追问全灭**——`_schedule_followup` 登记 `N:wxid` 会话键，而 `_send_text` 的 to_user 与 `_context_tokens` 表键均为裸 wxid → 发送目标错（ret=-3 invalid arguments）+ token 查键恒空；`_cancel_followup(from_user)` 裸键取消会话键待发表永不匹配（接话后追问照发）；新增 `_peer_wxid_from_session` + 取消改会话键。+14 回归（test_reply_quality_overhaul：DB 真源/隔离/双形态/承诺提取/键还原/沉浸式新旧措辞）。分块基线 **1269 通过/4 跳过零失败** + ruff 全绿；生产实证（e7fb801f）：空 RAM 从 DB 恢复 50 条真实历史（修复前 0，首条恰为承诺类对话「要是你忘了我生日你就完蛋了」）、沉浸式新措辞在线上。**已知遗留登记**：错误占位回复（「处理超时」等）写入 chat_history 污染上下文；user_facts 无用户维度（跨用户共享，需独立批次） |
| **v1.16** | **2026-09-20** | **全仓遍历·文档对齐批次（代码领先、文档落后，零代码变更）**：09-19 白天 v1.12 全仓扫描落账**之后**，当晚 22:26 `3e66930` 落地**每人独立微信通道隔离**（+2195 行：`wechat_direct` 新增 channel_paths/connector_registry/peer_character、`api/routers/wechat_channel_routes.py` 双 router、DB 新表 wechat_channel_sessions+wechat_peer_preferences、`scripts/migrate_legacy_wechat_channel.py` 遗留凭证迁 admin、旧全局微信端点收敛 admin 兼容面）与 **JWT-only 复核收口**（`verify_api_key_dep` 有效 Bearer JWT 优先放行——用户侧 API 仅 JWT、前端不含 API Key 明文、API Key 留给机器/脚本/E2E），后续 09-20 又有材质/克隆假进度/角色/提示词四批次，均只落 LOG 未回扫代码实况文档。本次逐一历遍（变更带 `54c3b1a..HEAD` 111 文件 +7682 行全量核对 + 全部探针复测）：**端点 204→215 / 唯一路径 171→181**（+wechat-channel 9 + admin-wechat 2）、include_router 16→**18**、routers 21→**22**、api/ 44→**45**、DB 6→**8 表**、wechat_direct 2→**5 文件**、main.py 438 行；CODE_GRAPH **v3.8.6**（§1.1/§2/§4.1/§4.2/新增 §4.7.1 通道子系统/§4.7 链双真源注记+常驻同步事件循环性能修复/§10 语音行 edge-tts 残留清除/§13）+ README（徽章 1353、口径 1255+98、结构树 22 路由/215 端点）+ CODEMAPS 六件 + docs 入口 + FUNCTION_INVENTORY（WECHAT-1..4 通道语义、N-CHANNEL-1、MESSAGE-5 追问/MESSAGE-6 回复模式、STICKERS-1 撤除登记、STORY-1/2 剧情线独立页、GLOBAL-3 材质体系、五 tab、页面行数校准）+ DECISION_LEDGER（09-19 晚通道/JWT-only + 09-20 四批次行）+ VISION + P1_BACKLOG。验证：分块 pytest **1255 passed/4 skipped**（410+317+319+209 与收集 1259 精确吻合，test_knowledge_routes_index 已在分块内）+ vitest **98/98** + tsc **0 错** + `create_api_app` 内省 215/181 复测 |
| **v1.15** | **2026-09-20** | **提示词构建行业对齐批次**（用户指令：移除全部角色卡场景字段 + 调研角色扮演提示词优化 + 参照行业成熟项目改善 prompt 构建）：① **41 卡全量移除 scenario 字段**——scenario 是「开场情境」却被缓存复用导致角色被永久锚定在开场画面（09-19 已在生产实证 62105bca「用户在路上」锚定），移除是根因级修复；代码层保留 scenario 兼容渲染（带「仅开场氛围」守卫）以支持导入的 SillyTavern 卡。② **prompt 重排（参照 SillyTavern 默认序列 + chara-card-spec-v2，来源见 LOG）**：creator_notes（扮演规则）移至对话历史**之后**——post-history instructions 位置，两大权威源共同明确「历史之后指令权重远高于历史之前」；新增 **# 对话示例** 段（mes_example，SillyTavern dialogueExamples 位，历史之前 few-shot，上限 2000 字）；知识库双重注入去重（prompt_builder 为唯一 owner，PersonaService 的 JSON dump 重复段移除，仅兜底）；orchestrator 人设片段精简为身份绑定（移除 500/60 字截断重复——SillyTavern 惯例角色定义只注入一次）。③ **索引同步**：scenario 块出库（米彩 18→17 块），41 索引重建。测试 **1259 收集 / 1255 通过 / 4 跳过**（+5 结构回归 TestSystemPromptStructure + 2 契约测试改写）+ vitest 98/98；生产实证：41 卡 0 scenario、米彩 stats 17 块无 scenario 源、检索正常、health 200 |
| **v1.14** | **2026-09-20** | **角色完善与文学导入批次**：① **角色卡 25→41 张**——新增《我的26岁女房客》（超级大坦克科比）米彩/昭阳/乐瑶/简薇、《从你的全世界路过》（张嘉佳）陈末/幺鸡/茅十八/荔枝/猪头、《云边有个小卖部》（张嘉佳）刘十三/王莺莺/程霜、《某某》（木苏里）江添/盛望、《天堂旅行团》（张嘉佳）宋一鲤/余小聚，共 16 张全字段卡（描述/性格文本/场景/扮演规则/8 锚点/数值字典/示例对话；角色设定经通用搜索核实，来源=百度百科/维基百科/知乎书评，留痕 LOG）；② **既有 25 卡完善**——伊蕾娜·艾斯特莱雅损坏字段（description 3 字/scenario 3 字/creator_notes 2 字）按《魔女之旅》重写，23 卡补 personality/speaking_style 数值字典（此前仅孙颖莎/林挽夏有），25 卡全补 mes_example（示例对话，同时成为知识库可检索语料），椎名真昼/莉莉娅 scenario 扩写、孙颖莎补 personality_text；③ **知识库激活**——`scripts/rebuild_knowledge_index.py` 补透传 `PersonaProfile(core_anchors)`+`source_data`（旧重建比运行时抽取少锚点/示例对话两类块且缺口常驻），`CharacterKnowledgeService.search()` 双路合并改交错式（修复扩展路占满注入窗口挤出原路高 idf 块的缺陷，实测米彩「昭阳是谁」修复后命中），41 卡索引全量重建约 1750 块 + 检索冒烟 5/5。**注意**：`config/characters` 系 gitignore 目录（§9.4），新卡不入公开仓，服务器私有投递 + 服务器端重建索引。测试 **1254 收集 / 1250 通过 / 4 跳过**（本批 +37 用例零失败）+ vitest **98/98** + ruff 全绿；生产实证：41 卡 API 全可见、米彩知识库 18 块/7 源、「昭阳是谁」检索命中 |
| **v1.13** | **2026-09-19** | **主动消息「白天一条都不发」根因修复批次**（用户报障「为什么还是没有给我主动发消息」，生产日志实证驱动）。**根因：静默时段（23-7）内引擎照常生成消息并扣配额，消息却在投递层被丢弃** —— `ase.tick()` 内部 `_generate_and_return()` 即调 `_record_proactive_sent()`（`daily_count+1`、写 `_last_proactive_time`、`urgency.reset()`），而投递发生在 tick 返回**之后**由 `scheduler._deliver()→_send_to_all()` 执行，后者首句即判 `_is_quiet_hours()` 并 `return False`。生产实证：`00:02–04:05` 每 35 分钟一条、连续 **8 条全被丢弃却全计数**（30 分钟冷却被空转）→ 配额凌晨 4 点即 **8/8 满额** → 当天 07:00 后每个 tick 都 `result=False`，**全天零投递**，而 urgency 一直挂在 8.50（用户已 90 小时未聊天，missing_bonus 拉满）。09-18 同一模式复现，**每天重演**。修复：① **记账与投递解耦** —— `tick()` 返回值改为**未记账的候选**，新增 `commit_sent()`，由 `_deliver()`（改为**返回 bool**）成功后才扣配额/写冷却/重置紧迫度；未送达不产生任何副作用。② **静默前置到生成层** —— `scheduler._check_ase` 在调 `tick()` 前短路（只 `dry_run` 更新紧迫度），`ASEEngine.set_quiet_hours()` 由 scheduler 注入并随配置重载同步。③ **场景日期标记延迟置位** —— `_check_scene_triggers(commit=False)` 返回 `_scene`/`_scene_date`，投递成功才置位（旧实现生成即置位，被丢弃后当天该场景永不补发）。④ **LLM 输出清洗** —— 新增 `sanitize_message()`，拦截推理过程泄漏（生产实证原文 `02:55属于深夜，不在早安、吃饭或晚安的特定时间点…` 被当成消息投递）、超长（>60 字）、多行思考；旧实现只判 `len>5` 等于不判。⑤ **去重与节流** —— `_is_duplicate` 改归一化精确匹配 + 窗口收敛到 6（模板池仅 3~8 条/类，原 50 窗口会让池子整体判重致彻底发不出）；生成时把最近 6 条注入 prompt 要求换角度；`_select_type_by_urgency()` 加同类消息节流。**注**：相似度去重经实测**不可用** —— 「都半夜了还不睡…」vs「都两点多了还不睡…」的 SequenceMatcher 比值仅 0.37，而正常换说法的「早啊」/「早安呀」也有 0.25，阈值无法区分。⑥ **可观测性** —— `_check_frequency()` 由 `bool` 改为 `(bool, reason)`，`tick()` 输出 `_last_skip_reason`（daily_limit/min_interval/quiet_hours/below_threshold/…），修正 `result=True` 行打印 `urgency=0.00` 的误导（`urgency.reset()` 副作用）；`/api/proactive/state` 增 `max_daily`/`quiet_hours`/`last_skip_reason`。⑦ **连带修复重要日期祝福** —— `_check_important_dates` 原**只**由 00:05 每日维护调用，恒落在静默内 → 生日/纪念日祝福**从未送达**；改为每小时任务 + 静默跳过 + 当日幂等键。**运维要点**：应用日志在 `data/app.log`（**不在 journald**，unit 的 `StandardOutput=append:/var/log/...` 基本无内容，只查 journal 会误判"服务无异常"）。测试 1082 通过 / 4 跳过（+22 用例，零回归） |
| **v1.12** | **2026-09-19** | **全仓扫描·文档对齐批次（代码领先、文档落后，零代码变更）**：§0 页面 19→**17**（实测 `frontend/src/pages` 17 个 .tsx）；§0 语音行修正——Edge-TTS 已于 08-28 MiMo-only 收敛删除，旧句「MiMo 云 / Edge-TTS / 本地模型」残留误导。同步 CODE_GRAPH v3.8.2 / README / docs 入口 / CODEMAPS 六件 / FUNCTION_INVENTORY / DECISION_LEDGER / VISION / P1_BACKLOG 拉齐代码实况（供应商链 4 家、`/psych` 需登录、shisi 115 文件、SP-1/SP-11 已执行等）。验证：pytest 1060 passed/4 skipped + vitest 87/87 + tsc 0 错 |
| **v1.11** | **2026-09-19** | **体验修复批次**（用户报四项体验问题，全链路修复，9 个提交 `ff65e60`→`4cb69d7`）：① **人设不贴合**——机制性根因是 `CharacterAggregate.build_system_prompt` **只注入 name+description+人格数值**，而角色卡里承载"怎么说话"的 `personality_text`/`scenario`/`creator_notes` **从未进入 prompt**（原始卡 32/32 均有）；补齐三字段并按其注入（实测阿哈 prompt 3789→13018 字）。配套修 `_extract_from_character` 同类三段只读 `source_data` 的缺口。② **主动消息不发**——`daily_count` 跨日未重置（CronTrigger 无 `misfire_grace_time` + 多 worker 覆盖）+ `tick()` 把频率检查前置导致 `_update_urgency()` 永不执行 → `missing_bonus` 恒 0，**两 bug 连锁死锁**；改跨日惰性重置 + 紧迫度先于频率检查；另修「控制台手动发送吃掉当日配额」；`_check_ase` 静默失败改为每 tick 可观测。③ **响应慢**——fallback 链首选 `sensenova` 但生产 `.env` **从未配置其 key** → 每次对话白跑一轮失败尝试；**移除 sensenova、接入 agnes 为首选**。④ **知识库没用上**——索引块数 520→**1000+**（新增 `scripts/rebuild_knowledge_index.py` 从权威真源重建）；检索注入 `top_k` 3→8；新增 **BM25 查询扩展**（双路互补检索，修「你家里有什么人」高分误命中无关块的排序问题）；新增 `scripts/expand_short_descriptions.py` 按已有素材扩写 10 张描述不足的卡。**前端**：StatusCenter 记忆体系重构为「三层管线」。**文档**：VISION 战略口径统一（基座免费开源 + 增值层商业化，与已提交 BP 对齐）、GAP-2/3 结案、CODE_GRAPH 供应商章节同步。测试 1060 通过 / 4 跳过，零回归 |
| **v1.10** | **2026-09-18** | **死代码与假端点清理批次**（用户裁决「三项全做」）：① 删除 `shisi/api/v2/` 共 6 文件——`v2_router` 全仓无 `include_router` 挂载，**推翻 `docs/DELETION_LOG.md` 早先"保留待将来集成"裁决**，同步 4 处文档引用（本文件 Owner Map / `CODE_GRAPH.md` 分层表 / `CODEMAPS/DATABASE.md` / `CODEMAPS/MODULES.md`）；② `DELETE /api/shisi/memory/{memory_id}` 由"谎报已移入回收站"改为 **501 Not Implemented**（`memory_recycle_bin` 表已存在但无删除链路；保留未确认时的 400 前置校验）；③ `unfavorite_memory` 的 `fav_id` 路径参数由被忽略改为唯一判据（新增 `FavoriteManager.unfavorite_by_id`）；④ `/api/shisi/status` **纳入认证**，`/api/shisi` **31/31 全覆盖**（该端点暴露 12 个内部模块的初始化状态，属控制面；探活职责由刻意豁免认证的 `/api/health`·`/api/ready` 承担）；并更正 F1 告警文案 `AUTH_ENABLED`→`API_KEY_ENABLED` + 修补 ruff F401/I001 两处门禁破坏。测试 1060 通过 / 4 跳过，零回归 |
| **v1.9** | **2026-09-18** | **五项裁决执行批次**：① 双角色库收敛——`config/characters` 为唯一权威真源（data/characters 53 张旧卡 tar 备份后删除并入；7 处代码改指向：knowledge_routes 兜底链/shisi manager·importer·exporter/migration×2/preflight；`sync_character_files.py` 双库同步脚本删除；4 张无对应孤立卡裁决废弃封存）；③⑤ `ASEEngine._monologues` 冗余副本删除；④ `extract_intent` 死方法删除；② bg 背景不恢复。测试基线 1064 收集/1060 通过（+48=25 卡 persona 注入参数化全覆盖） |
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

<!-- aoci:begin -->
## AOCI 仓库认知

AOCI 为本仓库维护一个稳定、可版本化、可增量更新的仓库级认知层，供模型跨任务复用对系统的理解。

`aoci.txt` 是面向模型的结构化认知索引。它以每个受管理文件、数据库表或其他受管理对象一条独立 Entry 的方式，用符号标签与 F/R/A/S 语义表达对象的核心职责、重要关系、对外契约，以及理解或修改系统时必须知道的非显然约束和设计决策。

Header、目录段和全部 Entry 共同组成完整仓库索引，可以覆盖前端、后端、配置、数据库结构及其他受管理内容。受管理内容发生变化时，通常只需维护受影响的认知条目，不需要重新生成整个索引。

AOCI 提供系统架构、对象职责、重要关系、对外契约和关键约束的高密度视图。

### 工作原理

AOCI 采用“模型生成、模型读取”的认知闭环。

Header、Entry 和 Curation 语义的创作只按当前机器签发的 Plan 与实时 Guide 执行；由 Host 模型基于当前绑定证据独立完成。

Entry 的语义必须来自模型对真实证据的理解。不得仅依据路径、文件名、扩展名、AST、符号列表、依赖扫描、正则、固定模板或规则引擎推导、预填、拼接或改写索引语义。

对 Fresh Bootstrap，只按当前机器签发的 Plan 和实时 Guide 执行。当它们要求创作时，Host 模型创作 Root、Meta、Tag 和 F/R/A/S，提供 authoring-run 声明，并把它绑定到 Plan、Evidence 与完整 Candidate。不得要求 AOCI 填写 `origin=host_model`、制造 Receipt 或把程序生成的 Framework 当作语义。本文件不自行重建 Onboarding 流程。内部批次不是用户决策；只有遇到既有批准边界或真实的安全、漂移、CAS、Recovery 条件才停止。

### 最小使用入口

- `aoci_rules`：取得当前AOCI版本的会话运行合同。
- `aoci_overview`：建立或恢复本仓库的完整认知。
- `aoci_maintain`：受管理对象达到最终稳定状态后检查认知是否需要维护。
- `aoci_update_entry`：提交与当前证据和源码摘要绑定的完整语义更新批次。
- `aoci_report`：仅当当前布局和工具状态支持时，在证据不足、无法可靠生成语义时登记待办，不猜写。

其他MCP工具、CLI命令、参数和专项流程，以当前工具说明、Guide和 `--help` 返回内容为准，不在本文件中重复完整手册。

本区块只规定仓库接入、认知使用和收尾原则。`aoci_rules` 承载当前会话合同，Guide实时输出承载当前Plan的执行顺序与停点，工具Schema、Spec和Validator承载机器结构与判据；Prompt、Description、README和静态文档不能覆盖这些机器事实。

### 建立、生成和恢复认知

1. 每个新的 Agent Run 开始时，应先判断：

   - 本仓库是否已经存在可用的完整AOCI索引；
   - 当前上下文中是否已有与本仓库根、当前索引版本和当前AOCI服务相匹配，并且模型仍可可靠使用的完整仓库认知。

2. 仓库已经存在可用的完整索引，但当前Run没有可靠完整认知时，先调用 `aoci_rules`，再调用 `aoci_overview`。

   完整认知仍可靠时直接复用。局部不确定本身不要求机械重读系统全貌。

   本Run从已知Host上下文压缩恢复时（包括宿主注入的压缩摘要），必须把此前模型认知视为不可靠。压缩handoff不得保留或摘要正式Whole-Index，也不得保留或摘要任何Overview Header、Entry、Chunk、Challenge或Attestation正文；只能保留安全续接所需的receipt身份、未完成write或Recovery状态，以及立即重载指令。复制进handoff的Whole-Index语义或receipt不能证明恢复后模型的当前认知可靠。若当前上下文已无法可靠保留运行合同，先调用 `aoci_rules`。继续业务任务前，使用 `refresh_reasons=["context_compaction"]` 和新的 `refresh_event_id` 调用普通完整Whole-Index `aoci_overview`（不设置 `check_only` 或设为false）；不得使用 `check_only` 或认知probe。原样跟随每个 `next_cursor` 直到 `completed=true`，确认交付，并且只基于新交付正文提交一次Attestation。完成这次新的完整传输后，即使Attestation为partial或fail也消费该generation，并按既有合同继续source-bound任务，不再自动调用第二次Overview。

   AOCI可以针对 `context_compaction`、项目 `cognition_refresh_threshold` 下的机器 `semantic_threshold` 或主要 `phase_transition` 提供checkpoint与认知状态事实。只需要这些紧凑事实时使用 `check_only=true`；这些事实只向Agent提供建议，不替模型决定是否需要系统全貌。

   Agent显式调用普通 `aoci_overview`（未设置 `check_only` 或为false）时，只要能形成一致的CognitionSet，AOCI必须完整交付请求scope。不得因为已有receipt、阈值未达到或没有待处理刷新原因而抑制正文。正式认知Dirty或Stale时仍交付正文，但必须标记不可靠。存在未决恢复或无法形成一致snapshot时失败关闭，不返回混合正文。

   普通Overview返回 `continuation_required=true` 时，必须原样提交 `next_cursor` 并自动继续到 `completed=true`。不得询问用户、开始业务任务或给出阶段性系统结论。Host截断、缺块、重复、乱序、cursor失败、Index变化或`chunk_tokens`变化时停止本次认知链。Attestation完成前不得用Memory、源码、Spec、`aoci.txt`、历史会话、scope、search或Entry读取修补或补充Whole-Index认知。Challenge ordinal是正式Entry序列中的1-based位置；Header内容、注释、空行、Section/Overview/Chunk Marker、Receipt与Metadata均不计数，Chunk Receipt ordinal使用同一序列。Attestation必须原样回绑本次Challenge发布的当前`index_sha256`、`entry_sequence_sha256`与`entry_count`；旧Index、旧Entry序列、旧数量或旧Attestation均无效。完整链结束后只正式提交一次既有模型认知Attestation；同一响应只允许一次不改变语义答案的JSON Schema或字段格式修正。对象、Tag或F不匹配即失败且认知吸收不确定，不得语义重试或旁路补答。首次认知失败时还不得执行Root/Meta、Migration、全局布局或其他未重新绑定的系统级决策。上下文压缩刷新若传输完整、认知身份不变、治理对齐且没有Recovery或第三方冲突，即使Attestation为partial或fail也消耗该refresh generation，并继续原任务，不再自动重读Overview。`system_mastery_percent`只自评系统框架——架构、职责、强关系、稳定外部契约以及高熵安全和维护约束——不表示完整实现或运行实况知识；机器索引覆盖率必须分开。默认只向用户输出由本次真实覆盖率、Challenge、块数、Token和掌握度生成的规定成功或失败一句话。Host截断时提示用户把 `overview_delivery.chunk_tokens` 设置为更小的合法值后重新开始，不得自动修改。

   加法认知等级必须与严格证明字段分开解释。`delivery_verified`表示已加载Index且Host交付已确认，但完整认知验证仍未完成；应表达为“已加载且交付已验证”，不得描述为“没有认知”或“没有理解系统”。`cognition_verified`要求Attestation通过（Challenge至少80%的ordinal完全正确且对象身份至多失手一处），`cognition_governed`还要求治理对齐。通用完整读取失败句只用于真实交付故障。

   当Overview响应包含可选`cognition-state/v2`投影时，必须分别解释各维度。其Level止于`model_cognition_usable`；`strict_attestation_verified`、`governance_aligned`与`current_system_cognition_reliable`都是独立状态，绝不参与该Level。ordinal、对象身份、Tag或核心F不匹配可以导致严格Attestation失败，而模型认知仍然可用；不得仅凭这种不匹配就宣称模型没有理解系统。只有`current_system_cognition_reliable=true`允许无保留地声称当前完整系统认知可靠。投影缺失时继续使用上述Legacy解释。

   普通的只读审计、分析、检查、不修改代码或不提交、不push，不自动等于严格零写入，也不改变上述认知有效性判断。Codex Memory和历史Skill只能辅助恢复经验、用户偏好与调查方向，不能替代与当前仓库根、索引摘要、AOCI服务身份和认知范围匹配的当前认知收据；项目AGENTS和当前AOCI身份在AOCI状态上优先于历史Memory。

   只有用户明确禁止Ledger、元数据、`.aoci`运行资产及任何文件写入时，才按严格零写入处理。若必要的认知建立与该边界冲突，必须报告冲突并请求用户裁决或建议使用隔离副本，不得静默以Memory替代当前仓库认知。

3. 仓库没有可用的完整索引，或当前只有最小骨架、Header不完整、Entries未完成、必要Curation尚未裁决时，如果需要建立正式完整AOCI索引，先取得 `aoci_rules`，然后进入当前AOCI Guide。由Guide依据仓库真实状态决定下一阶段并完成必要安全步骤。

   `aoci_maintain` 不替代索引建立流程。

   不在本文件中自行重建或硬编码完整索引生成状态机。

4. 在长程任务中，模型负责保留当前认知收据并正确使用刷新门禁：

   - Host报告上下文压缩或模型已知系统全貌丢失时，执行上述强制 `context_compaction` 重载规则；AOCI不能自行推断Host事件；
   - 进入真正的主要阶段时声明 `phase_transition`，不得把函数、测试运行或小步骤当作阶段；
   - 在有用的稳定检查点通过 `check_only=true` 取得机器语义计数；
   - 除已知压缩的强制重载外，由Agent判断当前任务是否需要再次显式获取指定scope或完整Overview；
   - 在维护和对齐完成前，保留AOCI报告的Dirty或Stale可靠性状态。

### 任务收尾与认知维护

5. 纯只读问答、分析、版本核验，或没有产生受AOCI管理对象变化的任务，不需要调用维护工具。当前AOCI版本是任意`aoci_overview` check_only或`aoci_maintain`响应里的`cognition_receipt.mcp_service_version`；二进制路径是项目`.mcp.json`里的`command`，CLI不必在PATH上。

6. 发生受AOCI管理对象变化时，待其达到本次任务的最终稳定状态后，只调用一次 `aoci_maintain`。不要在每次中间修改后逐文件维护。

7. 若维护结果返回真实语义候选，Host 模型必须基于每个候选绑定的对象和必要证据，独立创作完整标签与F/R/A/S更新。通过 `aoci_update_entry` 一次提交当前机器签发批次的完整候选集合，同时原样保留每项 `source_sha256`、`candidate_id` 与对应domain批次身份。`max_entries`只限制单次请求和原子事务，不限制logical plan、Whole-Index或Managed Scope。`remaining`非零时，在当前批次成功Apply后重新调用Maintain并从新preimage继续；绝不能为满足transport上限缩减Index覆盖或自行截取返回批次。

   没有足够证据且当前布局支持 `aoci_report` 时，使用它而不猜测、套用模板或为消除待办而生成缺乏证据的认知。

8. 必须遵守工具返回的结构化状态和安全边界：

   - `repair_required`：只修复明确命中的候选，再重新提交当前机器签发的完整批次；
   - `stopped`：结束当前写入尝试并检查 `failed_step`、错误、正式写入证据与Recovery。auto模式下，已证明零写入则记录closure并重新Plan；完整Intent和可证明postimage则Resume；策略要求Rollback且preimage可证明则精确恢复后重新Plan。只有证据不足、第三方正式字节冲突、需要审批或外部动作，或命中其他真实安全边界时，才停止整个用户任务；
   - 冲突、审批、人工裁决、权限和安全信号不得忽略；
   - 已经对齐后不得重复维护或重复写入；`refresh_ready_for_overview` 是checkpoint事实，由Agent决定是否为下一阶段请求普通完整Overview。

   维护完成后如果又修改了任何受管理对象，之前的维护结果失效，应在新的最终稳定状态重新完成收尾。

9. 用户只限制业务文件范围，但没有明确禁止仓库托管资产时，AOCI托管资产可以在收尾阶段为保持认知一致而更新，并应在审计和提交中与业务文件区分。

   用户明确禁止修改 `aoci.txt`、`.aoci`、元数据或任何额外文件时，以用户限制为准，不得写入，并如实报告剩余不一致。

### 专项流程

初始化、完整索引生成、Header生成、Entries生成、数据库结构索引、Curation、人工评审和故障恢复，只按当前AOCI Guide或工具在对应阶段返回的指令、命令和安全停点执行。

不预加载、不猜测，也不自行重建这些专项流程。平台调用方式、请求格式、批次上限、审批规则、索引格式细节和恢复步骤由对应Guide、工具说明、模型Prompt和CLI帮助按需提供。
<!-- aoci:end -->

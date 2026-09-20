# AGENTS.md — 唯一的你（ai-girlfriend）项目 Agent 宪法

> **项目**：unique-you — 唯一的你·十四 — 基于 LLM 的智能情感陪伴系统
> **版本**：v1.19（2026-09-20 墙钟时区缺陷修复批次：**B1a/B1b 修复 + 公共时钟真源**——① 新 `utils/local_time.py::now_local()`，逻辑取自原先全项目唯一正确的一处 `proactive/ase_engine._local_now`（显式 UTC+8 回退），`_local_now` 改为**委托**它（保留函数名，`proactive/scheduler.py` 既有 import 与"静默时段必须共用同一时钟源"注释继续成立）；② **修复已生效缺陷**：`shisi/memory/legacy/memory_pipeline.py` 有 4 处墙钟判定误用 `datetime.now(tz=timezone.utc)`——`after_chat` 深夜情感加权（`:286`）、`daily_maintenance` 日记日期键（`:582`）、`get_formatted_context` 当日摘要查询键（`:686`）、`_do_fact_extraction` 的 `should_store_as_fact` 入参（`:778`）；UTC+8 部署下 `_is_late_night`（23:00–05:00）实落在**本地 07:00–13:59** → **"深夜情感记忆加权 importance +0.3"整体错位到上午/中午**（功能反向，非崩溃；精度更正：同一函数的 `should_store_as_fact` 规则 3 默认 `return True`，故规则 2 的布尔值与默认等价、**真实活影响只在 importance 加权**）+ 日记/当日摘要按 UTC 切日；③ **修复潜伏缺陷**：`my_character/enhanced_prompt_engine.py::TimeContext.now()` 由 UTC 改本地（生产 `prompt_mode: layered` 故此前未生效，但方向性错误 + 与 ASE 分段表分歧）；④ **删死代码** `my_character/persona_utils.py::build_time_context()`（全仓零调用者，且其内部调用的正是带缺陷的 `TimeContext.now()`——"看着像接好的线"，本次即被其误导过一次）（入 `DELETION_LOG`）；⑤ **有意保留 UTC**：`memory_pipeline` 的 `session_id` 生成（`:206`）与 `_apply_forgetting` 的 `updated_at`/`days_old` 时间差运算（`:738-739`）；⑥ 勘误：`dynamic_anchor.py:65 time_of_day` 原被列为"第三套分类器"，实为 `AnchorContext` dataclass 字段（默认 `"daytime"`）**全仓零 setter**，非分类器；观察项 `utils/important_dates.py:54` 用裸 `datetime.now()`（naive 本地）在生产 `TZ=Asia/Beijing` 下正确、但主机时区一旦变更即静默失效。**突变验红已做**：改回 `datetime.now(tz=timezone.utc)` → `test_mp_after_chat_feeds_local_clock_to_late_night` + 静态防护 `test_no_wall_clock_utc_regression_in_fixed_sites` **同时变红**（两用例与运行时刻无关，无墙钟巧合）；测试 **1323 收集/1319 通过/4 跳过**（=v1.18 口径 1307/1303 + 本批 16 用例：`tests/test_local_time.py` 13 + 深夜加权 3；分块实跑 241+386+328+364 精确吻合）+ ruff 0.16.8 全仓 0 错 + CI 门禁 4/4；端点数不变）。上一批次 v1.18（2026-09-20 提醒意图管线批次：修复「六点叫起床」事故全链路——①**三级意图管线**取代关键词裁决（新 `orchestrator/tool_gate.py`：L0 零成本晋级线只晋级不裁决→L1 LLM function calling 终审，全量工具+`ask_user` 伪工具三分支=调真工具/自然澄清提问/闲聊；防假承诺守卫拦截「听到啦」式空口承诺强制复核一次）；②**澄清状态机**（新表 `pending_intents`：会话级槽位合并、两轮上限=第二轮最优猜测+复述确认、15min TTL、话题转移作废）；③**到期投递闭环**（新 `proactive/reminder_delivery.py`+scheduler 每分钟轮询 `reminder_check`：session_key 定向投递微信 `send_text(to_user)`、**豁免静默时段**、文案 LLM 投递前一刻生成原文兜底、失败 3 次判 failed、存量无主提醒永不投递）；④**时区修复**（应用层北京时间比较，弃 SQL `datetime('now')` UTC 差 8h 口径）；⑤reminders 表迁移 +session_key/user_id/status/delivered_at/fail_count，DB 8→**9 表**；`_meta` 服务端注入调用归属（LLM 不可决定 user_id））。用户裁决：分级思路+LLM 真判断+澄清后调度（C 主干+A/B 配套+先调研）；调研留痕 arXiv 2511.08798 SAGE-Agent（澄清克制）/scallopbot（无回执不承诺、原文兜底）/CharacterWakeUp。生产实证：**端到端送达 1/1**（id=3 `delivered`@10:27:12，用户微信实收）+3 次失败判死 1/1；首验失败系微信 web 协议会话窗口失效（既有脆弱性 L2，非本批代码），重试机制兜住。测试 **1307 收集/1303 通过/4 跳过**（=v1.17 口径 1269+本批 34 新用例，分块实跑四块精确吻合）+ vitest 98/98 不变；`create_api_app` 内省 219=215+4 零端点变更。上一批次 v1.17（2026-09-20 回复质量根治批次：用户报障「回复生硬、固定回两句、不按话题演进」——上下文真源改 DB chat_history【重启不失忆/会话隔离/N:wxid 双形态合并】、fact_extractor 增 commitment 承诺类别、微信追问链 session_key↔裸 wxid 键错位双修复【ret=-3 全灭根因】、沉浸式 3~25 字放宽 10~80 字跟话题【禁动作/禁编造保留，小说式零改动】、41 卡 mes_example 多轮话题演进、19 卡句数硬限弹性化，+14 回归；上一批次 v1.16 全仓遍历·文档对齐批次，零代码变更：09-19 晚「每人独立微信通道隔离」大型批次（`3e66930`，+2195 行——per-user×slot 通道多租户/好友自选角色/DB +2 表/+11 端点）与「JWT-only 复核收口」（用户侧 API 仅 JWT，API Key 留机器/E2E，前端不含 Key 明文）落地后从未回扫代码实况文档，本次逐一历遍补齐——端点 204→**215**/唯一路径 **181**、include_router 16→**18**、routers 21→**22**、api/ 44→**45 文件**、DB **6→8 表**（wechat_channel_sessions/wechat_peer_preferences）、wechat_direct 2→**5 文件**（channel_paths/connector_registry/peer_character）、RoleSettings 六 tab→**五 tab**（StickersTab 撤除）、新增 reply_mode（沉浸式/小说式）与对话内追问 follow_up、安全 LLM 分类/注入检测默认关闭（规则闸门为实际生效层）、链双真源补注（生产主链仍 agnes 首选）；测试复测 1259 收集/1255 通过/4 跳过 + vitest 98/98 + tsc 0 错；上一批次 v1.15 提示词构建行业对齐（41 卡移除 scenario 根因级修复开场锚定 + prompt 重排 PHI 位/对话示例段/知识去重/人设片段精简，参照 SillyTavern 默认序列与 chara-card-spec-v2）；上一批次 v1.14 角色完善与文学导入（角色卡 25→41 张——《我的26岁女房客》《从你的全世界路过》《云边有个小卖部》《某某》《天堂旅行团》主要角色 16 张新卡全字段落库，既有 25 卡补数值字典/mes_example/修复伊蕾娜损坏字段，知识索引重建脚本补透传 + 检索双路交错合并修复，41 索引全量重建；上一批次 v1.13 主动消息「静默时段空耗配额」根因修复，v1.12 全仓扫描·文档对齐，v1.11 体验修复——人设注入三字段/主动消息连锁死锁/agnes 替换 sensenova/知识索引重建+BM25）
> **工作目录**：`D:\Desktop\ai-girlfriend`
> **Python**：3.10+（见 `pyproject.toml`）
> **主语言**：中文（代码注释遵循用户最新消息语言）

---

## 0. 项目身份

- **产品形态**：微信扫码即用的 LLM 智能情感陪伴系统，扫码登录后控制台调角色与语音
- **技术栈**：Python 3.10+ / React 19 / Vite 8 / TypeScript 6 / Tailwind 4 / Zustand 5 / 测试 1417（1319 Py 通过 + 4 跳过，98 FE 通过；2026-09-20 系统 Python 3.12 + vitest 实测，见 §4.3）/ MIT
- **核心能力**（见 `README.md`）：
  - 微信聊天（扫码登录，文字/语音，多用户独立；09-19 起每人独立微信通道——一人最多 2 条、好友可回复「角色」自选扮演角色）
  - 角色系统（每用户绑角色卡，性格/风格/口头禅可调）
  - 情感引擎（亲密度、情感阶段变化）
  - 主动搭话（不全是被动等待）
  - 语音合成（MiMo 云唯一引擎 + Windows SAPI 本地兜底；Edge-TTS 等已于 08-28 删除）
  - 记忆系统（三层：短期 + 情景 + 长期）
  - 工具（天气、日历、提醒、搜索）
  - 剧情线（支线、进度追踪）
  - 邀请码注册 + 管理控制台（17 个页面）
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
| 入口 | 后端开发 | `main.py`（控制台/微信）或 `python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000`；`start_all.cmd` 已于 2026-08-28 删除 |
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
| 微信集成 | 集成开发 | `wechat_direct/` / `shisi/`（DDD 核心 plane: affinity/emotion_stage/persona/stats/vital_signs） |
| 克隆训练 | 训练开发 | `clone_training/`（weclone_adapter 已于 08-28 删除，克隆收敛为本地提取+JSON 上传） |
| 前端 | 前端开发 | `frontend/`（React 19 + Vite 8） |
| 部署 | 部署开发 | `deploy/`（bat/ps1 双版本部署脚本已于 08-28 删除） |
| 可观测性 | 运维开发 | `observability/` |
| 安全 | 安全开发 | `security/` |
| 测试 | QA | `tests/`（1319 Python 测试通过 + 4 跳过；前端 98 测试；2026-09-20 实测，口径同 CODE_GRAPH §1.1) |
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

- 626+ 测试已建立，新功能必须附测试（实测基线:1319 Python 通过 + 4 跳过 / 98 前端，2026-09-20 实跑,与 CODE_GRAPH §1.1 对齐)
- 用户明确反对采样验证，要求完整验证
- **测试口径注记（2026-09-20 七次刷新）**：系统 Python 3.12 实测 **1323 收集 / 1319 通过 / 4 跳过**（七次刷新：本批 +16 墙钟时区修复回归 —— `tests/test_local_time.py` 13【now_local 时区契约 2 / `_is_late_night` 边界参数化 6 + 语义钉死 1 / `TimeContext` 本地时钟 2 / 静态防护 1 / `_local_now` 委托 1】+ `tests/test_memory_pipeline.py` 3【喂本地时钟 1 + 深夜加权正/反 2】；分块实跑四块 **241+386+328+364** 与收集精确吻合）（上批六次刷新 **1307 收集 / 1303 通过**：+34 提醒意图管线回归 test_reminder_intent_pipeline 15 晋级线参数化 + 7 终审三分支/防假承诺 + 3 澄清状态机 + 6 到期投递 + 2 工具层 + 1 老库迁移）（上批五次刷新 +14 回复质量根治回归 test_reply_quality_overhaul）（更早 +168 = 本批角色卡扩充 +34【16 新卡 × persona 参数化 2 用例】+ 知识库修复回归 5【交错合并 2 + knowledge 路由建索引 3】+ 09-19 晚通道隔离等先前提交增量约 131 未同步文档）+ 前端 vitest **98/98**（16 文件）。**角色卡现役 41 张**（25 既有 + 16 文学导入），persona 注入参数化用例数 = 2 × 卡数，卡数变动会动基数。
  ⚠️ **已知环境问题：单进程整跑 `pytest -q` 会在 30%~97% 之间的**随机位置**停住**（三次实测分别停在 `test_integration.py` / `test_web_enricher.py` / `test_llm_providers_routes.py`），且这三个文件**单独跑全部通过** —— 属聚合态资源问题（疑似前序用例泄漏线程/事件循环），**不是某个用例失败**。分块跑可稳定复现完整基线，推荐工作流：
  ```bash
  files=$(ls tests/*.py tests/core/*.py | grep test_ | sort)
  for i in 0 1 2 3; do echo "$files" | awk -v i=$i 'NR%4==i' | tr '\n' ' ' > /tmp/c$i.txt; done
  for i in 0 1 2 3; do timeout 200 env PYTHONPATH= python -m pytest $(cat /tmp/c$i.txt) -q -p no:cacheprovider; done
  ```
  四块结果相加应等于 `--collect-only` 的收集数（当前 **1323**；2026-09-20 复测四块 241+386+328+364 精确吻合，`test_knowledge_routes_index.py` 已在分块清单内，无需再单独跑）。
- **测试口径注记（2026-09-17 二次刷新）**：历史文档口径 1117（1042 Python + 75 前端，2026-09-01 .venv 实测）——该环境随 09-14 主仓事故丢失，此后不再作为可复现基线。**当时口径**：系统 Python 3.12 实测 **1064 收集 / 1060 通过 / 4 跳过**（2026-09-18 双角色库收敛后复测，166.48s；较上批 +48 = config 25 张卡 × `test_persona_injection` 每卡 2 个参数化用例全覆盖）+ 前端 vitest **87/87**（15 文件）+ `tsc --noEmit` 0 错误。跑测试：`PYTHONPATH= python -m pytest -q`（`PYTHONPATH=` 前缀用于清空宿主注入的 safe-delete 护栏，见本机环境注记）。
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
| 一键启动 | ~~`start_all.cmd`~~（**已删除 2026-08-28**）→ `python main.py` 或 `python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000` + `cd frontend && npm run dev` |
| 部署打包 | ~~`deploy_ai_girlfriend.bat`~~（**已删除 2026-08-28**）→ 部署统一走 `deploy/`（systemd + nginx 模板 + remote_deploy.sh） |

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
| L6 | 语音合成优先级 — **MiMo Cloud TTS 为唯一引擎**（2026-08-28 起；Edge-TTS / 本地模型已删除）。不可假设单一源永久可用，故障时走可观测告告警而非静默切换到已删除引擎 |
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

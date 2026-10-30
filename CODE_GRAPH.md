# 代码图谱 — unique-you (唯一的你) **v3.8.36**（2026-10-30 批7 收尾口径）

> **v3.8.36（2026-10-30 批7 收尾 · 本地 15 提交领先 origin、未部署）**：D20 前端全站体验六批（批 1–6：`908db1c` P0 止血 → `38e517c` 契约修复 → `6d57e30` admin 数据面 → `cbdaf96` 首日旅程与教学 → `a91ed34` 个性化与 IA → `69c1d71` 收尾）+ 批 6 对抗验收收编（`69aecd2`：F1 教学页死路由 / F4 公告 XSS scheme 白名单 / F5 毫秒时间戳守卫 + F9 端点口径更正 101→106；`f9ccfd4`：F2 last-admin 删除不变式测试）+ **批 7 四收编**（`d182b6c` NEW-20 高危操作二次确认 / `c6ed735` NEW-31 组件库收编（SubTabBar 删除）/ `65e0fee` NEW-32 设计 token（+`DesignTokens` 守卫测试）/ `9815c27` NEW-22 用户列表排序全页化（sort/sort_dir 白名单 7 键 + `tests/test_admin_users_sort.py` 28 例））。端点 235/197 → **249/205**（+14 全部来自批 1–6 体验面四组；**批 7 零新端点**，NEW-22 仅给既有端点增查询参数）；新增 §4.16。测试口径（主控 2026-10-30 实测）：后端四分块 **4296 通过 / 2 跳过 / 3 个存量失败族**（均 HEAD 对照实证与批 7 无关）+ ruff 0；前端 vitest **342/342（47 文件）** + tsc 0；**突变验红 4/4**。⚠️ 角色卡 42→**41**（`99c89c39.json` 变体已不在盘，gitignore 无轨迹，待主控定性——见 §1.1）。读数见 §1.1。
> **结构真源 = AOCI**（`aoci.txt` 卷头 → `aoci.code.txt` 实体条目 **1116 行** → `aoci.meta.txt` 词表）；本文件只保留人读的规模读数、分层骨架与维护指针。**历史版本（v3.8.35 话题状态机/拟人化部署批 / v3.8.34 八项裁决批 / v3.8.33 重建批 / v1.55·v1.54 质量批）要点压缩归档至 `LOG.md` 对应条目，本文件不再逐条保留**。
> 批次详情、生产取证与历史版本见 `LOG.md`（唯一只追加不覆盖文件）。

> 由维护者手动维护 | **最后核实: 2026-10-30（v3.8.36 批7 收尾口径）**，读数命令见各节「核实方法」列；更早的核实记录一律见 `LOG.md` 对应批次条目。
> **读出纪律**：① 引用测试基线必须同时声明**角色卡数**与**工作树是否含在制品**（见 §1.1 表下注）；② 端点口径只认 `APIRoute` 实例数，不认 `len(app.routes)`（后者恒定多 4 条框架路由）。

---

## 1. 全局指标

### 1.1 实时核实指标（2026-10-30 本批亲跑）

| 维度 | 数值 | 核实方法 |
|------|------|---------|
| 仓库 HEAD | **`9815c27`**（批 7 尾：NEW-22 用户列表排序全页化）；**未部署**——本地 **15 提交**领先 `origin/main`（`cdbe546`）= 12 实现（批 1–6 ×6 + 批 6 对抗收编 ×2 + 批 7 ×4）+ 3 文档（LOG×2 / AGENTS×1）；**工作树 5 项在制品**（本图谱自身 + 主控并行收口写入的 AGENTS.md（v1.61）/ LOG.md（批 7 条目）/ docs/HANDOFF_REPORT.md（v1.61）/ docs/DELETION_LOG.md（NEW-31 补登）——**全部 `.md` = B 档文档，业务代码零在制品**） | `git log --oneline origin/main..HEAD`（本批实跑 = 15）+ `git status --porcelain`（本批实跑 = 5 项 `M`，后缀全 `.md`） |
| API 业务端点 | **249 `APIRoute` / 205 条唯一路径**；`len(app.routes)` = **253**（含 4 条框架路由 `/openapi.json` `/docs` `/docs/oauth2-redirect` `/redoc`） | `create_api_app()` 内省（本批实跑，`AI_GF_ENV=dev`）：`len([r for r in app.routes if isinstance(r, APIRoute)])` = **249**；唯一路径 **205**。较 v3.8.35 的 235/197 增 **14**——全部来自批 1–6 体验面：`POST /api/user/llm-config/test` ×1 + `/api/user/humanize` ×4（NEW-34）+ `/api/announcements` ×2 + `/api/admin/announcements` ×4（V2-5）+ `/api/admin/audit` + `/api/admin/global-view` ×2（NEW-21/V2-1）；**批 7 零新端点**（NEW-22 仅增查询参数） |
| 方法分布 | **118 GET / 90 POST / 18 PUT / 23 DELETE** | 同上，按 `route.methods` 计数（本批实跑） |
| 路由挂载 | 字面 **22 处 `app.include_router(`**（其中 1 处在 `_mount_required_router` 辅助函数内 `:361`）+ **9 处 `_mount_required_router(...)` 调用** → 运行期路由挂载点 **30 处**；另加 `setup_shisi` 一处（`api/app_factory.py:297-302`） | `grep -c "app\.include_router(" api/app_factory.py` = 22；`grep -c "_mount_required_router("` = 10（含 def，本批实跑）。公告双挂载 `announcement_router`/`announcement_admin_router` @ `:276-277`；`topic_state_router` @ `:273` |
| 域路由模块 | **26 个**（`api/routers/*.py` 不含 `__init__.py`；26 个全部带路由；+`announcement_routes.py` 一双 router 文件） | `ls api/routers/*.py \| grep -v __init__ \| wc -l`（本批实跑） |
| Python 规模（tracked = HEAD） | **594 文件 / 161,346 行** | `git ls-files '*.py' \| wc -l` + `xargs cat \| wc -l`（本批实跑 @ `9815c27`） |
| Python 规模（磁盘全量） | **755 文件 / 199,840 行**（剔除 `.git` / `node_modules` / `__pycache__`）；其中含 `大创赛报名以及后期发展/` 软著代码副本（gitignored，不入库） | `find . -name '*.py' -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*__pycache__*'` + `xargs cat \| wc -l`（本批实跑） |
| main.py | **573 行** | `wc -l main.py`（本批实跑，未变） |
| orchestrator/ | **9 文件 / 5,154 行**：`optimized_orchestrator.py` **2464**、`_stream_mixin.py` **607**、`context_budget.py` **666**、`_init_mixin.py` **589**（11 个 `_init_*` 阶段）、`tool_gate.py` **387**、`session_locks.py` 175、`console_chat.py` 134、`voice_detector.py` 108、`__init__.py` 24 | `wc -l orchestrator/*.py`（本批实跑；批 1–7 后端改动未触及 orchestrator/，`git diff bdab4f7..HEAD -- orchestrator/` 为空） |
| Python 测试 | **269 测试文件 / 主控实测 4296 通过 + 2 跳过 / 3 个存量失败族**（① `tests/test_api_routes.py` sqlalchemy **14 errors**② `test_w10_metrics_multiproc` 503→404 口径债③ `test_w3_scheduler_state` Depends 形态——**三族均 HEAD 对照实证、与批 7 无关**）；ruff **0**；收集总数本版未复测（本任务禁跑 pytest，以主控口径为准） | 文件数本批 `find tests -name 'test_*.py' \| wc -l` 与 `git ls-files` **双重实测 269**（+3 新文件 = `tests/api/test_announcements_api.py` / `tests/test_admin_routes_last_admin.py` / `tests/test_admin_users_sort.py`，且批 1–7 无测试文件删除）；⚠️ 主控 LOG/HANDOFF/v1.61 口径作 **268**，与实读差 1——以实读为准，留主控对账；通过/跳过/失败族引主控 2026-10-30 四分块实测 |
| 前端测试 | **342 通过 / 47 文件**（vitest ^5） | 文件数本批实测 `find frontend/src \( -name '*.test.*' -o -name '*.spec.*' \) \| wc -l` = **47**（36→47，+11 新文件清单见 §4.9）；用例数 342 + tsc 0 引主控 2026-10-30 实测口径 |
| 测试合计（徽章） | **4639**（4296 Py 通过 + 342 FE + 1 跳过标记） | ⚠️ `README.md:11` 徽章仍为 `Tests-4508`（v3.8.35 口径）——**徽章同步归收口批，本版不动 README**（本批 grep 复核） |
| 现役角色卡 | **41 张**（`config/characters/*.json`）——⚠️ **漂移登记**：v3.8.35 口径 42，`99c89c39.json`（user_id=1 林挽夏克隆变体）已不在盘，`find .` 全仓无踪；目录被 `.gitignore` 忽略故无 git 轨迹可追，**原因待主控定性** | `ls config/characters/*.json \| wc -l`（本批实跑 41）+ `find . -name '99c89c39*' -not -path './.git/*'`（无踪）。**卡数不随 git 复现** |
| 模块 .py/.ts/.tsx 计数 | **api 51 / orchestrator 9 / proactive 14 / shisi 116 / utils 26 / tools 13 / voice 8 / wechat_direct 4 / frontend/src 154** | `find <pkg> -name '*.py' \| wc -l` ×8 + `find frontend/src \( -name '*.ts' -o -name '*.tsx' \) \| wc -l`（本批逐项实跑；api +2 = `audit.py` + `announcement_routes.py`；frontend/src 138→154） |
| 前端页面 / API 模块 / store | **20 页 / 16 API 模块 / 6 store**（auth / characterBuilder / error / **globalView** / humanize / topicState） | `ls frontend/src/pages/*.tsx`、`ls frontend/src/api/*.ts`、`ls frontend/src/store/`（本批实跑；+`HelpGuidePage` / +`announcements.ts` / +`globalViewStore.ts`） |
| tools/builtin | **9 文件 / 17 个工具类**；默认注册 **14**（`config/system.yaml` `tools.builtin_tools`） | `find tools/builtin -name '*.py' \| wc -l` = 9；`grep -h "^class .*Tool" tools/builtin/*.py \| wc -l` = 17（本批复测，数值未变） |
| 数据库 | **本地 `data/sqlite.db` 35 表**（不变；无 topic 三表 = **惰性建表未触发**）；**新增口径 `data/users.db` 14 表**（10 → 14：+`announcements` / `announcement_reads` / `operator_prefs` / `operator_audit` 四张批 3–4 新表，纯新增 create_all 幂等；model 真源 `api/database.py:406/:447/:468/:487`） | python sqlite3 `sqlite_master` 枚举（本批实跑：sqlite.db 35 表 / users.db 14 表含 `sqlite_sequence`）；topic 三表 schema 真源 = `utils/topic_state.py:86/:107/:119`（生产三表已在位） |

> ⚠️ **工作树口径（v3.8.36）**：本版数值读数采集于 HEAD=`9815c27`、工作树 **5 项 `.md` 在制品**（本图谱 + 主控并行收口的 AGENTS/LOG/HANDOFF/DELETION_LOG 四件）时点——**业务代码（A 档 .py/.ts）零在制品**，§1.1 全部数值读数即 HEAD 实况；按读出纪律①，引用 v3.8.36 须注明「工作树含 5 项 B 档文档在制品（含主控并行收口写入）」。
> ⚠️ **聚合态跑法**：单进程整跑 `pytest -q` 会在随机位置停住（环境问题，非用例失败），须**串行**四分块；分块清单必须 `find` 递归生成——本批实测 **269** 个 `test_*.py`（含 `tests/core/` 与 `tests/api/`；漏子目录会少跑一批用例，10-17 批已实证漏 `tests/api/` 少 32 例）。
> ✅ **基线公式复核**：`tests/test_persona_injection.py` 单独收集 = **2 × 卡数 + 13**；卡数已由 42 漂移为 **41**（见上表「现役角色卡」行），即现行公式 2×41+13 = **95**，卡数变则全量收集数随之浮动。
> ✅ **3 个存量失败族定性（2026-10-30 主控实测）**：① `tests/test_api_routes.py` **14 errors**（sqlalchemy 形态债）② `test_w10_metrics_multiproc`（503→404 口径债）③ `test_w3_scheduler_state`（Depends 形态）——三族均已 **HEAD 对照实证与批 7 无关**，归专项口径债清理；批 7 自身四分块 4296 通过 + 2 跳过、ruff 0、**突变验红 4/4**（M1 排序闸 / M2 停用确认 / M3 token 回潮 / M4 Button 收编）。
> ✅ **2 例跳过为非回归**：`tests/test_cache.py:297`（Redis not available）+ `tests/golden/test_golden_set.py:48`（`live_llm` 用例默认不跑，设 `GOLDEN_LIVE=1` 才启用真实供应商）。
> ✅ **跨文档口径**：`AGENTS.md` **v1.61** 版本头 + `LOG.md` 批 7 条目 + `docs/HANDOFF_REPORT.md` v1.61 顶栏 + `docs/DELETION_LOG.md` NEW-31 补登——主控并行收口与本图谱回扫**同帧进行**（F9 端点口径更正 101→106、批 7 突变验红 4/4、3 存量失败族定性均互洽）；**待同步**：`README.md:11` 徽章 4508→**4639**（收口批）、CODEMAPS 六件（CODEMAPS 对齐随本收口批提交后本行应删）。测试文件口径已对账为 **269**（本图谱双重实读；主控文档中原 268 系笔误、已随本收口批更正）。

### 1.2 图谱工具快照（历史归档）

> **历史归档**：codebase-memory 图谱（节点/边/Leiden 社区/fan-in 榜单）最后一轮有效索引为 2026-09-02（7997 节点 / 33187 边），产物目录 `.codebase-memory/` 已被 `.gitignore:131` 忽略且**当前磁盘不存在**——本文件不再承载其读数。重索引命令与查询方式见 §12；历史读数见 `LOG.md` 2026-08-28 / 2026-09-02 条。

---

## 2. 架构分层（文字版，替代旧图谱 mermaid）

请求自外向内四层，**调用方向单向**（越界即循环依赖风险）：

1. **入口层**：`main.py`（`_run_orchestrator`，573 行）/ `api/run_api.py`（uvicorn，`create_api_app()` @ `:626`）。
2. **接口层**：`api/`（`app_factory.py:94 create_api_app()` + 25 域路由 + `setup_shisi`）与 `shisi/api/`（DDD 核心域 30 端点）。
3. **编排层**：`orchestrator/`（`OptimizedOrchestrator` 三件套 + `tool_gate` / `context_budget` / `session_locks`）。
4. **领域与服务层**：`shisi/`（DDD 核心）、`my_character/`、`persona_extractor/`、`security/`、`llm_provider/`、`proactive/`、`voice/`、`wechat_direct/`、`tools/`、`utils/`。

> **历史归档**：旧版「知识图谱基于 fan-in/fan-out 自动识别的 4 层 mermaid」为图谱产物（含 226 路由旧读数），已删除；分层判断以本节与 §4 模块表为准。

---

## 3. 核心数据流 — 主链热路径

`OptimizedOrchestrator.process_message`（`orchestrator/optimized_orchestrator.py:2038`）是全系统最关键调用链；流式走 `_stream_mixin.process_message_stream`（`orchestrator/_stream_mixin.py:216`）。上下文准备 `_prepare_context`（`:1038`）、出站定稿 `_finalize_reply`（`:1952`）、后处理 `_after_process`（`:1704`）。

```
用户消息(text, session_id)
 ├─ 会话锁 _get_session_lock / ProcessSessionLock（跨 worker 互斥）
 ├─ 安全线：PIIAnonymizer → PromptInjectionDetector → ContentSafetyFilter.check_input
 ├─ 上下文（并发 gather）
 │    ├─ 历史：DB 会话过滤真源 + annotate_history_time_gaps(context_budget，
 │    │        相邻 ≥6h 插 [时段分隔] 头注/翻篇尾注) + 历史回灌就地净化(沉浸式剥括号/删元解释)
 │    │        + 会话尾巴净化 scrub_history_rows(prompt_sanitize.py:922)
 │    ├─ 记忆：retrieve_context / get_recent_context（按 user_key 隔离）
 │    ├─ 话题状态（3fe2771）：get_topic_snapshot（utils/topic_state.py:889，快照任务
 │    │        optimized_orchestrator:1152-1161，TOPIC_SNAPSHOT_TIMEOUT_SECONDS=0.2 @:64 只读不阻塞）
 │    │        → _attach_topic_state(:67/:1401) → render_topic_state_block 状态块（≤160 字）注入
 │    ├─ 人格：PersonaExtractor → PersonaService.build_system_prompt
 │    │        Phase 1 shisi prompt_builder(角色+RAG+情感+历史；RAG 片 dedup_rag_snippets 去重)
 │    │        → Phase 2 五层对齐；「# 行为规则」段（4B 行为蒸馏 §4.14，两字段皆空整段省略）
 │    │        沉浸式：mes_example / first_mes / knowledge_context / rag_context 先 strip_stage_directions
 │    │        能力声明段按真实可用性渲染（_memory_recall_available() 唯一判定，:183）
 │    └─ 话题锚点：_extract_user_anchor_sentences(prompt_sanitize.py:308，只取 user 行完整短句，24h 窗)
 ├─ 工具：ToolRegistry.get_tools_by_permission → tool_gate L0/L1 → ToolDispatcher.dispatch
 │        L0 memory 工具晋级规则（『你还记得…/我说过…吗』→ memory）
 ├─ LLM：MultiProviderGateway（Agnes→智谱→讯飞→千帆；熔断阈值 2 / 冷却 120s / agnes 40s）
 │        轮次预算总账：llm_deadline = now + TOTAL_TURN_LLM_BUDGET(100.0s，:165)；
 │        各跳取 min(单跳额度, remaining_turn_llm_budget(:168))；主链主跳 = MAIN_CHAIN_LLM_TIMEOUT_SECONDS(60.0s，:147)
 ├─ 出站终审：terminal_reply_guard(utils/prompt_sanitize.py:1058)
 │        逐字复述剥除 / 元记忆解释+零信息问卷句级删 / 归属翻转句删 / 无由头「你呢…？」截断 /
 │        沉浸式动作段剥离（utils/reply_mode.py:66 _STAGE_DIRECTION_RE：圆括号/星号/[]/【】
 │        四形态单一真源，character few-shot / RAG 注入 / 出站守卫三消费点同源，c3ede77）/
 │        系统标记剥除 / 称呼呼语剥除
 │        真流式：IncrementalReplyGuard(stream_guard.py:207) 按句边增量下发，已发前缀永不回滚，
 │        未发余量流末交 _finalize_reply(:1952) 终裁
 ├─ 拟人节奏（v1.57）：supersede 登记(:2080-2081) + 判定(:2141-2150，superseded → 静默取消
 │        返回 {"reply":"","superseded":True})；阅读延迟 compute_read_delay_ms
 │        (utils/humanize_read_delay.py:79) + typing（iLink sendtyping，失败静默 no-op）；
 │        节奏判定入口 @:2371-2380（返回 superseded/delay_ms/typing）
 └─ 后处理：MemoryPipeline.after_chat → ASEEngine.on_chat → AffinityMapper.sync → 画像同步智能体
          → 话题状态机（_after_process:1704）：_run_topic_judge(:1762，每 4 轮稀疏 LLM，8s 硬超时
          失败保留原状态) + _safe_topic_then_after_chat(:1800) → update_after_turn(topic_state.py:922)
```

> **本链的已知叠加层**：`reply_mode`（`utils/reply_mode.py`，275 行；`DEFAULT_REPLY_MODE = "immersive"` @`:45`；`read_reply_mode(user_key)` 支持 per-user 覆盖表 `user_reply_mode`）决定括号段存废并持有 `_STAGE_DIRECTION_RE` 剥离正则单一真源；`utils/address_terms.py`（237 行）为称呼白名单唯一真源；`utils/session_key.py`（165 行）为 `owner:peer` 会话键唯一真源；`utils/stream_guard.py`（597 行，`IncrementalReplyGuard` @`:207`）为出站增量守卫状态机；`utils/topic_state.py`（1769 行）为话题状态机唯一 owner（§4.12）；`shisi/core/conversation_turn.py`（177 行）为「轮 + 发言者归属」唯一真源。
> **历史归档**：2026-07-01 的 PersonaService 两阶段说明仍有效（见上），但当年 5 层对齐的中间态细节与 07-26 拆分过程见 `LOG.md`。

---

## 4. 模块目录与职责

### 4.1 入口与编排

| 模块 | 文件 | 职责 |
|------|------|------|
| `main.py` | **573 行** | 入口 + `_run_orchestrator`（`:469`）统一启动 + `_wire_proactive_runtime`（`:281`，提醒/主动两入口唯一装配 owner）+ 控制台/微信模式 |
| `orchestrator/` | **9 文件包 / 5,154 行** | `optimized_orchestrator.py` **2464 行**（主类 + `_prepare_context` `:1038` / `process_message` `:2038` / `_finalize_reply` `:1952` / `_after_process` `:1704` / `health_check`；**轮次 LLM 预算总账** `TOTAL_TURN_LLM_BUDGET=100.0` `:165` / `remaining_turn_llm_budget()` `:168` / `MAIN_CHAIN_LLM_TIMEOUT_SECONDS=60.0` `:147`；`TOPIC_SNAPSHOT_TIMEOUT_SECONDS=0.2` `:64` + `_attach_topic_state` `:67`（话题状态机接线 §4.12）；`_memory_recall_available()` `:183` 为能力声明唯一判定）+ `_init_mixin.py` **589 行**（11 个 `_init_*` 阶段，唯一初始化真源）+ `_stream_mixin.py` **607 行**（`process_message_stream` `:216` 真流式 SSE：按句边 `IncrementalReplyGuard` 增量下发；`_finalize_reply` 契约 @`:84`，补传 `session_id`/`last_turn_age_seconds`）+ `context_budget.py` **666 行**（预算/去重/时间分段/信封；`dedup_rag_snippets`）+ `tool_gate.py` **387 行**（三级工具意图管线；L0 memory 工具晋级）+ `session_locks.py` 175 + `console_chat.py` 134 + `voice_detector.py` 108 + `__init__.py` 24 |
| `api/run_api.py` | 642 行 | API-Only 启动入口（`create_api_app()` @ `:626`，import 于 `:45`）；`_ensure_scheduler_singleton()`（`:222`，flock @ `:245-250`，锁文件 `/tmp/ai-girlfriend-scheduler.lock`）；`_autostart_wechat_connector()`（`:500`）按 `channel_paths` 逐 user/slot 恢复 |
| `user_scheduler.py` | 747 行 | 多用户调度，每个微信用户独立情感状态 |

> **架构演进（历史归档）**：`_run_fast_mode` + `_run_full_mode` 于 2026-07-28 合并为 `_run_orchestrator`，`console_chat` 已自 main.py 迁出——详见 `LOG.md` 2026-07-28 / 2026-08-28 条。

### 4.2 API 层（**249 业务端点 / 205 唯一路径** — 2026-10-30 批7 收尾口径内省实扫）

| 来源 | 路径 | 端点数 | 文件数 | 说明 |
|------|------|--------|------|------|
| `api/routers/` | **26 个域路由模块**（不含 `__init__.py`；26 个全部带路由） | **214** | 27（含 `__init__.py`） | character / auth / admin / invite / voice / mimo_voice / storyline / wechat / wechat_channel / emotion / memory / knowledge / persona_card / chat / clone / misc / personality / safety / tools / training / users / llm_providers / agent_plane / character_template / topic / **announcement（批 4 新增，一双 router 文件）** |
| `api/`（非 routers） | `health_routes.py` / `qrcode_store.py` / `app_factory.py` 内联 | **5** | 3 | health 3（含 `/api/metrics`）+ qrcode 1（admin 兼容面）+ app_factory 内联 `/api/shisi/status` 1（untagged） |
| `shisi/api/` | v1（`v2/` 死模块 2026-09-18 删除） | **30** | 11（其中 **8** 个文件带路由） | character 7 / memory 6 / sticker 5 / affinity 4 / emotion_stage 3 / persona 3 / stats 1 / vital_signs 1（+ 上面内联 status = 31 域面）；`__init__` / `common` / `registry` 无路由 |
| **合计** | | **249** | | 118 GET / 90 POST / 18 PUT / 23 DELETE；`len(app.routes)`=253 |

> 🔢 **两个数字不是同一件事**（易误读，特此点明；本批用 `inspect.getmodule` 分组实测）：表中 `api/routers/` 行的 **214 = 该层端点数**（26 文件）；§1.1 的 **205 = 全站唯一路径数**。校验：214（routers）+ 30（shisi/api，8 个文件带路由）+ **5**（api 非 routers：health 3 + qrcode 1 + app_factory 内联 1）= **249** = `APIRoute` 总数 ✓。

> ⚠️ **口径纠错**：旧口径「206 / 208 端点」取自 `len(app.routes)`，其中固定含 4 条 FastAPI 框架路由，系统性偏高 4。**业务端点 = `APIRoute` 实例数**。演进链：2026-09-21 `220/186` → +W4 forwards 1 +W10 metrics 1 +W12 templates 2 +W9 自服务 5 = 229 → D16 退役 peers 3 = **226/193** → v3.8.33 重建批 **229/194** → v3.8.34 **231/195**（R7 reply-mode 2）→ v3.8.35 **235/197**（+4 = `/api/topic-state` ×3 + `DELETE /api/humanize/config`）→ **v3.8.36** 实测 **249/205**（+14 = 批 1–6 体验面四组：misc +1 / training +4 / announcements +6 / admin +3；**批 7 零新端点**）。

**按 tag 的端点分布**（内省实测，权威口径；较 v3.8.35 变化：**training 19→23** / **misc 16→17** / **admin 5→8** / 新增 **announcements 2 + admin-announcements 4**，其余一致）：

```
training 23 │ character 21 │ misc 17 │ auth 13 │ safety-infra 12 │ chat 11
memory 11 │ personality 10 │ wechat 9 │ clone 8 │ knowledge 8 │ admin 8
users 7 │ characters 7 │ tools 6 │ wechat-channel 6 │ voice 6 │ mimo-tts 6
storyline 6 │ llm-providers 6 │ agent-plane 5 │ stickers 5 │ affinity 4 │ invite 4
admin-announcements 4 │ health 3 │ topic-state 3 │ emotion-stage 3 │ persona 3 │ persona-card 3
announcements 2 │ admin-wechat 2 │ character-templates 2 │ emotion 2 │ vital-signs 1 │ stats 1
(untagged) 1
                                          ────────── 合计 249
```

> 注：`character`(21) = `api/routers/character_routes.py`；`characters`(7) = `shisi/api/character_routes.py`。`memory`(11) = api `memory_routes`(5) + shisi `memory_routes`(6)。`auth`(13) = 原 8 + W9 自服务 5。`training`(23) = training 5 + proactive 6 + humanize/config 4 + knowledge/collect-config 2 + reply-mode 2 + **user/humanize 自助 4**（NEW-34，`/api/user/humanize` GET/POST/PUT/DELETE）——`api/routers/training_routes.py` 单文件承载六组。`misc`(17) = 原 16 + **`POST /api/user/llm-config/test`**（NEW-13 BYOK 连通性）。`admin`(8) = 原 5 + **audit 1 + global-view 2**（NEW-21 / V2-1）。`announcements`(2) + `admin-announcements`(4) = `api/routers/announcement_routes.py` 一双 router 文件（V2-5）。`wechat-channel`(6) + `admin-wechat`(2) 同属 `wechat_channel_routes.py`。`(untagged)`(1) = app_factory 内联 `/api/shisi/status`。

> 📐 **批 6 收编 F9 更正（子集口径注记）**：`tests/test_api_routes.py:171-189` 契约断言「9 子路由合计 = **97**」（misc 17 / chat 11 / personality 10 / users 7 / training 23 / tools 6 / safety 12 / clone 8 / topic 3）；批 6 收编登记的全口径 **106** = 97 + announcements 6 + admin 新增 3（原记 101 系算术错误，`69aecd2` 更正）。⚠️ 该 106 为**管理/自助面子集记账**，图谱权威口径始终以本表 `APIRoute` 全量 **249/205** 为准。

**`api/app_factory.py:94 create_api_app()`** 是 FastAPI 应用唯一构造入口（`FastAPI(..., version="3.1.0")`），被 `api/run_api.py:626` 与 `main.py` 调用。挂载清单（源码逐行核对；22 处字面 `app.include_router`（含辅助函数内 1 处）+ 9 处 `_mount_required_router` 辅助调用 + `setup_shisi` @`:297-302`；`topic_state_router` @`:273`，公告双挂载 @`:276-277`）：

**按定义模块的端点归属**（`inspect.getmodule(route.endpoint)` 实测，合计 214 + 30 + 5 = **249**）：

```
api/routers/（26 文件，214 端点）
  training_routes 23 │ character_routes 21 │ misc_routes 17 │ auth_routes 13
  safety_routes 12 │ chat_routes 11 │ personality_routes 10 │ clone_routes 8
  knowledge_routes 8 │ wechat_channel_routes 8 │ wechat_routes 8 │ admin_routes 8
  users_routes 7 │ tools_routes 6 │ announcement_routes 6 │ mimo_voice_routes 6
  storyline_routes 6 │ llm_providers_routes 6 │ voice_routes 6 │ agent_plane_routes 5
  memory_routes 5 │ invite_routes 4 │ persona_card_routes 3 │ topic_routes 3
  character_template_routes 2 │ emotion_routes 2
shisi/api/（8 个带路由文件，30 端点）
  character 7 │ memory 6 │ sticker 5 │ affinity 4 │ emotion_stage 3 │ persona 3 │ stats 1 │ vital_signs 1
api 非 routers（5 端点）
  health_routes 3（/api/health /api/ready /api/metrics）
  qrcode_store 1（/api/wechat/qrcode，admin 兼容面）
  app_factory 内联 1（/api/shisi/status，untagged）
```

### 4.3 shisi/ — Clean Architecture 重构（核心域）

| 子包 | 职责 | 关键类（源码核对） |
|------|------|--------------------|
| `application/` | 应用服务 | **ShisiMemoryService**、**PersonaService**（`rag_context` 消费 `orchestrator.context_budget.dedup_rag_snippets` 去重；**4B schema 往返**：`behavior_patterns`/`honesty_boundary` 默认 `:518-519` + 卡回读 `:572-573`）、**MigrationService**、**PromptService**、**ShisiKnowledgeAdapter**（⚠️ 旧文所列 `CharacterService` 已于批6b 删除，现无此类） |
| `character/` | 角色卡完整子系统 | **`CharaCardV2`**（模型）+ 解析链 `CharaCardV2Parser` / `AiyuPromptsParser` / `SillyTavernParser` / `ParserDispatcher`；`PersonaImporter` / `PersonaExporter`；`CharacterManager`；`png_codec`（模块函数 + `PNGCodecError`，**无 `PNGCodec` 类**）；Store / Validator / models |
| `core/models/` | 领域模型 | `AffinityLevel`、`CharacterId`、`EmotionalState`、`EmotionType`、`PersonaProfile`、**`CharacterAggregate`**（**4B：`behavior_patterns` 字段 `:51` + before-validator `:76-82` + `_behavior_rules_block` `:199/:299`——两字段皆空时整段省略**） |
| `core/ports/` | 端口协议 | `CharacterRepository`（Protocol） |
| `core/services/` | 领域服务 | **`prompt_builder`（模块级 `build()`/`merge_with_persona_prompt()`；`capability_flags`——能力声明段按真实可用性渲染）**；⚠️ 旧文所列 `EmotionDetector` 全仓不存在 |
| `core/conversation_turn.py` | **轮与发言者归属唯一真源（177 行，2026-09-26 新增）** | row → Turn（成对 + 归属）→ 显式标注渲染；存储侧只做加法 |
| `infrastructure/persistence/` | 持久化 | `SQLiteCharacterRepository`、`schema` |
| `infrastructure/migration/` | 迁移 | `MigrationResult` 等，`migration_runner` / `rollback_runner` |
| `memory/legacy/` | 记忆管线（`legacy` 表历史迁移，非待删） | `MemoryPipeline`、`WorkingMemory`、`EpisodicMemory`、`SemanticMemory`、`StructuredMemory`、`VectorMemory`、`FactExtractor`、`ImportanceScorer`、`ForgettingManager`、`ConflictDetector`、`ReflectionEngine`、`ConversationSummarizer`、`DiarySummarizer`、`UserProfileStore`、`_legacy_*` 系列 |
| `memory/`（非 legacy） | 收藏/转发 | `favorite_manager`、`forward_manager` |
| `affinity/` | 亲密度 | `AffinityEnhancer`、`AffinityMapper`、`DecayEngine`、`UnlockManager`、**`scale.py`（刻度唯一真源）** |
| `storyline/` | 剧情线 | `StorylineEngine`（+ `StorylineState` / `StageTransition`）、detector、config |
| `emotion_stage/` | 情感阶段 | **`EmotionStageEngine`**、event_dispatcher、stage_config |
| `vital_signs/` | 生命体征 | `VitalSignsEngine` / `VitalSignsState`、`emotion_mapping`、`vital_prompt` |
| `sticker/` | 表情包 | `StickerManager`、`EmotionRecommender`、`SafetyCheck`、`Importer`、default_provider |
| `voice/`（shisi） | 语音 | `CharacterVoiceManager`（+ `CharacterVoiceSpec`）、`VoiceEnhancer`（EmotionVoiceMapper 已删） |
| `wechat/` | 微信集成 | **仅 `sticker_adapter.py`**（CommandHandler/CommandParser/ProactiveMessenger 已于 2026-09-17 删除） |
| `vault/` | 数据收集 | `VaultCollector` / `CollectLoop`、`_persona_adapter` |
| `ase/` | 场景叙事 | **仅 `__init__` 文档桩**（`trigger_engine` 死桩 2026-09-21 删除，SceneNarrator/TriggerEngine 均不存在） |
| `knowledge/` | 知识检索 | `KeywordRetriever`/`BM25Retriever`、`CharacterKnowledgeService`、`CharacterCrawlerAdapter`、**`SourceStore`**、hot_topics（`knowledge/legacy/` 已于 2026-09-21 整删） |
| `stats/` | 统计 | `analytics.py` |
| `agent_plane/` | 运行时账本与投影 | `runtime`、`event_ledger`、`curator`、`profile_projection` |

**shisi/character/ 说明（源码核对，替换旧表述）**：
`png_codec.py` = SillyTavern PNG tEXt chunk 编解码（**模块级函数**：`is_png()` / `extract_card_from_png()` / `embed_card_to_png()` / `has_chara_chunk()` + `PNGCodecError`，依赖 Pillow）；`importer.py::PersonaImporter` / `exporter.py::PersonaExporter` 支持 .json 与 .png 双向；`character_card_v2.py` 的 `ParserDispatcher` 按格式分派 `CharaCardV2Parser`（SillyTavern V2/V3）/ `AiyuPromptsParser` / `SillyTavernParser`；`manager.py::CharacterManager` 由 `shisi/api/registry.py:setup_shisi` 装配。PNG 集成路径：`api/routers/character_routes.py` import 并调用 `extract_card_from_png()`（捕 `PNGCodecError`）与 `embed_card_to_png()`；前端 `frontend/src/api/characters.ts` 调 `POST /api/characters/import` 与 `GET /api/characters/{id}/export?format=png`。

### 4.4 my_character/ — 角色引擎（11 模块 + `__init__`，项10 + v1.35 死码清除后实况）

> 项10 已删除：`emotion_memory.py`、`enhanced_prompt_engine.py`（含 `TimeContext`）、`persona_evaluator.py`、`style_enhancer_v2.py`、`anchor_protection.py`、`constraint_validator.py`、`contextual_behavior.py`、`evolution_engine.py`（见 `docs/DELETION_LOG.md`）。`PersonaEngine` 三版并存化石一并移除，收敛为「五维画像 + 公共分层构建器 + verify_anchors + check_consistency + 只读 get_evolution_log」。

| 模块 | 职责 | 备注 |
|------|------|------|
| `character_config.py` | ConfigLoader | 全项目最高 fan-in（历史快照 423） |
| `emotion_engine.py` | 情感引擎核心（`EmotionEngine` / `CompoundEmotionalState` / `ContinuityGuard` / `LLMEmotionClassifier`） | 风格修饰器双接口已随项10 删除 |
| `emotion_style_coupler.py` | 情感-风格耦合 | — |
| `persona_engine.py` | 人格引擎 | 项10 收敛为存活面；`/api/persona/evolution-log` 只读、日志恒空属已知限制 |
| `persona_card.py` / `persona_schema.py` | 角色卡 schema | — |
| `persona_utils.py` | 人格工具 | `build_time_context()` 已于 v1.19 删（零调用） |
| `tone_mimic.py` | 语气模仿 | `add_conversation` 由 `training_routes` 克隆摄入在用 |
| `consistency_checker.py` | 回复一致性检查 | 热路径；项6 起按 `core_anchors` 缓存复用 |
| `counter_rebuttal.py` | 反驳计数器 | 热路径节点 |
| `dynamic_anchor.py` | 动态锚点系统 | 强化回路已随项10 删除 |

### 4.5 persona_extractor/ — 人格提取器（12 模块）

| 模块 | 职责 |
|------|------|
| `fusion.py` | PersonaExtractor 融合入口（含 `profile_scope`） |
| `style_vectorizer.py` | 风格向量化 |
| `mental_health.py` | 心理健康分析 |
| `dark_triad.py` | 暗黑三人格检测 |
| `hexaco.py` | HEXACO 人格模型 |
| `liwc_analyzer.py` | LIWC 语言分析 |
| `pado_detector.py` | PADO 检测 |
| `cognitive_distortions.py` | 认知偏差检测 |
| `emotion_coupler.py` | 情感耦合器（chameleon filter） |
| `persona_bank.py` | 用户人格库 |
| `models.py` | Pydantic 模型 |
| `web_enricher.py` | 网络人设增强（火爬虫，降级 Crawl4AI） |

### 4.6 security/ — 安全模块（4 模块 + `__init__`）

| 模块 | 类 | 检查时机 |
|------|-----|---------|
| `content_safety.py` | ContentSafetyFilter | 输入 + 输出 |
| `prompt_injection.py` | PromptInjectionDetector | 输入 |
| `pii_anonymizer.py` | PIIAnonymizer | 输入 |
| `encryption.py` | EncryptionManager | 静态加密 |

安全检查在 process_message 中执行两次（输入 3 层 / 输出 4 层）；`content_safety._log_safety_event()` 记录 category/direction/text_length/text_hash/时间戳（按用户隔离），日志失败从不阻塞安全执行。

**安全测试线**：`tests/test_sec_p0_*` **9 文件**（auth / privacy_data / read_gates / shisi_surface / persona_card / character_card / voice_files / channel_slot / deps_deploy_hygiene）+ 2026-10-09 攻击面扫描批 4 新测试文件；SEC-15 批后 `npm audit --audit-level=high` **0 vulnerabilities**，CI 前端审计门复绿。遗留登记见 `docs/P1_BACKLOG.md` SEC-1~11 / SEC-12~18。

### 4.7 llm_provider/ — LLM 网关（4 文件，5 供应商）

| 模块 | 职责 |
|------|------|
| `llm_gateway.py` | `LLMGatewayV2`（`ModelCooldown` 冷却 / 自动 fallback） |
| `multi_provider_gateway.py` | 多供应商网关：默认链 **Agnes → 智谱AI → 讯飞星火 → 百度千帆**；用户级 gateway 缓存；**常驻同步事件循环**（`chat_sync` 不再每次 `asyncio.run`）；**熔断** `_BREAKER_THRESHOLD=2` / `_BREAKER_COOLDOWN=120s`，`_mark_provider_result(immediate=True)` 供 `primary_only` 链一次失败即开闸 |
| `openai_compatible_provider.py` | OpenAI 兼容供应商（agnes/zhipu/xunfei/baidu 共用）；`_request_timeout` 按供应商收紧；**HTTP 200 空 content 在 `mark_success()` 之前判空抛错**（含 choices / reasoning_only / message_keys），主备失败均 WARNING 带响应体前缀 300 字；**消费 `stream_enabled`**：为真时走 `_chat_aggregated()` 流式聚合；`_log_call_outcome()` 延迟日志（provider/model/stream/ok/elapsed_ms） |
| `__init__.py` | `invalidate_user_llm(user_id)` — 清除用户级 gateway 缓存 |

| 供应商 | 注册方式 | 模型 | 认证 |
|--------|---------|------|------|
| **agnes（链首）** | `get_llm(provider="agnes")` | agnes-3.0-flash（`request_timeout=40`） | Bearer |
| 智谱AI (zhipu) | `get_llm(provider="zhipu")` | glm-4.5-flash（`extra_payload.thinking=disabled`） | Bearer |
| 讯飞星火 (xunfei) | `get_llm(provider="xunfei")` | spark-lite | Bearer |
| 百度千帆 (baidu) | `get_llm(provider="baidu")` | ernie-speed-128k | OAuth |
| DeepSeek | `get_llm(provider="deepseek")` | deepseek-chat / reasoner | LLMGatewayV2（不入 auto 默认链） |

> ⚠️ **链真源现状**：① `config/system.yaml` `llm.fallback_chain`（**agnes 首选**，经编排器 `_init_llm` 显式传入）= **生产主链**；② `config/llm_providers.json` 顶层 `fallback_chain` 同为 agnes 首选——两处一致，不构成「双真源」分歧。**sensenova 已于 2026-09-18 移除**。
> ⚠️ **超时真源两处不一致（观察项）**：`config/llm_providers.json` agnes `request_timeout=40`（v1.55 批），而 `config/system.yaml` 注释仍写 20s；以编排器实际传入的 `llm` 段与 providers 文件为准，`system.yaml` 注释待清理。

**多用户 API Key 隔离**：`User.llm_config` JSON 字段（`api/database.py:85`）→ `/api/user/llm-config` GET（脱敏 `****`）/ POST（自动 `invalidate_user_llm()`），在 `api/routers/misc_routes.py`。调用链：`SettingsLLM.tsx` → `frontend/src/api/system.ts` → `POST /api/user/llm-config` → `users.llm_config` → 下次 `process_message` 按 `user_id` 取专属 LLM。

### 4.7.1 wechat_direct/ — 每人独立微信通道（4 文件；D16 后 `peer_character.py` 已退役）

> 2026-09-19 裁决：通道层由全局单例改为 **per-user × slot 多租户**；2026-10-12 D16 补裁 **一人一 bot**（`MAX_CHANNELS_PER_USER = 1`，`channel_paths.py:13`），全局并发上限 `WECHAT_MAX_CHANNELS`（默认 100）。方案文档：`docs/plans/2026-09-19-每人独立微信通道方案.md`。

| 文件 | 职责 |
|------|------|
| `channel_paths.py` | 磁盘路径唯一真源 `data/wechat_sessions/<user_id>/slotN/`；`max_channels()`；`list_user_slots_with_credentials()`；**`scanner_ids_for_user()`** 枚举各通道扫码人 |
| `connector_registry.py` | `ConnectorRegistry`（`get_registry()` 单例）：`(owner_user_id, slot)` 键控；`ensure/disconnect/status_for_user/primary_status/start_login/restore_on_boot`；`ChannelQuotaError`/`ChannelSlotError`；每会话目录 `poll.lock` 去重多 worker 轮询 |
| `wechat_connector.py` | 连接器本体按 owner/slot 隔离：`load_session_state/qrcode`、`get_wechat_state`、`split_reply_for_wechat()`、**D16 扫码人守卫**（`ilink_user_id` 三路径装载 + 非扫码人拒绝 + 告警）、入站 `parse_ilink_items`（图片 item 2 / 语音 item 3）、图片/语音/emoji 发送、`token_window_state(owner)` 六态探针、`_drain_outbox` 出站宿主；**v1.57 新增 `_send_typing`（`:881-887`，iLink `ilink/bot/sendtyping`，1=开始 / 2=结束）与 `send_typing(to_user, on)`（`:1174`），失败静默 no-op**；对话内追问引擎已随 D14 退役 |
| `__init__.py` | 导出 |

**D16 跨域机制**：① activate 直连扫码人绑定（`character_routes` 激活时经 `scanner_ids_for_user()` 落 `wechat_bindings` + `upsert_binding` 缓存 + 活实例热切）；② 主动节奏按账号聚合（`proactive/llm_proactive.py:289 account_pacing_state()` 单一真源，同 owner 前缀取 hours=min / last_proactive=max）。
**双入口契约**：`sync_disk_sessions_to_db()`（async，lifespan 调用）与 `_sync_disk_sessions_to_db_sync()`（CLI）共同委托 `_sync_disk_sessions_async()` 内核，禁在运行中事件循环内 `asyncio.run()`。
**遗留迁移**：`scripts/migrate_legacy_wechat_channel.py` 一次性把旧全局凭证迁入 admin（user_id=1）`slot0`。旧全局端点 `/api/channels/wechat/*` 与 `/api/wechat/qrcode` 收敛为 admin 兼容面，普通用户一律走 `/api/wechat/channel*`（仅 JWT）。

### 4.8 voice/ — 语音合成（MiMo 唯一引擎）

> **MiMo-only 收敛（用户裁决 A，2026-08-28）**：Edge-TTS/SoVITS/CosyVoice/Bert-VITS2 四 provider 与 `voice_training.py` 已删；`/api/mimo/*` 为唯一语音 API；云故障由 Windows SAPI 本地合成兜底（`win-tts-fallback` 可选依赖）。**4D BreezeTTS2 仅设计文档（`c0a4d2c`），遵守暂不引入裁决，未落任何代码。**

| 文件 | 说明 |
|------|------|
| `mimo_tts_provider.py` | 唯一引擎：MiMo Cloud API（8 情感映射内置）+ SAPI 本地兜底 |
| `tts_manager.py` | 单引擎管理（多引擎降级链已删） |
| `tts_provider_base.py` | Provider 抽象基类 |
| `audio_converter.py` | 音频格式转换（silk） |
| `clone_data_manager.py` | 聊天克隆数据管理（克隆域，与 TTS 无关） |
| `voice_catalog.py` | 音色 catalog（`data/voice_catalog.json`） |
| `audio_result.py` | 合成结果封装 |

### 4.9 前端（React 19 管理控制台）

- **20 个页面文件**（`ls frontend/src/pages/*.tsx` 本批实测 20；全部挂载）：AdminProvidersPage、AdminUsersPage、CreateRole、**HelpGuidePage（批 4 新增 `/help`，`App.tsx:155`）**、IntroPage、LoginPage、NotFoundPage、PsychProfilePage、RoleSettings、RolesPage、SettingsAccount、SettingsLLM、SettingsLogs、SettingsSecurity、SettingsVoice、StatusCenter、SystemSettingsLayout、TemplatesPage、ToolsDashboard、WeChatPage；`StorylinePage` 为 App.tsx 内联包装（`/roles/:roleId/storyline`，不占页面文件）。
  - 分面：公开 IntroPage（`/intro`，SP-11 公开门面，根路径未登录重定向）/ LoginPage / PsychProfilePage（`/psych`，**需登录**）；角色 RolesPage / CreateRole / RoleSettings / **TemplatesPage**；设置 SettingsLLM / SettingsVoice / ToolsDashboard / SettingsSecurity / **SettingsAccount**（同意/导出/注销 + **NEW-34 个人偏好区**）/ SettingsLogs；状态 StatusCenter / WeChatPage；管理 AdminUsersPage / AdminProvidersPage（admin）；**教学 HelpGuidePage**；NotFoundPage / SystemSettingsLayout。
- **16 个 API 模块**：admin, **announcements（V2-5）**, auth, characters, client, clone, emotion, llmProviders, mimo, normalize, queryClient, selfservice, system, templates, training, wechat（demo/chat/users 已删）。
- **R7 模式隔离 UI（2026-10-21）**：`components/admin/RoleSettingsTabs.tsx` 新增 `reply_mode` 作用域切换（global/user）+ 目标 user 选择；`api/system.ts` 增 `getMyReplyMode()` / `setMyReplyMode()`（走 `/api/user/reply-mode` 自助端点）。
- **话题状态机 UI（2026-10-27/28）**：`components/admin/TopicStateSection.tsx` + `store/topicStateStore.ts` + `api/system.ts`（消费 `/api/topic-state/*` 三端点）；**骰子闸配置面**同在 `RoleSettingsTabs.tsx` + `api/system.ts`（`/api/proactive/config` 的 `dice_gate` 块）。
- **6 个 Zustand store**：authStore, errorStore, characterBuilderStore, humanizeStore, topicStateStore, **globalViewStore**（V2-1 全局运维视图开关，批 3 新增）。
- **React Query hooks 5 文件**（`useAuth` / `useQueries` / `useInView` / `useMousePosition` / `index`）；Playwright E2E 配置在册；`frontend/src/constants/` 含 `cloneAgentGuide.ts`（CreateRole 任务书）、`agreement.ts`、`persona.ts`。新依赖 **driver.js `^1.10.2`（MIT，教学导览）** 与 `lucide-react ^0.510.0`（NEW-32 图标统一）。
- **vitest 342 用例 / 47 文件**（vitest ^5；文件数 2026-10-30 实测 47 = 36 + **11 新文件**：`AdminProvidersPage` / `AnnouncementBanner` / `Breadcrumb` / `DesignTokens` / `GlobalViewBanner` / `HelpGuidePage` / `Modal` / `RoleSettingsTabsHistory` / `SharedComponents` / `UserForm` + `hooks/useMemoryFacts`；用例数 342 + tsc 0 引主控 2026-10-30 实测口径）。

**页面说明（沿用，逐条仍在位）**：ToolsDashboard = 工具健康/启停；StatusCenter = 系统状态（记忆三层管线）；SettingsVoice = 语音设置（ENGINE_OPTIONS 仅 mimo-tts）；WeChatPage 仅 LiveStatusBanner + QrCodeConnectionModal（移除 0 数据 StatsBar）；SettingsLLM 走 `/api/user/llm-config`（普通用户不再 403，批 4 增「测试连接」走 `/api/user/llm-config/test`）；PsychProfilePage 消费 `/api/psych/*` 五端点；IntroPage 接替已删 Demo 的访客转化；CreateRole 克隆三步流（智能体代跑，任务书 `constants/cloneAgentGuide.ts`）；RoleSettings DataTab 含「网络增强」→ `/api/characters/{id}/enrich`；**HelpGuidePage** = 13 模块教学卡 + 学习进度按 user_id 持久化 + driver.js 导览（「需活跃角色」显式标注）。

**组件库收编（NEW-31 / NEW-32，批 7）**：共享件真源 `components/shared/`（`ConfirmDialog` 56 行 / `EmptyState` 21 行 / `Skeleton` 17 行 / `Modal` 99 行——**顶层栈** `openModals` depth 过滤 `:49-50`，同屏多 Modal 仅顶层响应 / `Slider` 60 行——滑轨渐变引用 `var(--color-accent-400)`）；`components/admin/UserBadges.tsx`（57 行，徽章 tag 族统一）+ `components/layout/Breadcrumb.tsx`（173 行，材质统一）；**`SubTabBar` 已删除**（批 7 `c6ed735`，本批 `find` 零文件零引用）；守卫测试 `tests/components/DesignTokens.test.tsx`（红测前提注释钉死改前三硬编码形态：Slider 孤立青色 / 徽章 rose-amber-sky 族 / 面包屑 mat-floating）。

### 4.10 tools/ — 工具系统（17 工具 / 默认 14）

```
tools/
├── __init__.py          # 导出 BaseTool, ToolResult, ToolRegistry, ToolDispatcher
├── base_tool.py         # 核心框架（4 个类）
├── tool_state.py        # 工具/插件开关统一库存（data/runtime_switches.json 唯一 owner，dispatch 首查状态门）
├── url_guard.py         # SSRF 统一守卫共享真源（getaddrinfo + ipaddress，逐跳重校验）
└── builtin/             # 9 文件
    ├── __init__.py · calendar_tool · character_crawler_tool · extra_tools
    ├── profile_agent_tools · reminder_tool · search_tool · time_awareness_tool · weather_tool
```

| 工具名 | 类 | 权限 | 说明 |
|--------|-----|------|------|
| `weather` | WeatherTool | public | 天气（插件优先 → wttr.in 回退） |
| `search` | SearchTool | public | 联网搜索（DDGS → Bing HTML 回退） |
| `calendar` | CalendarTool | public | 当前日期时间 |
| `calculator` | CalculatorTool | public | AST 安全表达式计算 |
| `set_reminder` | ReminderTool | public | 提醒（trigger_time 必填北京时间；`_meta` 服务端注入 session_key/user_id；到点 reminder_check 定向投递、豁免静默时段） |
| `query_reminders` | CalendarQueryTool | public | 按会话查询待处理提醒 |
| `manage_reminder` | ReminderManageTool | public | cancel / reschedule / restore |
| `time_awareness` | TimeAwarenessTool | public | 农历/节假日/工作日 |
| `character_card` | CharacterCrawlerTool | friend | Web 爬取角色资料（baike→wiki→baidu） |
| `update_user_profile` | UpdateUserProfileTool | public | 画像智能体：编辑用户画像 |
| `remember_facts` | RememberFactsTool | public | 画像智能体：记忆事实 |
| `forget_facts` | ForgetFactsTool | public | 画像智能体：遗忘事实 |
| `query_profile` | QueryProfileTool | public | 画像智能体：查询画像 |
| `memory` | MemoryTool | public | 长期记忆事实查询（已列入默认 config：`system.yaml` `builtin_tools` + ToolGate L0 晋级规则『你还记得…/我说过…吗』→ memory；检索经服务端注入 `_meta.session_key` 归属、结果按 untrusted 信封注入、主链只读不写） |
| `scheduler` ⚑ | SchedulerTool | public | 一次性日程安排（同上） |
| `web_summary` ⚑ | WebSummaryTool | public | URL 内容摘要（同上） |
| `image_gen` ⚑ | ImageGenTool | public | AI 图片生成（Agnes apihub 端点） |

**核心类**：`BaseTool`（name/description/permission_level/parameters_schema → `execute()` → ToolResult；`to_openai_fc_schema()`）、`ToolResult`（success + data/error；`to_dict()`/`to_fc_result()`）、`ToolRegistry`（`register/get/get_tools_by_permission(affinity)/health_check_all`）、`ToolDispatcher`（权限 → 限速 3 次/分/工具 → 执行 → 重试 → 埋点）。
**安全特性**：SSRF 防护（`url_guard`）、AST 计算替代 eval、public/friend/intimate/admin 四级门控、逐工具优雅降级。

### 4.11 其余模块包（2026-10-30 批7 收尾口径刷新）

| 包 | 文件数 | 内容与关键 owner |
|----|--------|------------------|
| `proactive/` | **14** | `scheduler.py`（**2821 行**：决策线主循环；**骰子闸例外 hook `:1576-1592`**（`evaluate_dice_gate(requested_mode="live")` @`:1580`，账本由引擎写 `actor=proactive_dice` 不冒充 `proactive_llm`；用户裁决接受该例外，回退 `DICE_GATE_PRODUCTION_WIRED=False`）；**话题状态机接线**：`build_topic_recall` `:1691`（import `:1390`）、`topic_plan` 全周期 `take_next_plan` `:1514/:1703/:1820/:1932(consume=True)` / `save_next_plan` `:1811/:1933`、7 天 TTL `purge_expired` `:2289-2293`）、`ase_engine.py`（**2065 行**：`evaluate_dice_gate` `:1287` / `_record_dice_event` `:1356` / dice 双桶状态 `:956-963` / `_read_dice_gate_config_safe` `:59` + `_dice_gate_wired` `:76`）、`frequency.py`（**470 行**：节奏控制器 + 骰子闸纯函数面 §4.13）、`llm_proactive.py`（**1445 行**，决策线 + `account_pacing_state` @`:289`）、`proactive_context.py`（**216 行**，人设/约束统一构造器）、`reminder_delivery.py`（**360 行**，投递守卫）、`ase_hub.py`（332）、`proactive_hooks.py`（201）、`post_draft_judge.py`（**171 行**，v1.57 A-4 主动 post-draft judge fail-closed，只判发/不发）、`reflection.py`（143）、`runtime_plane.py`（**804 行**，跨进程运行时平面 `data/runtime_plane.db`）、`runtime_assembly.py`（140 行，提醒/主动两入口唯一装配 owner）、**`topic_recall.py`（100 行：parked 复提候选 + `judge_context`，三重闸候选侧——judge 关闭/不可调用 → 空候选，fail-closed）** |
| `utils/` | **26** | 共享真源包：**`topic_state.py`（1769 行，话题状态机唯一 owner，§4.12）**、`stream_guard.py`（597 行：`IncrementalReplyGuard` `:207` + `read_stream_mode()` `:157` / `true_streaming_enabled()` `:165`）、`prompt_sanitize.py`（**1152 行**：`terminal_reply_guard` `:1058`、`_extract_user_anchor_sentences` `:308`、`scrub_history_rows` `:922`）、`reply_mode.py`（**275 行**：`DEFAULT_REPLY_MODE` `:45`、per-user 覆盖 `read_reply_mode(user_key)` + 表 `user_reply_mode`、**`_STAGE_DIRECTION_RE` `:66` 四形态单一真源（圆括号/星号/`[]`/`【】`，c3ede77）**）、`character_helpers.py`（CSP 行为蒸馏清洗与渲染 §4.14）、`humanize_config.py`（`HUMANIZE_FIELDS` 声明式字段表 `:103`，`DEFAULTS` `:128` / `INT_BOUNDS` `:131` / `WRITABLE_FIELDS` `:137` 全派生；`user_humanize` 按用户覆盖）、`humanize_read_delay.py`（**119 行**，`compute_read_delay_ms` `:79`）、`humanize_typo.py`（**54 行**，可选口误，`typo_rate` 默认 0）、`humanize_delivery.py`（114 行，主动拆条 2–4 段 + 段间节奏，两投递点共用）、`session_key.py`（165 行）、`address_terms.py`（237 行）、`local_time.py`、`project_paths.py`、`llm_bridge.py`、`json_state.py`、`async_utils.py`、`deletion_guard.py`、`inbound_context.py`、`fallback_lines.py`、`affinity_state.py`、`character_resolver.py`、`bootstrap.py`、`emotion_state.py`、`health_check.py`、`important_dates.py` |
| `context/` | 2 | `world_info_provider.py`（世界书/lorebook） |
| `multimodal/` | 3 | `image_attachment.py`（入站图片归一化，magic bytes，不落盘）、`multimodal_processor.py` |
| `clone_training/` | 4 | `data_cleaner` / `data_extractor` / `style_analyzer`（weclone_adapter 已删） |
| `observability/` | 8 | metrics / health / logging_setup / sentry / graceful_shutdown / config_manager / config_models |
| `plugins/` | 2 | `weather.py`（外部数据源插件位） |
| `scripts/` | 19 | 运维/迁移脚本（`rebuild_knowledge_index.py`、`migrate_legacy_wechat_channel.py`、`link_identities.py` 等，见 `ls scripts/*.py`） |
| `deploy/` | — | systemd unit + nginx 模板 + `remote_deploy.sh` + `backup_manager.py`（577 行）+ `restore_manager.py`（329 行） |
| `api/` 非路由 | **20 文件** + `api/state/`（3 模块 + `__init__`） | 请求链：`run_api.py`（642 行）/ `app_factory.py`（**523 行**，+4 = 公告双挂载）/ `auth.py` / `auth_jwt.py` / `deps.py` / `runtime_config.py` / `session_manager.py` / `path_security.py` / `password_policy.py` / `byok.py` / `achievement_engine.py` / `main_routes.py` / **`audit.py`（91 行，批 3 新增——NEW-21 operator_audit + V2-1 全局视图开关唯一 owner，见 §4.16）**；数据与生命周期：`database.py`（**666 行**，+4 表 model）、`lifecycle.py`（账号/角色删除五阶段作业）、`consent.py`（同意状态机）、`qrcode_store.py`、`health_routes.py`、`websocket_server.py`；`api/state/`：`safety_log.py`、`tool_history.py`、`training_state.py` |
| `api/routers/topic_routes.py` | **85 行** | 话题状态机管理端（2026-10-27 新增）：`GET/POST /api/topic-state/config`（热读/partial merge 热写 `data/scheduler_config.json` 的 `topic_state` 块，admin）+ `GET /api/topic-state/{session_key}`（只读快照，不回传 prompt 文本）；**注册顺序硬约束 `/config` 在 `/{session_key}` 之前**；会话键沿用 `_resolve_admin_target_key`（纯数字仅当存在微信绑定才还原 `N:wxid`，否则 400 不猜键） |
| `api/routers/character_template_routes.py` | 278 行 | 角色模板面阶段 1：`GET /api/character-templates` + `POST /{id}/clone`（模板文件零改动） |
| `tests/golden/` | 12 文件 | **R6 黄金集（2026-10-21 立项）**：`cases/*.jsonl` **45 条用例**（8 分类：aiyu_contrast 6 / degradation_quality 4 / echo_attribution 6 / guard_regression 8 / meta_memory 4 / mode_boundary 7 / topic_awareness 5 / ungrounded_reciprocal 5）+ `runner.py`（792 行）+ `judges.py`（154 行）+ `test_golden_set.py`（402 行，pytest 入口，单文件收集 69 例 = 68 passed + 1 skipped live_llm）+ `README.md`。**确定性优先、判定全部委托被测真源、LLM 判定不计基线** |

> **历史归档**：`tools/url_guard.py` / `tool_state.py` / `runtime_plane.py` / `runtime_assembly.py` / `inbound_context.py` / `lifecycle.py` / `deletion_guard.py` / `consent.py` / `character_template_routes.py` / `source_store.py` / `voice_catalog.py` / `backup_manager.py` / `restore_manager.py` 的落库提交号与归属窗口（W1–W12），详见 `LOG.md` 2026-09-27 条与 `docs/DECISION_LEDGER.md`。

### 4.12 话题状态机链路（2026-10-27/28，`3fe2771` 三端部署，默认开启）

**三表**（`data/sqlite.db`，schema 真源 = `utils/topic_state.py:86/:107/:119` 三段幂等 `CREATE TABLE IF NOT EXISTS`，经 `_connect(ensure_schema=True)` 仅后台写路径建表 `:380-389`——只读路径 `ensure_schema=False` 不建表，故本地库 35 表无 topic 表属未触发；生产三表已存在且 `topic_plan` 有真实计划行）：`topic_state`（active/parked/closed 状态行）/ `topic_plan`（主动线计划）/ `topic_meta`（元数据）。

**`utils/topic_state.py`（1769 行，唯一 owner）公开 API 面**（本批 grep 实测）：

| API | 行 | 消费方 |
|-----|-----|--------|
| `get_topic_snapshot` | `:889` | 主链快照任务（见下） |
| `update_after_turn` | `:922` | `_after_process._safe_topic_then_after_chat`（`optimized_orchestrator:1800`） |
| `select_proactive_recall` | `:1183` | `proactive/topic_recall.py`（parked 复提候选侧） |
| `save_next_plan` / `take_next_plan` | `:1261` / `:1323` | `scheduler.py:1811/:1933` / `:1514/:1703/:1820/:1932(consume=True)` |
| `purge_expired` | `:1380` | `scheduler.py:2293`（每日维护，全状态 7 天 TTL） |
| `read_topic_config` / `write_topic_config` | `:152` / `:182` | 管理端 `/api/topic-state/config` |
| `should_run_llm_judge` / `run_llm_judge` | `:1476` / `:1641`（`timeout=8.0` @`:1648`） | `_after_process._run_topic_judge`（`optimized_orchestrator:1762`）——每 4 轮稀疏 judge，超时/失败**保留原状态** |
| `render_topic_state_block` | `:509` | prompt 状态块渲染（≤160 字） |

**主链接线**（`orchestrator/optimized_orchestrator.py`）：并发快照任务 `:1152-1161`（`asyncio.wait_for` + `TOPIC_SNAPSHOT_TIMEOUT_SECONDS=0.2` @`:64`，失败只降级为空、不给首字加等待）→ `_attach_topic_state` `:67/:1401`（dict 键 `topic_state_text`）→ `:1421-1427` 随记忆上下文注入。

**主动线 `topic_plan` 全周期**：决策时 `skip` 也存计划（`save_next_plan`）→ 重启后读回（跨进程仍在）→ 到点注入决策上下文 → 消息发送成功才 `consume=True` 消费（`scheduler.py:1932`）。

**parked 复提三重闸**（Q3-A）：`proactive/topic_recall.py`（100 行，`build_topic_recall` @ `scheduler.py:1691`）——候选侧要求 **≥6h 闲置 + 非静默时段 + judge allow** 三条同时成立；judge 关闭/不可调用 → 空候选（fail-closed）。

**管理端与前端**：`/api/topic-state/*` 三端点（`api/routers/topic_routes.py:85` 行，admin，见 §4.11）+ 前端 `components/admin/TopicStateSection.tsx` + `store/topicStateStore.ts`。**回滚开关**：管理端热写 `topic_state.enabled=false` / `llm_judge_enabled=false`（partial merge，其余配置块逐字保留）。多 active 并行上限 3（Q6-B `_enforce_active_cap` @`:840`）。

### 4.13 dsh-speak-up 零 token 骰子闸（`aabc867`，默认关闭 + dry_run 默认开）

**设计**：主动消息发送与否先掷骰后调 LLM——峰谷时段不同命中线 + 时机因子 + 连没中加成，给主动消息引入「随机性但不失节奏」的自然感，零 token 成本（纯本地随机数）。

**纯函数面**（`proactive/frequency.py`，470 行；本批 grep 实测）：
- 常量与契约：`DICE_LINE_MAX=100.0` `:177`；**`DICE_GATE_DEFAULTS` `:180`：`enabled=False` / `dry_run=True`（默认关闭 + 开启后默认干跑，只记账不拦截）** + `peak_line=20` / `offpeak_line=60` / `miss_bonus=10`（上限 40）/ `idle_short_minutes=20` / `idle_long_minutes=360` / `idle_short_factor=0.15` / `idle_long_factor=1.5`；`DICE_GATE_BOUNDS` `:194`（越界读侧回落默认、写侧抛 ValueError → API 400 / pydantic 422）。
- 计算链：`is_peak_time` `:238` → `dice_timing_factor` `:250` → `dice_base_line` `:275`（峰 20 / 谷 60）→ `dice_hit_line` `:280`（+未中加成，cap 40）→ `roll_percent` `:298` → `evaluate_dice_roll` `:342`（返回 `DiceRollResult` `:315`）。
- 配置读写：`normalize_dice_gate_config` `:389` / `read_dice_gate_config` `:425` / `write_dice_gate_config` `:432`；配置真源 `data/scheduler_config.json` `dice_gate` 块。
- **回退开关**：`DICE_GATE_PRODUCTION_WIRED` `:209`（现 `True`）——若 scheduler 例外不成立而回退只读分支则置 `False`，管理端据此显示「当前不生效」。**scheduler.py 例外已由用户裁决接受（`dd3b470`，2026-10-28）**。

**接线**：① 引擎侧 `proactive/ase_engine.py`：`evaluate_dice_gate` `:1287` / `_record_dice_event` `:1356`（账本 `actor=proactive_dice`，含 roll/line/streak）；dry/live **双桶 streak** `:956-963`（干跑期不污染 live 的连没中加成）；`_read_dice_gate_config_safe` `:59` + `_dice_gate_wired` `:76`。② 决策侧 `proactive/scheduler.py:1576-1592`：**一次性例外 hook**——掷骰不通过 → skip 该 tick（`wait` + line/roll/streak 落日志），不烧决策 LLM。③ 配置面：管理端 `/api/proactive/config` 的 `dice_gate` 块（`training_routes.py:324-332`，读侧附 `empty_dice_gate_state` 运行态）+ 前端 `RoleSettingsTabs.tsx` / `api/system.ts`。

**测试时区缺陷**：骰子测试曾误用墙钟（UTC CI 主机差 8h → 4 分块门 4 失败），`165504f` 改 `now_local` 对齐引擎时钟根治（五文件复跑 128/128，见 §1.1 注）。

### 4.14 CSP 行为蒸馏移植（`5dff923`）

**设计**：把角色卡里「这个角色怎么说话、有什么行为规则」从死数据变成 prompt 段——AI 人设生成新增两字段，聚合层渲染「# 行为规则」紧凑段。

- **AI 人设生成**（`api/routers/character_routes.py:390-420`）：`behavior_patterns` / `honesty_boundary` 两字段入卡数据。
- **清洗与渲染 owner**（`utils/character_helpers.py`）：`normalize_behavior_patterns` `:208`（字符串按「→」切分、dict 只取三键、上限 7 条、单字段截断）/ `sanitize_honesty_boundary` `:155`（边界行前置且豁免，永不被条目挤掉）/ `render_behavior_section` `:261-269`（**`BEHAVIOR_SECTION_HEADER = "# 行为规则（一贯反应倾向，不是台词模板）"` `:113`，整段限长，无内容返回空串**）/ 卡提取 `:520-521`。
- **聚合层**（`shisi/core/models/character_aggregate.py`）：`behavior_patterns` 字段 `:51` + before-validator `:76-82`（委托 `normalize_behavior_patterns`）+ `_behavior_rules_block` `:199/:299`——**两字段皆空时整段省略**（无字段的老卡零影响）。
- **schema 往返**（`shisi/application/persona_service.py`）：默认值 `:518-519`（空列表/空串）+ 卡回读 `:572-573`。

### 4.15 拟人化 humanize 链（v1.57：`e7da4dc`/`fd003b8`/`b29beba`/`3943e5a`，随 `3fe2771` 上线）

- **声明式字段表**（`utils/humanize_config.py`）：`HUMANIZE_FIELDS` `:103` 单一字段表（名称/类型/默认值/边界/可写/可按用户覆盖/标签），`DEFAULTS` `:128` / `INT_BOUNDS` `:131` / `WRITABLE_FIELDS` `:137` 全部由它派生——新增字段只改一处。`user_humanize` **按用户覆盖**：用户 → 全局 → 默认，键缺失 = 继承。
- **主链节奏**（`orchestrator/optimized_orchestrator.py`，`humanize_read_delay`/`humanize_typo` 全仓唯一非测试消费点，本批 grep 实证）：supersede 序号 `:93-100` → 消息到达登记 `:2080-2081` → **superseded 判定 `:2141-2150`（延迟期新消息静默取消，返回 `{"reply":"","superseded":True}`）** → 节奏决策 `:2371-2380`（返回 `superseded/delay_ms/typing`）。阅读延迟 `compute_read_delay_ms`（`utils/humanize_read_delay.py:79`，119 行）；可选口误 `utils/humanize_typo.py`（54 行，`typo_rate` 默认 0）。
- **typing**（iLink `sendtyping`）：`wechat_direct/wechat_connector.py:881-887`（1=开始 / 2=结束）+ `send_typing(to_user, on)` `:1174`；未真机实测，失败静默 no-op；无 typing 渠道 5s 封顶。
- **主动 post-draft judge**（`proactive/post_draft_judge.py`，171 行）：A-4 fail-closed，只判发/不发；观测日志 `[proactive_judge]`。
- **拆条**：`utils/humanize_delivery.py`（114 行）2–4 段 + 段间节奏，两投递点共用。
- **API**：`GET/POST/PUT /api/humanize/config`（`training_routes.py:677-735`）+ **`DELETE` 清除用户覆盖**（`:741-756`，G-2，`clear_humanize_override`）——即 §4.2 端点 +1 的来源；观测日志 `[humanize][step=…]`。

### 4.16 D20 前端全站体验批（批 1–7；本地 15 提交领先 origin、未部署）

**背景**：2026-10-29 用户实测控制台报障四件（管理员视角全量裸渲 / 角色归属错认 / 前端太复杂 / 无导引公告）→ 审计工作流 `dwfrun-a50aa227`（80 发现 + 40 项 backlog，报告真源 `docs/reports/2026-10-28-frontend-ux-audit.md`）→ `DECISION_LEDGER` **D20 八裁决** → 六批计划全开工 → 批 6 对抗验收收编 → 批 7 四收编。逐批详情见 `LOG.md` 2026-10-29/30 条目。

**提交链**：`908db1c`（批 1 P0 止血）→ `38e517c`（批 2 契约修复）→ `6d57e30`（批 3 admin 数据面）→ `cbdaf96`（批 4 首日旅程与教学）→ `a91ed34`（批 5 个性化与 IA）→ `69c1d71`（批 6 收尾）→ `69aecd2`（批 6 对抗验收收编 F1/F4/F5/F9）→ `f9ccfd4`（F2 last-admin 删除不变式测试）→ `d182b6c`（NEW-20）→ `c6ed735`（NEW-31）→ `65e0fee`（NEW-32）→ `9815c27`（NEW-22）。

**新端点 14（全部属批 1–6；批 7 零新端点）**：

| 路径 | 方法 | tag / 模块 | 归属 |
|------|------|------------|------|
| `/api/user/llm-config/test` | POST | misc（`misc_routes`） | NEW-13 BYOK 连通性测试——走**真实** `_build_backend` 构造路径（SSRF/幽灵链拦截原样生效），不碰 gateway 缓存 |
| `/api/user/humanize` | GET/POST/PUT/DELETE | training（`training_routes`） | NEW-34 个人偏好自助——作用域键服务端从 token 派生绑定还原，**客户端自报 `user_key` 一律忽略**（安全语义有测试钉死）；未绑定 400 不猜键 |
| `/api/announcements/active` · `/api/announcements/{announcement_id}/read` | GET · POST | announcements（`announcement_routes`） | V2-5 公告读面（时间窗 + per-user 已读水位，markRead 幂等） |
| `/api/admin/announcements` · `/api/admin/announcements/{announcement_id}` | GET/POST · PUT/DELETE | admin-announcements | V2-5 公告 admin CRUD，全部落 operator_audit |
| `/api/admin/audit` | GET | admin（`admin_routes`） | NEW-21 审计面板读面 |
| `/api/admin/global-view` | GET/POST | admin（`admin_routes`） | V2-1 全局运维视图开关——**默认收窄**到本人前缀；`resolve_principal` 落 `request.state.admin_global_view`，`misc_routes._memory_scope_prefix` 消费 |

**新表 4 张（`data/users.db` 10 → 14 表；纯新增 `create_all` 幂等，无 ALTER）**：`announcements`（`api/database.py:406`）/ `announcement_reads`（`:447`，每用户已读水位）/ `operator_prefs`（`:468`，全局视图开关）/ `operator_audit`（`:487`，NEW-21——🔴 **隐私红线：不存用户消息内容**，target 只落归属标识、detail 只落结构化摘要）。唯一 owner = **`api/audit.py`（91 行）**：`record_operator_audit`（尽力而为，失败只告警绝不阻断被审计业务）+ `is_global_view_enabled`（无行 = False，默认收窄）。

**批 7 四收编**：

- **NEW-20 高危操作二次确认（`d182b6c`）**：只对**危险方向**加确认（三处）：UserForm 停用 / AdminProvidersPage 禁用 + 回退链徽章（标明降级回退顺序）/ SettingsSecurity 关闭过滤器；每处置**后果文案**（写清影响谁、能不能撤销）；`components/shared/Modal.tsx`（99 行）**顶层栈**——同屏嵌套弹窗按 **ESC 只关最上层**、不误关父层（`openModals` depth 过滤 `:49-50`）；启用/开启路径**零摩擦**（只拦恢复能力的操作，防「处处弹窗」疲劳）。
- **NEW-31 组件库收编（`c6ed735`）**：**`SubTabBar` 已删除**（本批 `find` 零残留）；ConfirmDialog Button 化；RolesPage / SettingsVoice 收编 `EmptyState` / `Skeleton`，加载态统一。
- **NEW-32 设计 token 与视觉语言统一（`65e0fee`）**：Slider 滑轨渐变引用 `var(--color-accent-400)`（替代硬编码 `#22d0df`）；徽章 tag 族（`UserBadges.tsx`）；Breadcrumb 材质；图标 lucide 化（`lucide-react ^0.510.0`）；守卫测试 `DesignTokens.test.tsx`。
- **NEW-22 用户列表排序全页化（`9815c27`）**：`api/routers/admin_routes.py` ——`_USER_SORT_COLUMNS` **白名单 7 键**（`:124-133`，`InstrumentedAttribute` 表驱动：查询参数只能查表取列对象，**严禁按用户输入拼列名**＝注入面封堵，非法键由 `Literal` 校验在进函数前 422）+ `list_users` 增 `sort`/`sort_dir`（`:140/:143`）+ `order_by(primary_order, User.id.desc())` **确定性次键先于分页**（`:181-184`；默认 `created_at desc` 与改动前行序一致）；前端 `admin.ts` 透传 + 表头改传参触发后端排序，删除「只排当前页」伪排序；`tests/test_admin_users_sort.py`（305 行 **28 例**：七键×双向全局序 / 跨页拼接 / 注入 422 / 默认口径 / 次键去重）。
- **F2 last-admin 删除不变式（`f9ccfd4` + 守卫随 `9815c27` 入账）**：批 6 NEW-17 只拦了 `update_user` 降权/停用唯一 active admin；对抗验收发现 `delete_user` 有缝隙（admin2 删 admin1 → 0 个 active admin）——补同口径守卫（`admin_routes.py:362-375`：删除前 `active_admins <= 1` → 400「至少保留一个启用中的管理员」），测试 `tests/test_admin_routes_last_admin.py` 扩容（+49 行）。

**对抗验收收编（批 6 `69aecd2`，5 项）**：F1 教学页死路由（`/help` 路由挂载 `App.tsx:155`）/ F4 公告 XSS scheme 白名单 / F5 毫秒时间戳守卫 / F9 端点口径更正 101→106（子集口径注记见 §4.2）/ F2 见上。

**测试口径（主控 2026-10-30 实测）**：后端四分块 **4296 通过 / 2 跳过 / 3 个存量失败族**（§1.1 注，均 HEAD 对照实证与批 7 无关）+ ruff 0；前端 vitest **342/342（47 文件）** + tsc 0；**突变验红 4/4**（M1 排序闸 / M2 停用确认 / M3 token 回潮 / M4 Button 收编）。新依赖 **driver.js `^1.10.2`**（MIT，教学导览；shepherd.js / intro.js 因 AGPL 商业双许可排除）。

**遗留登记**：① **部署未做**——本地 15 提交领先 origin（12 实现 + 3 文档），三端统一铁律下随用户裁决；② `README.md:11` 徽章 4508 → **4639** 与 CODEMAPS 六件归收口批；AGENTS v1.61 / LOG / HANDOFF v1.61 / DELETION_LOG（NEW-31 补登）已由主控并行收口；③ 扫码弹窗 playwright e2e spec 未做（以 fake-timers 组件测试替代钉住）；④ 批 6 遗留 NEW-22/20/31/32 已于批 7 清零；⑤ 3 个存量失败族待专项批。

---

## 5–9. 图谱派生视图（历史归档）

> **历史归档（原 §5–§9）**：Leiden 社区聚类（12 社区）、fan-in Top10（`ConfigLoader.get` 423 等）、`transitive_loop_depth` 复杂度榜、跨模块调用 Top8、`main → shisi` 调用表——全部为 2026-07-09 / 2026-08-28 的 codebase-memory 图谱快照，其中已删文件（`character_card/`、`weclone_adapter`、`TimeContext`/`PromptTemplateMgr`、`_run_full_mode`/`run_console_chat` 迁出）使读数失效；`.codebase-memory/` 产物当前不在磁盘。需要热点数据请按 §12 重跑索引，**不要引用旧表**。

---

## 10. 技术栈依赖

| 类别 | 依赖 |
|------|------|
| Web 框架 | FastAPI + uvicorn + Pydantic v2 |
| 数据库 | SQLAlchemy 2.0（**必须带 `[asyncio]` extra**，`pyproject.toml:30`；否则干净环境缺 `greenlet`，`sqlalchemy.ext.asyncio` 导入即失败）+ aiosqlite + ChromaDB |
| 向量 | sentence-transformers + rank-bm25 |
| LLM | httpx + tenacity（自动 fallback） |
| LLM 供应商 | **Agnes (agnes-3.0-flash，链首)**、智谱AI (glm-4.5-flash，须关 thinking，作降级位)、讯飞星火 (spark-lite)、百度千帆 (ernie-speed-128k) = 生产主链 4 家；DeepSeek 注册可用，不入 auto 链 |
| 语音 | MiMo Cloud TTS（唯一引擎）+ Windows SAPI 本地兜底 + FFmpeg（可选）；BreezeTTS2 仅设计文档（4D `c0a4d2c`，暂不引入裁决） |
| 缓存 | Redis（可选；不可用时 `test_cache.py:297` 相关 1 例跳过） |
| 可观测 | prometheus-client + OpenTelemetry + Sentry SDK |
| 安全 | pycryptodome + python-jose + passlib[bcrypt] |
| 调度 | APScheduler + schedule |
| 前端 | React 19（`^19.0.0`）+ Vite 8（声明 `^8.3.2`，lock 实锁 **8.3.5** @`165504f`）+ Zustand 5（`^5.0.0`）+ React Query + Playwright + vitest 5（`^5.0.3`）+ **driver.js `^1.10.2`（MIT，教学导览，批 4 引入）+ lucide-react `^0.510.0`（NEW-32 图标族）**；⚠️ `bun.lock` 仍钉旧 undici（package-lock 已升 **7.30.1**；审计门只读 package-lock，收敛留待下批——AGENTS v1.59.1 残留注记） |
| 测试 | pytest + pytest-asyncio + pytest-cov + ruff + mypy |
| 工具系统 | httpx, requests, beautifulsoup4, cloudscraper, duckduckgo_search, chinese_calendar, lunarcalendar |

---

## 11. 风险与建议（按 2026-10-30 现状重写）

| 风险 | 严重度 | 位置 | 建议 / 现状 |
|------|--------|------|-------------|
| **D20 批 1–7 未部署（15 提交本地领先）** | **中** | 本地 HEAD `9815c27` vs `origin/main` `cdbe546` | 批 1–7 全量（体验面 + 14 端点 + 4 表 + last-admin 删除守卫）只在本地；三端统一铁律下部署随用户裁决；收口同步面 = README 徽章 4639 / CODEMAPS / HANDOFF（§4.16 遗留登记） |
| **降级质量悬崖** | **高（产品级）** | `llm_provider/multi_provider_gateway.py` + `config/llm_providers.json` | agnes 超时 40s、熔断阈值 2 / 冷却 120s 在位。**未解决**：供应商侧抖动仍在；`c3ede77` 批观察记录：同窗口 agnes 40s 超时×2（18:13:05/18:13:33）→ 单轮 78.28s 延迟，zhipu 降级均接住、消息未丢（证据在 `LOG.md`）。三个候选方向（降级切同档位模型 / 降级轮走保守短回复 / 降低降级轮 prompt 复杂度）**用户裁决 2026-10-28 继续挂起（先不急）** |
| ~~话题仍是原话锚点~~ → **已根治** | — | `utils/topic_state.py` + `proactive/topic_recall.py` | **`3fe2771` 话题状态机已上线（§4.12）**：确定性状态机 + 每 4 轮稀疏 judge + parked 三重闸 + 7 天 TTL；原 `_extract_user_anchor_sentences`（`prompt_sanitize.py:308`）仍保留作锚点补充。**新观察项**：生产 judge 触发率与 parked 复提质量待运行观察（生产 `topic_plan` 已有真实计划行） |
| **骰子闸默认关闭（无生产行为影响）** | 低 | `proactive/frequency.py:180-193` + `scheduler.py:1576-1592` | `enabled=False` + `dry_run=True` 为出厂契约；开启后先干跑记账。回退开关 `DICE_GATE_PRODUCTION_WIRED=False`；scheduler.py 例外已由用户裁决接受（`dd3b470`）。生产启用时机待用户裁决 |
| **bun.lock 旧 undici 残留** | 低 | `frontend/bun.lock` | `package-lock.json` 已升 undici 7.30.1 / vite 8.3.5（`165504f`）；bun.lock 未跟上，`npm audit` 门只读 package-lock 故 CI 绿；收敛留待下批 |
| **回复模式开关残留全局默认** | 低（R7 已大幅缓解） | `utils/reply_mode.py` + `data/sqlite.db` `user_reply_mode` 表 | per-user 覆盖表 + `GET/POST /api/user/reply-mode` 在位；剩余风险：未覆盖用户仍共享 `data/scheduler_config.json` 全局 `reply_mode` 默认值 |
| **prompt 与超时的反馈回路** | 中 | 编排器 prompt 组装 | 每批 prompt 修复都让 system 变长 → 主模型更慢 → 更易被判超时 → 更多流量落弱模型（v1.55 定性的真根因）。**新增指令前先评估 token 成本**；R6 黄金集为量化该回路提供基线（待接入真实模型 A/B） |
| **黄金集未接入真实模型 A/B（质量量化缺基线）** | 中 | `tests/golden/`（45 条用例） | 确定性用例集已立项（判定委托被测真源、LLM 判定不计基线）；**未解决**：缺真实模型 A/B 量化以证明改善幅度 |
| ConfigLoader.get 高 fan-in | 中 | `my_character/character_config.py` | 单点故障面大，需缓存与降级 |
| `run_console_chat` 复杂度 | 低 | `orchestrator/console_chat.py`（134 行） | 已在 `orchestrator/` 包内，主路径不受阻；可继续函数化拆分 |
| 调度器单例 flock 仅 POSIX 生效 | 低 | `api/run_api.py:245-250`（`_ensure_scheduler_singleton` @ `:222`） | Windows 开发环境无锁（单 worker）；锁文件 `/tmp/ai-girlfriend-scheduler.lock` **禁止手删**（`PrivateTmp=no`，删后新 worker 会抢到新 inode → 调度器翻倍） |
| 多用户 LLM 缓存失效边界 | 低 | `llm_provider/__init__.py` `invalidate_user_llm` | 跨 worker 缓存一致性待验证 |
| 基线口径随卡数漂移 | 低 | `AGENTS.md §4.3` / 本文件 §1.1 | 引用基线必须声明**卡数（41——v3.8.36 实测由 42 漂移，`99c89c39.json` 下落待主控定性）**与**工作树在制品**（当前 = 5 项 `.md`，含主控并行收口写入，业务代码零在制品）；`config/characters/` 变动仍会浮动收集数（2×卡数+13） |
| 后处理并发写同一 SQLite | 低 | `orchestrator/optimized_orchestrator.py:1704 _after_process` | `after_chat` 重活（向量/事实/日记 + 话题 judge/update）经后台线程池执行，与记忆 SQLite 写竞争；已有单线程池约束，长会话下仍需观察写锁等待 |

---

## 12. 查询指南

代码图谱工具（codebase-memory，项目名 `D-Desktop-ai-girlfriend`；⚠️ 本机 `.codebase-memory/` 当前不存在，需先重索引）：

| 需求 | MCP 工具 | 命令 |
|------|----------|------|
| 架构概览 | get_architecture | `get_architecture(project="D-Desktop-ai-girlfriend")` |
| 搜索函数/类 | search_graph | `search_graph(project="D-Desktop-ai-girlfriend", query="emotion engine")` |
| 追踪调用链 | trace_path | `trace_path(project="D-Desktop-ai-girlfriend", function_name="process_message", mode="calls", depth=3)` |
| 自定义查询 | query_graph | `MATCH (f:Function) WHERE f.transitive_loop_depth >= 3 RETURN f.qualified_name, f.complexity` |
| 重索引 | CLI | `codebase-memory cli index_repository --repo-path D:/Desktop/ai-girlfriend` |

**备选方案（MCP 不可用时，本批实测可用）**：

| 需求 | 方法 | 命令 |
|------|------|------|
| 搜索函数/类 | ripgrep | `rg "class PersonaService"` / `rg "def process_message"` |
| 端点实数 | 内省 | `python -c "from api.app_factory import create_api_app; ...APIRoute..."`（见 §1.1） |
| 路由列表 | 装饰器扫描 | `rg "router\." --type py \| rg "\.(get\|post\|put\|delete)\("` |
| 前端页面 / store | 目录列举 | `ls frontend/src/pages/` · `ls frontend/src/store/` |
| 结构认知 | **AOCI** | `aoci.txt`（卷头）/ `aoci.code.txt`（文件级 F-R-A-S 条目）/ `aoci.meta.txt`（词表） |

---

## 13. 更新记录（历史归档）

> **历史归档**：版本表（v3.8.0 → v3.8.32 共 40+ 行，含 2026-07 至 2026-10 各批端点/测试/部署读数）已由 `LOG.md` 权威承载，本文件只保留近两版指针。批次详情、生产取证见 `LOG.md` 对应日期条目。

| 版本 | 日期 | 要点 |
|------|------|------|
| **v3.8.36** | **2026-10-30** | **批 7 收尾口径（D20 前端全站体验批 1–6 + 批 6 对抗收编 + 批 7 四收编；本地 15 提交领先 origin、未部署）**：新增 §4.16；端点 **235/197 → 249/205**（+14：misc +1 / training +4 / announcements +6 / admin +3；批 7 零新端点）；方法分布 113/85/16/21 → **118/90/18/23**；路由挂载 28 → **30**（公告双挂载 `:276-277`）；域路由 25 → **26**（+`announcement_routes`）；Python tracked 589/159,543 → **594/161,346**；api 49 → **51**（+`audit.py` / `announcement_routes.py`）；frontend/src 138 → **154**；页面 19 → **20**（+HelpGuidePage `/help`）；API 模块 15 → **16**（+announcements）；store 5 → **6**（+globalViewStore）；测试文件 266 → **269**（+3）；主控实测后端 **4296 通过 / 2 跳过 / 3 存量失败族**（均 HEAD 对照实证）+ 前端 **342/47** + ruff 0 + tsc 0 + 突变验红 4/4；徽章口径 **4508 → 4639**（README 同步归收口批）；users.db 口径新增 **14 表**（+4）；⚠️ 角色卡 42 → **41** 漂移登记。 |
| v3.8.35 | 2026-10-28 | 工作区整理批：话题状态机 `3fe2771`（§4.12）/ 拟人化 v1.57（§4.15）/ 四项跟进 + CI 修复 + hotfix 整本回扫；新增 §4.13 骰子闸 / §4.14 行为蒸馏；端点 231/195→235/197；收集 4263（266 文件）/ 4256+2+4；徽章 4508；store 4→5（+topicState）。要点归档 `LOG.md` 2026-10-28 条。 |
| v3.8.34 | 2026-10-21 | 八项裁决批：R4 真流式（`utils/stream_guard.py`）/ R3 工具记忆结合 / R6 黄金集立项（45 条用例）/ R7 模式显式切换按用户隔离 / 轮次 LLM 预算总账 / provider `stream_enabled` / RAG 去重 / AOCI 补全 1116 行；端点 231/195；收集 3900（工作树在制品口径）。 |
| v3.8.33 及更早 | 2026-07 ~ 10-20 | 重建批、窗口死寂批（v1.52）、时间感知 3.0（v1.53）、聊天质量两批（v1.54/v1.55）等——一律见 `LOG.md` 对应日期条目。 |

---

## 14. 架构债清理决策 + 双 API 分工

| 模块 | 评估结论 | 依据 |
|------|---------|------|
| `shisi/memory/legacy/` | **保留，不重命名** | 被 `shisi/application/memory_service.py` 与 `tests/test_memory*.py` 大量引用；`legacy` 仅表"历史迁移"非"待删除" |
| `shisi/knowledge/legacy/` | ~~保留~~ → **已整删（批6b 项2）** | RAGEngineV2 生产零读者，整包连同其测试删除；现役 `Retriever`/`CharacterKnowledgeService` 不受影响 |
| `character_card/` | ~~保留~~ → **已整删（批6b 项10）** | `CharacterCardAdapter` 全仓零读者，整包 6 文件删除 |
| `shisi/api/` | **保留** | 由 `api/app_factory.py:297` `from shisi.api.registry import setup_shisi` 装配（`:302` 调用）；与 `api/routers/` 形成双 API 层分工 |
| `requirements.txt` | **已删除** | `pyproject.toml` 为唯一权威依赖源 |

### 双 API 路由层分工

| 层 | 路径 | 职责 | 调用方 |
|---|---|---|---|
| 域路由层 | `api/routers/*.py`（**26 模块**，不含 `__init__.py`） | 控制 plane 端点：character/auth/admin/voice/wechat/clone/topic/announcement/… | `api/app_factory.py` 主挂载（22 处字面 `include_router` + 9 处 `_mount_required_router` 调用） |
| shisi 域层 | `shisi/api/*.py`（11 文件） | DDD 核心 plane 端点：character/affinity/emotion_stage/persona/stats/vital_signs/sticker/memory | `setup_shisi` 由 app_factory 调用（`api/app_factory.py:297` import / `:302` 装配） |

两层不冲突：域路由层面向"控制/管理"，shisi 域层面向"DDD 核心域"，统一经 `create_api_app()`（`api/app_factory.py:94`）装配。

### 关键架构决策（现状核对）

| 决策 | 位置 | 现状 |
|------|------|------|
| 多用户 LLM 隔离走 `User.llm_config` JSON 字段 | `api/database.py:85` / `llm_provider/__init__.py` | 生效；普通用户可读写自己的配置，admin 走 `/api/config` |
| 微信连接自动恢复用文件锁 | `api/run_api.py:500 _autostart_wechat_connector()` | 已改为**每会话目录 `poll.lock`**（`channel_paths`），旧全局 flock 方式废止 |
| PNG 角色卡走 `shisi/character/png_codec.py` | `shisi/character/png_codec.py` | 生效（SillyTavern 生态标准，归 DDD character 子域） |
| 克隆朋友架构 = 本地提取 → 上传 → 服务器分析 | `api/routers/clone_routes.py` | 生效（wechat-decrypt 依赖 Windows 进程，不能跑 Linux） |
| 多 worker 调度器单例走 flock | `api/run_api.py:222 _ensure_scheduler_singleton()`（flock @ `:245-250`） | 生效；锁文件 `/tmp/ai-girlfriend-scheduler.lock`，Windows 分支无锁（单 worker） |
| `--no-scheduler` 经 `DISABLE_SCHEDULER=1` 传子进程 | `main.py`（`_run_orchestrator` @ `:469`） | 生效；`run_api` 启动检查该变量等价 `--no-scheduler` |
| 话题状态机三表落 `data/sqlite.db` | `utils/topic_state.py:86/:107/:119`（DDL）+ `_connect(ensure_schema=True)` `:380-389` | 生效（`3fe2771`）；惰性建表（写路径首写才建），生产三表已在位 |
| **结构真源迁移 AOCI**（2026-10-20 起） | `aoci.txt` / `aoci.code.txt` / `aoci.meta.txt` | **已提交**：`aoci.code.txt` **1116 行**（本批 `wc -l` 实测）。本文件降级为人读快照与规模读数 |

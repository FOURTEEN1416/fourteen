# 代码图谱 — unique-you (唯一的你) **v3.8.34**（2026-10-21 八项裁决批）

> **v3.8.34（2026-10-21 八项裁决批 · 工作树在制品，未提交）**：① **R4 主链真流式**——`chat.stream_mode: true`（`config/system.yaml:63`，与供应商层 `llm.stream_enabled` 正交）按句边增量下发，新增 `utils/stream_guard.py`（**597 行**，`IncrementalReplyGuard` 出站守卫状态机；已发前缀永不回滚、未发余量流末交 `_finalize_reply` 终裁）；② **R3 主链工具/记忆结合**——能力声明段按**真实可用性**渲染（`_memory_recall_available()` 为唯一判定）+ ToolGate L0 新增 memory 工具晋级规则 + 内部标识出站剥离；③ **R6 黄金集立项**——新建 `tests/golden/`（**45 条用例**：8 个 jsonl 分类 + `runner.py` 792 行 / `judges.py` 154 行 / `test_golden_set.py` 入口，确定性优先、判定委托被测真源、LLM 判定不计基线）；④ **R7 模式显式切换（按用户隔离）**——全局 `reply_mode` 降为默认 + per-user 覆盖表（`data/sqlite.db`）+ 一轮单次解析透传 + admin 作用域与用户自助端点（新 `GET/POST /api/user/reply-mode`）；⑤ **轮次 LLM 预算总账**——`TOTAL_TURN_LLM_BUDGET=100.0` / `remaining_turn_llm_budget()`（`optimized_orchestrator.py:98/101`）与具名 `MAIN_CHAIN_LLM_TIMEOUT_SECONDS=60.0`，各跳取 `min(单跳, 剩余预算)`，覆盖主链 / `_stream_mixin` / `tool_gate`；⑥ **provider 消费 `stream_enabled` 死配置 + 延迟日志**——`openai_compatible_provider.py` 新增 `_chat_aggregated`（流式聚合）与 `_log_call_outcome`（provider/model/stream/ok/elapsed_ms）；⑦ **RAG 片段去重**（`context_budget.dedup_rag_snippets`）/ **会话尾巴净化**（`prompt_sanitize.scrub_history_rows`）/ **主动决策历史净化**（`scheduler` 对 `recent_messages` 过 `scrub_history_rows`）；⑧ **AOCI 补全**（`aoci.code.txt` 753→**1116** 行）。读数见 §1.1；跨文档口径冲突见 §1.1 表下注。
> **本版要点（v3.8.33 重建批）**：按实测读数整本重建（drifted 节重写、历史覆盖层与旧图谱快照压缩为归档指针、模块树补 `utils/`/`proactive/` 等缺章节）。**结构真源 = AOCI**（`aoci.txt` 卷头 → `aoci.code.txt` 实体条目 → `aoci.meta.txt` 词表）；本文件只保留人读的规模读数、分层骨架与维护指针。
> **v1.55（2026-10-20 质量悬崖根治，代码 @ `8eb4abe` 三端已部署）**：agnes 超时 15→**40s**、熔断阈值 1→**2**/冷却 300→**120s**、`primary_only` 链失败一次即开闸、主链外层 `wait_for` 60s、降级成功落 `quality_degraded`；`mes_example` 知识库第二泄漏通道两处注入点先 `strip_stage_directions`；出站守卫补归属翻转与裸「你呢」；历史回灌就地净化；话题改 `_extract_user_anchor_sentences` 原话锚点。
> **v1.54（同日首轮，`1bba5b2`，当时未部署）**：模式边界泄漏 / 过度指令 / 出站语义终审 / 供应商静默降级可观测 / 话题噪声五源根治。
> 批次详情、生产取证与历史版本见 `LOG.md`（唯一只追加不覆盖文件）。

> 由维护者手动维护 | **最后核实: 2026-10-21（v3.8.34 八项裁决批）**，读数命令见各节「核实方法」列；更早的核实记录一律见 `LOG.md` 对应批次条目。
> **读出纪律**：① 引用测试基线必须同时声明**角色卡数**与**工作树是否含在制品**（见 §1.1 表下注）；② 端点口径只认 `APIRoute` 实例数，不认 `len(app.routes)`（后者恒定多 4 条框架路由）。

---

## 1. 全局指标

### 1.1 实时核实指标（2026-10-21 本批亲跑）

| 维度 | 数值 | 核实方法 |
|------|------|---------|
| 仓库 HEAD | **`fc0e0e4`**（`docs(聊天质量二轮): LOG落账 + AGENTS v1.55 + HANDOFF`）；业务代码三端一致点 = **`8eb4abe`** | `git log --oneline -1` |
| API 业务端点 | **231 `APIRoute` / 195 条唯一路径**；`len(app.routes)` = **235**（含 4 条框架路由 `/openapi.json` `/docs` `/docs/oauth2-redirect` `/redoc`） | `create_api_app()` 内省（本批实跑，`AI_GF_ENV=dev`）：`len([r for r in app.routes if isinstance(r, APIRoute)])` = **231**；唯一路径 **195**。较 v3.8.33 的 229/194 增 2 = R7 新增 `GET/POST /api/user/reply-mode`（`api/routers/training_routes.py:519/541`） |
| 方法分布 | **111 GET / 84 POST / 16 PUT / 20 DELETE** | 同上，按 `route.methods` 计数 |
| 路由挂载 | 字面 **19 处 `app.include_router(`**（其中 1 处在 `_mount_required_router` 辅助函数内）+ **9 处 `_mount_required_router(...)` 调用** → 运行期路由挂载点 **27 处**；另加 `setup_shisi` 一处（`api/app_factory.py:295-300`） | `grep -c "app\.include_router(" api/app_factory.py` = 19；`grep -c "_mount_required_router(" ` = 10（含 def） |
| 域路由模块 | **24 个**（`api/routers/*.py` 不含 `__init__.py`） | `ls api/routers/*.py \| grep -v __init__ \| wc -l` |
| Python 规模（提交基线 @ HEAD） | **551 文件 / 142,168 行** | `git ls-tree -r --name-only HEAD \| grep '\.py$'` + 逐文件 `git show HEAD:<f> \| wc -l` 累加（与审计 trueNumbers 完全一致） |
| Python 规模（工作树 tracked，含未提交改动） | 551 文件 / **144,603 行**（相对 HEAD 净 **+2,435**） | `git ls-files '*.py' \| xargs wc -l` = **144,603**；`git diff --numstat -- '*.py'` 净 **+2,435**（**不含** 16 个未追踪 .py 文件：12 个新测试 + `utils/stream_guard.py` + `tests/golden/{runner,judges,test_golden_set}.py`，`git ls-files --others --exclude-standard '*.py'` = 16） |
| Python 规模（磁盘全量） | **728 文件 / 188,189 行**（剔除 `.git` / `node_modules` / `__pycache__`）；剔除 `大创赛报名以及后期发展/`（gitignored，**160** 文件）后 **568 文件 / 149,842 行** | `find . -name '*.py' -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*__pycache__*'` + `xargs cat \| wc -l`（本批实跑）。`大创赛报名以及后期发展/` 内为软著申请用的 `user_data/` 代码副本（160 个 .py） |
| main.py | **573 行 / 25,343 字节** | `wc -l main.py`；`ls -la main.py` |
| orchestrator/ | **9 文件 / 4,323 行（@HEAD）**：`optimized_orchestrator.py` **1969**、`_init_mixin.py` **589**（11 个 `_init_*` 阶段）、`_stream_mixin.py` **352**、`context_budget.py` **591**、`tool_gate.py` **381**；**工作树（含在制品）9 文件 / 4,880 行**：`optimized_orchestrator.py` **2191** / `_stream_mixin.py` **607** / `context_budget.py` **665** / `tool_gate.py` **387** | `git show HEAD:<file> \| wc -l`；工作树 `wc -l`（本批实跑） |
| Python 测试 | **工作树收集 3900（250 测试文件，42 卡在位）/ 3898 通过 + 2 跳过 / 0 失败**；**提交基线 @`fc0e0e4`：收集 3627（237 文件）/ 3626 通过 + 1 跳过 / 0 失败** | **收集数本批亲测**：工作树 `pytest --collect-only -q` = **3900**（14.03s）；四分块串行实跑合计 = **3898 通过 + 2 跳过 / 0 失败**（见下注）。HEAD 基线引 `LOG.md` 2026-10-20 二轮条目 / AGENTS v1.55 |
| 前端测试 | **217 通过 / 33 文件**（vitest ^5） | 文件数本批亲测：`find frontend/src -name '*.test.*' -o -name '*.spec.*' \| wc -l` = **33**；用例数引 AGENTS v1.55 / `LOG.md` |
| 测试合计（徽章） | **4117**（3898 Py 通过 + 217 FE + 2 跳过标记，工作树口径） | 本批重算；提交基线徽章 = **3844**（3626 Py + 217 FE + 1）。⚠️ 跨文档：`README.md:11` 徽章仍为 **3833**（v1.54 口径），**待文档收口员同步**（见 §1.1 表下注冲突①） |
| 现役角色卡 | **42 张**（`config/characters/*.json`） | `ls config/characters/*.json \| wc -l`。目录被 `.gitignore:120` 忽略（`git check-ignore -v` 实测；AGENTS 旧文写 `:117` 为行号漂移），**卡数不随 git 复现** |
| 前端页面 / API 模块 / store | **19 页 / 15 API 模块 / 4 store**（auth / characterBuilder / error / **humanize**） | `ls frontend/src/pages/*.tsx`、`ls frontend/src/api/*.ts`、`ls frontend/src/store/` |
| tools/builtin | **9 文件 / 17 个工具类**；默认注册 **14**（`config/system.yaml` `tools.builtin_tools`；较上版 **+`memory`**，R3 启用） | `ls tools/builtin/*.py`；`grep -c "^class .*Tool"`；`python -c "import yaml; print(len(yaml.safe_load(open('config/system.yaml'))['tools']['builtin_tools']))"` = 14 |

> ⚠️ **口径注记（v3.8.34 实测，必须连读）**：① 上表「3900 / 250」是**工作树含在制品**口径（八项裁决批本轮实施：真流式 / 工具记忆结合 / 黄金集 / 模式隔离 4 个实施任务，未提交）。**提交基线（@`fc0e0e4`）= 收集 3627（237 文件）/ 3626 通过 + 1 跳过 / 0 失败**（AGENTS v1.55 口径）。工作树增量 = **12 个新根测试文件**（memory_capability_declaration / memory_probe_on_demand / memory_usage_rule / proactive_decision_history_scrub / provider_call_latency_log / provider_stream_enabled / rag_snippet_dedup / session_tail_scrub / tool_gate_llm_budget / tool_gate_memory_rule / true_streaming / turn_llm_budget）+ **`tests/golden/test_golden_set.py`** + **12 个已改测试文件**。引用任一数字都必须声明是「HEAD 基线」还是「含在制品」。
> ⚠️ **聚合态跑法**：单进程整跑 `pytest -q` 会在随机位置停住（环境问题，非用例失败），须**串行**四分块；分块清单必须 `find` 递归生成——工作树实测 **250** 个 `test_*.py` = 根目录 **241** + `tests/core/` **6** + `tests/api/` **2** + `tests/golden/` **1**（HEAD 口径 237 = 229 + 6 + 2；漏子目录会少跑一批用例，10-17 批已实证漏 `tests/api/` 少 32 例）。
> ✅ **基线公式复核**：`tests/test_persona_injection.py` 单独收集 = **2 × 42 卡 + 13**（沿用 v3.8.33 实测 97），卡数变则全量收集数随之浮动。
> ✅ **收集数复核方式**：工作树实跑 `pytest --collect-only -q` = **3900**（本批亲测，14.03s）。
> ✅ **四分块串行实测（本批亲跑）**：**896 + 962（含 2 跳过）+ 954 + 1086 = 3898 通过 + 2 跳过 / 0 失败**（四块相加 = 收集 **3900** 精确吻合；分块清单 `find` 递归生成、`-p no:cacheprovider` 串行）。**2 例跳过为非回归**：`tests/test_cache.py:297`（Redis not available）+ `tests/golden/test_golden_set.py:48`（`live_llm` 用例默认不跑，设 `GOLDEN_LIVE=1` 才启用真实供应商）。
> 📌 **跨文档口径冲突（由文档收口员处理，本图谱不改他文）**：① `README.md:11` 徽章仍 `Tests-3833`（v1.54 口径）；② `README.md:19` 与 `README.md:196` 测试口径仍为 **3615 passed + 1 skipped / 收集 3616**（v1.54 数字）；③ `AGENTS.md §4.3` 写角色卡目录被 `.gitignore:117` 忽略，实测 `git check-ignore -v` = **`.gitignore:120`**（行号漂移，本图谱 §1.1 已用 120）；④ `docs/HANDOFF_REPORT.md`「当前状态」快照停在 2026-10-20 批（`8eb4abe`），未含 2026-10-21 在制品批——引用时须知其口径 = 已提交态。

### 1.2 图谱工具快照（历史归档）

> **历史归档**：codebase-memory 图谱（节点/边/Leiden 社区/fan-in 榜单）最后一轮有效索引为 2026-09-02（7997 节点 / 33187 边），产物目录 `.codebase-memory/` 已被 `.gitignore:131` 忽略且**当前磁盘不存在**——本文件不再承载其读数。重索引命令与查询方式见 §12；历史读数见 `LOG.md` 2026-08-28 / 2026-09-02 条。

---

## 2. 架构分层（文字版，替代旧图谱 mermaid）

请求自外向内四层，**调用方向单向**（越界即循环依赖风险）：

1. **入口层**：`main.py`（`_run_orchestrator`，573 行）/ `api/run_api.py`（uvicorn，`create_api_app()` @ `:626`）。
2. **接口层**：`api/`（`app_factory.py:93 create_api_app()` + 24 域路由 + `setup_shisi`）与 `shisi/api/`（DDD 核心域 30 端点）。
3. **编排层**：`orchestrator/`（`OptimizedOrchestrator` 三件套 + `tool_gate` / `context_budget` / `session_locks`）。
4. **领域与服务层**：`shisi/`（DDD 核心）、`my_character/`、`persona_extractor/`、`security/`、`llm_provider/`、`proactive/`、`voice/`、`wechat_direct/`、`tools/`、`utils/`。

> **历史归档**：旧版「知识图谱基于 fan-in/fan-out 自动识别的 4 层 mermaid」为图谱产物（含 226 路由旧读数），已删除；分层判断以本节与 §4 模块表为准。

---

## 3. 核心数据流 — 主链热路径

`OptimizedOrchestrator.process_message`（`orchestrator/optimized_orchestrator.py:1838` @工作树 / `:1634` @HEAD）是全系统最关键调用链；流式走 `_stream_mixin.process_message_stream`（`orchestrator/_stream_mixin.py:128` @HEAD）。上下文准备 `_prepare_context`（`:950` @工作树 / `:787` @HEAD）、出站定稿 `_finalize_reply`（`:1763` @工作树 / `:1570` @HEAD）。

```
用户消息(text, session_id)
 ├─ 会话锁 _get_session_lock / ProcessSessionLock（跨 worker 互斥）
 ├─ 安全线：PIIAnonymizer → PromptInjectionDetector → ContentSafetyFilter.check_input
 ├─ 上下文（并发 gather）
 │    ├─ 历史：DB 会话过滤真源 + annotate_history_time_gaps(context_budget:418，
 │    │        相邻 ≥6h 插 [时段分隔] 头注/翻篇尾注) + 历史回灌就地净化(沉浸式剥括号/删元解释)
 │    │        + 会话尾巴净化 scrub_history_rows(prompt_sanitize，R7 跨会话尾巴 assistant 前缀剥除)
 │    ├─ 记忆：retrieve_context / get_recent_context（按 user_key 隔离）
 │    ├─ 人格：PersonaExtractor → PersonaService.build_system_prompt
 │    │        Phase 1 shisi prompt_builder(角色+RAG+情感+历史；RAG 片 dedup_rag_snippets 去重) → Phase 2 五层对齐
 │    │        沉浸式：mes_example / first_mes / knowledge_context / rag_context 四处先 strip_stage_directions
 │    │        能力声明段按真实可用性渲染（_memory_recall_available() 唯一判定，R3）
 │    └─ 话题：_extract_user_anchor_sentences（只取 user 行完整短句，24h 窗）
 ├─ 工具：ToolRegistry.get_tools_by_permission → tool_gate L0/L1 → ToolDispatcher.dispatch
 │        L0 新增 memory 工具晋级规则（『你还记得…/我说过…吗』→ memory；R3；memory 已在 system.yaml 启用）
 ├─ LLM：MultiProviderGateway（Agnes→智谱→讯飞→千帆；熔断阈值 2 / 冷却 120s / agnes 40s）
 │        轮次预算总账：llm_deadline = now + TOTAL_TURN_LLM_BUDGET(100.0s)；
 │        各跳取 min(单跳额度, remaining_turn_llm_budget(deadline))；主链主跳 = MAIN_CHAIN_LLM_TIMEOUT_SECONDS(60.0s)
 │        （optimized_orchestrator:1965 @工作树；budget 常量 @:80/98/101）
 ├─ 出站终审：terminal_reply_guard(utils/prompt_sanitize.py:891 @HEAD)
 │        reply_mode 参数 → 逐字复述剥除 / 元记忆解释+零信息问卷句级删 /
 │        归属翻转句删 / 无由头「你呢…？」截断 / 沉浸式括号段剥离 / 系统标记剥除 / 称呼呼语剥除
 │        真流式（R4）：IncrementalReplyGuard 按句边增量下发，已发前缀永不回滚，未发余量流末交 _finalize_reply 终裁
 └─ 后处理：MemoryPipeline.after_chat → ASEEngine.on_chat → AffinityMapper.sync → 画像同步智能体
```

> **本链的已知叠加层**：`reply_mode`（`utils/reply_mode.py`，`DEFAULT_REPLY_MODE = "immersive"` @HEAD`:42`；**R7 起 `read_reply_mode(user_key)` 支持 per-user 覆盖表**——全局值降为默认、按 `user_key` 读覆盖，旧无参签名见 @HEAD`:114`）决定括号段存废；`utils/address_terms.py`（237 行）为称呼白名单唯一真源；`utils/session_key.py`（165 行）为 `owner:peer` 会话键唯一真源；**`utils/stream_guard.py`（597 行，R4 新增）为出站增量守卫状态机**；`shisi/core/conversation_turn.py`（177 行）为「轮 + 发言者归属」唯一真源。
> **历史归档**：2026-07-01 的 PersonaService 两阶段说明仍有效（见上），但当年 5 层对齐的中间态细节与 07-26 拆分过程见 `LOG.md`。

---

## 4. 模块目录与职责

### 4.1 入口与编排

| 模块 | 文件 | 职责 |
|------|------|------|
| `main.py` | **573 行 / 25,343 B** | 入口 + `_run_orchestrator`（`:469`）统一启动 + `_wire_proactive_runtime`（`:281`，提醒/主动两入口唯一装配 owner）+ 控制台/微信模式 |
| `orchestrator/` | **9 文件包 / 4,323 行**（HEAD） | `optimized_orchestrator.py` **1969 行**（主类 + `_prepare_context` / `process_message` / `_finalize_reply` / `health_check`；**轮次 LLM 预算总账** `TOTAL_TURN_LLM_BUDGET=100.0` / `remaining_turn_llm_budget()` / `MAIN_CHAIN_LLM_TIMEOUT_SECONDS=60.0`，`_memory_recall_available()` 为能力声明唯一判定）+ `_init_mixin.py` **589 行**（11 个 `_init_*` 阶段，唯一初始化真源）+ `_stream_mixin.py` **352 行**（**R4 真流式 SSE：按句边 `IncrementalReplyGuard` 增量下发；FIX-3 与主链共用轮次预算；FIX-4 `_finalize_reply` 补传 `session_id`/`last_turn_age_seconds`**）+ `session_locks.py` + `voice_detector.py` + `console_chat.py` + `tool_gate.py` **381 行**（三级工具意图管线；**L0 新增 memory 工具晋级**）+ `context_budget.py` **591 行**（预算/去重/时间分段/信封；**`dedup_rag_snippets` RAG 片段去重**） |
| `api/run_api.py` | 642 行 | API-Only 启动入口（`create_api_app()` @ `:626`，import 于 `:45`）；`_ensure_scheduler_singleton()`（`:222`，flock @ `:245-250`，锁文件 `/tmp/ai-girlfriend-scheduler.lock`）；`_autostart_wechat_connector()`（`:500`）按 `channel_paths` 逐 user/slot 恢复 |
| `user_scheduler.py` | 743 行 | 多用户调度，每个微信用户独立情感状态 |

> ⚠️ **工作树在制品读数**（含 2026-10-21 八项裁决批，未提交；勿当基线）：`optimized_orchestrator.py` **2191** / `_stream_mixin.py` **607** / `context_budget.py` **665** / `tool_gate.py` **387**（orchestrator 包 **4,880 行**）。
> **架构演进（历史归档）**：`_run_fast_mode` + `_run_full_mode` 于 2026-07-28 合并为 `_run_orchestrator`，`console_chat` 已自 main.py 迁出——详见 `LOG.md` 2026-07-28 / 2026-08-28 条。

### 4.2 API 层（**231 业务端点 / 195 唯一路径** — 2026-10-21 八项裁决批内省实扫）

| 来源 | 路径 | 端点数 | 文件数 | 说明 |
|------|------|--------|------|------|
| `api/routers/` | **24 个域路由模块**（不含 `__init__.py`；24 个全部带路由） | **196** | 25（含 `__init__.py`） | character / auth / admin / invite / voice / mimo_voice / storyline / wechat / wechat_channel / emotion / memory / knowledge / persona_card / chat / clone / misc / personality / safety / tools / training / users / llm_providers / agent_plane / character_template |
| `api/`（非 routers） | `health_routes.py` / `qrcode_store.py` / `app_factory.py` 内联 | **5** | 3 | health 3（含 `/api/metrics`）+ qrcode 1（admin 兼容面）+ app_factory 内联 `/api/shisi/status` 1（untagged） |
| `shisi/api/` | v1（`v2/` 死模块 2026-09-18 删除） | **30** | 11（其中 **8** 个文件带路由） | character 7 / memory 6 / sticker 5 / affinity 4 / emotion_stage 3 / persona 3 / stats 1 / vital_signs 1（+ 上面内联 status = 31 域面）；`__init__` / `common` / `registry` 无路由 |
| **合计** | | **231** | | 111 GET / 84 POST / 16 PUT / 20 DELETE；`len(app.routes)`=235 |

> 🔢 **两个数字不是同一件事**（易误读，特此点明；本批用 `inspect.getmodule` 分组实测）：表中 `api/routers/` 行的 **196 = 该层端点数**（24 文件）；§1.1 的 **195 = 全站唯一路径数**。校验：196（routers）+ 30（shisi/api，8 个文件带路由）+ **5**（api 非 routers：health 3 + qrcode 1 + app_factory 内联 1）= **231** = `APIRoute` 总数 ✓。

> ⚠️ **口径纠错**：旧口径「206 / 208 端点」取自 `len(app.routes)`，其中固定含 4 条 FastAPI 框架路由，系统性偏高 4。**业务端点 = `APIRoute` 实例数**。演进链：2026-09-21 `220/186` → +W4 forwards 1 +W10 metrics 1 +W12 templates 2 +W9 自服务 5 = 229 → D16 退役 peers 3 = **226/193** → v3.8.33 重建批 **229/194** → **v3.8.34** 实测 **231/195**（+2 = R7 用户自助 `GET/POST /api/user/reply-mode`，同在 `training_routes.py`、tag 归 `training`）。

**按 tag 的端点分布**（内省实测，权威口径；**training 18**，其余与上一版一致）：

```
character 21 │ training 18 │ misc 16 │ auth 13 │ safety-infra 12 │ chat 11
memory 11 │ personality 10 │ wechat 9 │ clone 8 │ knowledge 8 │ users 7
characters 7 │ tools 6 │ wechat-channel 6 │ voice 6 │ mimo-tts 6 │ storyline 6
llm-providers 6 │ agent-plane 5 │ stickers 5 │ admin 5 │ affinity 4 │ invite 4
health 3 │ emotion-stage 3 │ persona 3 │ persona-card 3 │ admin-wechat 2
character-templates 2 │ emotion 2 │ vital-signs 1 │ stats 1 │ (untagged) 1
                                          ────────── 合计 231
```

> 注：`character`(21) = `api/routers/character_routes.py`；`characters`(7) = `shisi/api/character_routes.py`。`memory`(11) = api `memory_routes`(5) + shisi `memory_routes`(6)。`auth`(13) = 原 8 + W9 自服务 5。`training`(18) = training 5 + proactive 6 + **humanize/config 3** + knowledge/collect-config 2 + **reply-mode 2（R7）**（`api/routers/training_routes.py` 单文件承载五组）。`wechat-channel`(6) + `admin-wechat`(2) 同属 `wechat_channel_routes.py`（一双 router 文件）。

**`api/app_factory.py:93 create_api_app()`** 是 FastAPI 应用唯一构造入口（`FastAPI(..., version="3.1.0")` @ `:112`），被 `api/run_api.py:626` 与 `main.py` 调用。挂载清单（源码逐行核对；19 处字面 `app.include_router` + 9 处 `_mount_required_router` 辅助调用 + `setup_shisi`）：

**按定义模块的端点归属**（`inspect.getmodule(route.endpoint)` 实测，合计 196 + 30 + 5 = **231**）：

```
api/routers/（24 文件，196 端点）
  character_routes 21 │ training_routes 18 │ misc_routes 16 │ auth_routes 13
  safety_routes 12 │ chat_routes 11 │ personality_routes 10 │ clone_routes 8
  knowledge_routes 8 │ wechat_channel_routes 8 │ wechat_routes 8 │ users_routes 7
  llm_providers_routes 6 │ mimo_voice_routes 6 │ storyline_routes 6 │ tools_routes 6
  voice_routes 6 │ admin_routes 5 │ agent_plane_routes 5 │ memory_routes 5
  invite_routes 4 │ persona_card_routes 3 │ character_template_routes 2 │ emotion_routes 2
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
| `application/` | 应用服务 | **ShisiMemoryService**、**PersonaService**（**R7：`rag_context` 消费 `orchestrator.context_budget.dedup_rag_snippets` 去重**，`persona_service.py:374-376` @工作树）、**MigrationService**、**PromptService**、**ShisiKnowledgeAdapter**（⚠️ 旧文所列 `CharacterService` 已于批6b 删除，现无此类） |
| `character/` | 角色卡完整子系统 | **`CharaCardV2`**（模型）+ 解析链 `CharaCardV2Parser` / `AiyuPromptsParser` / `SillyTavernParser` / `ParserDispatcher`；`PersonaImporter` / `PersonaExporter`；`CharacterManager`；`png_codec`（模块函数 + `PNGCodecError`，**无 `PNGCodec` 类**）；Store / Validator / models |
| `core/models/` | 领域模型 | `AffinityLevel`、`CharacterId`、`EmotionalState`、`EmotionType`、`PersonaProfile`、**`CharacterAggregate`** |
| `core/ports/` | 端口协议 | `CharacterRepository`（Protocol） |
| `core/services/` | 领域服务 | **`prompt_builder`（模块级 `build()`/`merge_with_persona_prompt()`；R3 新增 `capability_flags`——能力声明段按真实可用性渲染）**；⚠️ 旧文所列 `EmotionDetector` 全仓不存在 |
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

**shisi/character/ 说明（本批源码核对，替换旧表述）**：
`png_codec.py` = SillyTavern PNG tEXt chunk 编解码（**模块级函数**：`is_png()` `:41` / `extract_card_from_png()` `:46` / `embed_card_to_png()` `:94` / `has_chara_chunk()` `:155` + `PNGCodecError` `:37`，依赖 Pillow）；`importer.py::PersonaImporter` / `exporter.py::PersonaExporter` 支持 .json 与 .png 双向；`character_card_v2.py` 的 `ParserDispatcher` 按格式分派 `CharaCardV2Parser`（SillyTavern V2/V3）/ `AiyuPromptsParser` / `SillyTavernParser`；`manager.py::CharacterManager` 由 `shisi/api/registry.py:setup_shisi` 装配。PNG 集成路径：`api/routers/character_routes.py:959` import 并调用 `extract_card_from_png()`（`:964` 捕 `PNGCodecError`），`:1031` import 并调用 `embed_card_to_png()`；前端 `frontend/src/api/characters.ts` 调 `POST /api/characters/import` 与 `GET /api/characters/{id}/export?format=png`。

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
| `multi_provider_gateway.py` | 多供应商网关：默认链 **Agnes → 智谱AI → 讯飞星火 → 百度千帆**；用户级 gateway 缓存；**常驻同步事件循环**（`chat_sync` 不再每次 `asyncio.run`，稳态 ~2.7s）；**熔断** `_BREAKER_THRESHOLD=2` / `_BREAKER_COOLDOWN=120s`（`:296-297` @HEAD），`_mark_provider_result(immediate=True)`（`:413`）供 `primary_only` 链一次失败即开闸 |
| `openai_compatible_provider.py` | OpenAI 兼容供应商（agnes/zhipu/xunfei/baidu 共用）；`_request_timeout` 按供应商收紧；**HTTP 200 空 content 在 `mark_success()` 之前判空抛错**（含 choices / reasoning_only / message_keys），主备失败均 WARNING 带响应体前缀 300 字；**消费 `stream_enabled`（FIX-2，2026-10-21）：该字段此前是死配置，`chat()` 现于其为真时走 `_chat_aggregated()` 流式聚合**；新增 `_log_call_outcome()` 延迟日志（provider/model/stream/ok/elapsed_ms） |
| `__init__.py` | `invalidate_user_llm(user_id)`（`:332` @HEAD / `:336` @工作树）— 清除用户级 gateway 缓存 |

| 供应商 | 注册方式 | 模型 | 认证 |
|--------|---------|------|------|
| **agnes（链首）** | `get_llm(provider="agnes")` | agnes-3.0-flash（`request_timeout=40`） | Bearer |
| 智谱AI (zhipu) | `get_llm(provider="zhipu")` | glm-4.5-flash（`extra_payload.thinking=disabled`） | Bearer |
| 讯飞星火 (xunfei) | `get_llm(provider="xunfei")` | spark-lite | Bearer |
| 百度千帆 (baidu) | `get_llm(provider="baidu")` | ernie-speed-128k | OAuth |
| DeepSeek | `get_llm(provider="deepseek")` | deepseek-chat / reasoner | LLMGatewayV2（不入 auto 默认链） |

> ⚠️ **链真源现状（v3.8.34 复核，原「链双真源」表述已失效）**：① `config/system.yaml` `llm.fallback_chain`（**agnes 首选**，经编排器 `_init_llm` 显式传入）= **生产主链**；② `config/llm_providers.json` 顶层 `fallback_chain` 现**同为 agnes 首选** = `['agnes','zhipu','xunfei','baidu','deepseek']`——两处一致，**不再构成「双真源」分歧**；历史「zhipu 首选」口径已失效（本批复核：`git show HEAD:config/llm_providers.json` 与工作树 JSON 读取均输出 agnes 首选）。**sensenova 已于 2026-09-18 移除**（生产 `.env` 从未配其 key = 每次对话白跑一轮失败）。
> ⚠️ **超时真源两处不一致（观察项）**：`config/llm_providers.json` agnes `request_timeout=40`（v1.55 批），而 `config/system.yaml` 注释仍写 20s；以编排器实际传入的 `llm` 段与 providers 文件为准，`system.yaml` 注释待清理。

**多用户 API Key 隔离**：`User.llm_config` JSON 字段（`api/database.py:85`）→ `/api/user/llm-config` GET（`api/routers/misc_routes.py:512`，脱敏 `****`）/ POST（`:564`，自动 `invalidate_user_llm()`）。调用链：`SettingsLLM.tsx` → `frontend/src/api/system.ts` → `POST /api/user/llm-config` → `users.llm_config` → 下次 `process_message` 按 `user_id` 取专属 LLM。

### 4.7.1 wechat_direct/ — 每人独立微信通道（4 文件；D16 后 `peer_character.py` 已退役）

> 2026-09-19 裁决：通道层由全局单例改为 **per-user × slot 多租户**；2026-10-12 D16 补裁 **一人一 bot**（`MAX_CHANNELS_PER_USER = 1`，`channel_paths.py:13`），全局并发上限 `WECHAT_MAX_CHANNELS`（默认 100）。方案文档：`docs/plans/2026-09-19-每人独立微信通道方案.md`。

| 文件 | 职责 |
|------|------|
| `channel_paths.py` | 磁盘路径唯一真源 `data/wechat_sessions/<user_id>/slotN/`；`max_channels()`；`list_user_slots_with_credentials()`；**`scanner_ids_for_user()`（`:75`）** 枚举各通道扫码人 |
| `connector_registry.py` | `ConnectorRegistry`（`get_registry()` 单例）：`(owner_user_id, slot)` 键控；`ensure/disconnect/status_for_user/primary_status/start_login/restore_on_boot`；`ChannelQuotaError`/`ChannelSlotError`；每会话目录 `poll.lock` 去重多 worker 轮询 |
| `wechat_connector.py`（**2354 行**） | 连接器本体按 owner/slot 隔离：`load_session_state/qrcode`、`get_wechat_state`、`split_reply_for_wechat()`、**D16 扫码人守卫**（`ilink_user_id` 三路径装载 + 非扫码人拒绝 + 告警）、入站 `parse_ilink_items`（图片 item 2 / 语音 item 3）、图片/语音/emoji 发送、`token_window_state(owner)` 六态探针、`_drain_outbox` 出站宿主；对话内追问引擎已随 D14 退役 |
| `__init__.py` | 导出 |

**D16 跨域机制**：① activate 直连扫码人绑定（`character_routes` 激活时经 `scanner_ids_for_user()` 落 `wechat_bindings` + `upsert_binding` 缓存 + 活实例热切）；② 主动节奏按账号聚合（`proactive/llm_proactive.py:277 account_pacing_state()` 单一真源，同 owner 前缀取 hours=min / last_proactive=max）。
**双入口契约**：`sync_disk_sessions_to_db()`（async，lifespan 调用）与 `_sync_disk_sessions_to_db_sync()`（CLI）共同委托 `_sync_disk_sessions_async()` 内核，禁在运行中事件循环内 `asyncio.run()`。
**遗留迁移**：`scripts/migrate_legacy_wechat_channel.py` 一次性把旧全局凭证迁入 admin（user_id=1）`slot0`。旧全局端点 `/api/channels/wechat/*` 与 `/api/wechat/qrcode` 收敛为 admin 兼容面，普通用户一律走 `/api/wechat/channel*`（仅 JWT）。

### 4.8 voice/ — 语音合成（MiMo 唯一引擎）

> **MiMo-only 收敛（用户裁决 A，2026-08-28）**：Edge-TTS/SoVITS/CosyVoice/Bert-VITS2 四 provider 与 `voice_training.py` 已删；`/api/mimo/*` 为唯一语音 API；云故障由 Windows SAPI 本地合成兜底（`win-tts-fallback` 可选依赖）。

| 文件 | 说明 |
|------|------|
| `mimo_tts_provider.py` | 唯一引擎：MiMo Cloud API（8 情感映射内置）+ SAPI 本地兜底 |
| `tts_manager.py` | 单引擎管理（多引擎降级链已删） |
| `tts_provider_base.py` | Provider 抽象基类 |
| `audio_converter.py` | 音频格式转换（silk） |
| `clone_data_manager.py` | 聊天克隆数据管理（克隆域，与 TTS 无关） |
| `voice_catalog.py`（144 行） | 音色 catalog（`data/voice_catalog.json`） |
| `audio_result.py` | 合成结果封装 |

### 4.9 前端（React 19 管理控制台）

- **19 个页面文件**（`ls frontend/src/pages/*.tsx` 实测 19；全部挂载）：AdminProvidersPage、AdminUsersPage、CreateRole、IntroPage、LoginPage、NotFoundPage、PsychProfilePage、RoleSettings、RolesPage、SettingsAccount、SettingsLLM、SettingsLogs、SettingsSecurity、SettingsVoice、StatusCenter、SystemSettingsLayout、TemplatesPage、ToolsDashboard、WeChatPage；`StorylinePage` 为 App.tsx 内联包装（`/roles/:roleId/storyline`，不占页面文件）。
  - 分面：公开 IntroPage（`/intro`，SP-11 公开门面，根路径未登录重定向）/ LoginPage / PsychProfilePage（`/psych`，**需登录**）；角色 RolesPage / CreateRole / RoleSettings / **TemplatesPage**；设置 SettingsLLM / SettingsVoice / ToolsDashboard / SettingsSecurity / **SettingsAccount**（同意/导出/注销自服务）/ SettingsLogs；状态 StatusCenter / WeChatPage；管理 AdminUsersPage / AdminProvidersPage（admin）；NotFoundPage / SystemSettingsLayout。
- **15 个 API 模块**：admin, auth, characters, client, clone, emotion, llmProviders, mimo, normalize, queryClient, selfservice, system, templates, training, wechat（demo/chat/users 已删）。
- **R7 模式隔离 UI（2026-10-21）**：`components/admin/RoleSettingsTabs.tsx` 新增 `reply_mode` 作用域切换（global/user）+ 目标 user 选择；`api/system.ts` 增 `getMyReplyMode()` / `setMyReplyMode()`（走 `/api/user/reply-mode` 自助端点）。前端测试文件数不变（**33**）。
- **4 个 Zustand store**：authStore, errorStore, characterBuilderStore, **humanizeStore**（拟人化批新增）。
- **React Query hooks 5 文件**（`useAuth` / `useQueries` / `useInView` / `useMousePosition` / `index`）；Playwright E2E 配置在册；`frontend/src/constants/` 含 `cloneAgentGuide.ts`（CreateRole 任务书）、`agreement.ts`、`persona.ts`。
- **vitest 217 用例 / 33 文件**（vitest ^5；文件数 2026-10-21 实测，用例数引 AGENTS v1.55）。

**页面说明（沿用，逐条仍在位）**：ToolsDashboard = 工具健康/启停；StatusCenter = 系统状态（记忆三层管线）；SettingsVoice = 语音设置（ENGINE_OPTIONS 仅 mimo-tts）；WeChatPage 仅 LiveStatusBanner + QrCodeConnectionModal（移除 0 数据 StatsBar）；SettingsLLM 走 `/api/user/llm-config`（普通用户不再 403）；PsychProfilePage 消费 `/api/psych/*` 五端点；IntroPage 接替已删 Demo 的访客转化；CreateRole 克隆三步流（智能体代跑，任务书 `constants/cloneAgentGuide.ts`）；RoleSettings DataTab 含「网络增强」→ `/api/characters/{id}/enrich`。

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
| `memory` | MemoryTool | public | 长期记忆事实查询（**R3 起已列入默认 config**：`system.yaml` `builtin_tools` + ToolGate L0 晋级规则『你还记得…/我说过…吗』→ memory；检索经服务端注入 `_meta.session_key` 归属、结果按 untrusted 信封注入、主链只读不写） |
| `scheduler` ⚑ | SchedulerTool | public | 一次性日程安排（同上） |
| `web_summary` ⚑ | WebSummaryTool | public | URL 内容摘要（同上） |
| `image_gen` ⚑ | ImageGenTool | public | AI 图片生成（Agnes apihub 端点） |

**核心类**：`BaseTool`（name/description/permission_level/parameters_schema → `execute()` → ToolResult；`to_openai_fc_schema()`）、`ToolResult`（success + data/error；`to_dict()`/`to_fc_result()`）、`ToolRegistry`（`register/get/get_tools_by_permission(affinity)/health_check_all`）、`ToolDispatcher`（权限 → 限速 3 次/分/工具 → 执行 → 重试 → 埋点）。
**安全特性**：SSRF 防护（`url_guard`）、AST 计算替代 eval、public/friend/intimate/admin 四级门控、逐工具优雅降级。

### 4.11 其余模块包（骨架补全，2026-10-21 新增节）

| 包 | 文件数 | 内容与关键 owner |
|----|--------|------------------|
| `proactive/` | 12 | `scheduler.py`（**2533 行** @HEAD / **2580 行** @工作树）、`ase_engine.py`、`ase_hub.py`、`frequency.py`、`llm_proactive.py`（**1355 行**，决策线 + `account_pacing_state` @`:277`）、`proactive_context.py`（**164 行** @HEAD / **216 行** @工作树，人设/约束统一构造器）、`proactive_hooks.py`、`reflection.py`、`reminder_delivery.py`、**`runtime_plane.py`（804 行，跨进程运行时平面 `data/runtime_plane.db`）**、**`runtime_assembly.py`（140 行，提醒/主动两入口唯一装配 owner）**。**R7 新增：`scheduler` 对决策线 `recent_messages` 过 `scrub_history_rows`（`scrubbed_recent`，`scheduler.py:1572-1594` @工作树）+ `reminder_delivery` 补投递守卫** |
| `utils/` | 22 @HEAD / **23 @工作树** | 共享真源包：`humanize_delivery.py`（114 行，主动拆条+段间节奏，两投递点共用）、`session_key.py`（165 行，`owner:peer` 键）、`local_time.py`、`project_paths.py`、**`stream_guard.py`（597 行，R4 新增：`IncrementalReplyGuard` 出站增量守卫状态机 + `read_stream_mode()`/`true_streaming_enabled()`）**、`prompt_sanitize.py`（**985 行** @HEAD / **1138 行** @工作树；`terminal_reply_guard`@HEAD`:891`、`_extract_user_anchor_sentences`@`:284`、`_strip_system_markers`@`:700`、`_strip_meta_memory_talk`@`:799`、`_strip_attribution_flip`@`:819`、`_strip_ungrounded_reciprocal`@`:844`、`_strip_immersive_stage_directions`@`:870`；**R7 新增 `scrub_history_rows`@工作树`:908` / `_is_assistant_tail_head`@`:902`**）、`reply_mode.py`（**150 行** @HEAD / **268 行** @工作树；**R7 per-user 覆盖**：`read_reply_mode(user_key)` + `OVERRIDE_TABLE="user_reply_mode"`（`data/sqlite.db`）+ `clear_reply_mode_override()`）、`address_terms.py`（237 行）、`llm_bridge.py`、`json_state.py`、`async_utils.py`、`deletion_guard.py`（88 行）、`inbound_context.py`（40 行）、`fallback_lines.py`、`affinity_state.py`、`character_resolver.py`、`character_helpers.py`、`bootstrap.py`、`emotion_state.py`、`health_check.py`、`humanize_config.py`、`important_dates.py` |
| `context/` | 2 | `world_info_provider.py`（世界书/lorebook） |
| `multimodal/` | 3 | `image_attachment.py`（入站图片归一化，magic bytes，不落盘）、`multimodal_processor.py` |
| `clone_training/` | 4 | `data_cleaner` / `data_extractor` / `style_analyzer`（weclone_adapter 已删） |
| `observability/` | 8 | metrics / health / logging_setup / sentry / graceful_shutdown / config_manager / config_models |
| `plugins/` | 2 | `weather.py`（外部数据源插件位） |
| `scripts/` | 19 | 运维/迁移脚本（`rebuild_knowledge_index.py`、`migrate_legacy_wechat_channel.py`、`link_identities.py` 等，见 `ls scripts/*.py`） |
| `deploy/` | — | systemd unit + nginx 模板 + `remote_deploy.sh` + `backup_manager.py`（577 行）+ `restore_manager.py`（329 行） |
| `api/` 非路由 | **19 文件** + `api/state/` | 请求链：`run_api.py`（642 行）/ `app_factory.py`（517 行）/ `auth.py` / `auth_jwt.py` / `deps.py` / `runtime_config.py` / `session_manager.py` / `path_security.py` / `password_policy.py` / `byok.py` / `achievement_engine.py` / `main_routes.py`；数据与生命周期：`database.py`、**`lifecycle.py`（1178 行，账号/角色删除五阶段作业）**、**`consent.py`（227 行，同意状态机）**、`qrcode_store.py`、`health_routes.py`、`websocket_server.py`；`api/state/`：`safety_log.py`、`tool_history.py`、`training_state.py` |
| `api/routers/character_template_routes.py` | 278 行 | 角色模板面阶段 1：`GET /api/character-templates` + `POST /{id}/clone`（模板文件零改动） |
| `tests/golden/` | 12 文件 | **R6 黄金集（2026-10-21 立项）**：`cases/*.jsonl` **45 条用例**（8 分类：aiyu_contrast 6 / degradation_quality 4 / echo_attribution 6 / guard_regression 8 / meta_memory 4 / mode_boundary 7 / topic_awareness 5 / ungrounded_reciprocal 5）+ `runner.py`（**792 行**）+ `judges.py`（**154 行**）+ `test_golden_set.py`（**402 行**，pytest 入口，单文件收集 **69** 例 = 68 passed + 1 skipped live_llm）+ `README.md`（171 行）。**确定性优先、判定全部委托被测真源、LLM 判定不计基线** |

> **历史归档**：`tools/url_guard.py` / `tool_state.py` / `runtime_plane.py` / `runtime_assembly.py` / `inbound_context.py` / `lifecycle.py` / `deletion_guard.py` / `consent.py` / `character_template_routes.py` / `source_store.py` / `voice_catalog.py` / `backup_manager.py` / `restore_manager.py` 的落库提交号与归属窗口（W1–W12），详见 `LOG.md` 2026-09-27 条与 `docs/DECISION_LEDGER.md`。

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
| 语音 | MiMo Cloud TTS（唯一引擎）+ Windows SAPI 本地兜底 + FFmpeg（可选） |
| 缓存 | Redis（可选；不可用时 `test_cache.py:297` 相关 1 例跳过） |
| 可观测 | prometheus-client + OpenTelemetry + Sentry SDK |
| 安全 | pycryptodome + python-jose + passlib[bcrypt] |
| 调度 | APScheduler + schedule |
| 前端 | React 19（`^19.0.0`）+ Vite 8（`^8.3.2`）+ Zustand 5（`^5.0.0`）+ React Query + Playwright + vitest 5（`^5.0.3`） |
| 测试 | pytest + pytest-asyncio + pytest-cov + ruff + mypy |
| 工具系统 | httpx, requests, beautifulsoup4, cloudscraper, duckduckgo_search, chinese_calendar, lunarcalendar |

---

## 11. 风险与建议（按 2026-10-21 现状重写）

| 风险 | 严重度 | 位置 | 建议 / 现状 |
|------|--------|------|-------------|
| **降级质量悬崖** | **高（产品级）** | `llm_provider/multi_provider_gateway.py` + `config/llm_providers.json` | v1.55 已把 agnes 超时提到 40s（覆盖实测 P90）、熔断阈值 2 / 冷却 120s。**未解决**：供应商侧抖动仍在（实测 1.1~51.7s），长尾仍会走到降级位；端到端实证降级轮直接出「好的，注意保暖哦」。三个候选方向（降级切同档位模型 / 降级轮走保守短回复 / 降低降级轮 prompt 复杂度）**待裁决** |
| **话题仍是原话锚点，非对话状态机** | 中 | `utils/prompt_sanitize.py:284` @HEAD `_extract_user_anchor_sentences` | 24h 窗 + user 行短句已替换关键词袋，但无未完成事项跟踪、无话题栈，长对话指代消解仍弱 |
| **回复模式开关为全局单例** | 低（R7 已大幅缓解） | `utils/reply_mode.py` + `data/sqlite.db` `user_reply_mode` 表 | **R7（2026-10-21）已按 user 维度隔离**：全局值降为默认 + per-user 覆盖表 + `GET/POST /api/user/reply-mode`；剩余风险：旧全局 `reply_mode` 键仍在 `data/scheduler_config.json`（作默认值），未覆盖用户仍共享默认 |
| **prompt 与超时的反馈回路** | 中 | 编排器 prompt 组装 | 每批 prompt 修复都让 system 变长 → 主模型更慢 → 更易被判超时 → 更多流量落弱模型（v1.55 已定性的真根因）。**新增指令前先评估 token 成本**；R6 黄金集为量化该回路提供基线（待接入真实模型 A/B） |
| **黄金集未接入真实模型 A/B（质量量化缺基线）** | 中 | `tests/golden/`（R6 已立项，45 条用例） | AGENTS v1.55 / HANDOFF 遗留⑤「黄金集未建」**已由 R6 立项部分填补**——现为确定性用例集（判定委托被测真源、LLM 判定不计基线）；**未解决**：缺真实模型 A/B 量化以证明改善幅度，位置 `docs/verification/` 批次 F 规划 |
| ConfigLoader.get 高 fan-in | 中 | `my_character/character_config.py:53` | 单点故障面大，需缓存与降级 |
| `run_console_chat` 复杂度 | 低 | 已迁出至 `orchestrator/console_chat.py`（134 行） | 已在 `orchestrator/` 包内，主路径不受阻；可继续函数化拆分 |
| 调度器单例 flock 仅 POSIX 生效 | 低 | `api/run_api.py:245-250`（`_ensure_scheduler_singleton` @ `:222`） | Windows 开发环境无锁（单 worker）；锁文件 `/tmp/ai-girlfriend-scheduler.lock` **禁止手删**（`PrivateTmp=no`，删后新 worker 会抢到新 inode → 调度器翻倍） |
| 多用户 LLM 缓存失效边界 | 低 | `llm_provider/__init__.py:332` @HEAD（`:336` @工作树）`invalidate_user_llm` | 跨 worker 缓存一致性待验证 |
| 基线口径随工作树漂移 | 低 | `AGENTS.md §4.3` / 本文件 §1.1 | 引用基线必须同时声明**卡数**与**在制品**；本批实测 HEAD 3627 收集 vs 工作树 3900 即为此风险实例 |
| 后处理并发写同一 SQLite | 低 | `orchestrator/optimized_orchestrator.py:1386 _after_process` / `:1444 _safe_after_chat` / `:1452` 提交后台线程池（@HEAD） | `after_chat` 重活（向量/事实/日记）经 `_get_background_executor()` 串行线程池执行，与记忆 SQLite 写竞争；已有单线程池约束，长会话下仍需观察写锁等待 |

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

> **历史归档**：版本表（v3.8.0 → v3.8.32 共 40+ 行，含 2026-07 至 2026-10 各批端点/测试/部署读数）已由 `LOG.md` 权威承载，本文件不再复制。当前版本 = **v3.8.34（2026-10-21 八项裁决批）**；上一版 v3.8.33（2026-10-21 重建批）及 v3.8.32（2026-10-17 窗口死寂批）、10-19 时间感知 3.0、10-20 聊天质量两批，见 `LOG.md` 对应日期条目。

---

## 14. 架构债清理决策 + 双 API 分工

| 模块 | 评估结论 | 依据 |
|------|---------|------|
| `shisi/memory/legacy/` | **保留，不重命名** | 被 `shisi/application/memory_service.py` 与 `tests/test_memory*.py` 大量引用；`legacy` 仅表"历史迁移"非"待删除" |
| `shisi/knowledge/legacy/` | ~~保留~~ → **已整删（批6b 项2）** | RAGEngineV2 生产零读者，整包连同其测试删除；现役 `Retriever`/`CharacterKnowledgeService` 不受影响 |
| `character_card/` | ~~保留~~ → **已整删（批6b 项10）** | `CharacterCardAdapter` 全仓零读者，整包 6 文件删除 |
| `shisi/api/` | **保留** | 由 `api/app_factory.py:295` `from shisi.api.registry import setup_shisi` 装配；与 `api/routers/` 形成双 API 层分工 |
| `requirements.txt` | **已删除** | `pyproject.toml` 为唯一权威依赖源 |

### 双 API 路由层分工

| 层 | 路径 | 职责 | 调用方 |
|---|---|---|---|
| 域路由层 | `api/routers/*.py`（**24 模块**，不含 `__init__.py`） | 控制 plane 端点：character/auth/admin/voice/wechat/clone/… | `api/app_factory.py` 主挂载（19 处字面 `include_router` + 9 处 `_mount_required_router` 调用） |
| shisi 域层 | `shisi/api/*.py`（11 文件） | DDD 核心 plane 端点：character/affinity/emotion_stage/persona/stats/vital_signs/sticker/memory | `setup_shisi` 由 app_factory 调用（`api/app_factory.py:295` import / `:300` 装配） |

两层不冲突：域路由层面向"控制/管理"，shisi 域层面向"DDD 核心域"，统一经 `create_api_app()` 装配。

### 关键架构决策（现状核对）

| 决策 | 位置 | 现状 |
|------|------|------|
| 多用户 LLM 隔离走 `User.llm_config` JSON 字段 | `api/database.py:85` / `llm_provider/__init__.py:332` @HEAD（`:336` @工作树） | 生效；普通用户可读写自己的配置，admin 走 `/api/config` |
| 微信连接自动恢复用文件锁 | `api/run_api.py:500 _autostart_wechat_connector()` | 已改为**每会话目录 `poll.lock`**（`channel_paths`），旧全局 flock 方式废止 |
| PNG 角色卡走 `shisi/character/png_codec.py` | `shisi/character/png_codec.py` | 生效（SillyTavern 生态标准，归 DDD character 子域） |
| 克隆朋友架构 = 本地提取 → 上传 → 服务器分析 | `api/routers/clone_routes.py` | 生效（wechat-decrypt 依赖 Windows 进程，不能跑 Linux） |
| 多 worker 调度器单例走 flock | `api/run_api.py:222 _ensure_scheduler_singleton()`（flock @ `:245-250`） | 生效；锁文件 `/tmp/ai-girlfriend-scheduler.lock`，Windows 分支无锁（单 worker） |
| `--no-scheduler` 经 `DISABLE_SCHEDULER=1` 传子进程 | `main.py`（`_run_orchestrator` @ `:469`） | 生效；`run_api` 启动检查该变量等价 `--no-scheduler` |
| **结构真源迁移 AOCI**（2026-10-20 起） | `aoci.txt` / `aoci.code.txt` / `aoci.meta.txt` | **索引已写入并补全**：AOCI 提交时 `aoci.code.txt` **753 行**（@HEAD），工作树 **1116 行**（八项裁决批补全）；三件均被 git 跟踪。本文件降级为人读快照与规模读数 |

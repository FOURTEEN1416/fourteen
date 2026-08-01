# 代码图谱 — unique-you (唯一的你) v3.1.0

> 由 维护者 手动维护 | 上次大规模扫描: 2026-07-09 | 最后更新: 2026-08-01
> ✅ 路由/文件/模块/测试数已通过 Grep + LS + pytest + vitest 实时核实（2026-07-30）。
> ⚠️ codebase-memory 图谱工具 节点/边数据仍为 2026-07-09 快照（未重新索引）。

---

## 1. 全局指标

### 1.1 实时核实指标（2026-07-30 Grep/LS/pytest/vitest 扫描）

| 维度 | 数值 | 核实方法 |
|------|------|---------|
| API 端点（create_api_app 实扫） | **204 端点** / 17 include_router | `python -c "from api.app_factory import create_api_app; app=create_api_app(); sum(len(r.methods-{'HEAD','OPTIONS'}) for r in app.routes if hasattr(r,'methods'))"` |
| main.py 体量 | **726 行 / 35.9 KB** | `(Get-Content \| Measure-Object -Line).Lines` |
| 前端页面 | 19 个 | Glob `frontend/src/pages/*.tsx`（新增 `AdminProvidersPage.tsx`） |
| 前端 API 模块 | **14 个** | Glob `frontend/src/api/*.ts`（新增 `llmProviders.ts`） |
| 前端 Zustand store | 4 个 | LS `frontend/src/store/` |
| Python 测试用例 | **1025 个全部通过 / 36 文件 / 124.44s** | `python -m pytest --tb=short -q`（2026-07-30 实跑,19 warnings,0 failed） |
| 前端测试用例 | **79 个全部通过 / 14 文件** | `npx vitest run`（2026-07-30 实跑） |
| 测试用例合计 | **1104 个**(1025 Python + 79 前端) | pytest + vitest 实跑 |
| tools/builtin 工具文件 | 8 个（含 __init__.py） | Glob |

### 1.2 知识图谱快照指标（2026-08-01 重新索引）

| 维度 | 数值 |
|------|------|
| 总节点 | 6771 |
| 总边 | 26079 |
| Method | — |
| Function | — |
| Class | — |
| File | — |
| Module | — |
| Route（图数据库记录） | — |
| Interface (TS) | — |
| 测试用例边 (TESTS) | 1449 |
| 相似函数对 (SIMILAR_TO) | 122 |
| 语义关联 (SEMANTICALLY_RELATED) | 86 |
| HTTP 跨服务调用 | — |
| 协同变更文件对 (FILE_CHANGES_WITH) | — |
| 继承关系 (INHERITS) | — |

> **注**: 2026-08-01 使用 codebase-memory 图谱工具 0.9.0 重新索引。详细节点/边类型分布需通过 `get_graph_schema` 工具查询。
> 旧版 0.8.x 的详细类型分布（Method/Function/Class 等）在新版中需通过 `query_graph` 获取。

**边类型分布（前 8，2026-07-09 快照）**：USAGE(6331) > CALLS(6116) > DEFINES(5110) > DEFINES_METHOD(1885) > WRITES(1549) > TESTS(1413) > IMPORTS(755) > DECORATES(621)

**语言分布**：Python 309 · TypeScript 79 · YAML 14 · Bash 5 · TOML 1 · JS 1 · HTML 1 · CSS 1

**2026-07-28 后新增/重构的包**：
- `shisi/character/`（8 文件，新增完整角色卡子系统：character_card_v2 / exporter / importer / manager / models / png_codec / store / validator）
- `api/routers/clone_routes.py`（+`/api/clone/upload` 端点）
- `api/routers/knowledge_routes.py`（+`/api/characters/{id}/enrich` 端点）
- `api/routers/misc_routes.py`（+`/api/user/llm-config` GET/POST 端点）
- `api/run_api.py`（+flock 文件锁自动恢复微信连接）
- `api/database.py` User 模型（+`llm_config` JSON 字段）
- `llm_provider/__init__.py`（+`invalidate_user_llm()` 用户级 gateway 缓存失效）
- `frontend/src/pages/AdminProvidersPage.tsx`（+LLM 供应商管理页面，admin 角色）
- `frontend/src/api/llmProviders.ts`（+LLM 供应商管理 API 客户端）
- `api/routers/llm_providers_routes.py`（+LLM 供应商 CRUD 端点）

---

## 2. 架构分层

知识图谱基于 fan-in/fan-out 自动识别出 4 个架构层：

```mermaid
graph TD
    subgraph ENTRY["入口层 entry"]
        OA["OptimizedOrchestrator (orchestrator/)"]
    end
    subgraph API["接口层 api"]
        AR["api/ 168+ 路由"]
        SA["shisi/api/ v1+v2"]
    end
    subgraph CORE["核心层 core (高 fan-in)"]
        APP["application (13 in)"]
        CS["content_safety (8 in)"]
        FUS["fusion (6 in)"]
        PI["prompt_injection (5 in)"]
        PTM["prompt_template_manager (23 in)"]
        SCHED["scheduler (4 in)"]
        WS["websocket_server (5 in)"]
    end
    subgraph INTERNAL["内部层 internal"]
        MAIN["main (4 in/14 out)"]
    end
    OA -->|"14 calls"| PTM
    OA -->|"13 calls"| APP
    OA -->|"8 calls"| CS
    OA -->|"6 calls"| FUS
    OA -->|"5 calls"| PI
    OA -->|"4 calls"| SCHED
    MAIN -->|"9 calls"| PTM
    MAIN -->|"5 calls"| WS
    AR --> SA
```

**分层逻辑**：
- **entry** — 只有出站调用，是系统总指挥
- **core** — 高 fan-in（被很多模块调用），是基础设施和服务
- **api** — 定义 HTTP 路由
- **internal** — 工具/部署脚本，fan-in=0 或很低

---

## 3. 核心数据流 — process_message 热路径

`OptimizedOrchestrator.process_message`（`orchestrator/optimized_orchestrator.py`）处理每一条用户消息，是全系统最关键调用链。

> **架构变更 (2026-07-26)**: `OptimizedOrchestrator` 现在继承 `_InitPhasesMixin` + `_StreamPipelineMixin`：
> - `orchestrator/optimized_orchestrator.py` (920行): 主类 `__init__` / 会话锁 / `_prepare_context` / `process_message` / `health_check`
> - `orchestrator/_init_mixin.py` (438行): `initialize` 拆分为 9 个 `_init_*` 阶段
> - `orchestrator/_stream_mixin.py` (238行): `process_message_stream` SSE 真流式/伪流式降级
>
> **最新更新 (2026-07-01)**: PersonaService.build_system_prompt 已重构为**两阶段构造**，第一阶段由 shisi PromptBuilder 生成角色 + RAG 知识 + 情感 + 对话历史，第二阶段注入 PersonaEngine 的 5 层对齐层（世界/时间信息 → RAG 上下文 → 情感 → 风格 → 约束）。

```mermaid
sequenceDiagram
    participant U as 用户消息
    participant O as OptimizedOrchestrator
    participant S as Security 安全线
    participant M as Memory 记忆
    participant P as Persona 人格
    participant L as LLM Gateway
    participant T as Tools 工具系统
    participant A as After 后处理

    U->>O: process_message(text, session_id)
    O->>O: _get_session_lock(session_id)
    O->>O: _detect_voice_request(text)
    O->>O: _load_character_persona_segment()

    rect rgb(255, 230, 230)
        O->>S: PIIAnonymizer.anonymize(text)
        O->>S: PromptInjectionDetector.detect(text)
        O->>S: ContentSafetyFilter.check_input(text)
    end

    rect rgb(230, 255, 230)
        O->>M: MemoryPipeline.get_chat_context()
        O->>M: WorkingMemory.get_recent()
        O->>M: EpisodicMemory.search()
    end

    rect rgb(230, 230, 255)
        O->>P: PersonaExtractor.process_message()
        O->>P: PersonaService.build_system_prompt()
        Note over P: Phase 1: shisi PromptBuilder (角色+RAG+情感+历史)
        Note over P: Phase 2: 5 层对齐注入 (世界/情感/风格/约束)
        O->>P: AffinityMapper.sync()
        O->>P: AffinityEnhancer.update()
        O->>P: EmotionStageEngine.evaluate()
    end

    rect rgb(240, 240, 200)
        O->>T: ToolRegistry.get_tools_by_permission()
        O->>T: LLM 决策 → ToolDispatcher.dispatch()
        O->>T: 工具结果注入 prompt 上下文
    end

    O->>L: PromptTemplateMgr.get() then render()
    O->>L: LLMGatewayV2.chat() with fallback

    rect rgb(255, 230, 230)
        O->>S: ContentSafetyFilter.check_output(reply)
        O->>S: DynamicAnchorSystem.check_consistency()
        O->>S: ConsistencyChecker.check_and_correct_reply()
        O->>S: CounterRebuttal.check_and_increment()
    end

    rect rgb(255, 245, 230)
        O->>A: MemoryPipeline.after_chat()
        O->>A: ASEEngine.on_chat()
        O->>A: ToneMimic.update_style_profile()
        O->>A: PersonaBank.update_persona_with_snapshot()
        O->>A: metrics.record_chat_duration/tokens
    end

    O-->>U: reply (+ optional voice)
```

**新增（2026-07-01）**：PersonaService 调用链上方新增 Tools 工具系统中间层：LLM 通过函数调用感知工具 → 按 affinity 权限过滤 → 执行后注入上下文作为 prompt 增强。

---

## 4. 模块目录与职责

### 4.1 入口与编排

| 模块 | 文件 | 职责 |
|------|------|------|
| `main.py` | main.py（**~23 KB / 574 行**，2026-07-28 双模式合并后瘦身） | 入口 + `_run_orchestrator` 统一启动 + 控制台/微信/克隆模式 |
| `orchestrator/` | orchestrator/ (5 文件包) | `optimized_orchestrator.py` 主类 + `_init_mixin.py` **10 阶段初始化**（唯一真相源） + `_stream_mixin.py` SSE 流式 + `session_locks.py` + `voice_detector.py` |
| `api/run_api.py` | api/run_api.py | API-Only 启动入口（uvicorn 直接挂载），含 `_autostart_wechat_connector()` flock 文件锁自动恢复微信连接 |
| `user_scheduler.py` | user_scheduler.py | 多用户调度，每个微信用户独立情感状态 |

**架构演进 (2026-07-28 双模式合并)**：
- 原 `_run_fast_mode` + `_run_full_mode` 双路径合并为 `_run_orchestrator` 单入口（-297 行）
- `_init_mixin.initialize()` 是唯一初始化真相源（10 阶段），`_run_full_mode` 的 300 行手工组件注入已删除
- 修复双调度器 bug：原 `_init_mixin` 与 `_run_*_mode` 各创建一个 `ProactiveScheduler` 并行运行，现统一复用
- `_init_mixin` 新增第 10 阶段 `_init_multimodal`，并补齐 `EncryptionManager` / `classifier_mode` / `prompt_mode` 参数

**OptimizedOrchestrator 运行模式**：
- `_run_orchestrator(mode="fast")` — 快速模式（fusion_cfg.orchestrator_mode="fast"）
- `_run_orchestrator(mode="full")` — 完整模式（默认，与 fast 共用 initialize()）
- `run_console_chat` — 控制台交互
- `run_wechat_mode` — 微信模式
- `run_clone_pipeline` — 克隆训练管线

### 4.2 API 层（204 路由 — 2026-07-30 create_api_app 实扫)

两个路由来源：

| 来源 | 路径 | 端点数 | 文件数 | 说明 |
|------|------|--------|------|------|
| `api/routers/` | 21 个域路由（不含 `__init__.py`） | ~155 | 21 | 域路由：character/auth/admin/invite/voice/mimo/storyline/wechat/emotion/memory/knowledge/persona_card/demo/chat/clone/misc/personality/safety/tools/training/users |
| `shisi/api/` | v1 + v2 | ~49 | 13 | shisi 域：affinity/character/emotion_stage/memory/persona/stats/sticker/training/vital_signs + v2 健康检查/迁移/persona/character |
| **合计（实扫）** | | **204** | **34** | `app.routes` 实测(2026-07-30) |

**app_factory.py 实际挂载策略**（核实于源码）：

```
health_router          → /api/health, /api/ready（2 端点，无认证）
misc_router            → /api/stats, /api/dashboard, /api/memory/facts,
                        /api/logs, /api/logs/stream, /api/config,
                        /api/user/llm-config (GET/POST, 新增),
                        /api/channels, /api/routes（11 端点）
chat_router            → /api/chat/*, /api/session/*, /api/wechat/status（11 端点）
demo_router            → /api/demo/*（4 端点，无认证）
personality_router     → /api/emotion/*, /api/persona/*, /api/psych/*（9 端点）
users_router           → /api/users/*（7 端点，admin only）
training_router        → /api/training/*, /api/proactive/*（11 端点）
tools_router           → /api/system/tools, /api/system/tools/health, /api/plugins/*（6 端点）
safety_router          → /api/safety/*, /api/rag/*, /api/voice/*, /api/files/*, /api/cache/*（12 端点）
clone_router           → /api/clone/*（9 端点，含 /api/clone/upload 新增）
auth_router            → /api/auth/*（7 端点）
admin_router           → /api/admin/*（5 端点）
invite_router          → /api/auth/register-invite, /api/admin/invites（4 端点）
character_router       → /api/characters/*, /api/presets/*（19 端点，含 .png 导入/导出）
voice_router           → /api/character/voice/*（6 端点）
mimo_voice_router      → /api/mimo/*（6 端点）
memory_bridge_router   → /api/memory/*（4 端点，桥接 shisi FavoriteManager/ForwardManager）
persona_card_router    → /api/persona-card/*（3 端点）
storyline_router       → /api/storyline/*（6 端点）
knowledge_router       → /api/characters/{id}/knowledge/*, /api/characters/{id}/enrich（8 端点，含 enrich 新增）
wechat_router          → /api/wechat/*（8 端点）
emotion_params_router  → /api/emotion/params/*（2 端点）
qrcode_router          → /api/wechat/qrcode（1 端点）
+ shisi setup          → /api/shisi/* + /api/shisi/status（49 端点）
```

**`api/app_factory.py:84 create_api_app()`** 是 FastAPI 应用唯一构造入口，被 `api/run_api.py:232` 和 `main.py` 调用。FastAPI 实例 `version="3.1.0"`。

### 4.3 shisi/ — Clean Architecture 重构（核心域）

DDD 分层架构，是项目最重要的重构成果：

| 子包 | 职责 | 关键类 |
|------|------|--------|
| `application/` | 应用服务 | CharacterService, MemoryService, PersonaService, KnowledgeService, PromptService, MigrationService |
| `character/`（新增 2026-07-28） | 角色卡完整子系统 | **CharaCardV2/V3**, **PNGCodec**, Importer, Exporter, Manager, Store, Validator |
| `core/models/` | 领域模型 | AffinityLevel, CharacterId, EmotionalState, PersonaProfile, CharacterAggregate |
| `core/ports/` | 端口接口 | CharacterRepository |
| `core/services/` | 领域服务 | EmotionDetector, PromptBuilder |
| `infrastructure/persistence/` | 持久化 | SQLiteRepository, Schema |
| `infrastructure/migration/` | 迁移 | MigrationRunner, RollbackRunner |
| `memory/legacy/` | 记忆管线 | WorkingMemory(49 fan-in), EpisodicMemory, SemanticMemory, StructuredMemory, VectorMemory, MemoryPipeline, FactExtractor, ImportanceScorer, ReflectionEngine, ConversationSummarizer, DiarySummarizer |
| `affinity/` | 亲密度 | AffinityEnhancer(46 fan-in), AffinityMapper, DecayEngine, UnlockManager |
| `storyline/` | 剧情线 | Engine, Detector, Config |
| `emotion_stage/` | 情感阶段 | StageEngine, EventDispatcher, StageConfig |
| `vital_signs/` | 生命体征 | VitalEngine, EmotionMapping |
| `sticker/` | 表情包 | StickerManager, EmotionRecommender, SafetyCheck, Importer |
| `voice/` | 语音 | CharacterVoice, EmotionTTS |
| `wechat/` | 微信集成 | CommandHandler, CommandParser, ProactiveMessenger, StickerAdapter |
| `vault/` | 数据收集 | CollectLoop, PersonaAdapter |
| `ase/` | 场景叙事 | SceneNarrator, TriggerEngine |
| `knowledge/` | 知识检索 | Retriever, CharacterKnowledgeService, **CrawlerAdapter** |

**shisi/character/ 详细说明（2026-07-28 新增）**：

| 文件 | 类/函数 | 职责 |
|------|---------|------|
| `png_codec.py` | `extract_card_from_png()` / `embed_card_to_png()` / `has_chara_chunk()` / `is_png()` | SillyTavern PNG tEXt chunk 编解码：PNG → base64 → JSON 解析；JSON → base64 → 嵌入 PNG tEXt chunk（关键字 `chara`）。依赖 Pillow |
| `importer.py` | `import_file()` / `import_directory()` | 角色卡批量导入，支持 .json 与 .png |
| `exporter.py` | `export_card_png()` / `export_card_png_to_file()` | 角色卡导出为 PNG（含 chara tEXt chunk） |
| `character_card_v2.py` | `CharaCardV2` / `CharaCardV2Parser` | SillyTavern V2/V3 角色卡 schema 解析 |
| `manager.py` | `CharacterManager` | 角色卡 CRUD（被 `shisi/api/registry.py:setup_shisi` 装配） |
| `store.py` | 持久化 | 角色卡 JSON 文件存储 |
| `validator.py` | 校验 | 角色卡 schema 合法性 |
| `models.py` | Pydantic 模型 | 角色卡数据模型 |

PNG tEXt chunk 集成路径：`api/routers/character_routes.py:520` 调用 `extract_card_from_png()`，`api/routers/character_routes.py:593` 调用 `embed_card_to_png()`。前端 `frontend/src/api/characters.ts` 调用 `POST /api/characters/import` 与 `GET /api/characters/{id}/export?format=png`。

**shisi/knowledge/ 关键更新 (2026-07-01)**：

| 新增/变更 | 说明 |
|-----------|------|
| `CrawlerAdapter` | 双阶段索引管线：_index_card（角色卡结构化分块）→ crawl_and_index（Web 爬取 + BM25 索引） |
| `CharacterKnowledgeService.ensure_index()` | 新增：检查记忆 → 尝试磁盘加载 → 从角色卡构建 → 自动保存，一句调用"让它就绪" |
| `CharacterKnowledgeService.add_knowledge_chunks()` | 新增：增量追加知识块到现有 BM25 索引，爬虫使用 |
| 名称索引 | 修复：`CharacterKnowledgeService.index_character()` 现在提取 name chunk（解决"她叫什么名字"查询） |
| BM25 持久化 | `save_index()` / `load_index()` 到 `data/knowledge/{character_id}.json` |
| `PersonaService.build_system_prompt()` | 重构为两阶段：Phase 1 shisi PromptBuilder 构建角色基础 prompt → Phase 2 注入 PersonaEngine 的 5 层对齐（世界/时间/RAG/情感/风格/约束） |
| `_build_character()` | 双路径解析：角色卡优先（character_id）→ PersonaEngine 回退 |
| `normalize_character_card()` | 新增 `utils/character_helpers.py`，统一展平 SillyTavern 角色卡格式 |

### 4.4 my_character/ — 角色引擎（21 模块）

| 模块 | 职责 | 备注 |
|------|------|------|
| `character_config.py` | ConfigLoader | **423 fan-in，全项目最高** |
| `emotion_engine.py` | 情感引擎核心 | — |
| `emotion_memory.py` | 情感记忆 | — |
| `emotion_style_coupler.py` | 情感-风格耦合 | — |
| `enhanced_prompt_engine.py` | TimeContext | **103 fan-in** |
| `persona_engine.py` | 人格引擎 | — |
| `persona_evaluator.py` | 人格评估 | — |
| `style_enhancer.py` / `style_enhancer_v2.py` | 风格增强 | v1/v2 共存 |
| `tone_mimic.py` | 语气模仿 | — |
| `consistency_checker.py` | 回复一致性检查 | 热路径节点 |
| `counter_rebuttal.py` | 反驳计数器 | 热路径节点 |
| `dynamic_anchor.py` | 动态锚点系统 | 热路径节点 |
| `anchor_protection.py` | 锚点保护 | — |
| `constraint_validator.py` | 约束验证 | — |
| `contextual_behavior.py` | 上下文行为 | — |

### 4.5 persona_extractor/ — 人格提取器（12 模块）

| 模块 | 职责 |
|------|------|
| `fusion.py` | PersonaExtractor 融合入口（6 fan-in） |
| `style_vectorizer.py` | 风格向量化 |
| `mental_health.py` | 心理健康分析 |
| `dark_triad.py` | 暗黑三人格检测 |
| `hexaco.py` | HEXACO 人格模型 |
| `liwc_analyzer.py` | LIWC 语言分析 |
| `pado_detector.py` | PADO 检测 |
| `cognitive_distortions.py` | 认知偏差检测 |
| `emotion_coupler.py` | 情感耦合器（chameleon filter） |
| `persona_bank.py` | 用户人格库 |

### 4.6 security/ — 安全模块

| 模块 | 类 | fan-in | 检查时机 |
|------|-----|--------|---------|
| `content_safety.py` | ContentSafetyFilter | 8 | 输入 + 输出 |
| `prompt_injection.py` | PromptInjectionDetector | 5 | 输入 |
| `pii_anonymizer.py` | PIIAnonymizer | — | 输入 |
| `encryption.py` | 加密模块 | — | — |

安全检查在 process_message 中执行两次：输入检查（3 层）+ 输出检查（4 层）。

**安全日志更新 (2026-07-01)**：`content_safety.py` 新增 `_log_safety_event()` 函数，在每次规则命中/LLM 分类命中后记录安全事件（category、direction、text_length、text_hash、时间戳，按用户隔离）。日志失败从不阻塞安全执行。

### 4.7 llm_provider/ — LLM 网关（5+ 供应商）

| 模块 | 职责 |
|------|------|
| `llm_gateway.py` | LLMGatewayV2，自动 fallback 链 |
| `multi_provider_gateway.py` | 多供应商网关（自动 fallback: 商汤日日新 → 智谱AI → 讯飞星火 → 百度千帆）+ 用户级 gateway 缓存 |
| `openai_compatible_provider.py` | OpenAI 兼容供应商（被 zhipu/xunfei/baidu/sensenova 共用） |
| `prompt_template_manager.py` | PromptTemplateMgr（**54 fan-in**） |
| `__init__.py` | **`invalidate_user_llm(user_id)`**（新增 2026-07-28）— 清除用户级 gateway 缓存，下次对话按新配置重建 |

**新增供应商 (2026-07-01)**：

| 供应商 | 注册方式 | 模型 | 认证方式 |
|--------|---------|------|---------|
| **sensenova** | `get_llm(provider="sensenova")` | glm-5.2 / deepseek-v4-flash / sensenova-6.7-flash-lite | Bearer Token |
| 智谱AI (zhipu) | `get_llm(provider="zhipu")` | glm-4-flash | Bearer Token |
| 讯飞星火 (xunfei) | `get_llm(provider="xunfei")` | spark-lite | Bearer Token |
| 百度千帆 (baidu) | `get_llm(provider="baidu")` | ernie-speed-128k | OAuth (API Key + Secret) |
| DeepSeek | `get_llm(provider="deepseek")` | deepseek-chat / deepseek-reasoner | LLMGatewayV2 |

**多用户 API Key 隔离（2026-07-28 新增）**：

| 组件 | 位置 | 职责 |
|------|------|------|
| `User.llm_config` JSON 字段 | `api/database.py:71` | 每用户独立 LLM 配置（provider/api_key/model 等） |
| `/api/user/llm-config` GET | `api/routers/misc_routes.py:288` | 普通用户读取自己的 LLM 配置（脱敏 api_key 为 `****`），未配置时回退到全局 admin 配置 |
| `/api/user/llm-config` POST | `api/routers/misc_routes.py:314` | 普通用户保存自己的 LLM 配置（不再 403），自动调用 `invalidate_user_llm()` 清缓存 |
| `invalidate_user_llm(user_id)` | `llm_provider/__init__.py:259` | 失效用户级 gateway 缓存，下次对话按新配置重建 LLM 实例 |

**调用链**：前端 `SettingsLLM.tsx` → `frontend/src/api/system.ts` → `POST /api/user/llm-config` → 写入 `users.llm_config` → `invalidate_user_llm(user_id)` → 下次 `OptimizedOrchestrator.process_message` 时按 `user_id` 取用户专属 LLM。

### 4.8 voice/ — 语音合成（5 Provider）

| Provider | 文件 | 说明 |
|----------|------|------|
| MiMo Cloud | `mimo_tts_provider.py` | 默认引擎 |
| Edge-TTS | `edge_tts_provider.py` | 免费 |
| SoVITS | `sovits_provider.py` | 本地模型 |
| Bert-VITS2 | `bert_vits2_provider.py` | 本地模型 |
| CosyVoice | `cosyvoice_provider.py` | 本地模型 |
| TTS Manager | `tts_manager.py` | 统一管理 |
| Voice Training | `voice_training.py` | 语音训练 |
| Audio Converter | `audio_converter.py` | 音频格式转换 |

### 4.9 前端（React 19 管理控制台）

- **19 个页面**：
  - 用户/认证：LoginPage, UsersPage, AdminUsersPage, UserWorkspace
  - 角色管理：RolesPage, CreateRole, RoleSettings
  - 设置：SettingsLLM, SettingsSecurity, SettingsLogs, **SettingsVoice**
  - 工具/状态：**ToolsDashboard**, **StatusCenter**
  - 微信集成：WeChatPage, BindingDetailPage
  - LLM 供应商管理：**AdminProvidersPage**（admin 角色）
  - 其他：DemoPage, NotFoundPage, SystemSettingsLayout
- **14 个 API 模块**（新增 `queryClient.ts` + `llmProviders.ts`）：
  - admin, auth, characters, chat, client, clone, demo, **llmProviders**, mimo, **queryClient**, system, training, users, wechat
- 4 个 Zustand store（authStore, chatStore, errorStore, characterBuilderStore）
- React Query hooks
- 79 个 Vitest 测试用例（across 14 files,全部通过 2026-07-30）— SettingsLLM/SettingsVoice/StatusCenter/ToolsDashboard/AdminUsersPage/WeChatPage 等
- Playwright E2E 测试配置

**页面说明**：

| 页面 | 文件 | 功能 |
|------|------|------|
| **ToolsDashboard** | `ToolsDashboard.tsx` | 内置工具仪表盘：展示所有已注册工具的实时健康状态（绿点/红点）、启停控制（toggle）、描述提示。通过 `/api/system/tools` 和 `/api/system/tools/health` 获取数据。 |
| **StatusCenter** | `StatusCenter.tsx` | 系统状态中心 |
| **SettingsVoice** | `SettingsVoice.tsx` | 语音设置页 |
| **WeChatPage**（2026-07-28 简化） | `WeChatPage.tsx` | 移除冗余 StatsBar 与绑定列表表格，仅保留 LiveStatusBanner + QrCodeConnectionModal，避免数据为 0 的误导 |
| **SettingsLLM**（2026-07-28 修复 403） | `SettingsLLM.tsx` | 改用 `/api/user/llm-config`（用户级配置端点）替代 `/api/config`，普通用户不再 403 |
| **RoleSettings** | `RoleSettings.tsx` | DataTab 新增"网络增强"按钮，调用 `/api/characters/{id}/enrich`；`RoleSettingsConstants.tsx` 中 `ENGINE_OPTIONS` 简化为仅保留 `mimo-tts` |

### 4.10 tools/ — 工具系统（新增 2026-07-01）

插件式工具系统，为 AI 伴侣提供可调用的功能，以 OpenAI Function Calling schema 暴露给 LLM：

```
tools/
├── __init__.py          # 导出 BaseTool, ToolResult, ToolRegistry, ToolDispatcher
├── base_tool.py         # 核心框架（4 个类）
└── builtin/             # 具体工具实现（12 个工具）
    ├── __init__.py      # 重新导出全部内置工具
    ├── calendar_tool.py          # CalendarTool + CalculatorTool
    ├── character_crawler_tool.py # CharacterCrawlerTool (角色资料爬虫)
    ├── extra_tools.py            # MemoryTool, WebSummaryTool, ImageGenTool (新增), SchedulerTool
    ├── reminder_tool.py          # ReminderTool + CalendarQueryTool
    ├── search_tool.py            # SearchTool (DuckDuckGo + Bing 回退)
    ├── time_awareness_tool.py    # TimeAwarenessTool (农历/节假日)
    └── weather_tool.py           # WeatherTool (插件 → wttr.in 回退)
```

**12 个已注册工具**：

| 工具名 | 类 | 权限 | 说明 |
|--------|-----|------|------|
| `weather` | WeatherTool | public | 天气查询（插件优先 → wttr.in 回退） |
| `search` | SearchTool | public | 联网搜索（DDGS → Bing HTML 回退） |
| `calendar` | CalendarTool | public | 当前日期时间 |
| `calculator` | CalculatorTool | public | 安全表达式计算（AST 解析） |
| `set_reminder` | ReminderTool | friend | 设置提醒 |
| `query_reminders` | CalendarQueryTool | friend | 查询待处理提醒 |
| `memory` | MemoryTool | public | 长期记忆事实查询 |
| `scheduler` | SchedulerTool | friend | 一次性日程安排 |
| `time_awareness` | TimeAwarenessTool | public | 农历/节假日/工作日查询 |
| `character_card` | CharacterCrawlerTool | friend | Web 爬取角色资料（baike → wiki → baidu 回退） |
| `web_summary` | WebSummaryTool | public | URL 内容摘要 |
| `image_gen` | ImageGenTool | public | **AI 图片生成**（Agnes-AI apihub 端点，新增 2026-07-01，已修复 API 端点） |

**核心类架构**：

| 类 | 职责 | 关键功能 |
|----|------|---------|
| `BaseTool` | 抽象基类 | name/description/permission_level/parameters_schema → `execute()` → ToolResult；`to_openai_fc_schema()` 生成 OpenAI 函数调用 JSON |
| `ToolResult` | 返回包装 | success + data/error；`to_dict()` / `to_fc_result()` 序列化 |
| `ToolRegistry` | 注册中心 | `register()` / `get()` / `get_tools_by_permission(affinity)` / `health_check_all()` |
| `ToolDispatcher` | 执行网关 | dispatch → 权限检查 → 速率限制（3次/分钟/工具）→ 执行 → 重试 → 埋点 |

**集成方式**：main.py 中 `OptimizedOrchestrator.__init__` 创建 `ToolRegistry` → 注册所有工具 → 包装为 `ToolDispatcher` → 存入 `self.components`。`_execute_tool_calls()` 在 LLM chat 管线中调用，工具结果注入 prompt 上下文。

**安全特性**：
- SSRF 防护：`CharacterCrawlerTool._validate_url()` 拦截 localhost/私有 IP/未注册协议
- 安全 eval：`CalculatorTool` 使用 AST 解析替代 eval()
- 权限门控：public/friend/intimate/admin 四级
- 优雅降级：每个工具有依赖不满足时的回退路径

---

## 5. 社区聚类（Leiden 算法）

知识图谱通过 Leiden 社区检测识别出 12 个自然模块：

| ID | 标签 | 成员数 | 内聚度 | 涉及包 |
|----|------|--------|--------|--------|
| 8 | my_character | 269 | 0.50 | api, shisi, my_character, proactive, character_card |
| 17 | my_character | 245 | 0.59 | weclone_adapter, my_character, tests, proactive |
| 0 | api | 209 | 0.52 | main, frontend, shisi, api, utils |
| 9 | shisi | 207 | 0.69 | api, shisi, tests, clone_training, llm_provider |
| 3 | tests | 164 | 0.66 | main, tests, shisi, api, voice |
| 49 | tests | 158 | 0.58 | shisi, tests, proactive, my_character, api |
| 12 | shisi | 150 | 0.75 | shisi, tests, my_character, frontend, api |
| 11 | api | 134 | 0.61 | api, shisi, persona_extractor, tests, character_card |
| 99 | tests | 132 | 0.80 | shisi, frontend, tests, orchestrator, api |
| 2 | shisi | 117 | 0.72 | main, shisi, proactive, my_character, tests |
| 6 | tests | 112 | 0.65 | main, shisi, proactive, persona_extractor, tests |
| 48 | tests | 95 | 0.67 | shisi, tests |

**观察**：
- shisi 模块内聚度最高（0.69-0.80），Clean Architecture 重构效果显著
- my_character 模块跨越多个包，是系统的胶水层
- 测试节点占大量聚类成员，测试覆盖良好

---

## 6. 热点函数（fan-in Top 10）

| 排名 | 函数 | fan-in | 文件 | 角色 |
|------|------|--------|------|------|
| 1 | `ConfigLoader.get` | 423 | my_character/character_config.py | 配置读取中枢 |
| 2 | `SafetyLogManager.append` | 234 | api/state/safety_log.py | 安全日志 |
| 3 | `deploy.setup.info` | 209 | deploy/setup.sh | 部署日志 |
| 4 | `TimeContext.now` | 103 | my_character/enhanced_prompt_engine.py | 时间上下文 |
| 5 | `deploy.setup.error` | 80 | deploy/setup.sh | 部署错误 |
| 6 | `WeChatConnector.run` | 63 | wechat_direct/wechat_connector.py | 微信连接 |
| 7 | `PromptTemplateMgr.get` | 54 | llm_provider/prompt_template_manager.py | Prompt 模板 |
| 8 | `WorkingMemory.add` | 49 | shisi/memory/legacy/memory_pipeline.py | 工作记忆 |
| 9 | `AffinityEnhancer.update` | 46 | shisi/affinity/enhancer.py | 亲密度更新 |
| 10 | `MigrationService.execute` | 44 | shisi/application/migration_service.py | 数据迁移 |

**风险**：ConfigLoader.get 的 423 fan-in 意味着它是单点故障 — 出问题则 423 个调用点受影响。

---

## 7. 复杂度热点（transitive_loop_depth）

> ✅ **2026-07-28 双模式合并后 main.py 已从 35.9 KB → 22.7 KB / 574 行**。
> 原 `_run_fast_mode`（complexity=15）与 `_run_full_mode`（complexity=19）已合并为单一 `_run_orchestrator`，复杂度大幅降低。
> 以下为 2026-07-09 codebase-memory 图谱工具 历史快照，仅供对照：

| 函数（历史快照） | 历史复杂度 | 历史传递循环深度 | 当前状态 |
|------|--------|-------------|---------|
| `main.run_clone_pipeline` | 3 | 12 | 仍在 main.py |
| `main.main` | 4 | 12 | 仍在 main.py（精简） |
| `main._run_fast_mode` | 15 | 12 | ✅ 已删除（合并入 `_run_orchestrator`） |
| `main._run_full_mode` | 19 | 12 | ✅ 已删除（合并入 `_run_orchestrator`） |
| `main.run_console_chat` | 24 | 11 | 仍在 main.py，**最高复杂度**（待后续优化） |
| `main.run_wechat_mode` | 3 | 9 | 仍在 main.py |
| `main._detect_voice_request` | 17 | 4 | 已迁移到 `orchestrator/voice_detector.py` |
| `main._run_orchestrator` | — | — | ✅ 新增（替代双模式，复杂度 ~10） |

**建议**：`run_console_chat`（complexity=24）仍是 main.py 的复杂度热点，但已不阻塞主路径。下一轮可考虑迁移到 `orchestrator/` 包。

---

## 8. 模块间边界（跨模块调用 Top 10）

| 调用方 to 被调方 | 调用次数 | 性质 |
|------------------|---------|------|
| OptimizedOrchestrator to prompt_template_manager | 14 | 编排 to Prompt |
| OptimizedOrchestrator to application | 13 | 编排 to 应用服务 |
| main to prompt_template_manager | 9 | 入口 to Prompt |
| OptimizedOrchestrator to content_safety | 8 | 编排 to 安全 |
| OptimizedOrchestrator to fusion | 6 | 编排 to 人格融合 |
| main to websocket_server | 5 | 入口 to WebSocket |
| OptimizedOrchestrator to prompt_injection | 5 | 编排 to 安全 |
| OptimizedOrchestrator to scheduler | 4 | 编排 to 调度 |
| OptimizedOrchestrator to setup | 4 | 编排 to 初始化 |
| OptimizedOrchestrator to main | 4 | 编排到入口（循环依赖风险） |

**注意**：`OptimizedOrchestrator to main` 的 4 次调用可能形成循环依赖。

---

## 9. main to shisi 跨模块调用

| main 调用方 | shisi 被调方 | 目标文件 |
|------------|-------------|---------|
| `_detect_voice_request` | `KeywordRetriever.search` | shisi/knowledge/retriever.py |
| `run_console_chat` | `WorkingMemory.start_session` | shisi/memory/legacy/memory_pipeline.py |
| `run_console_chat` | `StructuredMemory.count_chats_today` | shisi/memory/legacy/structured_memory.py |
| `_run_full_mode` | `PersonaService` | shisi/application/persona_service.py |
| `_run_full_mode` | `ShisiMemoryService` | shisi/application/memory_service.py |
| `_run_full_mode` | `ShisiKnowledgeAdapter` | shisi/application/knowledge_service.py |

`_run_full_mode` 是 main.py 与 shisi/ Clean Architecture 的主要集成点。

**新增热点 (2026-07-01)**：`OptimizedOrchestrator` 中增加的 `_execute_tool_calls()` 将 main.py 与 `tools/` 包联结，`ToolRegistry.get_tools_by_permission()` 和 `ToolDispatcher.dispatch()` 成为热路径上的新节点。

---

## 10. 技术栈依赖

| 类别 | 依赖 |
|------|------|
| Web 框架 | FastAPI + uvicorn + Pydantic v2 |
| 数据库 | SQLAlchemy 2.0 + aiosqlite + ChromaDB |
| 向量 | sentence-transformers + rank-bm25 |
| LLM | httpx + tenacity（自动 fallback） |
| LLM 供应商 | 智谱AI (glm-4-flash), 讯飞星火 (spark-lite), 百度千帆 (ernie-speed-128k), DeepSeek, **sensenova (glm-5.2)** |
| 语音 | edge-tts + FFmpeg（可选） |
| 缓存 | Redis（可选） |
| 可观测 | prometheus-client + OpenTelemetry + Sentry SDK |
| 安全 | pycryptodome + python-jose + passlib[bcrypt] |
| 调度 | APScheduler + schedule |
| 前端 | React 19 + Vite + Zustand + React Query + Playwright |
| 测试 | pytest + pytest-asyncio + pytest-cov + ruff + mypy |
| 工具系统 | httpx, requests, beautifulsoup4, cloudscraper, duckduckgo_search, chinese_calendar, lunarcalendar |

---

## 11. 风险与建议

| 风险 | 严重度 | 位置 | 建议 |
|------|--------|------|------|
| ~~main.py 巨型文件~~ | ~~高~~ | ~~main.py~~ | ✅ **已解决** (2026-07-28)：双模式合并后 22.7 KB / 574 行，`_run_full_mode`/`_run_fast_mode` 已删除 |
| ~~双调度器并行运行 bug~~ | ~~高~~ | ~~main.py + _init_mixin~~ | ✅ **已修复** (2026-07-28)：原 `_init_mixin` 与 `_run_*_mode` 各创建一个 `ProactiveScheduler`；现统一复用 |
| `run_console_chat` complexity=24 | 中 | main.py | 仍是 main.py 最高复杂度函数，但已不阻塞主路径。下一轮可迁移到 `orchestrator/` 子模块 |
| ConfigLoader.get 423 fan-in | 中 | my_character/character_config.py | 加缓存、加降级，避免单点故障 |
| OptimizedOrchestrator to main 循环依赖 | 中 | main.py | 检查 4 次回调是否可消除 |
| process_message 全链 CRITICAL | 中 | orchestrator/optimized_orchestrator.py | 每个 hop=1 节点都需要降级路径 |
| **多用户 LLM 缓存失效边界** | 低 | llm_provider/__init__.py:259 `invalidate_user_llm` | 用户改 LLM 配置 → 缓存失效 → 下次对话按新配置重建。验证：worker 进程间缓存一致性 |
| **微信 flock 文件锁仅在 Linux 生效** | 低 | api/run_api.py:165 `fcntl.flock` | Windows 开发环境会 fallback 到 `ImportError`，开发模式下无锁竞争（单 worker） |
| **工具系统引入热路径新节点** | 低 | tools/base_tool.py | ToolRegistry/ToolDispatcher 成为 LLM 回复前必经路径，需确保可用性 |
| ~~测试基线漂移~~ | ~~中~~ | ~~tests/~~ | ✅ **已修复** (2026-07-30)：实测 1025 Python 测试 + 79 前端测试 = 1104 全部通过；前端 6 个过时测试已修正(WeChatPage 绑定功能迁移到 UsersPage、SettingsVoice ENGINE_OPTIONS 精简为 MiMo Cloud、SettingsLLM mock 补全 useAuthStore/listProviders) |

---

## 12. 查询指南



代码图谱已重新索引到 codebase-memory 图谱工具（2026-07-03 确认可用），项目名为 D-Desktop-ai-girlfriend：

| 需求 | MCP 工具 | 命令 |
|------|----------|------|
| 架构概览 | get_architecture | get_architecture(project="D-Desktop-ai-girlfriend") |
| 搜索函数/类 | search_graph | search_graph(project="D-Desktop-ai-girlfriend", query="emotion engine") |
| 追踪调用链 | trace_path | trace_path(project="D-Desktop-ai-girlfriend", function_name="process_message", mode="calls", depth=3) |
| 自定义查询 | query_graph | query_graph(project="D-Desktop-ai-girlfriend", query="MATCH (f:Function) WHERE f.transitive_loop_depth >= 3 RETURN f.qualified_name, f.complexity") |
| 找热点路径 | query_graph | MATCH (f:Function) WHERE f.transitive_loop_depth >= 3 RETURN f.qualified_name ... ORDER BY f.transitive_loop_depth DESC |

**备选方案**（MCP 工具不可用时）：

| 需求 | 方法 | 命令 |
|------|------|------|
| 搜索函数/类 | ripgrep 全局搜索 | rg "class PersonaService" / rg "def process_message" |
| 追踪调用链 | grep 调用点 | rg "PersonaService\." --type py |
| 目录概览 | 目录树 | tree /F |
| 路由列表 | 搜索路由装饰器 | rg "router\." --type py \| rg "\.(get\|post\|put\|delete)\(" |
| 前端页面 | 列出 pages | ls frontend/src/pages/ |

图谱存储于 .codebase-memory/graph.db.zst（3.3MB）。
---

## 13. 更新记录

| 日期 | 提交 | 变更摘要 |
|------|------|---------|
| 2026-07-01 | 9c0b636..b455222 (7 commits) | 初始创建：新增 tools/ 工具系统、sensenova LLM 供应商、PersonaService 两阶段构造、18 个前端页面、安全日志 |
| 2026-07-03 | 9a0ca50, 78acdc9 (2 commits) | 修复 ImageGenTool Agnes API 端点 (apihub.agnes-ai.com)，删除 response_format 参数；更新环境模板文档 |
| 2026-07-09 | — | 知识图谱索引刷新（+12 节点 / +8 边，扫描时间更新至 2026-07-09） |
| 2026-07-14 | — | 投产前安全审计修复：路径遍历防护、认证统一、IDOR 修复、部署加固；清理墓碑代码（8个 set_dependencies 函数、2个死函数）、删除18个一次性脚本和临时文件、恢复 app_factory.py |
| 2026-07-26 | c436ed5 / 3488e60 / 118affd | orchestrator 架构债清理：拆分 optimized_orchestrator.py 为 5 文件包（主类 + _init_mixin + _stream_mixin + components 共享状态）；清理 web_enricher.py 路径硬编码；重建部署链 |
| 2026-07-27 | 7c9e12a / 1130925 | **9 项 P0/P1 修复**：(1) WeChatPage.tsx 移除冗余 StatsBar/绑定列表；(2) RoleSettingsConstants.tsx ENGINE_OPTIONS 仅留 mimo-tts；(3) `/api/clone/upload` 端点（本地提取→上传→服务器分析）；(4) `/api/user/llm-config` GET/POST 解决 403；(5) `/api/characters/{id}/enrich` 端点（火爬虫+AgentReach 人设增强）；(6) AGENT_REACH_PATH 改环境变量；(7) `api/run_api.py` flock 文件锁自动恢复微信连接；(8) `User.llm_config` JSON 字段 + `invalidate_user_llm()` 用户级 LLM 网关缓存；(9) 修复 misc_routes 测试基线 |
| 2026-07-28 | 2815135 / 30616d3 | **P1 架构债清理 + 测试同步**：(1) 删除根目录临时脚本 `_download_model.py`/`_sse_final.py`；(2) 删除冗余 `requirements.txt`（pyproject.toml 为唯一权威依赖源）；(3) 为 `shisi/memory/legacy/` 与 `shisi/knowledge/legacy/` 添加命名说明注释（消除"legacy=待删除"误导）；(4) 新增 `shisi/character/png_codec.py` 支持 SillyTavern PNG tEXt chunk 角色卡格式（含 importer/exporter/manager/store/validator 完整子系统）；(5) `character_routes.py` 导入支持 .png 文件 + 导出支持 `?format=png`；(6) 同步本地 SQLite schema（添加 llm_config JSON 列到 users 表）；(7) 测试同步：clone_routes 端点 8→9，misc_routes 端点 9→11 |
| 2026-07-28 (图谱刷新) | — | **CODE_GRAPH.md v3.0.0 → v3.1.0**：通过 Grep + LS 实时核实路由数（332→207）、main.py 体量（103KB→35.9KB/726 行）、前端 API 模块数（12→13）、测试用例数（626+→540）；新增 shisi/character/ 子包说明、多用户 LLM 隔离章节、PNG 角色卡集成路径；标记 codebase-memory 图谱工具 快照未刷新的指标 |
| 2026-07-28 (双模式合并) | (working tree) | **架构升级 v3.2.0**：(1) `_run_fast_mode` + `_run_full_mode` 合并为单一 `_run_orchestrator`（main.py 35.9KB→22.7KB / 871→574 行 / -297 行）；(2) `_init_mixin` 成为唯一初始化真相源，新增第 10 阶段 `_init_multimodal` + 补齐 `EncryptionManager`/`classifier_mode`/`prompt_mode` 参数；(3) 修复双调度器并行 bug（原 `_init_mixin` 与 `_run_*_mode` 各创建一个 `ProactiveScheduler`）；(4) 清理 main.py 19 个冗余 import（已迁移至 `_init_mixin`）；(5) 1007 tests passed + 1 skipped（行为不变验证完成） |
| 2026-07-28 (调度器单例) | fb83232 | **多 worker 调度器单例保护**：(1) `api/run_api.py` 新增 `_ensure_scheduler_singleton()` — flock 文件锁（`/tmp/ai-girlfriend-scheduler.lock`）确保 4 个 uvicorn worker 中只有 master 持有调度器，其他 worker 停止调度器避免 N 倍主动消息；(2) `main.py --no-scheduler` 设置 `DISABLE_SCHEDULER=1` 环境变量供子进程继承，停止 `_init_mixin` 已启动的调度器；(3) `api/run_api.py` 启动时检查 `DISABLE_SCHEDULER` 环境变量，等价 `--no-scheduler`；(4) 生产环境验证：日志显示 1 个 master 持锁 + 3 个 worker 停止调度器；Playwright headless 测试 5 个页面（首页 / /wechat / /settings/llm / /api/health / /roles）全部 200，`/settings/llm` 不再 403 |
| 2026-07-30 (文档对齐) | (working tree) | **Truth 文档对齐 + 前端测试修复**：(1) `api/app_factory.py` 顶部注释从"9 子路由 75 端点"修正为"17 include_router 204 端点"(create_api_app 实扫);(2) CODE_GRAPH.md 测试数 540→1104(1025 Python + 79 前端,pytest+vitest 实跑)、端点数 207→204(实扫)、前端测试 84→79;(3) 修复前端 6 个过时测试:WeChatPage 4 个(绑定功能已迁移到 UsersPage)、SettingsVoice 2 个(ENGINE_OPTIONS 精简为仅 MiMo Cloud)、SettingsLLM 1 个(补全 useAuthStore/listProviders/system.ts importOriginal mock);(4) AGENTS.md Owner Map 补全 6 个缺失目录;(5) docs/CODEMAPS/MODULES.md 修正不存在的文件引用 |

*此图谱将持续更新以反映项目变化。下一次刷新应重跑 codebase-memory 图谱工具 索引以更新节点/边数据。*

---

## 14. 架构债清理决策（2026-07-28）

为避免后续维护者误判，记录以下评估结论：

| 模块 | 评估结论 | 依据 |
|------|---------|------|
| `shisi/memory/legacy/` | **保留，不重命名** | 被 `shisi/application/memory_service.py` + `tests/test_memory*.py` 81 处引用；`legacy` 仅表"历史迁移"非"待删除" |
| `shisi/knowledge/legacy/` | **保留，不重命名** | 被 `tests/test_rag_engine.py` 52 处引用，提供 RAGEngineV2 等核心 RAG 抽象 |
| `character_card/` | **保留** | 被 `orchestrator/_init_mixin.py` 通过 `from character_card.integration import CharacterCardAdapter` 引用，是角色卡融合入口 |
| `shisi/api/` | **保留** | 被 `api/app_factory.py` 通过 `from shisi.api.registry import setup_shisi` 引用；与 `api/routers/` 形成双 API 层分工（见下） |
| `requirements.txt` | **已删除** | pyproject.toml 已是权威完整依赖源，文件头部已声明"以 pyproject.toml 为权威" |

### 双 API 路由层分工说明

| 层 | 路径 | 职责 | 调用方 |
|---|---|---|---|
| 域路由层 | `api/routers/*.py` (21 模块，不含 `__init__.py`) | 控制 plane 端点：character/auth/admin/voice/wechat/clone/... | `api/app_factory.py` 主挂载 |
| shisi 域层 | `shisi/api/*.py` + `shisi/api/v2/` | DDD 核心 plane 端点：affinity/emotion_stage/persona/stats/vital_signs + v2 迁移 | `shisi/api/registry.py:setup_shisi` 由 app_factory 调用 |

两层不冲突：域路由层面向"控制/管理"，shisi 域层面向"DDD 核心域"。两者通过 `app_factory.create_api_app()` 统一装配。

### 新增架构决策（2026-07-28）

| 决策 | 模块 | 依据 |
|------|------|------|
| **多用户 LLM 隔离走 `User.llm_config` JSON 字段** | `api/database.py:71` / `llm_provider/__init__.py:259` | 避免每用户单独建表；JSON 字段灵活承载 provider/api_key/model；普通用户可读写自己的配置（不再 403）；admin 仍走 `/api/config` 全局配置 |
| **微信连接自动恢复使用 flock 文件锁** | `api/run_api.py:158 _autostart_wechat_connector()` | uvicorn `--workers 4` 启动 4 个进程，无锁会同时启动 4 个 WeChatConnector 轮询线程导致消息重复；`fcntl.flock(LOCK_EX \| LOCK_NB)` 确保只有一个 worker 持有锁；锁在进程退出时自动释放（不显式释放） |
| **PNG 角色卡走 `shisi/character/png_codec.py` 而非 `character_card/`** | `shisi/character/png_codec.py` | PNG tEXt chunk 是 SillyTavern 生态标准，归入 shisi DDD 核心 character 子域；`character_card/` 保留角色卡融合入口职责，不混入编解码细节 |
| **克隆朋友架构改为本地提取→上传→服务器分析** | `api/routers/clone_routes.py:132 /api/clone/upload` | wechat-decrypt 依赖 Windows 微信进程 + Windows API，无法在 Linux 服务器运行；用户本地提取 JSON → 服务器分析 → 生成人设预览；通过 `WECHAT_DECRYPT_PATH` 环境变量支持本地模式（开发/测试） |
| **保留 `shisi/memory/legacy/` 与 `shisi/knowledge/legacy/` 不重命名** | 同 v3.0.0 决策 | 81+ / 52+ 引用，`legacy` 仅表"历史迁移"非"待删除"（v3.1.0 已添加命名说明注释） |
| **`requirements.txt` 已删除** | — | pyproject.toml 已是权威完整依赖源，文件头部已声明"以 pyproject.toml 为权威" |
| **多 worker 调度器单例走 flock 文件锁** | `api/run_api.py:131 _ensure_scheduler_singleton()` | uvicorn `--workers 4` 启动 4 个进程，每个都执行 `orchestrator.initialize()` → `_init_ase_and_scheduler` → `scheduler.start()`，若无锁会有 4 个调度器并行运行导致 4 倍主动消息。`fcntl.flock(LOCK_EX \| LOCK_NB)` 确保只有 master worker 持锁；锁在进程退出时自动释放；锁文件 `/tmp/ai-girlfriend-scheduler.lock` |
| **`--no-scheduler` 通过 `DISABLE_SCHEDULER=1` 环境变量传递给子进程** | `main.py:485 _run_orchestrator` | `main.py` 启动时 `--no-scheduler` 仅停止当前进程的调度器，但 uvicorn 派生的 worker 子进程会重新初始化。通过 `os.environ["DISABLE_SCHEDULER"]="1"` 让子进程继承，`api/run_api.py:_ensure_scheduler_singleton()` 检查到该变量后立即停止调度器，等价于在所有 worker 中执行 `--no-scheduler` |

# 后端业务模块地图

**最近更新:** 2026-07-30（修正不存在的文件引用，对齐实际目录结构）
**Python 版本:** ≥3.10 | **总文件:** ~309 .py 文件

---

## 模块总览

| 模块 | 文件数 | 路径 | 职责 | 状态 |
|------|--------|------|------|------|
| **shisi** | 96 | `shisi/` | 核心业务逻辑（角色/情感/记忆/故事线/知识库等，DDD 分层架构） | ✅ 活跃 |
| **api** | 36 | `api/` | FastAPI 路由层（22 个 routers + health/main_routes + state） | ✅ 活跃 |
| **persona_extractor** | ~14 | `persona_extractor/` | 人格提取与注入 | ✅ 活跃 |
| **voice** | 11 | `voice/` | 语音合成 (TTS) | ✅ 活跃 |
| **observability** | 9 | `observability/` | 可观测性（日志/指标/追踪/健康检查） | ✅ 活跃 |
| **tools** | 10 | `tools/` | 工具系统（12 个内置工具） | ✅ 活跃 |
| **llm_provider** | 6 | `llm_provider/` | LLM 多供应商网关 | ✅ 活跃 |
| **security** | 5 | `security/` | 安全过滤与加密 | ✅ 活跃 |
| **proactive** | 5 | `proactive/` | 主动消息推送 | ✅ 活跃 |
| **character_card** | 6 | `character_card/` | 角色卡解析/验证/构建 | ✅ 活跃 |
| **clone_training** | 5 | `clone_training/` | 克隆训练（数据清洗/数据提取/风格分析；dataset_builder/lora_trainer 已删除） | ✅ 活跃 |
| **orchestrator** | 5 | `orchestrator/` | 优化编排器（初始化阶段/流式/会话锁/语音检测） | ✅ 活跃 |
| **weclone_adapter** | 3 | `weclone_adapter/` | 微信克隆适配 | ✅ 活跃 |
| **multimodal** | 2 | `multimodal/` | 多模态处理 | ✅ 活跃 |
| **wechat_direct** | 2 | `wechat_direct/` | 微信直连 | ✅ 活跃 |
| **plugins** | 2 | `plugins/` | 插件系统 | ✅ 活跃 |
| **cache** | 2 | `cache/` | LLM 缓存 + Redis 客户端 | ✅ 活跃 |
| **context** | 1 | `context/` | 上下文（世界书提供器） | ✅ 活跃 |
| **memory_ext** | 1 | `memory_ext/` | 记忆扩展（mem0 后端） | ✅ 活跃 |
| **my_character** | ~ | `my_character/` | 自定义角色模块 | ✅ 活跃 |

---

## shisi/ — 核心业务逻辑 (96 文件)

**入口:** `shisi/api/registry.py`（由 `api/app_factory.py` 调用 `setup_shisi` 装配）
**配置:** `shisi/config.py` / `shisi/migrations.py`

**子模块:**

| 子模块 | 职责 | 关键文件 |
|--------|------|----------|
| `api/` | shisi DDD 核心 plane 路由 (49 endpoints) | `registry.py`, `affinity_routes.py`, `character_routes.py`, `emotion_stage_routes.py`, `memory_routes.py`, `persona_routes.py`, `stats_routes.py`, `sticker_routes.py`, `training_routes.py`, `vital_signs_routes.py`, `common.py` |
| `api/v2/` | v2 迁移路由 | `health_routes.py`, `migration_routes.py`, `persona_routes.py`, `character_routes.py`, `schemas.py` |
| `application/` | 应用服务层 | `character_service.py`, `memory_service.py`, `persona_service.py`, `knowledge_service.py`, `prompt_service.py`, `migration_service.py` |
| `character/` | 角色卡完整子系统（SillyTavern V2/V3 + PNG tEXt chunk） | `character_card_v2.py`, `png_codec.py`, `importer.py`, `exporter.py`, `manager.py`, `store.py`, `validator.py`, `models.py` |
| `core/models/` | 领域模型 | `affinity_level.py`, `character_id.py`, `emotional_state.py`, `emotion_type.py`, `persona_profile.py`, `character_aggregate.py` |
| `core/ports/` | 端口接口 | `character_repository.py` |
| `core/services/` | 领域服务 | `emotion_detector.py`, `prompt_builder.py` |
| `infrastructure/persistence/` | 持久化 | `sqlite_repository.py`, `schema.py` |
| `infrastructure/migration/` | 迁移 | `migration_runner.py`, `rollback_runner.py` |
| `memory/legacy/` | 记忆管线（生产路径，`legacy` 仅表历史迁移非待删除） | `memory_pipeline.py`, `working_memory.py`, `episodic_memory.py`, `semantic_memory.py`, `structured_memory.py`, `vector_memory.py`, `fact_extractor.py`, `importance_scorer.py`, `reflection_engine.py`, `conversation_summarizer.py`, `diary_summarizer.py`, `conflict_detector.py`, `cross_session_reasoner.py`, `forgetting_manager.py` |
| `memory/` | 记忆管理（收藏/转发） | `favorite_manager.py`, `forward_manager.py` |
| `affinity/` | 好感度系统 | `enhancer.py`, `mapper.py`, `decay_engine.py`, `unlock_manager.py` |
| `emotion_stage/` | 情感阶段 | `stage_engine.py`, `event_dispatcher.py`, `stage_config.py` |
| `storyline/` | 故事线 | `engine.py`, `detector.py`, `config.py` |
| `vital_signs/` | 生命指标 | `vital_engine.py`, `emotion_mapping.py` |
| `sticker/` | 表情包 | `sticker_manager.py`, `emotion_recommender.py`, `safety_check.py`, `importer.py`, `default_provider.py` |
| `voice/` | 语音 | `character_voice.py`, `emotion_tts.py` |
| `wechat/` | 微信集成 | `command_handler.py`, `command_parser.py`, `proactive_messenger.py`, `sticker_adapter.py` |
| `knowledge/` | 知识检索 | `retriever.py`, `character_knowledge_service.py`, `crawler_adapter.py` |
| `knowledge/legacy/` | RAGEngineV2（保留，被 tests/test_rag_engine.py 52 处引用） | `rag_engine.py` |
| `ase/` | 场景叙事 | `scene_narrator.py`, `trigger_engine.py` |
| `vault/` | 数据收集 | `collect_loop.py`, `_persona_adapter.py` |
| `stats/` | 统计 | `analytics.py` |

**依赖:** llm_provider, tools, voice, security, orchestrator
**被依赖:** api (通过 `shisi/api/registry.py:setup_shisi` 由 `app_factory` 装配)

---

## api/ — FastAPI 路由层 (36 文件)

**入口:** `api/run_api.py` → `api/app_factory.py:create_api_app()`

**结构:**
- 根目录: `app_factory.py`（应用工厂）, `run_api.py`（启动入口）, `main_routes.py`（模型/常量/Helper）, `health_routes.py`（健康检查）, `auth.py`/`auth_jwt.py`（认证）, `database.py`（SQLAlchemy）, `deps.py`（依赖注入）, `session_manager.py`, `websocket_server.py`, `qrcode_store.py`, `path_security.py`, `runtime_config.py`
- `routers/` 21 个路由模块（2026-08-28：demo_routes 已删除，原 22）: `admin_routes`, `auth_routes`, `character_routes`, `chat_routes`, `clone_routes`, `emotion_routes`, `invite_routes`, `knowledge_routes`, `llm_providers_routes`, `memory_routes`, `mimo_voice_routes`, `misc_routes`, `persona_card_routes`, `personality_routes`, `safety_routes`, `storyline_routes`, `tools_routes`, `training_routes`, `users_routes`, `voice_routes`, `wechat_routes`
- `state/` 3 个状态模块: `safety_log`, `tool_history`, `training_state`

**实际挂载:** 16 个 `include_router` 调用,共 199 端点（2026-08-28 `create_api_app` 实扫；07-30 基线为 17/204，demo 删除后 -1 路由 -4 端点）
**依赖:** shisi, security, llm_provider, database

---

## persona_extractor/ — 人格提取 (~14 文件)

**职责:** 从对话中提取用户人格特征，注入角色回复

**关键文件:**
- `fusion.py` — 人格融合主入口
- `models.py` — 数据模型
- `persona_bank.py` — 人格库
- `style_vectorizer.py` — 风格向量化
- `hexaco.py`, `dark_triad.py`, `mental_health.py` — 人格维度分析

**依赖:** llm_provider, shisi/memory
**被依赖:** shisi (通过 Orchestrator)

---

## voice/ — 语音合成 (11 文件)

**职责:** 文本转语音，多 TTS 引擎支持

**关键文件:**
- `tts_manager.py` — TTS 管理器
- `tts_provider_base.py` — TTS 供应商基类
- `mimo_tts_provider.py` — MiMo TTS 实现
- `edge_tts_provider.py` — Edge TTS 实现
- `bert_vits2_provider.py` — Bert-VITS2 实现
- `cosyvoice_provider.py` — CosyVoice 实现
- `sovits_provider.py` — So-VITS 实现

**依赖:** config (emotion.yaml)
**被依赖:** api (voice_routes, mimo_voice_routes)

---

> **注:** 记忆系统已整合到 `shisi/memory/legacy/` 子模块中（见 shisi/ 总览），
> 扩展记忆后端见下方 `memory_ext/` 模块。

---

## tools/ — 工具系统 (10 文件, 12 个内置工具)

**职责:** 工具定义、调度、执行

**关键文件:**
- `base_tool.py` — 工具基类
- `builtin/` — 内置工具目录 (12 个内置工具)
  - `search_tool.py` — 搜索
  - `weather_tool.py` — 天气
  - `calendar_tool.py` — 日历
  - `reminder_tool.py` — 提醒
  - `time_awareness_tool.py` — 时间感知
  - `character_crawler_tool.py` — 角色爬取
  - `extra_tools.py` — 扩展工具

**依赖:** shisi
**被依赖:** Orchestrator

---

## observability/ — 可观测性 (9 文件)

**职责:** 日志、指标、链路追踪、健康检查

**关键文件:**
- `logging_setup.py` — 结构化日志
- `metrics.py` — Prometheus 指标
- `tracing.py` — OpenTelemetry 追踪
- `config_manager.py` — 配置管理
- `health.py` — 健康检查

**依赖:** 无外部（标准库 + OpenTelemetry）
**被依赖:** 全局

---

## llm_provider/ — LLM 供应商 (6 文件)

**职责:** 多 LLM 供应商统一接入

**关键文件:**
- `multi_provider_gateway.py` — `MultiProviderGateway` 主入口
- `openai_compatible_provider.py` — OpenAI 兼容 API
- `prompt_template_manager.py` — 提示模板管理
- `llm_gateway.py` — LLM 网关（LLMGatewayV2）
- `__init__.py` — `invalidate_user_llm()` 用户级 gateway 缓存失效

**依赖:** config/llm_providers.json
**被依赖:** shisi, persona_extractor, cache

---

## security/ — 安全模块 (5 文件)

**职责:** 内容安全、PII 匿名化、注入检测、加密

| 文件 | 职责 |
|------|------|
| `content_safety.py` | 内容安全过滤 |
| `pii_anonymizer.py` | PII 匿名化 |
| `prompt_injection.py` | 提示注入检测 |
| `encryption.py` | 加密管理 |

**依赖:** 无
**被依赖:** Orchestrator (流水线第 1-3 步)

---

> **注:** RAG 检索引擎已整合到 `shisi/knowledge/` 子模块中（见 shisi/ 总览），
> 包含 `retriever.py`（检索器）和 `shisi/knowledge/legacy/rag_engine.py`（RAGEngineV2）。

---

## orchestrator/ — 优化编排器 (5 文件)

**职责:** 聊天流水线编排，组件初始化阶段化，SSE 流式输出，会话锁管理，语音检测

**关键文件:**
- `optimized_orchestrator.py` — 主类 `OptimizedOrchestrator`：`__init__` / 会话锁 / 上下文准备 / `process_message` / 健康检查（920 行）
- `_init_mixin.py` — `_InitPhasesMixin`：`initialize` 拆分为 10 个 `_init_*` 阶段
- `_stream_mixin.py` — `_StreamPipelineMixin`：`process_message_stream` SSE 真流式/伪流式降级
- `session_locks.py` — 会话锁管理（`SessionLockManager`）
- `voice_detector.py` — 语音活动检测

> **架构:** `OptimizedOrchestrator` 继承 `_InitPhasesMixin` + `_StreamPipelineMixin`，通过 `self.components` 共享状态。公共 API 100% 兼容，外部导入路径 `from orchestrator import Orchestrator` 不变。

**依赖:** shisi, llm_provider, security, tools, cache
**被依赖:** api (chat_routes)

---

## cache/ — 缓存层 (2 文件)

**职责:** LLM 响应缓存，Redis 客户端管理

**关键文件:**
- `llm_cache.py` — LLM 响应缓存
- `redis_client.py` — Redis 客户端

**依赖:** redis (外部)
**被依赖:** llm_provider, orchestrator

---

## character_card/ — 角色卡 (6 文件)

**职责:** 角色卡解析、验证、提示构建

**关键文件:**
- `parser.py` — 角色卡解析器
- `validator.py` — 角色卡验证器
- `models.py` — 数据模型
- `prompt_builder.py` — 提示构建器
- `integration.py` — 集成接口（被 `orchestrator/_init_mixin.py` 装配）

**依赖:** 无
**被依赖:** shisi, api

---

## clone_training/ — 克隆训练 (5 文件)

**职责:** 微信聊天数据清洗、数据提取、风格分析

**关键文件:**
- `data_cleaner.py` — 数据清洗
- `data_extractor.py` — 数据提取
- `style_analyzer.py` — 风格分析
- `wechat_decrypt_source.py` — 微信解密源（Windows 微信进程依赖）

> **注:** `dataset_builder.py` 与 `lora_trainer.py` 已删除（架构改为本地提取→上传→服务器分析）。

**依赖:** 无
**被依赖:** api (clone_routes)

---

## context/ — 上下文 (1 文件)

**职责:** 世界书上下文提供

**关键文件:**
- `world_info_provider.py` — 世界书提供器

**依赖:** 无
**被依赖:** shisi, orchestrator

---

## memory_ext/ — 记忆扩展 (1 文件)

**职责:** 基于 mem0 的扩展记忆后端

**关键文件:**
- `mem0_backend.py` — mem0 记忆后端

**依赖:** mem0 (外部)
**被依赖:** shisi/memory

---

## 模块依赖图

```
api/ ←── orchestrator/ ←── shisi/ ←── llm_provider/ ←── 外部 LLM API
  │           │              │            │
  │           │              │            └── cache/ ←── redis
  │           │              │
  │           │              ├── shisi/memory/legacy/ ←── memory_ext/ (mem0)
  │           │              │      │
  │           │              │      └── ChromaDB (向量存储)
  │           │              │
  │           │              ├── shisi/knowledge/
  │           │              │      │
  │           │              │      └── ChromaDB
  │           │              │
  │           │              ├── tools/ (12 内置工具)
  │           │              │
  │           │              ├── persona_extractor/ ←── llm_provider/
  │           │              │
  │           │              └── voice/ ←── config/
  │           │
  │           ├── security/ ←── 无依赖 (独立)
  │           │
  │           ├── context/ (世界书)
  │           │
  │           └── character_card/
  │
  ├── observability/ ←── 无依赖 (独立)
  │
  ├── clone_training/
  │
  └── my_character/ ←── shisi/
```

---

## 相关地图

- [BACKEND.md](BACKEND.md) — API 层与模块的衔接
- [ARCHITECTURE.md](ARCHITECTURE.md) — 模块在整体架构中的位置
- [../../CODE_GRAPH.md](../../CODE_GRAPH.md) — 代码图谱（含端点分布、热点函数、聚类分析）

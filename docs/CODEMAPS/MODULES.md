# 后端业务模块地图

> **✅ 2026-09-20 增量刷新**：在 09-19 全量刷新基线上补齐 09-19 晚通道隔离批次（wechat_direct 2→5 文件、api 44→45、routers 21→22）。权威口径以 `CODE_GRAPH.md` v3.8.6 为准。
> **⚠️ 09-17 死代码清洗留痕**：`shisi/wechat/command_handler.py`/`command_parser.py`（微信指令系统）已删除，正文已同步。

**最近更新:** 2026-09-20
**Python 版本:** ≥3.10 | **总文件:** ~511 .py 文件（含 tests/）

---

## 模块总览（2026-09-20 实测）

| 模块 | 文件数 | 路径 | 职责 | 状态 |
|------|--------|------|------|------|
| **shisi** | 115 | `shisi/` | DDD 核心域（角色/情感/记忆/故事线/知识库等，v2 死模块删除后口径） | ✅ 活跃 |
| **api** | 45 | `api/` | FastAPI 路由层（22 routers + app_factory/achievement_engine/state 等） | ✅ 活跃 |
| **my_character** | 21 | `my_character/` | 情感引擎 + 角色引擎 | ✅ 活跃 |
| **persona_extractor** | 13 | `persona_extractor/` | 人格提取与注入（+web_enricher 网络画像增强） | ✅ 活跃 |
| **observability** | 9 | `observability/` | 可观测性（日志/指标/追踪/健康检查/sentry/优雅停机） | ✅ 活跃 |
| **tools** | 10 | `tools/` | 工具系统（12 个内置工具） | ✅ 活跃 |
| **orchestrator** | 7 | `orchestrator/` | 编排器（主类/init/stream mixin/会话锁/语音检测/console_chat） | ✅ 活跃 |
| **character_card** | 6 | `character_card/` | 角色卡解析/验证/构建/集成 | ✅ 活跃 |
| **voice** | 6 | `voice/` | 语音合成（MiMo 唯一引擎，08-28 收敛） | ✅ 活跃 |
| **llm_provider** | 5 | `llm_provider/` | LLM 多供应商网关 | ✅ 活跃 |
| **security** | 5 | `security/` | 安全过滤与加密 | ✅ 活跃 |
| **proactive** | 5 | `proactive/` | 主动消息推送（ase_engine/scheduler/frequency/reflection） | ✅ 活跃 |
| **clone_training** | 4 | `clone_training/` | 克隆训练（数据清洗/数据提取/风格分析） | ✅ 活跃 |
| **multimodal** | 3 | `multimodal/` | 多模态处理（image_attachment/multimodal_processor） | ✅ 活跃 |
| **wechat_direct** | 5 | `wechat_direct/` | 微信直连（**每人独立通道**：connector_registry/channel_paths/peer_character/wechat_connector） | ✅ 活跃 |
| **plugins** | 2 | `plugins/` | 插件系统 | ✅ 活跃 |
| **cache** | 3 | `cache/` | LLM 缓存 + Redis 客户端 | ✅ 活跃 |
| **context** | 2 | `context/` | 上下文（世界书提供器） | ✅ 活跃 |
| **memory_ext** | 2 | `memory_ext/` | 记忆扩展（mem0 后端） | ✅ 活跃 |

> 文件数含 `__init__.py`；`weclone_adapter/` 已于 08-28 删除（克隆收敛为本地提取+JSON 上传）。

---

## shisi/ — DDD 核心域 (115 文件)

**入口:** `shisi/api/registry.py`（由 `api/app_factory.py` 调用 `setup_shisi` 装配）
**配置:** `shisi/config.py` / `shisi/migrations.py`

**子模块:**

| 子模块 | 职责 | 关键文件 |
|--------|------|----------|
| `api/` | shisi DDD 核心 plane 路由（31 端点已挂载） | `registry.py`, `affinity_routes.py`, `character_routes.py`, `emotion_stage_routes.py`, `memory_routes.py`, `persona_routes.py`, `stats_routes.py`, `sticker_routes.py`, `vital_signs_routes.py`, `common.py` |
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
| `voice/` | 语音 | `character_voice.py`, `emotion_tts.py`（EmotionTTS 仅余 VoiceEnhancer） |
| `wechat/` | 微信集成 | `proactive_messenger.py`, `sticker_adapter.py`（command_handler/command_parser 已于 09-17 删除） |
| `knowledge/` | 知识检索 | `retriever.py`, `character_knowledge_service.py`, `crawler_adapter.py` |
| `knowledge/legacy/` | RAGEngineV2（保留，被 tests/test_rag_engine.py 52 处引用） | `rag_engine.py` |
| `ase/` | 场景叙事 | `scene_narrator.py`, `trigger_engine.py` |
| `vault/` | 数据收集 | `collect_loop.py`, `_persona_adapter.py` |
| `stats/` | 统计 | `analytics.py` |

**依赖:** llm_provider, tools, voice, security, orchestrator
**被依赖:** api (通过 `shisi/api/registry.py:setup_shisi` 由 `app_factory` 装配)

---

## api/ — FastAPI 路由层 (45 文件)

**入口:** `api/run_api.py` → `api/app_factory.py:create_api_app()`

**结构:**
- 根目录: `app_factory.py`（应用工厂）, `run_api.py`（启动入口）, `main_routes.py`（模型/常量/Helper）, `health_routes.py`（健康检查）, `auth.py`/`auth_jwt.py`（认证，09-19 起 **JWT 优先**）, `database.py`（SQLAlchemy **8 表**）, `achievement_engine.py`（成就引擎，ADR-0014）, `byok.py`（W1 用户自带 Key 强制策略）, `consent.py`（W2 使用即同意协议）, `password_policy.py`（密码策略唯一真源 ≥8 含字母数字）, `deps.py`（依赖注入）, `session_manager.py`, `websocket_server.py`, `qrcode_store.py`, `path_security.py`, `runtime_config.py`
- `routers/` 22 个路由模块: `admin_routes`, `auth_routes`, `character_routes`, `chat_routes`, `clone_routes`, `emotion_routes`, `invite_routes`, `knowledge_routes`, `llm_providers_routes`, `memory_routes`, `mimo_voice_routes`, `misc_routes`, `persona_card_routes`, `personality_routes`, `safety_routes`, `storyline_routes`, `tools_routes`, `training_routes`, `users_routes`, `voice_routes`, `wechat_channel_routes`（每人独立通道，09-19）, `wechat_routes`
- `state/` 3 个状态模块: `safety_log`, `tool_history`, `training_state`

**实际挂载:** 18 个 `include_router` 调用 + `setup_shisi(app)` 装配，共 **215 业务端点 / 181 唯一路径**（2026-09-20 `create_api_app` 内省实扫；`len(app.routes)=219` 含 4 条框架路由）
**依赖:** shisi, security, llm_provider, database

---

## persona_extractor/ — 人格提取 (13 文件)

**职责:** 从对话中提取用户人格特征，注入角色回复；web_enricher 网络画像增强

**关键文件:**
- `fusion.py` — 人格融合主入口
- `models.py` — 数据模型
- `persona_bank.py` — 人格库
- `style_vectorizer.py` — 风格向量化
- `hexaco.py`, `dark_triad.py`, `mental_health.py` — 人格维度分析
- `web_enricher.py` — 网络人设增强（火爬虫/Crawl4AI）

**依赖:** llm_provider, shisi/memory
**被依赖:** shisi (通过 Orchestrator)

---

## voice/ — 语音合成 (5 模块 + __init__)

**职责:** 文本转语音。**MiMo 唯一引擎**（08-28 裁决 A：Edge-TTS/SoVITS/CosyVoice/Bert-VITS2 四 provider 与 voice_training.py 已删除）

**关键文件:**
- `mimo_tts_provider.py` — MiMo Cloud API（8 情感映射内置）+ Windows SAPI 本地兜底
- `tts_manager.py` — 单引擎管理
- `tts_provider_base.py` — Provider 抽象基类
- `audio_converter.py` — 音频格式转换（silk 编解码，pilk 可选依赖）
- `clone_data_manager.py` — 聊天克隆数据管理（克隆域，与 TTS 无关）

**依赖:** config (system.yaml voice 段)
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

## llm_provider/ — LLM 供应商 (5 文件)

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

## orchestrator/ — 优化编排器 (7 文件)

**职责:** 聊天流水线编排，组件初始化阶段化，SSE 流式输出，会话锁管理，语音检测

**关键文件:**
- `optimized_orchestrator.py` — 主类 `OptimizedOrchestrator`：`__init__` / 会话锁 / 上下文准备 / `process_message` / 健康检查（1050 行）
- `_init_mixin.py` — `_InitPhasesMixin`：`initialize` 拆分为 10 个 `_init_*` 阶段
- `_stream_mixin.py` — `_StreamPipelineMixin`：`process_message_stream` SSE 真流式/伪流式降级
- `session_locks.py` — 会话锁管理（`SessionLockManager`）
- `voice_detector.py` — 语音活动检测
- `console_chat.py` — 控制台聊天通道（08-28 自 main.py 迁入）

> **架构:** `OptimizedOrchestrator` 继承 `_InitPhasesMixin` + `_StreamPipelineMixin`，通过 `self.components` 共享状态。公共 API 100% 兼容，外部导入路径 `from orchestrator import Orchestrator` 不变。

**依赖:** shisi, llm_provider, security, tools, cache
**被依赖:** api (chat_routes)

---

## wechat_direct/ — 微信直连 (5 文件)

**职责:** 每人独立微信通道（2026-09-19 起多租户化：一人最多 2 条、全局上限 100、好友自选角色）

**关键文件:**
- `connector_registry.py` — `ConnectorRegistry`：(owner_user_id, slot) 键控注册表，ensure/disconnect/status/start_login/restore_on_boot，每会话 poll.lock 去重多 worker
- `channel_paths.py` — 磁盘路径唯一真源 `data/wechat_sessions/<uid>/slotN/`
- `peer_character.py` — 好友自选角色（(owner, peer)→card 落库 + 微信内「角色」指令菜单）
- `wechat_connector.py` — 连接器本体（1659 行）：回复拆分/追问引擎/图片语音 emoji/收包守卫
- `__init__.py`

**依赖:** api/database（通道会话表）, utils/project_paths
**被依赖:** api (chat/wechat/wechat_channel 路由), orchestrator, user_scheduler, proactive

---

## cache/ — 缓存层 (3 文件)

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

## clone_training/ — 克隆训练 (4 文件)

**职责:** 微信聊天数据清洗、数据提取、风格分析

**关键文件:**
- `data_cleaner.py` — 数据清洗
- `data_extractor.py` — 数据提取
- `style_analyzer.py` — 风格分析

> **注:** `dataset_builder.py`/`lora_trainer.py` 已删除（架构改为本地提取→上传→服务器分析）；`wechat_decrypt_source.py` 已于 08-28 删除（解密必须在用户本地环境进行，服务器不经手微信数据）。

**依赖:** 无
**被依赖:** api (clone_routes)

---

## context/ — 上下文 (2 文件，含 __init__)

**职责:** 世界书上下文提供

**关键文件:**
- `world_info_provider.py` — 世界书提供器

**依赖:** 无
**被依赖:** shisi, orchestrator

---

## memory_ext/ — 记忆扩展 (2 文件，含 __init__)

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

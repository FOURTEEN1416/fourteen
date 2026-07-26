# 后端业务模块地图

**最近更新:** 2026-07-13
**Python 版本:** ≥3.10 | **总文件:** ~309 .py 文件

---

## 模块总览

| 模块 | 文件数 | 路径 | 职责 | 状态 |
|------|--------|------|------|------|
| **shisi** | 96 | `shisi/` | 核心业务逻辑（角色/情感/记忆/故事线/知识库等） | ✅ 活跃 |
| **api** | 36 | `api/` | FastAPI 路由层 | ✅ 活跃 |
| **persona_extractor** | ~14 | `persona_extractor/` | 人格提取与注入 | ✅ 活跃 |
| **voice** | 11 | `voice/` | 语音合成 (TTS) | ✅ 活跃 |
| **observability** | 9 | `observability/` | 可观测性（日志/指标/追踪/健康检查） | ✅ 活跃 |
| **tools** | 10 | `tools/` | 工具系统（12 个内置工具） | ✅ 活跃 |
| **llm_provider** | 6 | `llm_provider/` | LLM 多供应商网关 | ✅ 活跃 |
| **security** | 5 | `security/` | 安全过滤与加密 | ✅ 活跃 |
| **proactive** | 5 | `proactive/` | 主动消息推送 | ✅ 活跃 |
| **character_card** | 6 | `character_card/` | 角色卡解析/验证/构建 | ✅ 活跃 |
| **clone_training** | 6 | `clone_training/` | 克隆训练（数据清洗/数据集构建/LoRA） | ✅ 活跃 |
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

**入口:** `shisi/api/` + `shisi/app.py`

**子模块:**

| 子模块 | 职责 | 关键文件 |
|--------|------|----------|
| `api/` | 旧版 API 路由 (49 endpoints) | `app.py`, `v2/` |
| `character/` | 角色管理 | `character_card_v2.py`, `persona.py` |
| `affinity/` | 好感度系统 | `affinity_manager.py` |
| `emotion_stage/` | 情感阶段 | `emotion_engine.py` |
| `memory/` | 记忆管理 | `memory_manager.py`, `recall.py` |
| `sticker/` | 表情包 | `sticker_service.py` |
| `voice/` | 语音 | `voice_service.py` |
| `wechat/` | 微信集成 | `wechat_service.py` |
| `storyline/` | 故事线 | `storyline_manager.py` |
| `knowledge/` | 知识库 | `retriever.py`, `knowledge_base.py` |
| `stats/` | 统计 | `stats_service.py` |
| `vital_signs/` | 生命指标 | `vital_signs.py` |
| `infrastructure/` | 持久化 (SQLite) | `database.py`, `repository.py` |
| `vault/` | 知识库 | `collect_loop.py`, `_persona_adapter.py` |

**依赖:** llm_provider, tools, voice, security, orchestrator
**被依赖:** api (旧路由通过 deps.py 调用)

---

## api/ — FastAPI 路由层 (36 文件)

**入口:** `api/run_api.py` → `api/app_factory.py`

**结构:**
- 8 个旧路由模块: `_misc_routes`, `_chat_routes`, `_personality_routes`, `_users_routes`, `_training_routes`, `_tools_routes`, `_safety_routes`, `_clone_routes`
- 12 个新路由模块: `routers/auth_routes`, `routers/admin_routes`, `routers/invite_routes`, `routers/character_routes`, `routers/voice_routes`, `routers/mimo_voice_routes`, `routers/storyline_routes`, `routers/wechat_routes`, `routers/emotion_routes`, `routers/memory_routes`, `routers/knowledge_routes`, `routers/persona_card_routes`

**依赖:** shisi, security, llm_provider, database
**导出:** ~146 API endpoints

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

> **注:** 记忆系统已整合到 `shisi/memory/` 子模块中（见 shisi/ 总览），
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
- `opencode_zen_provider.py` — OpenCode Zen API
- `prompt_template_manager.py` — 提示模板管理
- `llm_gateway.py` — LLM 网关

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
> 包含 `retriever.py`（检索器）和 `knowledge_base.py`（知识库）。

---

## orchestrator/ — 优化编排器 (5 文件)

**职责:** 聊天流水线编排，组件初始化阶段化，SSE 流式输出，会话锁管理，语音检测

**关键文件:**
- `optimized_orchestrator.py` — 主类 `OptimizedOrchestrator`：`__init__` / 会话锁 / 上下文准备 / `process_message` / 健康检查（920 行）
- `_init_mixin.py` — `_InitPhasesMixin`：`initialize` 拆分为 9 个 `_init_*` 阶段（core/emotion/persona/tone → memory/ASE/scheduler/tools/RAG → world_info → character_card → voice → memory_ext → persona_extractor → vault）
- `_stream_mixin.py` — `_StreamPipelineMixin`：`process_message_stream` SSE 真流式/伪流式降级
- `session_locks.py` — 会话锁管理（`SessionLockManager`）
- `voice_detector.py` — 语音活动检测

> **架构:** `OptimizedOrchestrator` 继承 `_InitPhasesMixin` + `_StreamPipelineMixin`，通过 `self.components` 共享状态。公共 API 100% 兼容，外部导入路径 `from orchestrator import Orchestrator` 不变。
>
> **注:** 根目录 `orchestrator.py` 已删除，编排逻辑统一由 `orchestrator/` 包提供。

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
- `integration.py` — 集成接口

**依赖:** 无
**被依赖:** shisi, api

---

## clone_training/ — 克隆训练 (6 文件)

**职责:** 微信聊天数据清洗、数据集构建、LoRA 训练

**关键文件:**
- `data_cleaner.py` — 数据清洗
- `data_extractor.py` — 数据提取
- `dataset_builder.py` — 数据集构建
- `lora_trainer.py` — LoRA 训练器
- `style_analyzer.py` — 风格分析

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
  │           │              ├── shisi/memory/ ←── memory_ext/ (mem0)
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

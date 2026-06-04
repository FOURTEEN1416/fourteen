# 后端业务模块地图

**最近更新:** 2026-06-03
**Python 版本:** 3.12 | **总文件:** ~324 .py 文件

---

## 模块总览

| 模块 | 文件数 | 路径 | 职责 | 状态 |
|------|--------|------|------|------|
| **shisi** | 96 | `shisi/` | 核心业务逻辑（角色/情感/记忆/故事线等） | ✅ 活跃 |
| **api** | 36 | `api/` | FastAPI 路由层 | ✅ 活跃 |
| **persona_extractor** | 12 | `persona_extractor/` | 人格提取与注入 | ✅ 活跃 |
| **voice** | 11 | `voice/` | 语音合成 (TTS) | ✅ 活跃 |
| **memory** | 11 | `memory/` | 记忆系统 | ✅ 活跃 |
| **tools** | 9 | `tools/` | 工具系统 | ✅ 活跃 |
| **observability** | 8 | `observability/` | 可观测性（日志/指标/追踪） | ✅ 活跃 |
| **llm_provider** | 6 | `llm_provider/` | LLM 多供应商网关 | ✅ 活跃 |
| **security** | 5 | `security/` | 安全过滤与加密 | ✅ 活跃 |
| **weclone_adapter** | 3 | `weclone_adapter/` | 微信克隆适配 | ✅ 活跃 |
| **proactive** | 3 | `proactive/` | 主动消息推送 | ✅ 活跃 |
| **multimodal** | 2 | `multimodal/` | 多模态处理 | ✅ 活跃 |
| **wechat_direct** | 2 | `wechat_direct/` | 微信直连 | ✅ 活跃 |
| **plugins** | 2 | `plugins/` | 插件系统 | ✅ 活跃 |
| **rag_engine** | 2 | `rag_engine/` | RAG 检索引擎 | ✅ 活跃 |
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

**依赖:** llm_provider, memory, rag_engine, tools, voice, security
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

## persona_extractor/ — 人格提取 (12 文件)

**职责:** 从对话中提取用户人格特征，注入角色回复

**关键文件:**
- `extractor.py` — 人格提取主逻辑
- `profiler.py` — 用户画像
- `injector.py` — 人格注入

**依赖:** llm_provider, memory
**被依赖:** shisi (通过 Orchestrator)

---

## voice/ — 语音合成 (11 文件)

**职责:** 文本转语音，多 TTS 引擎支持

**关键文件:**
- `tts_engine.py` — TTS 引擎抽象
- `mimo_tts.py` — MiMo TTS 实现
- `baidu_tts.py` — 百度 TTS 实现
- `voice_cloning.py` — 声音克隆

**依赖:** config (emotion.yaml)
**被依赖:** api (voice_routes, mimo_voice_routes)

---

## memory/ — 记忆系统 (11 文件)

**职责:** 短期/长期记忆管理，向量化存储

**关键文件:**
- `memory_manager.py` — 记忆管理器
- `vector_memory.py` — 向量记忆
- `recall.py` — 记忆召回

**依赖:** llm_provider, ChromaDB
**被依赖:** shisi, persona_extractor

---

## tools/ — 工具系统 (9 文件)

**职责:** 工具定义、调度、执行

**关键文件:**
- `tool_dispatcher.py` — 工具调度器
- `tool_registry.py` — 工具注册表
- `builtin_tools.py` — 内置工具

**依赖:** shisi
**被依赖:** Orchestrator

---

## observability/ — 可观测性 (8 文件)

**职责:** 日志、指标、链路追踪

**关键文件:**
- `logger.py` — 结构化日志
- `metrics.py` — Prometheus 指标
- `tracer.py` — OpenTelemetry 追踪
- `audit_logger.py` — 审计日志

**依赖:** 无外部（标准库 + OpenTelemetry）
**被依赖:** 全局

---

## llm_provider/ — LLM 供应商 (6 文件)

**职责:** 多 LLM 供应商统一接入

**关键文件:**
- `gateway.py` — `MultiProviderGateway` 主入口
- `openai_provider.py` — OpenAI 兼容 API
- `deepseek_provider.py` — DeepSeek API
- `claude_provider.py` — Anthropic Claude API

**依赖:** config/llm_providers.json
**被依赖:** shisi, persona_extractor, memory, rag_engine

---

## security/ — 安全模块 (5 文件)

**职责:** 内容安全、PII 匿名化、注入检测、加密

| 文件 | 职责 |
|------|------|
| `content_safety_filter.py` | 内容安全过滤 |
| `pii_anonymizer.py` | PII 匿名化 |
| `prompt_injection_detector.py` | 提示注入检测 |
| `encryption_manager.py` | 加密管理 |
| `security_manager.py` | 安全管理器 (组合以上) |

**依赖:** 无
**被依赖:** Orchestrator (流水线第 1-3 步)

---

## rag_engine/ — RAG 引擎 (2 文件)

**职责:** 检索增强生成

**关键文件:**
- `retriever.py` — 检索器主逻辑
- `embedder.py` — 嵌入生成

**依赖:** llm_provider, ChromaDB, knowledge_vault/
**被依赖:** Orchestrator

---

## 模块依赖图

```
api/ ←── shisi/ ←── llm_provider/ ←── 外部 LLM API
  │           │            │
  │           ├── memory/ ←┘
  │           │      │
  │           │      └── ChromaDB (向量存储)
  │           │
  │           ├── rag_engine/ ←── knowledge_vault/
  │           │      │
  │           │      └── llm_provider/
  │           │
  │           ├── tools/
  │           │
  │           ├── persona_extractor/ ←── llm_provider/
  │           │
  │           └── voice/
  │
  ├── security/ ←── 无依赖 (独立)
  │
  ├── voice/ ←── config/
  │
  ├── observability/ ←── 无依赖 (独立)
  │
  └── my_character/ ←── shisi/
```

---

## 相关地图

- [BACKEND.md](BACKEND.md) — API 层与模块的衔接
- [ARCHITECTURE.md](ARCHITECTURE.md) — 模块在整体架构中的位置

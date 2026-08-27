# 📚 Tests + 根入口 模块阅读报告

**读取进度**：tests/ 65文件 ✅ + 根目录2文件 ✅ | **读取时间**：2026-08-26  
**模块定位**：全量测试套件 + main.py融合主入口 + user_scheduler多用户调度器  

---

## 🧠 测试数量真值验证

```
pytest --collect-only 实测: 1035 tests collected (3.21s)
与文档基线对照: AGENTS.md记载"1025 Python + 79 前端 = 1104"
实测1035 vs 文档1025 → +10个测试漂移（后续新增测试未同步文档）
前端79个另计。总口径应为 1035 Python + 79 前端
⚠️ 建议更新AGENTS.md §4.3 测试基线数字
```

---

## 🧠 tests/ 测试文件分类全景（58根级+7 core子目录）

### 大型集成测试（>15KB，7个）
| 文件 | 覆盖域 |
|------|--------|
| test_memory_pipeline.py (27KB) | 记忆管线V1/V2/Optimized融合 |
| test_ops_lifecycle.py (26KB) | 运维生命周期(启动/关闭/信号) |
| test_admin_ops.py (24KB) | 管理员操作流 |
| test_rag_engine.py (24KB) | RAGEngineV2/BM25/Reranker/HallucinationGuard深度测试 |
| test_llm_providers_routes.py (23KB) | LLM供应商管理API |
| test_proactive.py (19KB) | ASE主动消息引擎全套 |
| test_memory.py (18KB) | 三层记忆 |

### 中型功能测试（8-15KB，9个）
test_invite_codes(邀请码17.6KB)/test_voice_manager(TTS降级14KB)/test_tools(工具系统13KB)/test_main_stream(SSE主流式11.8KB)/test_llm_config_verification(LLM配置验证11.8KB)/test_cache(Redis缓存10.6KB)/test_shisi_features/test_shisi_character/test_bindings(微信绑定)

### 安全专项（6个）
test_p0_fixes(P0修复回归)/test_pii_anonymizer/test_prompt_injection/test_content_safety/**test_production_hardening(生产加固)**/test_config_permissions

### shisi DDD专项（12个）
test_shisi_affinity/test_shisi_knowledge/test_shisi_ase/test_shisi_infrastructure/test_shisi_vault/test_shisi_migrations + **tests/core/7个DDD核心单测**(affinity_level/character_aggregate/character_id/emotion_type/emotional_state/persona_profile六模型逐一覆盖)

### 其他域测试（24个）
wechat×2/connection_lifecycle/request_context_isolation/session_locks/voice_detector/storyline/sticker_recommend/web_enricher/config_manager/image_gen_tool/tool_orchestration/voice_training/crawler_adapter/tool_health/affinity_mapper/character_crawler/character_voice/emotion_tts/audio_converter/time_awareness/emotion_recommender/integration/api_routes/modules/character/persona_injection/emotion_affinity/llm_provider等

### conftest.py
全局fixture（1.35KB轻量）

---

## 🧠 main.py（21.9KB）— 融合统一版主入口

**三版融合自述**：
- V1: 微信直连通道+自动重连
- V2: 可观测性(链路追踪+指标+健康)+安全层(加密)+工具系统+RAG+API
- Optimized: 并行初始化编排器+结构化日志+--log-level
- **双编排器模式**: "full"(V2完整流程) / "fast"(Optimized简洁流程)
- Fusion配置节: config/system.yaml → fusion

**CLI参数**：--console控制台聊天/--no-api/--no-scheduler/--log-level DEBUG/--clone wxid_xxx风格克隆(提示词注入模式)/--init-only仅初始化

**关键函数**：parse_args/load_fusion_config/run_clone_pipeline/run_console_chat/run_wechat_mode/_start_api_service/_create_proactive_sender(ws+wechat双holder)/main/_run_orchestrator

## 🧠 user_scheduler.py（14.2KB）— 多微信用户核心调度器

**核心设计（docstring明示）**：
- 每个用户独立EmotionEngine实例（情感完全隔离——对应L3教训）
- 所有用户共享LLM/安全层/工具/RAG（省资源）
- 消息进来→换入用户专属engine→处理→换出
- 控制台前端API管理用户

UserInstance数据类(affinity_level/affinity_name/primary_emotion属性) + UserManager(get_or_create/remove_user/reset_user/set_user_character换角色/get_all_users/health_check)

---

## ⚠️ 发现的文档漂移（需登记）

| # | 漂移项 | 实况 | 建议 |
|---|--------|------|------|
| 1 | 测试基线数字 | AGENTS.md写1025 Python,实收1035 | 更新为1035 |
| 2 | shisi/api路由未入CODE_GRAPH 204端点统计? | shisi自有api/(11+v2 6文件)挂载在app_factory之外经registry | 核对CODE_GRAPH §4.2口径 |

---

## 📋 已读文件列表

tests/(65): conftest.py + 57根级test_*.py + core/7(test_affinity_level/test_character_aggregate/test_character_id/test_emotion_type/test_emotional_state/test_persona_profile/__init__)  
根目录(2): main.py, user_scheduler.py
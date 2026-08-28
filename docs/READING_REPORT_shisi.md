# 📚 Shisi（十四）DDD 核心模块阅读报告

**读取进度**：124/124 文件 ✅ 全部穷举阅读 | **读取时间**：2026-08-26  
**模块定位**：十四APP核心功能融合模块 — DDD四层架构（核心层/应用层/基础设施层/API层）+ 12业务子域  

---

## 🧠 架构总览

```
shisi/ (v0.1.0 "十四功能融合模块")
├── config.py          YAML加载+AIYU_环境变量覆盖(9个映射)
├── migrations.py      12张表建表SQL+WAL模式+schema版本记录
├── core/              ★DDD核心层: models(6)+ports(2)+services(3)
├── application/       ★应用服务层: 7个Service
├── infrastructure/    ★基础设施: migration(3)+persistence(3)
├── api/               v1路由(11文件,setter注入) + v2路由(6文件,Depends注入)
└── 业务子域×12: affinity/emotion_stage/character/knowledge/memory/
                sticker/stats/storyline/vault/vital_signs/voice/wechat/ase
```

---

## 🧠 core/ — DDD核心层（13文件）

| 文件 | 关键内容 |
|------|----------|
| `models/affinity_level.py` | AffinityLevel九级IntEnum(STRANGER→BOND),display_name中文(陌生人/认识/朋友/好朋友/知己/暧昧/恋人/热恋/羁绊),threshold阈值[0,10,25,50,80,120,200,350,500] |
| `models/character_aggregate.py` (7.7KB) | **角色聚合根**（Pydantic）：id/name/persona/emotional_state/storyline_config/version/source_format；update_emotion()关键词情感检测(正面词+1.0/负面词-0.5,基础delta 0.2)+等级自动晋升循环；build_system_prompt组装(角色设定→人设→当前状态→知识库→剧情线→历史)；from_legacy_card旧卡迁移(personality_traits五维/core_anchors/affinity兼容) |
| `models/character_id.py` | CharacterId frozen dataclass值对象 |
| `models/emotion_type.py` | EmotionType十情绪枚举(与my_character一致) |
| `models/emotional_state.py` | EmotionalState数据类：primary_emotion/intensity/energy/affinity_level/affection_points,__post_init__钳制0~1 |
| `models/persona_profile.py` | PersonaProfile **10维画像**：性格5维(warmth/playfulness/independence/jealousy/stubbornness)+风格5维(formality/emoji_frequency/sentence_length/emotional_expression/humor)+core_anchors锚点列表；to_prompt_segment中文渲染 |
| `ports/character_repository.py` | CharacterRepository Protocol接口(get_by_id/get_active/list_all/save/set_active/delete) |
| `services/emotion_detector.py` | detect_emotion关键词映射/calculate_affection_delta正负词计分 |
| `services/prompt_builder.py` | build()组装顺序：剧情线上下文(**含engine.tick时间推进**)→RAG知识库(top_k=3,无索引自动建)→角色prompt；两处均try/except非阻塞降级 |

## 🧠 application/ — 应用服务层（7文件）

| 文件 | 关键内容 |
|------|----------|
| `character_service.py` | 薄封装repo+build_prompt |
| `knowledge_service.py` (6.8KB) | ShisiKnowledgeAdapter：BM25检索返回兼容字典,character_id显式传入(set_character_id仅为旧调用保留) |
| `memory_service.py` (9.5KB) | ShisiMemoryService兼容MemoryPipeline接口；**核心记忆存储复用memory/legacy/**,收藏转发用FavoriteManager/ForwardManager；配置开关在shisi适配层与_legacy行为间切换 |
| `migration_service.py` | MigrationService包装infrastructure/migration runner |
| `persona_service.py` (13.6KB) | **人格应用服务**：不再直接用PersonaEngine完整system prompt，改为①CharacterAggregate+prompt_builder生成基础prompt②把PersonaEngine五层对齐规则(情感/风格/约束/身份自指/锚点保护)作为"注入层"附加——新旧人格体系桥接的关键设计 |
| `prompt_service.py` | PromptService薄封装 |

## 🧠 infrastructure/ — 基础设施层（7文件）

- `migration/migration_runner.py`：characters→characters_v2迁移(shutil备份+SQLite读写)
- `migration/rollback_runner.py`：回滚执行器
- `persistence/sqlite_repository.py` (6.3KB)：CharacterRepository的SQLite实现(characters_v2表)
- `persistence/schema.py`：建表语句

---

## 🧠 12业务子域速览

### affinity/ 好感度（5文件）
- **DecayEngine**：好感度时间衰减(grace_period_days宽限期)
- **AffinityEnhancer**：SQLite记录affinity_records/unlocks/audit三表,配置尺度[min,max]默认0~100
- **AffinityMapper**：⭐消除magic number设计——emotion维度affection_points满级500(BOND阈值)线性映射到shisi维度max_value,公式`affection_points/BOND_THRESHOLD*max_value`
- **UnlockManager**：阈值解锁事件(UnlockEvent+回调)

### emotion_stage/ 情感阶段（4文件）
- StageDefinition/EmotionStageConfig配置、StageChangeEvent事件分发(EventDispatcher)、EmotionStageEngine状态机(current_stage/stage_index持久化到emotion_stage_state表)

### character/ 角色卡子系统（9文件）
- **png_codec.py**：PNG tEXt chunk编解码(SillyTavern规范:chara关键字+base64 JSON,is_png探测/embed提取/has_chara_chunk)
- **character_card_v2.py** (11.9KB)：三解析器(CharaCardV2Parser/AiyuPromptsParser十四格式/SillyTavernParser)+ParserDispatcher自动分发+to_persona_config转换
- models.py标注@deprecated→请用core.models.CharacterAggregate
- manager.py：CharacterManager(OrderedDict LRU缓存+sqlite持久化)
- importer/exporter/validator(注入模式检测_INJECTION_PATTERNS+sanitize_text)/store

### knowledge/ 知识检索（4+2文件）
- **retriever.py**：KnowledgeChunk/RetrievalResult/KeywordRetriever(TF评分零依赖)/BM25Retriever
- **character_knowledge_service.py** (14KB)：从CharaCardV2/Aggregate提取知识建索引+磁盘缓存避免重建+get_knowledge_context注入LLM
- crawler_adapter.py：角色导入后自动索引+调character_crawler补全网络信息(_split_text 500字符overlap50分块)
- **knowledge/legacy/rag_engine.py** (11.3KB)：⚠️legacy=历史命名仍在活跃使用(docstring明确"不要按字面意思当作待删除")——BM25Index/Reranker重排/ContextBudgetMgr上下文预算/HallucinationGuard幻觉防护/RAGEngineV2

### memory/ 记忆增强（3+20文件）
- favorite_manager.py(FavoriteManager收藏)/forward_manager.py(ForwardManager转发)
- **memory/legacy/**（⚠️同上：活跃使用,MemoryService底层依赖）：
  - **memory_pipeline.py (34.5KB最大)**：V1/V2/Optimized融合统一编排器,自包含三层记忆(工作/情景/语义)
  - working_memory(deque短期)/episodic_memory(向量+摘要归档)/semantic_memory(事实去重+冲突检测+置信度)
  - structured_memory.py (23.7KB)：SQLite结构化存储(user_facts等),全局实例注册表(_register/_unregister/_close_all)
  - vector_memory.py (16.6KB)：ChromaDB async+sync兼容(chat_history三种向量记忆),_silence_stdout抑制C++库输出
  - fact_extractor(LLM/规则双模事实提取)/conflict_detector(向量相似度冲突)/forgetting_manager(艾宾浩斯指数衰减分层λ)/importance_scorer(关键词+情感+信息密度+时间衰减)/diary_summarizer(LLM+模板双模日记)/conversation_summarizer(200字压缩)/reflection_engine(高层次反思洞察prompt)/cross_session_reasoner(未来事件"下周去北京"跟踪)

### storyline/ 剧情线（4文件）
- config.py：StorylineConfig(StageBehaviorRule行为规则/StageStyleRule风格规则/StageTiming每句话+10分钟/EndingConfig结局+回忆柜)
- detector.py：自动检测人设文本中的时间/阶段/结局关键词建议开启
- engine.py (14.1KB)：运行时引擎(StorylineState进度/StageTransition演进/is_ended结局检测/tick时间推进/get_storyline_context注入)

### vault/ 知识宝库（3文件）
- _persona_adapter.py：从CharaCardV2提取五维PersonaFeatures(core_anchors/speaking_style/background/relationship/behavior_rules)
- collect_loop.py：事件驱动+定时批量收集队列→PersonaFeatures转KnowledgeChunk→索引注入

### vital_signs/ 生命体征（3文件）
- EMOTION_VITAL_MAP七情绪生理映射(生气心率+30体温+0.3呼吸+5最剧烈)
- VitalSignsEngine：heart_rate/temperature/breath_rate状态机(vital_signs_state表)

### voice/ 语音增强（3文件）
- character_voice.py：CharacterVoiceManager角色音色绑定(JSON持久化)
- emotion_tts.py：VoiceEnhancer语音增强+EmotionVoiceMapper情感→TTS参数映射

### sticker/ 表情包（6文件）
- StickerManager(SQLite stickers+character_stickers双表,unlock_threshold解锁机制)/EmotionRecommender情感推荐/StickerImporter(zip导入)/DefaultStickerProvider默认包/check_sticker_safety(文件名+元数据正则安全审查)

### stats/ 统计（2文件）
- AnalyticsService(Counter聚合分析)

### wechat/ 微信集成（5文件）
- command_parser/command_handler(WeChatCommandParser/Handler指令解析处理,依赖注入五个管理器)
- proactive_messenger.py：增强版主动消息引擎,**接入剧情线和场景叙事器**,保留原WeChatProactiveMessenger接口兼容
- sticker_adapter.py：表情包发送适配

### ase/ 自治场景引擎（3文件）
- trigger_engine.py (9KB)：五种触发器——TimeTrigger(剧情时间点)/StageTrigger(进出阶段)/AffinityTrigger(好感度阈值)/EventTrigger(特定内容)/IdleTrigger(长时间未回复)
- scene_narrator.py：基于剧情阶段+时间+角色状态生成场景旁白和主动消息

---

## 🧠 api/ — shisi自己的路由层（18文件）

### v1风格（11文件,setter注入模式）
registry.py：**AiyuRegistry服务注册表**(setup_shisi装配全模块+_mount_routes挂载app)——api.deps.shisi_reg即此对象
各routes文件通过`set_xxx()`模块级setter注入管理器(affinity_routes/character_routes/emotion_stage_routes/memory_routes/persona_routes/stats_routes/sticker_routes/training_routes/vital_signs_routes)

### v2 DDD风格（6文件,Depends注入模式）
- schemas.py：CreateCharacterRequest等9个Pydantic模型
- character_routes (6.1KB)/persona_routes/migration_routes/health_routes
- 依赖注入：get_character_service() Depends链(repo→service)

---

## 🔧 主要设计模式

### 1. DDD四层 + 防腐层
- core(models/ports/services) → application(用例编排) → infrastructure(仓储实现) → api(HTTP边界)
- PersonaService是新旧人格体系的防腐桥接层
- ShisiMemoryService/ShisiKnowledgeAdapter是记忆和知识的适配层

### 2. legacy命名澄清（重要真值）
- `memory/legacy/` 和 `knowledge/legacy/` 的docstring**双重声明**："legacy是历史命名（迁移自原根目录），仍在活跃使用，不要按字面意思当作待删除"
- 与AGENTS.md §0 Owner Map描述完全一致

### 3. 双API世代共存
- v1：setter模块级注入（简单直接）
- v2：FastAPI Depends协议端口绑定（可测试性更强），代表迁移方向

### 4. 非阻塞降级贯穿
- prompt_builder中剧情线/RAG失败仅warning不阻塞对话
- 所有外部依赖均有try/except兜底

---

## ⚠️ 关键技术要点

### 数据库表全景（migrations.py 12张表）
characters / characters_v2 / affinity_records / affinity_unlocks / affinity_audit / emotion_stage_state / stickers / character_stickers / vital_signs_state / memory_favorites / memory_recycle_bin(回收站restore_before) / shisi_schema_version

### 与其他模块的集成点
| 集成对象 | 方式 |
|---------|------|
| api/app_factory | deps.shisi_reg=AiyuRegistry,/api/shisi/status探测13管理器 |
| orchestrator._after_process | ase.on_chat经由此链路 |
| persona_extractor.web_enricher | 写入CharacterKnowledgeService BM25 |
| tools/builtin/character_crawler_tool | crawler_adapter调用爬虫结果入库 |
| my_character/PersonaEngine | PersonaService五层规则注入层保留其能力 |
| proactive/scheduler | wechat/proactive_messenger剧情线感知主动消息 |

---

## 📋 已读覆盖统计（完整124文件）

根3 + core13 + application7 + infrastructure7 + api18 + affinity5 + emotion_stage4 + character9 + knowledge6 + memory23 + sticker6 + stats2 + storyline4 + vault3 + vital_signs3 + voice3 + wechat5 + ase3 = **124 ✅**
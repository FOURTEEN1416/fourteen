# 📚 Orchestrator 模块阅读报告

**读取进度**：6/6 文件 ✅ 已全部穷举阅读  
**读取时间**：2026-08-26  
**覆盖范围**：orchestrator/ 目录下全部6个 Python 源码文件  

---

## 🧠 核心组件概览

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出入口 | `OptimizedOrchestrator` 主类<br>`Orchestrator = OptimizedOrchestrator`（向后兼容别名）<br>`from orchestrator import Orchestrator` 继续可用<br>`__all__ = ["OptimizedOrchestrator", "Orchestrator", "detect_voice_request", "SessionLockManager"]` |
| `optimized_orchestrator.py` | 优化版对话编排器 | `fast 模式`核心流程：安全→PII脱敏→注入检测→情感→记忆→RAG→LLM→输出安全→存储→ASE<br>`_InitPhasesMixin`：9个_init_*阶段化拆分<br>`_StreamPipelineMixin`：SSE流式处理<br>`_tool_intent_names`：零成本规则筛选工具意图<br>`_run_tools_if_needed`：并行执行互不依赖工具<br>`_prepare_context`：共享预处理逻辑<br>`_after_process`：共享后处理（after_chat→ASE→好感度同步）<br>`process_message`：完整消息处理流程（30s超时保护）<br>`health_check`：组件健康检查<br>`test_voice_pipeline`：端到端语音管线测试 |
| `session_locks.py` | 会话锁管理 | `per-session asyncio.Lock`系统<br>线程安全：`threading.Lock`保护内部字典<br>TTL过期清理：1小时无使用后清理<br>最大缓存数限制：1000个session<br>`_cleanup_expired_locks`：定期清理过期锁<br>`active_lock_count`属性 |
| `voice_detector.py` | 语音触发检测 | 5级触发Tier系统：<br>Tier1：直接命令词（高置信度）<br>Tier2：欲望/请求模式<br>Tier3：情感修饰+说<br>Tier4：能力询问<br>Tier5：上下文触发（排除误触发）<br>否定排除：`_RE_NEGATION`（不要/不想/懒得/算了）<br>文本偏好：`_RE_TEXT_PREFER`（文字就/才/更好/打字）<br>预编译正则优化 |
| `_init_mixin.py` | 初始化阶段混入 | 9个阶段 `_init_*`：组件注册/配置/加载/验证<br>通过 `self.components` 与主类共享状态<br>Mixin通过 `initialize()` 阶段化拆分 |
| `_stream_mixin.py` | 流式管道混入 | `process_message_stream` SSE流式处理<br>通过 `self.components` 与主类共享状态<br>向后兼容100% |

---

## 🔧 主要设计模式

### 1. Mixin架构
- **`_InitPhasesMixin`**：9个_init_*阶段（组件注册→配置→加载→验证→...）
- **`_StreamPipelineMixin`**：SSE流式处理复用
- **共享状态**：`self.components` dict在主类和Mixin间传递
- **100% API兼容**：Mixin不改写公共接口

### 2. 会话锁系统
- **per-session隔离**：不同session完全并行处理
- **同一session串行**：保证情感引擎状态一致性
- **TTL过期机制**：1小时无使用后自动清理
- **内存优化**：
  - 最大缓存数1000
  - 每100次访问触发一次清理
  - 超过限制清理最久未访问的

### 3. 工具意图零成本筛选
- **`_tool_intent_names`**：基于关键词匹配的规则筛选
- **13大意图组**：weather/search/calendar/calculator/set_reminder/query_reminders/time_awareness/memory/character_card/web_summary/image_gen/scheduler
- **普通聊天不额外调用模型**：无明确工具意图时返回空集合
- **并行执行**：`asyncio.gather(*(_dispatch(tc) for tc in tool_calls))`

### 4. 预处理/后处理共享逻辑
- **`_prepare_context`**：并行任务（人格/情感/记忆/RAG）→ prompt组装→工具调用
  - effective_user_id：`f"{character_id}:{session_id}"`（多用户/多角色隔离）
  - 并行执行：`asyncio.gather(*tasks.values(), return_exceptions=True)`
  - PersonaExtractor设置→情感引擎→记忆检索→RAG检索
  - system prompt组装→角色卡注入→工具调用
- **`_after_process`**：after_chat→ASE on_chat→好感度同步
  - 后台线程执行after_chat避免死锁
  - 好感度同步：`mapper.sync(character_id, affection_points, reason, source)`

### 5. 安全与注入检测
- **安全检查**：`components["safety"].check_input(user_msg)`
- **PII脱敏**：`components["pii"].anonymize(user_msg)`
- **注入检测**：`components["injection"].detect(user_msg_clean)`
- **输出安全**：`components["safety"].check_output(reply)`

---

## ⚠️ 关键技术要点

### 消息处理完整流程
```
process_message(user_msg, session_id, character_id)
  ↓
lock _get_session_lock(session_id)
  ↓
user级 LLM gateway（user_id/user_llm_config）
  ↓
安全检查 + PII脱敏 + 注入检测
  ↓
_prepare_context（并行：人格/情感/记忆/RAG）
    ↓
    → emotion_state + system_prompt + chat_history
    → persona_enhancement + rag_context
    → world_info动态注入
    → 角色卡人设动态注入（v3.1）
    → tool_results（工具调用）
    → system_prompt最终组装
  ↓
主 LLM 对话（30s超时保护，用户级或全局gateway）
  ↓
一致性检查 check_and_correct_reply（my_character/consistency_checker）
  ↓
计数反诘 _counter_rebuttal.check_and_increment
  ↓
输出安全 check_output
  ↓
_shared后处理 _after_process（after_chat→ASE→好感度同步）
  ↓
语音合成（用户明确要求时触发）
  ↓
返回结果{reply, emotion, process_time, voice?}
```

### 角色卡人设动态注入（v3.1）
- **缓存**：`_character_persona_cache: dict[str, str]`（character_id→片段字符串）
- **清除**：`invalidate_character_persona_cache(character_id)`
- **加载**：`_load_character_persona_segment(character_id)`：
  - `character_id`为空/“default”/“demo”→返回空串
  - 首命中缓存
  - 直接按 `{character_id}.json` 查找
  - 遍历 `config/characters/` 匹配JSON内部id字段
  - `normalize_character_card`展平嵌套格式
  - 构造人设片段（名字/描述/锚点/性格/风格/口头禅/场景/回复示例）
  - 缓存（最多100个防止膨胀）
- **强制优先级**：`# 当前必须扮演的角色（最高优先级）`

### 会话情绪引擎隔离
- **`_get_request_emotion_engine`**：返回请求所属情绪引擎
- **避免角色切换继承**：`key = f"{session_id}::{character_id}"`
- **测试替身**：`if not isinstance(template, EmotionEngine): return template`
- **外部显式传入**：`if explicit_engine is not None: return explicit_engine`

---

## 📋 已读文件列表（完整）

1. __init__.py
2. optimized_orchestrator.py
3. session_locks.py
4. voice_detector.py
5. _init_mixin.py
6. _stream_mixin.py
# 📚 Security + Observability 模块阅读报告

**读取进度**：14/14 文件 ✅ 全部穷举阅读 | **读取时间**：2026-08-26  
**模块定位**：安全防护（内容安全/PII脱敏/注入检测/加密）+ 可观测性（日志/追踪/指标/配置/健康/优雅关闭/Sentry）  

---

## 🧠 security/ 核心组件（5文件）

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出 | ContentSafetyFilter/SafetyCategory/SafetyResult/EncryptionManager(含两类Error)/PIIAnonymizer/PromptInjectionDetector |
| `content_safety.py` (9.7KB) | **内容安全过滤器** | 三类风险正则库：SELF_HARM_PATTERNS(自残)/VIOLENCE_PATTERNS(暴力)/PORN_PATTERNS(色情)；**自残干预热线硬编码**："400-161-9995（全国24小时）你不是一个人"；SafetyCategory枚举+SafetyResult结构化；双模式：_quick_scan规则快速扫描→_llm_classify LLM语义分类(ThreadPoolExecutor 2线程,模块级共享)；check_input/check_output双向检查；safe_alternative()安全替代回复生成；_log_safety_event安全事件日志 |
| `encryption.py` (2.6KB) | 加密管理器 | key从环境变量AI_GF_ENCRYPTION_KEY读取；enabled默认False；encrypt/decrypt支持associated_data(AEAD)；DecryptionError带ciphertext_preview；is_available可用性探测 |
| `pii_anonymizer.py` (5.1KB) | **PII脱敏器** | anonymize(text)→(脱敏文本,pii_map)；recover/deanonymize还原(逐个替换处理相同掩码碰撞)；pii_list_to_map列表转映射不暴露明文；掩码规则：phone前3+****+后4/id_card同/bank_card****+后4/email前2+***@域名/其他全****；正则识别电话/身份证/银行卡/邮箱 |
| `prompt_injection.py` (6KB) | **提示词注入检测器** | INJECTION_PATTERNS约22个正则：中英双语覆盖——忽略指令/角色切换(你现在是/pretend to be)/jailbreak|DAN/im_start等特殊token/进入XX模式/泄露提示词/忽略安全限制/developer mode/JSON结构包裹system；双层检测：_rule_check命中且conf≥0.8直接判定→_llm_check语义检测(ThreadPoolExecutor单线程,3秒超时,**fail-closed超时判可疑**)；sanitize替换为"[已过滤]"；extract_intent LLM提取真实意图去除指令性内容；TypeError接口不匹配降级规则 |

---

## 🧠 observability/ 核心组件（9文件）

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` (2.2KB) | 包导出总入口 | 导出六大子系统全部公共API（Logging/ConfigModels/ConfigManager/Tracing/Metrics/GracefulShutdown/HealthChecker） |
| `config_manager.py` (9.3KB) | **配置管理器** | YAML加载(system.yaml)+环境分层覆盖(system_{env}.yaml,AI_GF_ENV选择)；`${VAR:-default}`环境变量递归解析；AI_GF_前缀点号路径覆盖(AI_GF_LLM_CACHE_REDIS_HOST→data["llm"]["cache"]["redis"]["host"])；**敏感值保护**:_without_masked_secrets丢弃"****"占位符防止回写覆盖真实密钥(_MASKED_VALUE/_SENSITIVE_KEY_PARTS四关键词)；**原子写入**:tempfile+os.replace防半写；保存前先SystemConfig验证再落盘；watchfiles文件监听自动reload；on_change回调通知；deep_merge深合并 |
| `config_models.py` (5.5KB) | **Pydantic配置模型全家桶** | SystemConfig聚合11个子配置：LLMConfig(deepseek默认/chat+reasoner主备/temperature 0.85/max_tokens 2048/stream_enabled/first_token_timeout 3s/retry_count 2/retry_cooldown 30s/models_priority)；EmotionConfig(初始NEUTRAL/intensity_decay_per_minute 0.001/energy_recovery_per_hour 0.05/use_llm_classifier 500ms超时/continuity_blend_ratio 0.4)；MemoryConfig(working_memory_limit 20/episodic归档触发20轮或30分钟/importance_lambda 0.1~0.01/**retrieval_timeout 3秒注释:覆盖onnxruntime冷启动2-4s**/fact_type_weights七类权重health 1.0最高)；ProactiveConfig(max_daily 8/min_interval 30min/cooldown 15min/urgency_threshold 2.0/freq三级8-3-1)；SafetyConfig(input/output/pii三开关+encryption默认关+self_harm_intervention开)；ToolsConfig(执行10s超时/每工具每分钟3次限流/网络白名单openweathermap+duckduckgo+deepseek/内置六工具)；APIConfig(host 0.0.0.0/port 8000/ws 8765/cors localhost:3000+8000/rate_limit 60/min/api_key_enabled)；VoiceConfig(edge-tts默认zh-CN-XiaoxiaoNeural/gpt-sovits 9880/bert-vits2 5000珊瑚宫心海[中]/alias连字符别名)；CharacterCardConfig(config/characters目录)/MemoryExtConfig(默认disabled) |
| `graceful_shutdown.py` (3KB) | **优雅关闭管理器** | SIGTERM/SIGINT信号处理；活跃请求计数increment/decrement(threading.Lock)；关闭流程：设shutting_down标志拒新请求→等待活跃任务完成(Event.wait timeout 30s)→执行cleanup函数链；全局单例graceful_shutdown |
| `health.py` (4.2KB) | **健康检查器** | register同步/register_async异步双注册制；async_check并行探测(asyncio.gather+wait_for 5s超时)；三级状态healthy→degraded→unhealthy；register_defaults预置7组件(emotion_engine/tone_mimic/vector_memory/structured_memory/llm_gateway/ase_engine/scheduler)；全局单例health_checker |
| `logging_setup.py` (5.8KB) | **日志系统** | structlog可选(HAS_STRUCTLOG降级标准logging)；ContextVar三上下文trace_id/session_id/user_id；RingBufferHandler内存环形缓冲200条(供/api/logs端点,支持level/search/user_id过滤)；UserContextFilter自动注入user_id到LogRecord；RotatingFileHandler 10MB×5备份到data/app.log；JSON/Console双格式渲染；_add_trace_info把trace/session塞进event_dict |
| `metrics.py` (4.4KB) | **Prometheus指标** | prometheus_client可选降级；10组指标：chat_request_duration(Histogram按model)/chat_token_usage(Counter按model+type)/emotion_analysis_duration/memory_retrieval_duration(memory_type)/tool_call_duration+total(tool_name+status)/proactive_message_sent(trigger_type)/error_total(module+error_type)/active_sessions(Gauge)/**llm_provider_status_total(provider+status:success\|fallback\|error,fallback定义=响应以"（"开头触发降级)**；setup_metrics起9090端口(OSError端口占用警告) |
| `sentry.py` (2.7KB) | Sentry错误监控 | SENTRY_DSN未配置自动禁用零影响；FastAPI+Starlette+Logging三集成(Logging WARNING级,event ERROR级)；send_default_pii=False不发用户PII；before_send=_scrub_sensitive_data过滤headers/extra中password/token/api_key/secret/authorization/cookie六敏感键为[REDACTED]；traces_sample_rate=0.1 |
| `tracing.py` (4.3KB) | **链路追踪器** | TRACE_NODES预定义18个节点(message_received→multimodal_preprocess→input_safety_check→pii_anonymize→emotion_analyze→memory_retrieve→rag_retrieve→prompt_assemble→prompt_injection_check→llm_inference→tool_call→output_safety_check→reply_send→memory_store→emotion_memory_record→persona_evolution_eval→reflection)；TraceSpan数据类(perf_counter计时)；span上下文管理器(无trace_id时静默跳过)；TTL 1800秒过期清理(_cleanup_expired_locked持锁清理)；get_trace查询活跃trace |

---

## 🔧 主要设计模式

### 1. 安全纵深防御（四层）
```
输入 → ContentSafetyFilter.check_input(自残/暴力/色情)
     → PIIAnonymizer.anonymize(脱敏)
     → PromptInjectionDetector.detect(规则conf≥0.8 → LLM 3s fail-closed)
     → LLM推理
     → ContentSafetyFilter.check_output
```

### 2. 可选依赖优雅降级
- structlog/prometheus_client/watchfiles/yaml 全部try-import+HAS_XXX标志
- 缺依赖不影响启动，功能静默禁用

### 3. 配置三层合并
```
system.yaml(基础) ⊕ system_{env}.yaml(环境层) ⊕ 环境变量(${VAR}解析+AI_GF_点号覆盖)
```

### 4. Fail-safe设计亮点
- 注入检测LLM超时→fail-closed判可疑（安全优先）
- 配置保存先验证后原子写入，失败保留raw_config待修复
- 敏感字段"****"占位符永不回写覆盖真实密钥

---

## ⚠️ 关键技术要点

### 与其他模块的集成点
| 集成对象 | 方式 |
|---------|------|
| orchestrator | components["safety"]/["pii"]/["injection"]三组件注入 |
| llm_provider | metrics.record_provider_fallback记录provider降级 |
| voice/character_card/memory_ext | VoiceConfig/CharacterCardConfig/MemoryExtConfig对接配置 |
| proactive | ProactiveConfig频率参数与FrequencyController对应 |
| api/logs端点 | ring_buffer.get_recent()提供最近200条日志 |

### 合规与隐私设计
- Sentry send_default_pii=False + before_send敏感数据擦除
- PII脱敏在LLM调用前完成（聊天记录不出明文）
- 自残内容强制返回心理援助热线（400-161-9995全国24小时）

---

## 📋 已读文件列表（完整14个）

1. security/__init__.py
2. security/content_safety.py
3. security/encryption.py
4. security/pii_anonymizer.py
5. security/prompt_injection.py
6. observability/__init__.py
7. observability/config_manager.py
8. observability/config_models.py
9. observability/graceful_shutdown.py
10. observability/health.py
11. observability/logging_setup.py
12. observability/metrics.py
13. observability/sentry.py
14. observability/tracing.py
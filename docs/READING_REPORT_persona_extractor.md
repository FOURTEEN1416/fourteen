# 📚 Persona Extractor 模块阅读报告

**读取进度**：13/13 文件 ✅ 全部穷举阅读 | **读取时间**：2026-08-26  
**模块定位**：十四的人格克隆模块 — 融合 Agethos(数据结构) + PADO(COLING 2025检测prompt) + 原系统(运行时) 三方方案  

---

## 🧠 核心组件概览

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出入口 | 对外主入口 `PersonaExtractor`（来自fusion.py） |
| `models.py` (23KB) | 数据模型层 | OceanTraits(OCEAN五维0~1)、PadState(PAD三轴-1~1,含decay/stimulus/blend)、StyleVector(5维风格)、UserPersonaSnapshot(检测快照)、UserPersona(用户画像+演化历史,apply_snapshot贝叶斯融合) |
| `pado_detector.py` (20.5KB) | PADO风格OCEAN检测器 | 三档模式：全量PADO(11次API调用最准)/Lite PADO(1次调用够用)/Rule(0次零成本)；high/low双视角+Judge综合；检测历史平滑+Chameleon效应隔离；`_rule_detect`关键词规则兜底 |
| `style_vectorizer.py` | 5维风格向量提取 | formality/expressiveness/humor/directness/sentiment；中文语气词正则库(正式词/非正式词/幽默词/直接词/情绪词)；增量更新EMA权重0.3；`to_tone_mimic_profile`适配my_character/tone_mimic.py |
| `persona_bank.py` | 用户人格画像银行 | SQLite持久化(user_persona+user_persona_snapshots双表)；多用户隔离(user_id)；内存缓存+启动全量加载；稳定性S曲线评分`1/(1+10*e^(-0.5*count))`；快照历史上限控制 |
| `emotion_coupler.py` | PAD三轴情感耦合器 | 情绪标签↔PAD双向转换(PAD_EMOTION_MAP查表+最近邻查找)；OCEAN→基线PAD(Agethos from_ocean算法)；Chameleon隔离(threshold=0.5,短消息/高情绪消息降权)；适配EmotionEngine |
| `fusion.py` (12.2KB) | 统一融合适配器 | **对外主入口PersonaExtractor**；管线：user_msg→PADODetector→UserPersonaBank存储→StyleVectorizer更新→EmotionCoupler适配→system_prompt增强；挂载mental_health_pipeline |
| `hexaco.py` | HEXACO六因素模型 | OCEAN+第6维Honesty-Humility(诚实-谦逊)；`from_ocean`从五维推导六维；Ashton & Lee (2007)文献依据 |
| `dark_triad.py` | 暗黑三人格检测 | 自恋/马基雅维利主义/精神病态三维(SD3量表)；LLM检测+规则降级；亚临床特质声明(非疾病诊断)；prompt增强段生成 |
| `cognitive_distortions.py` | 认知扭曲检测 | Burns 10种认知扭曲(全或无/过度概括/心理过滤/读心术/应该陈述等)；LLM检测+CognitiveDistortionResult结构化输出；to_prompt_enhancement注入提示词 |
| `liwc_analyzer.py` | 心理语言学词表分析 | **自建中文词表**（借 LIWC 类别框架，未使用 LIWC2015 授权词表，未经中文效度验证）；34 类经 `CATEGORY_FIELDS` 显式映射到 `LiwcProfile` 字段 + 3 个 `DERIVED_FIELDS` 聚合，构造期双向校验闭合；分母=jieba 私有词典分词的词元数；纯规则(0 API成本)；`to_prompt_segment` 段头自带口径声明 |
| `mental_health.py` (16.3KB) | 情绪信号筛查 | **关键词命中的 0-1 相对信号**（维度分=min(1,命中词数/4)，total=各维度均值，level 按证据档 max(最强维度命中词数,命中维度数) 分档）；维度概念沿用 PHQ-9/GAD-7 条目但**未实现也未验证任何量表**，载荷自报 `scale/method/items_hit/evidence/formula` + snapshot `caveat`；DepressionIndicators/AnxietyIndicators/MentalHealthSnapshot；⚠️免责声明：辅助工具非诊断，高风险信号提示就医（危机拦截在 `security/content_safety.py` 热路径）；quick_screen快速筛查+风险汇总 |
| `web_enricher.py` (37.9KB) | 网络人设增强引擎 | **4类内容源**：①DirectScraper(requests+BS4正文提取)②AgentReachSource(bili-cli搜B站/Jina Reader抓网页/mcporter调Exa)③FirecrawlSource(需FIRECRAWL_API_KEY)④AgentReachChannels(Python渠道,13平台:bilibili/xiaohongshu/youtube/twitter/github/reddit/v2ex等)；抓取链路fallback:DirectScraper→Jina Reader→Firecrawl；_PERSONA_EXTRACTION_PROMPT五维度LLM提取(核心性格/背景设定/行为模式/说话风格/经典台词)；写入知识库BM25索引(shisi.knowledge.KnowledgeChunk)；AGENT_REACH_PATH环境变量配置,未安装优雅降级 |

---

## 🔧 主要设计模式

### 1. 三方融合架构（Agethos + PADO + 原系统）
```
user_msg → [PADODetector] → OCEAN+PAD快照
                         → [UserPersonaBank] → SQLite存储+演化
                         → [StyleVectorizer] → 风格向量更新
                         → [EmotionCoupler] → PAD→EmotionEngine适配
                         → [PersonaEngine] → system_prompt增强段
```

### 2. 检测成本分级
- **全量PADO**：11次API调用（5高视角+5低视角+1 Judge汇总）— 最准
- **Lite PADO**：1次API调用综合推理 — 默认档位
- **Rule规则**：0次调用，中文关键词词典匹配 — 零成本兜底

### 3. Chameleon效应隔离
- 区分「用户真实人格」vs「被对话感染的临时情绪」
- 短消息、高情绪强度消息自动降低PAD权重
- chameleon_threshold=0.5可配置

### 4. 心理学学术支撑体系
- OCEAN/PAD：Agethos论文数据结构
- PADO：COLING 2025多智能体辩论式检测
- HEXACO：Ashton & Lee (2007)
- 暗黑三：Paulhus & Williams (2002)/SD3
- 认知扭曲：Beck (1976)/Burns (1980)
- LIWC：Pennebaker (2015/2022) —— **仅借类别框架**，词表为自建中文词典，未使用授权词表、未经中文效度验证
- 心理健康：DSM-5-TR/GAD-7/PHQ-9 —— **仅沿用条目概念命名维度**，未实现也未验证任何量表；对外读数一律是 0-1 关键词命中信号（详见 `mental_health.py` 模块文档与载荷 `formula`/`caveat`）

### 5. 网络增强多源容错
- 所有外部源（bili-cli/Firecrawl/Jina/mcporter）均可用性探测+静默降级
- URL去重(seen_urls)+内容去重(seen_content)
- LLM提取失败回退段落分块（30~2000字符）
- 信息来源URL单独成chunk保留溯源

---

## ⚠️ 关键技术要点

### 与其他模块的集成点
| 集成对象 | 方式 |
|---------|------|
| `my_character/tone_mimic.py` | StyleVectorizer.to_tone_mimic_profile() 输出StyleProfile格式 |
| EmotionEngine | EmotionCoupler.adapt_to_emotion_engine() 双向转换 |
| PersonaEngine | UserPersona.to_prompt_enhancement() 注入system_prompt |
| StructuredMemory | UserPersonaBank复用同一个sqlite.db |
| shisi.knowledge | WebEnricher产出KnowledgeChunk写入BM25索引 |

### 安全与合规设计
- mental_health.py 明确声明"不能替代专业心理诊断"
- dark_triad.py 声明"亚临床特质，不等于精神疾病诊断"
- 高风险信号自动提示用户寻求专业帮助
- AGENT_REACH_PATH 环境变量避免硬编码本地路径

---

## 📋 已读文件列表（完整13个）

1. __init__.py
2. models.py
3. pado_detector.py
4. style_vectorizer.py
5. persona_bank.py
6. emotion_coupler.py
7. fusion.py
8. hexaco.py
9. dark_triad.py
10. cognitive_distortions.py
11. liwc_analyzer.py
12. mental_health.py
13. web_enricher.py
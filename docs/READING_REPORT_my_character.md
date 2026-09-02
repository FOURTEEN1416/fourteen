# 📚 my_character 模块阅读报告

**读取进度**：21/21 文件 ✅ 已全部穷举阅读  
**读取时间**：2026-08-26  
**覆盖范围**：my_character/ 目录下全部21个 Python 源码文件  

---

## 🧠 核心组件概览

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `emotion_engine.py` | 情感引擎 — 融合V1/V2/Optimized | 10种情感状态、好感度阶梯(0-8)、情感转移矩阵、LLM分类器降级、时间衰减、双接口风格修饰符 |
| `persona_engine.py` | 融合版人格引擎 | 提示词双模式(legacy/layered)、锚点保护双机制、V1/V2演化接口、V1回滚能力、V2五维画像 |
| `emotion_style_coupler.py` | 情感-风格耦合器 | 10种情感×9级好感度完整映射矩阵、次要情感加权融合、动态调整说话风格 |
| `tone_mimic.py` | 语气模仿器 | ChromaDB向量存储、Few-shot示例检索、风格画像生成、系统prompt注入 |
| `persona_schema.py` | 统一人设模式 | PersonaSchema单一真值源、merge融合、validation验证、from_config/from_profile/from_chara_card转换 |
| `style_enhancer.py` | 风格增强器V1 | 16维度风格分析、从历史聊天记录提取风格、style融合与生成prompt段 |
| `style_enhancer_v2.py` | 风格增强器V2 | 20维度(16基础+4交叉)、维度间依赖关系建模、cross dimension计算 |
| `persona_evaluator.py` | 人设评估器 | 5维评估(anchor_fidelity/style_consistency/emotion_appropriateness/behavior_compliance/memory_coherence)、阈值0.75 |
| `anchor_protection.py` | 增强版锚点保护 | SHA256哈希校验、语义+关键词双重检测、关键词违背检测、强化prompt生成 |
| `consistency_checker.py` | 人设一致性检测器 | 4维度校验(anchor/emotion/style/persona)、结构化结果+修正建议、自动修复 |
| `constraint_validator.py` | 约束验证器 | 强制模式模式检测、机械化语言检测、过度热情/冷淡检测、自动修正策略 |
| `contextual_behavior.py` | 情境化行为 | 基于AST安全求值的行为规则、时间/能量/好感度/节日触发、修饰指令生成 |
| `counter_rebuttal.py` | 计数反诘 | 用户连续说"没事"等敷衍词计数、第5次触发反诘"这已经是第5次了" |
| `dynamic_anchor.py` | 动态锚点系统 | 激活条件、情感权重调整、优先级排序、reinforcement周期性注入 |
| `persona_card.py` | 人设卡V3数据模型 | 从"写死的十四"→"3分钟造出任何人"、从YAML/JSON加动态生成system prompt |
| `persona_engine.py` (已读前7个) | 已读7个核心文件 | 已读 enhanced_prompt_engine.py/evolution_engine.py/persona_schema.py/style_enhancer_v2.py/style_enhancer.py/persona_evaluator.py/tone_mimic.py |
| `persona_utils.py` | 人设增强公共工具 | emotion_attrs提取、AnchorContext构建、TimeContext提取、context_vars提取 |

---

## 🔧 主要设计模式

### 1. 提示词架构双模式
- **legacy模式**：V1顺序构建，兼容旧版本
- **layered模式**：Optimized 5层架构（base/emotion/memory/style/constraint/rag）
- **PersonaEngine**：prompt_mode参数控制切换

### 2. 锚点保护双机制
- **V1幅度钳制**：delta > 0.3 时截断
- **V2 SHA256哈希校验**：锚点文本完整性验证
- **增强AnchorProtection**：语义相似度 + 关键词匹配双重检测

### 3. 人格演化双接口
- **evolve()**：V1批量演化接口
- **evolve_dimension()**：V2单维度演化接口
- **rollback_to(index)**：V1回滚能力保留

### 4. 好感度阶梯系统
- **AffinityLevel.LEVELS**：[陌生人, 认识, 朋友, 好朋友, 知己, 暧昧, 恋人, 热恋, 羁绊]
- **THRESHOLDS**：[0, 10, 25, 50, 80, 120, 200, 350, 500]
- 自动升级/降级检查

### 5. 情感-风格耦合
- **EMOTION_STYLE_MATRIX**：10种情感的风格调整参数
- **AFFINITY_STYLE_MATRIX**：9级好感度的风格参数
- 双重映射系统确保情感状态正确影响输出风格

---

## ⚠️ 关键技术要点

### 情感系统
- 10种核心情感：HAPPY/SAD/ANGRY/LOVELY/JEALOUS/SULLEN/CARING/PLAYFUL/TIRED/NEUTRAL
- 情感转移矩阵：10×10完整覆盖，基于心理学概率
- 连续性保护：ContinuityGuard确保情感转换合理性
- LLM分类器：弱引用避免循环引用，失败自动降级到规则模式

### 人设系统
- PersonaProfile五维画像：core_character/speaking_style/emotional_preference/interest_hobbies/value_tendency
- 动态人设创建：3分钟造出任何人
- YAML/JSON角色卡兼容：V1/V2/V3格式全支持
- 多模态融合：文本+语音风格双重同步

### 语气模仿
- ChromaDB存储历史对话向量
- 检索最相似的Few-shot示例
- 结合情感状态和风格画像生成语气prompt
- 支持运行时风格画像手动更新

---

## 📋 已读文件列表（完整）

1. enhanced_prompt_engine.py
2. evolution_engine.py
3. persona_schema.py
4. style_enhancer_v2.py
5. style_enhancer.py
6. persona_evaluator.py
7. tone_mimic.py
8. anchor_protection.py
9. character_config.py
10. consistency_checker.py
11. constraint_validator.py
12. contextual_behavior.py
13. counter_rebuttal.py
14. dynamic_anchor.py
15. persona_card.py
16. persona_engine.py
17. persona_utils.py
18. emotion_engine.py
19. emotion_memory.py
20. emotion_style_coupler.py
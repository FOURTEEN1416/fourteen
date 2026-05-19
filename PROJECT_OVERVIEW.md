# AI 伴侣女友 "小暖" — 项目全面文档

> **版本**: v1.2 / v2.0  
> **最后更新**: 2026-05-19  
> **项目状态**: 生产就绪，50/50 模块通过验证

---

## 一、项目概述

### 1.1 项目定位
这是一个基于大语言模型(LLM)的AI虚拟伴侣系统，名为"小暖"。系统具备完整的情感模拟、记忆管理、主动交互和风格克隆能力，可通过微信等渠道与用户进行自然、有情感温度的对话。

### 1.2 核心特性
- **情感引擎**: 实时情感状态机，包含8级好感度体系
- **记忆系统**: 三层记忆架构（工作记忆/情景记忆/语义记忆）
- **主动消息**: 基于情境感知的主动关怀消息生成
- **风格克隆**: 支持从微信聊天记录克隆特定人物说话风格
- **多通道接入**: 微信个人号、企业微信、控制台、WebSocket API
- **安全机制**: 内容过滤、PII脱敏、提示词注入检测

### 1.3 技术栈
- **后端**: Python 3.12
- **LLM**: DeepSeek API / OpenCode Zen / 多模型 fallback
- **向量数据库**: ChromaDB
- **结构化存储**: SQLite
- **前端**: React + TypeScript + Vite
- **微信接入**: CowAgent (WeChatFerry RPC)

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户交互层                                 │
├─────────────┬─────────────┬─────────────┬───────────────────────┤
│   微信个人号  │  企业微信    │   控制台     │    REST/WebSocket API │
│  (CowAgent) │  (WeCom)    │  (Terminal) │       (FastAPI)       │
└──────┬──────┴──────┬──────┴──────┬──────┴───────────┬───────────┘
       │             │             │                  │
       └─────────────┴─────────────┴──────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │   对话编排器        │
                    │   (Orchestrator)  │
                    └─────────┬─────────┘
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
┌──────▼──────┐    ┌──────────▼──────────┐   ┌──────▼──────┐
│   安全层     │    │      核心引擎        │   │   工具系统   │
│  Safety     │    │    Core Engines     │   │   Tools     │
├─────────────┤    ├─────────────────────┤   ├─────────────┤
│• 内容过滤   │    │• 情感引擎 V1/V2     │   │• 天气查询   │
│• PII脱敏   │    │• 人格引擎 V1/V2     │   │• 网络搜索   │
│• 注入检测   │    │• 语气模仿           │   │• 日历提醒   │
│• 加密管理   │    │• 记忆管线 V1/V2     │   │• 计算器     │
└─────────────┘    │• RAG引擎 V2         │   └─────────────┘
                   │• 主动消息引擎 V1/V2  │
                   └─────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │    LLM 网关        │
                    │  (LLM Gateway)    │
                    └─────────┬─────────┘
                              │
       ┌──────────────────────┼──────────────────────┐
       │                      │                      │
┌──────▼──────┐    ┌──────────▼──────────┐   ┌──────▼──────┐
│  DeepSeek   │    │    OpenCode Zen     │   │  模拟回复    │
│    API      │    │      (Free LLM)     │   │  (Fallback) │
└─────────────┘    └─────────────────────┘   └─────────────┘
```

### 2.2 双版本架构

项目同时维护两个版本：

| 维度 | V1 (main.py) | V2 (main_v2.py) |
|------|-------------|-----------------|
| **配置系统** | YAML静态配置 | Pydantic + 热重载 |
| **可观测性** | 基础日志 | 链路追踪 + 指标 + 健康检查 |
| **安全层** | 基础过滤 | 完整安全栈 |
| **记忆系统** | 两层记忆 | 三层记忆 + 重要性评分 |
| **LLM网关** | 单模型 | 多模型优先级 + 流式 |
| **工具系统** | 无 | 完整工具调度 |
| **RAG引擎** | 基础检索 | 混合检索 + 重排序 |
| **API服务** | 无 | REST + WebSocket |

---

## 三、核心模块详解

### 3.1 角色引擎 (my_character/)

#### 3.1.1 情感引擎 (EmotionEngine)
- **文件**: `emotion_engine.py` / `emotion_engine_v2.py`
- **功能**: 实时情感状态管理
- **核心状态**:
  - `emotion`: 当前情感（开心/难过/生气/惊讶/中性）
  - `energy`: 能量值（0-1，影响回复长度）
  - `affinity`: 好感度（0-500，8级阶梯）
  - `intensity`: 情感强度

**好感度等级体系**:
```yaml
0: 陌生人    (0聊天, 0好感)
1: 认识      (5聊天, 10好感)
2: 朋友      (15聊天, 25好感)
3: 好朋友    (30聊天, 50好感)
4: 知己      (60聊天, 80好感)
5: 暧昧      (100聊天, 120好感)
6: 恋人      (200聊天, 200好感)
7: 热恋      (400聊天, 350好感)
8: 羁绊      (800聊天, 500好感)
```

#### 3.1.2 人格引擎 (PersonaEngine)
- **文件**: `persona.py` / `persona_engine_v2.py`
- **功能**: 构建系统提示词，塑造角色性格
- **核心锚点**:
  - "表面傲娇，内心温柔"
  - "在你面前才会展现脆弱"
  - "嘴硬心软，从来不说实话"
  - "嘴上嫌弃其实在乎得要命"

#### 3.1.3 语气模仿 (ToneMimic)
- **文件**: `tone_mimic.py`
- **功能**: 基于向量检索的语气风格注入
- **实现**: 使用ChromaDB存储和检索风格示例

### 3.2 记忆系统 (memory/ + memory_v2/)

#### 3.2.1 V1 记忆架构
```
┌─────────────────────────────────────┐
│         MemoryPipeline              │
├───────────────┬─────────────────────┤
│  VectorMemory │   StructuredMemory  │
│  (ChromaDB)   │     (SQLite)        │
├───────────────┼─────────────────────┤
│• 语义检索     │• 对话历史           │
│• 相似度匹配   │• 事实提取           │
│• 风格示例     │• 日记摘要           │
└───────────────┴─────────────────────┘
```

#### 3.2.2 V2 三层记忆架构
```
┌─────────────────────────────────────────────┐
│           MemoryPipelineV2                  │
├─────────────┬───────────────┬───────────────┤
│ 工作记忆     │   情景记忆     │   语义记忆    │
│WorkingMemory│ EpisodicMemory│SemanticMemory │
├─────────────┼───────────────┼───────────────┤
│• 当前会话    │• 历史对话归档   │• 提取的事实   │
│• 短期上下文  │• 重要性评分    │• 用户画像    │
│• 20条限制   │• 自动遗忘      │• 长期知识    │
└─────────────┴───────────────┴─────────────┘
```

### 3.3 主动消息系统 (proactive/)

#### 3.3.1 ASE引擎 (Active Sentiment Engine)
- **文件**: `ase_engine.py` / `ase_engine_v2.py`
- **功能**: 生成主动关怀消息
- **触发条件**:
  - 长时间未聊天
  - 特定时间段（早安/晚安）
  - 用户情绪低落
  - 特殊日期

#### 3.3.2 调度器 (Scheduler)
- **文件**: `scheduler.py`
- **功能**: 定时任务管理
- **策略**:
  - 每日最多8条主动消息
  - 最小间隔30分钟
  - 回复后冷却期

### 3.4 风格克隆系统 (clone_training/)

#### 3.4.1 数据提取 (data_extractor.py)
支持多种数据源：
- WeChatFerry RPC (微信3.x)
- WeChatMsg 导出
- wechat-decrypt (微信4.x数据库解密)
- 手动导出 (txt/csv/json)

#### 3.4.2 风格分析 (style_analyzer.py)
12维度风格分析：
1. 平均回复长度
2. 标点使用密度
3. 语气词频率
4.  emoji 使用习惯
5. 问句比例
6. 祈使句比例
7. 词汇多样性
8. 句法复杂度
9. 情感表达强度
10. 正式程度
11. 幽默指数
12. 个人特色标记

#### 3.4.3 数据集构建 (dataset_builder.py)
输出格式：
- Alpaca格式（指令微调）
- ChatML格式（对话微调）
- JSONL格式（行式JSON）

#### 3.4.4 LoRA训练 (lora_trainer.py)
- 基于PEFT的轻量微调
- 支持量化推理
- 自动合并导出

### 3.5 LLM网关 (llm_provider/)

#### 3.5.1 V2多模型架构
```python
models_priority:
  - name: big-pickle (priority: 1)
  - name: nemotron-3-super-free (priority: 2)
  - name: qwen3.6-plus-free (priority: 3)
  - name: deepseek-v4-flash-free (priority: 4)
  - name: minimax-m2.5-free (priority: 5)
```

**特性**:
- 自动故障转移
- 流式输出支持
- Function Calling
- 提示词模板管理

### 3.6 安全层 (safety/)

| 组件 | 功能 |
|------|------|
| ContentSafetyFilter | 输入/输出内容过滤 |
| PIIAnonymizer | 个人身份信息脱敏 |
| EncryptionManager | 敏感数据加密 |
| PromptInjectionDetector | 提示词注入攻击检测 |

### 3.7 工具系统 (tool_system/)

内置工具列表：
- `weather` - 天气查询
- `search` - 网络搜索
- `calendar` - 日历管理
- `calculator` - 计算器
- `reminder` - 提醒设置
- `calendar_query` - 日历查询

---

## 四、项目结构

```
ai-girlfriend/
├── main.py                    # V1主入口
├── main_v2.py                 # V2主入口（推荐）
├── orchestrator.py            # 对话编排器
├── MEMORY.md                  # 项目记忆文档
│
├── config/                    # 配置文件
│   ├── system.yaml            # 主配置（V2）
│   ├── system_prod.yaml       # 生产配置
│   ├── persona.yaml           # 角色设定
│   ├── emotion.yaml           # 情感参数
│   └── prompts/               # 提示词模板
│
├── my_character/              # 角色引擎
│   ├── emotion_engine.py      # 情感引擎V1
│   ├── emotion_engine_v2.py   # 情感引擎V2
│   ├── persona.py             # 人格引擎V1
│   ├── persona_engine_v2.py   # 人格引擎V2
│   ├── tone_mimic.py          # 语气模仿
│   └── base.py                # 基础类
│
├── memory/                    # 记忆系统V1
│   ├── memory_pipeline.py
│   ├── vector_memory.py       # ChromaDB向量存储
│   ├── structured_memory.py   # SQLite结构化存储
│   ├── fact_extractor.py      # 事实提取器
│   └── diary_summarizer.py    # 日记摘要器
│
├── memory_v2/                 # 记忆系统V2
│   ├── memory_pipeline_v2.py
│   ├── working_memory.py      # 工作记忆
│   ├── episodic_memory.py     # 情景记忆
│   ├── semantic_memory.py     # 语义记忆
│   └── importance_scorer.py   # 重要性评分
│
├── proactive/                 # 主动消息系统
│   ├── ase_engine.py          # ASE引擎V1
│   ├── ase_engine_v2.py       # ASE引擎V2
│   └── scheduler.py           # 调度器
│
├── clone_training/            # 风格克隆训练
│   ├── data_extractor.py      # 数据提取
│   ├── style_analyzer.py      # 风格分析
│   ├── dataset_builder.py     # 数据集构建
│   ├── lora_trainer.py        # LoRA训练
│   ├── decrypt_source.py      # 微信解密适配
│   └── config.yaml            # 训练配置
│
├── weclone_adapter/           # WeClone集成适配
│   ├── adapter.py             # 主适配器
│   └── style_profiler.py      # 风格画像
│
├── cowagent_adapter/          # CowAgent微信适配
│   ├── girlfriend_bot.py      # 女友机器人
│   ├── patch.py               # 运行时补丁
│   ├── heartbeat.py           # 心跳监控
│   └── _globals.py            # 全局状态
│
├── cowagent_src/              # CowAgent源码（微信通道）
│   ├── app.py                 # 主应用
│   ├── channel/               # 多渠道支持
│   │   ├── weixin/            # 微信个人号
│   │   ├── wechatcom/         # 企业微信
│   │   ├── feishu/            # 飞书
│   │   ├── dingtalk/          # 钉钉
│   │   └── web/               # Web界面
│   ├── agent/                 # Agent核心
│   │   ├── chat/              # 对话服务
│   │   ├── memory/            # 记忆管理
│   │   ├── prompt/            # 提示词构建
│   │   ├── skills/            # 技能系统
│   │   └── tools/             # 工具系统
│   ├── models/                # LLM模型适配
│   │   ├── deepseek/          # DeepSeek
│   │   ├── openai/            # OpenAI
│   │   ├── claudeapi/         # Claude
│   │   └── ...                # 其他模型
│   └── plugins/               # 插件系统
│
├── llm_provider/              # LLM网关
│   ├── llm_gateway_v2.py      # V2网关
│   ├── deepseek_gateway.py    # DeepSeek适配
│   ├── opencode_zen_provider.py # OpenCode Zen
│   └── prompt_template_mgr.py # 模板管理
│
├── rag_engine/                # RAG引擎V2
│   └── rag_engine_v2.py
│
├── tool_system/               # 工具系统
│   ├── base.py                # 工具基类
│   └── builtin/               # 内置工具
│       ├── weather_tool.py
│       ├── search_tool.py
│       ├── calendar_tool.py
│       └── reminder_tool.py
│
├── safety/                    # 安全层
│   ├── content_safety.py
│   ├── pii_anonymizer.py
│   ├── encryption.py
│   └── prompt_injection.py
│
├── api/                       # API服务
│   ├── rest_api.py            # REST API
│   ├── websocket_server.py    # WebSocket服务
│   └── session_manager.py     # 会话管理
│
├── multimodal/                # 多模态处理
│   └── multimodal_processor.py
│
├── observability/             # 可观测性
│   ├── config_manager.py      # 配置管理
│   ├── tracing.py             # 链路追踪
│   ├── metrics.py             # 指标监控
│   ├── health.py              # 健康检查
│   └── logging_setup.py       # 日志设置
│
├── frontend/                  # 前端界面
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── style.css
│   ├── dist/                  # 构建输出
│   └── package.json
│
├── data/                      # 数据存储
│   ├── chroma_db/             # ChromaDB向量库
│   ├── sqlite.db              # SQLite数据库
│   ├── clone/                 # 克隆数据
│   └── app.log                # 应用日志
│
├── third_party/               # 第三方工具
│   └── wechat-decrypt/        # 微信数据库解密
│
└── scripts/                   # 启动脚本
    └── start.bat              # Windows启动脚本
```

---

## 五、启动方式

### 5.1 V1 启动方式

```bash
# 完整启动（微信 + 主动消息）
python main.py

# 控制台聊天模式
python main.py --no-wechat

# 仅微信（无主动消息）
python main.py --no-scheduler

# 仅初始化（测试）
python main.py --init-only

# 风格克隆模式
python main.py --clone <wxid或文件路径> --clone-source <wcf/wechatmsg/decrypt/txt/csv/json> --clone-name <名称>
```

### 5.2 V2 启动方式（推荐）

```bash
# 完整V2启动
python main_v2.py

# 控制台聊天模式
python main_v2.py --no-wechat

# 不启动API服务
python main_v2.py --no-api

# 无主动消息
python main_v2.py --no-scheduler

# 风格克隆
python main_v2.py --clone <目标> --clone-source <来源> --clone-name <名称>
```

---

## 六、配置说明

### 6.1 环境变量 (.env)

```bash
# LLM API Keys
DEEPSEEK_API_KEY=your_deepseek_key
OPENCODE_ZEN_API_KEY=your_opencode_key

# 微信配置
WECHAT_CONTACT_ID=wxid_xxx

# 加密密钥
ENCRYPTION_KEY=your_encryption_key
```

### 6.2 系统配置 (config/system.yaml)

```yaml
# 基础配置
env: dev                      # 环境: dev/prod
debug: true                   # 调试模式

# LLM配置
llm:
  provider: opencode_zen      # 主提供商
  primary_model: big-pickle   # 主模型
  temperature: 0.85           # 温度
  max_tokens: 2048            # 最大token
  stream_enabled: true        # 流式输出
  models_priority:            # 模型优先级
    - name: big-pickle
      priority: 1
    - name: nemotron-3-super-free
      priority: 2

# 情感配置
emotion:
  use_llm_classifier: true    # 使用LLM情感分类
  continuity_blend_ratio: 0.4 # 情感连续性混合比例

# 记忆配置
memory:
  working_memory_limit: 20    # 工作记忆条数限制
  retrieval_timeout_seconds: 1.0  # 检索超时

# 主动消息配置
proactive:
  max_daily_messages: 8       # 每日最大主动消息
  min_interval_minutes: 30    # 最小间隔
  urgency_threshold: 2.0      # 紧迫度阈值

# 安全配置
safety:
  input_filter_enabled: true  # 输入过滤
  output_filter_enabled: true # 输出过滤
  pii_anonymizer_enabled: true # PII脱敏
  prompt_injection_detection: true # 注入检测

# API配置
api:
  host: "0.0.0.0"
  port: 8000
  websocket_port: 8765

# 可观测性配置
observability:
  tracing_enabled: true       # 链路追踪
  metrics_enabled: true       # 指标监控
  metrics_port: 9090
  log_level: INFO
```

---

## 七、API接口

### 7.1 REST API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 发送消息 |
| `/chat/stream` | POST | 流式对话 |
| `/health` | GET | 健康检查 |
| `/status` | GET | 系统状态 |
| `/config` | GET/PUT | 配置管理 |

### 7.2 WebSocket API

```javascript
// 连接
const ws = new WebSocket('ws://localhost:8765/chat');

// 发送消息
ws.send(JSON.stringify({
    message: "你好",
    session_id: "user_123"
}));

// 接收回复
ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log(data.reply);
};
```

---

## 八、风格克隆流程

### 8.1 克隆步骤

```
1. 数据提取
   ↓ 从微信/导出文件提取对话
2. 数据清洗
   ↓ 过滤无效消息，保护隐私
3. 风格分析
   ↓ 12维度风格画像
4. 数据集构建
   ↓ 生成Alpaca/ChatML格式
5. LoRA训练
   ↓ 轻量微调模型
6. 模型合并
   ↓ 合并LoRA到基础模型
7. ToneMimic注入
   ↓ 将风格注入系统
```

### 8.2 使用示例

```bash
# 从微信解密数据库克隆
python main_v2.py --clone "wxid_xxx" --clone-source decrypt --clone-name "小明"

# 从WeChatMsg导出克隆
python main_v2.py --clone "./chat_export.json" --clone-source wechatmsg --clone-name "小红"

# 从文本文件克隆
python main_v2.py --clone "./chat.txt" --clone-source txt --clone-name "小李"
```

---

## 九、开发规范

### 9.1 代码组织
- 每个模块独立目录，包含 `__init__.py`
- 接口与实现分离，支持V1/V2双版本
- 配置与代码分离，支持热重载

### 9.2 健康检查
所有核心组件实现 `health_check()` 方法：

```python
def health_check(self) -> dict:
    return {
        "configured": True,
        "reachable": True,
        "details": {}
    }
```

### 9.3 链路追踪
使用 `observability.tracing` 进行性能追踪：

```python
from observability.tracing import tracer

with tracer.span("operation_name"):
    # 执行操作
    pass
```

---

## 十、部署建议

### 10.1 开发环境
```bash
# 安装依赖
pip install -r requirements.txt

# 启动开发服务器
python main_v2.py --no-wechat --no-scheduler
```

### 10.2 生产环境
```bash
# 使用生产配置
export COWAGENT_ENV=prod

# 启动完整服务
python main_v2.py
```

### 10.3 Docker部署
```dockerfile
# Dockerfile 示例
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main_v2.py"]
```

---

## 十一、故障排查

### 11.1 常见问题

| 问题 | 解决方案 |
|------|----------|
| 微信连接失败 | 检查WeChatFerry是否正常启动 |
| LLM无响应 | 检查API Key配置，查看fallback是否生效 |
| 记忆丢失 | 检查ChromaDB和SQLite文件权限 |
| 主动消息不发送 | 检查调度器状态和配置 |

### 11.2 日志位置
- 应用日志: `data/app.log`
- 后端日志: `data/backend.log`

### 11.3 调试命令
```bash
# 健康检查
python -c "from main_v2 import *; health_check_all({...})"

# 配置验证
python -c "from observability.config_manager import ConfigManager; ConfigManager().validate()"
```

---

## 十二、路线图

### 已完成 ✅
- [x] 基础对话系统
- [x] 情感引擎 V1/V2
- [x] 记忆系统 V1/V2
- [x] 微信通道集成
- [x] 风格克隆管线
- [x] 主动消息系统
- [x] 安全层
- [x] API服务

### 进行中 🚧
- [ ] 语音对话支持
- [ ] 图像生成集成
- [ ] 多语言支持
- [ ] 移动端适配

### 规划中 📋
- [ ] 情感可视化面板
- [ ] 长期关系演进
- [ ] 群体互动支持
- [ ] 元宇宙接入

---

## 十三、贡献指南

1. **Fork** 项目
2. 创建 **Feature Branch** (`git checkout -b feature/AmazingFeature`)
3. **Commit** 更改 (`git commit -m 'Add some AmazingFeature'`)
4. **Push** 到分支 (`git push origin feature/AmazingFeature`)
5. 创建 **Pull Request**

---

## 十四、许可证

本项目采用 MIT 许可证。

---

## 十五、联系方式

如有问题或建议，欢迎通过以下方式联系：
- 项目 Issues
- 邮件: [your-email@example.com]

---

> 💕 "小暖" — 你的AI伴侣女友，永远在这里等你。

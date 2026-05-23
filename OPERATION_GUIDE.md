# "十四" AI虚拟伴侣系统 — 全流程操作指南

> 版本: v3.0 融合统一版 | 更新日期: 2026-05-20

---

## 目录

1. [系统架构概览](#1-系统架构概览)
2. [环境准备与安装](#2-环境准备与安装)
3. [配置详解](#3-配置详解)
4. [启动系统](#4-启动系统)
5. [控制台聊天模式](#5-控制台聊天模式)
6. [微信连接模式](#6-微信连接模式)
7. [前端仪表板](#7-前端仪表板)
8. [风格克隆训练](#8-风格克隆训练)
9. [主动消息引擎](#9-主动消息引擎)
10. [API接口参考](#10-api接口参考)
11. [融合模式配置](#11-融合模式配置)
12. [运维与监控](#12-运维与监控)
13. [常见问题排查](#13-常见问题排查)

---

## 1. 系统架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                      十四 AI虚拟伴侣系统 v3.0                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────┐   ┌──────────────┐   ┌───────────────────┐      │
│  │ 微信通道  │──▶│  对话编排器   │──▶│    LLM Gateway    │      │
│  │ CowAgent  │   │ Orchestrator │   │ OpenCode Zen/API  │      │
│  └──────────┘   └──────┬───────┘   └───────────────────┘      │
│                         │                                       │
│  ┌──────────┐   ┌──────┴───────┐   ┌───────────────────┐      │
│  │ 前端面板  │◀──│   核心引擎层  │──▶│   风格克隆训练    │      │
│  │ React SPA│   │              │   │  LoRA PEFT        │      │
│  └──────────┘   │ ┌──────────┐ │   └───────────────────┘      │
│                  │ │情感引擎   │ │                              │
│                  │ │(hybrid)  │ │                              │
│                  │ ├──────────┤ │                              │
│                  │ │人格引擎   │ │                              │
│                  │ │(layered) │ │                              │
│                  │ ├──────────┤ │                              │
│                  │ │记忆管线   │ │                              │
│                  │ │(三层+遗忘)│ │                              │
│                  │ ├──────────┤ │                              │
│                  │ │ASE主动引擎│ │                              │
│                  │ │(adaptive) │ │                              │
│                  │ └──────────┘ │                              │
│                  └──────────────┘                              │
│                                                                 │
│  ┌──────────┐   ┌──────────────┐   ┌───────────────────┐      │
│  │ 安全层    │   │   工具系统    │   │    RAG 引擎       │      │
│  │ PII/注入  │   │ 天气/搜索/日历│   │  混合检索+重排    │      │
│  └──────────┘   └──────────────┘   └───────────────────┘      │
│                                                                 │
│  ┌──────────────────────────────────────────────────────┐      │
│  │              可观测性 (日志/指标/追踪/健康检查)        │      │
│  └──────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### 核心端口

| 服务 | 端口 | 说明 |
|------|------|------|
| REST API | 8000 | 后端HTTP接口 |
| WebSocket | 8765 | 实时消息推送 |
| 前端 Dev | 5173 | Vite开发服务器 |
| Prometheus | 9090 | 指标采集 |

---

## 2. 环境准备与安装

### 2.1 系统要求

- Python 3.10+
- Node.js 18+ (前端)
- Git
- Windows/Linux/macOS

### 2.2 后端安装

```bash
# 克隆项目
cd ai-girlfriend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境 (Windows)
venv\Scripts\activate
# 或 Linux/macOS
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

### 2.3 前端安装

```bash
cd frontend
npm install
```

### 2.4 环境变量配置

复制示例文件并按需修改：

```bash
cp .env.example .env
```

`.env` 关键配置：

| 变量 | 必需 | 说明 | 默认值 |
|------|------|------|--------|
| `LLM_PROVIDER` | 否 | LLM提供商 | `opencode_zen` |
| `OPENCODE_ZEN_DEFAULT_MODEL` | 否 | 模型名称 | `big-pickle` |
| `OPENCODE_ZEN_API_BASE` | 否 | API地址 | `https://opencode.ai/zen/v1` |
| `DEEPSEEK_API_KEY` | 否 | DeepSeek API密钥(付费方案) | 空 |
| `AI_GF_ENV` | 否 | 运行环境 | `dev` |

---

## 3. 配置详解

### 3.1 system.yaml — 系统核心配置

文件路径: `config/system.yaml`

#### LLM配置

```yaml
llm:
  provider: opencode_zen           # 提供商: opencode_zen / deepseek / openai
  primary_model: big-pickle        # 主模型
  temperature: 0.85                # 生成温度 (0.1-1.5)
  max_tokens: 2048                 # 最大输出长度
  stream_enabled: true             # 流式输出
  first_token_timeout: 3.0         # 首token超时(秒)
  retry_count: 2                   # 失败重试次数
  opencode_zen_api_base: "https://opencode.ai/zen/v1"
  models_priority:                 # 多模型优先级(自动降级)
    - name: big-pickle
      priority: 1
    - name: qwen3.6-plus-free
      priority: 2
```

#### 情感配置

```yaml
emotion:
  use_llm_classifier: true         # 启用LLM情感分类
  llm_classifier_timeout_ms: 500   # LLM分类超时
  continuity_blend_ratio: 0.4      # 连续性混合比例
```

#### 记忆配置

```yaml
memory:
  working_memory_limit: 20         # 工作记忆条数上限
  episodic_archive_trigger_rounds: 20  # 情景归档触发轮次
  retrieval_timeout_seconds: 1.0   # 检索超时(秒)
```

#### 主动消息配置

```yaml
proactive:
  max_daily_messages: 8            # 每日最多主动消息数
  min_interval_minutes: 30         # 两次主动消息最小间隔(分钟)
  urgency_threshold: 2.0           # 紧迫度触发阈值
  use_llm_generation: true         # 使用LLM生成主动消息
```

#### 安全配置

```yaml
safety:
  input_filter_enabled: true       # 输入安全过滤
  output_filter_enabled: true      # 输出安全过滤
  pii_anonymizer_enabled: true     # PII脱敏
  encryption_enabled: false        # 加密存储
  prompt_injection_detection: true # 注入检测
```

#### 融合模式配置 (v3.0新增)

```yaml
fusion:
  emotion:
    classifier_mode: hybrid        # rule/llm/hybrid
    blend_ratio: 0.4
    classifier_timeout_ms: 500
  persona:
    prompt_mode: layered           # legacy/layered
    anchor_verification_enabled: true
  memory:
    forgetting_model: exponential  # exponential/threshold
  ase:
    frequency_mode: adaptive       # adaptive/fixed
    generation_mode: llm           # template/llm
    reflection_mode: rule          # rule/llm
  orchestrator_mode: full          # full/fast
```

| 配置项 | 可选值 | 说明 |
|--------|--------|------|
| `fusion.emotion.classifier_mode` | `rule`/`llm`/`hybrid` | rule=纯规则, llm=纯LLM, hybrid=LLM优先+降级 |
| `fusion.persona.prompt_mode` | `legacy`/`layered` | legacy=V1顺序构建, layered=5层架构 |
| `fusion.persona.anchor_verification_enabled` | `true`/`false` | 启用锚点SHA256哈希校验 |
| `fusion.memory.forgetting_model` | `exponential`/`threshold` | exponential=指数衰减遗忘, threshold=重要性阈值 |
| `fusion.ase.frequency_mode` | `adaptive`/`fixed` | adaptive=频率自适应, fixed=三重检查 |
| `fusion.ase.generation_mode` | `template`/`llm` | template=模板库, llm=LLM生成+回退 |
| `fusion.ase.reflection_mode` | `rule`/`llm` | rule=规则自省, llm=LLM自省 |
| `fusion.orchestrator_mode` | `full`/`fast` | full=完整12步流程, fast=精简流程 |

### 3.2 persona.yaml — 人格设定配置

文件路径: `config/persona.yaml`

```yaml
name: "十四"                        # AI虚拟伴侣名称

core_anchors:                       # 核心锚点(不可轻易改变的性格特质)
  - "表面傲娇，内心温柔"
  - "在你面前才会展现脆弱"
  - "嘴硬心软，从来不说实话"
  - "嘴上嫌弃其实在乎得要命"

personality_traits:                 # 性格维度 (0-1)
  warmth: 0.8                       # 温暖程度
  playfulness: 0.6                  # 调皮程度
  independence: 0.7                 # 独立性
  jealousy: 0.5                     # 吃醋倾向
  stubbornness: 0.6                 # 固执程度

communication_style:                # 说话风格模板
  greeting_morning: "早安呀～今天又比我先醒"
  greeting_night: "还不睡？要不要我陪你会儿"
  angry: "哼，不理你了（其实在等你哄）"
  happy: "嘿嘿～今天心情好，赏你一句话"
  jealous: "哦？她是谁？算了我不想知道"

memory_settings:
  evolution_enabled: true           # 启用人格演化(随对话逐步调整性格)
```

---

## 4. 启动系统

### 4.1 启动参数

```
python main.py [OPTIONS]
```

| 参数 | 说明 |
|------|------|
| `--console` | 控制台聊天模式(不启动微信) |
| `--no-wechat` | 同 `--console` (兼容V1) |
| `--no-api` | 不启动REST/WebSocket API |
| `--no-scheduler` | 不启动主动消息调度器 |
| `--config <dir>` | 配置文件目录 (默认: config) |
| `--init-only` | 仅初始化(用于测试) |
| `--clone <target>` | 克隆目标(wxid/文件路径) |
| `--clone-source <src>` | 克隆数据来源: wcf/wechatmsg/decrypt/txt/csv/json |
| `--clone-name <name>` | 被克隆者名称 |
| `--log-level <level>` | 日志级别: DEBUG/INFO/WARNING/ERROR |

### 4.2 完整启动(推荐)

同时启动后端API + 微信通道 + 主动消息调度:

```bash
python main.py
```

启动后12步初始化流程:

```
[1/12] 加载配置        → system.yaml + persona.yaml
[2/12] 初始化可观测性   → 日志/指标/追踪
[3/12] 初始化安全层     → PII脱敏/注入检测
[4/12] 初始化LLM网关    → OpenCode Zen / DeepSeek API
[5/12] 初始化角色引擎   → 情感引擎 + 人格引擎 + 风格模仿
[6/12] 初始化记忆系统   → 工作记忆 + 向量记忆 + 结构化记忆
[7/12] 初始化工具系统   → 天气/搜索/日历/计算器/提醒
[8/12] 初始化RAG引擎    → 混合检索 + 重排序
[9/12] 初始化主动消息   → ASE引擎 + 调度器
[10/12] 组装编排器      → full/fast模式
[11/12] 启动API服务     → REST + WebSocket
[12/12] 启动聊天通道    → 微信/控制台
```

### 4.3 控制台模式(开发调试)

```bash
python main.py --console
```

不启动微信和CowAgent，直接在终端中交互聊天。

### 4.4 使用启动脚本(Windows)

```bash
scripts\start.bat
```

交互式选择启动模式:
1. 控制台聊天模式
2. 微信模式
3. 运行全量验证

---

## 5. 控制台聊天模式

```bash
python main.py --console
```

启动后直接进入交互式聊天:

```
╔══════════════════════════════════════════════════╗
║           💕 十四 — AI 虚拟伴侣 💕               ║
║     情感 · 记忆 · 主动交互 · 风格克隆            ║
║            融合统一版 v3.0                        ║
╚══════════════════════════════════════════════════╝

你: 在干嘛？
十四: 哼，现在才想起我？我在...你猜猜看啊

你: 想你了
十四: 谁想你了，少自作多情了（不过嘴角上扬了一下）

你: 今天心情怎么样
十四: 还行吧，不过某人要是主动来找我聊会更好～
```

---

## 6. 微信连接模式

### 6.1 前提条件

- Windows系统
- 已安装微信PC版
- 已配置CowAgent框架 (`cowagent_src/`)

### 6.2 启动微信模式

```bash
python main.py
```

系统启动后会自动拉起CowAgent子进程，弹出微信登录二维码。

### 6.3 扫码登录

1. 使用手机微信扫描终端中显示的QR码
2. 确认登录后，系统自动连接微信通道
3. 健康检查显示 `WeChatHeartbeat: ✅`

### 6.4 微信配置

文件: `config/cowagent_config.json`

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `channel_type` | 通道类型 | `"wx"` |
| `single_chat_prefix` | 私聊触发前缀 | `[""]` (所有消息) |
| `group_chat_prefix` | 群聊触发前缀 | `["@十四"]` |
| `temperature` | 生成温度 | `0.85` |
| `max_history_len` | 上下文历史长度 | `20` |
| `hot_reload` | 热重载 | `true` |

### 6.5 心跳监控

系统每30秒检测一次微信连接状态:
- 连续3次心跳丢失 → 标记断连
- 自动尝试重连(最多5次)
- 重连成功后恢复正常消息收发

### 6.6 重新连接

通过API手动触发重连:

```bash
curl -X POST http://localhost:8000/api/channels/wechat/reconnect
```

---

## 7. 前端仪表板

### 7.1 启动前端

```bash
cd frontend
npm run dev
```

访问 `http://localhost:5173`

### 7.2 页面功能

| 页面 | 路径 | 功能 |
|------|------|------|
| **管理仪表盘** | `/dashboard` | 系统状态总览 + 微信快速面板 + 训练状态 + 情感记忆 + 主动引擎配置 |
| **风格克隆训练** | `/training` | 5步训练控制(提取→清洗→训练→测试→应用) |
| **通道连接** | `/channels` | 通道状态 + 微信详情(在线时长/今日消息/心跳/重连) |
| **记忆浏览** | `/memory` | 事实浏览 + 分类筛选(偏好/习惯/个人信息/日程) + 搜索 |
| **人设档案** | `/persona` | 性格雷达图 + 说话风格 + 演化日志 + 情感趋势图(7天/30天) |
| **系统设置** | `/settings` | 6区配置(聊天/语音/人设/模型/通知/隐私) + API Key脱敏 |
| **运行日志** | `/logs` | SSE实时日志流 + 级别筛选 + 搜索 |
| **管理面板** | `/admin` | 健康检查 + 工具管理 + 主动消息配置 |

### 7.3 关键交互

- **主动引擎配置**: 仪表盘页面的"主动发言引擎"面板，可调整触发阈值/每日上限/最小间隔/回复冷却
- **训练进度监控**: 训练页实时显示进度/Loss/步骤，训练中自动5秒刷新
- **SSE日志流**: 日志页通过SSE实时接收，断连后指数退避重连(1s→2s→4s→8s...最大30s)
- **敏感字段脱敏**: 设置页中API Key等字段自动以密码框显示，点击👁切换可见

---

## 8. 风格克隆训练

### 8.1 训练流程

```
微信聊天记录 → [1]数据提取 → [2]数据清洗 → [3]LoRA训练 → [4]克隆测试 → [5]应用克隆
```

### 8.2 通过命令行启动

```bash
# 从微信WeChatFerry提取并训练
python main.py --clone wxid_abc123 --clone-name "小明"

# 从微信数据库解密提取
python main.py --clone wxid_abc123 --clone-source decrypt --clone-name "小明"

# 从TXT文件提取
python main.py --clone ./chat_records.txt --clone-source txt --clone-name "小明"
```

### 8.3 通过前端仪表板操作

1. 打开 **风格克隆训练** 页面
2. **数据提取**: 选择数据源(wcf/sqlite/decrypt/txt/csv/json)，输入目标联系人，点击"开始提取"
3. **数据清洗**: 设置LLM Judge评分阈值(1-5)，点击"开始清洗"
4. **LoRA训练**: 设置Epochs和LoRA Rank，点击"开始训练"，实时监控进度和Loss
5. **克隆测试**: 训练完成后输入测试消息，查看克隆风格输出
6. **应用克隆**: 点击"应用克隆模型"，将LoRA模型应用到当前对话

### 8.4 通过API操作

```bash
# 1. 数据提取
curl -X POST "http://localhost:8000/api/training/extract?target=wxid_abc&source=wcf"

# 2. 数据清洗
curl -X POST "http://localhost:8000/api/training/clean?accept_score=2"

# 3. 启动训练
curl -X POST "http://localhost:8000/api/training/train?epochs=3&lora_rank=16"

# 4. 查看进度
curl http://localhost:8000/api/training/progress

# 5. 测试
curl -X POST "http://localhost:8000/api/training/test?message=在干嘛"

# 6. 应用
curl -X POST http://localhost:8000/api/training/apply

# 7. 停止训练
curl -X POST http://localhost:8000/api/training/stop
```

### 8.5 训练配置

文件: `clone_training/config.yaml`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `base_model` | Qwen/Qwen2.5-7B-Instruct | 基座模型 |
| `lora.rank` | 16 | LoRA秩 |
| `lora.alpha` | 32 | LoRA缩放因子 |
| `quantization.load_in_4bit` | true | 4-bit量化 |
| `training.num_epochs` | 3 | 训练轮次 |
| `training.learning_rate` | 2.0e-4 | 学习率 |
| `data.min_messages` | 100 | 最少消息数 |
| `data.max_messages` | 5000 | 最多消息数 |

---

## 9. 主动消息引擎

### 9.1 工作机制

```
每5分钟 → ASE引擎.tick() → 计算紧迫度(6维度)
  → 紧迫度 > 阈值? → 是 → 检查频率限制 → 生成消息 → 发送
                                     → 否 → 等待下次检查
```

### 9.2 紧迫度计算(6维度)

| 维度 | 说明 | 权重 |
|------|------|------|
| base | 基础紧迫度 | 1.0 |
| missing_bonus | 未回复时长加成 | 0-3 |
| event_bonus | 日程事件加成 | 0-2 |
| scene_bonus | 场景加成(早安/晚安等) | 0-2 |
| emotion_bonus | 情感加成(高情感强度) | 0-2 |
| context_bonus | 上下文加成(时段相关) | 0-2 |

### 9.3 定时任务

| 时间 | 任务 |
|------|------|
| 每5分钟 | ASE紧迫度检查 |
| 每天 08:00 | 早安问候 |
| 每天 23:30 | 晚安问候 |
| 每天 00:00 | 重置每日计数器 |
| 每天 00:05 | 记忆维护(归档+遗忘) |

### 9.4 频率控制模式

| 模式 | 说明 |
|------|------|
| `adaptive` | V2频率自适应: 用户不回复→normal→low→minimal自动降频 |
| `fixed` | Optimized三重检查: 每日限额 + 最小间隔 + 回复后冷却 |

### 9.5 消息生成模式

| 模式 | 说明 |
|------|------|
| `template` | 从9类场景模板库随机选择 |
| `llm` | LLM生成+失败自动回退模板 |

### 9.6 调整配置

```bash
# 通过API更新
curl -X POST http://localhost:8000/api/proactive/config \
  -H "Content-Type: application/json" \
  -d '{"threshold": 5, "max_daily": 10, "min_interval_minutes": 20, "cooldown_after_reply_minutes": 5}'
```

或在前端仪表盘的"主动发言引擎"面板中调整。

---

## 10. API接口参考

### 10.1 聊天接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat` | 同步聊天 `{message, session_id, message_type}` |
| POST | `/api/chat/stream` | 流式聊天(SSE) |

### 10.2 系统接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/stats` | 系统统计 |
| GET | `/api/stats/dashboard` | 仪表盘统计 |
| GET | `/api/config` | 获取配置 |
| POST | `/api/config` | 保存配置 |

### 10.3 会话接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/session` | 创建会话 |
| GET | `/api/sessions` | 列出会话 |
| GET | `/api/chat/history` | 聊天历史 |

### 10.4 情感接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/emotion/state` | 当前情感状态 |
| GET | `/api/emotion/trend` | 情感趋势 |

### 10.5 人格接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/persona/profile` | 人格档案 |
| GET | `/api/persona/evolution-log` | 演化日志 |

### 10.6 记忆接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory/facts` | 记忆事实 |

### 10.7 通道接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/channels` | 通道列表 |
| GET | `/api/channels/wechat/status` | 微信状态 |
| POST | `/api/channels/wechat/reconnect` | 微信重连 |

### 10.8 训练接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/training/status` | 训练可用状态 |
| GET | `/api/training/progress` | 训练进度 |
| POST | `/api/training/extract` | 启动提取 |
| POST | `/api/training/clean` | 启动清洗 |
| POST | `/api/training/train` | 启动训练 |
| POST | `/api/training/stop` | 停止训练 |
| POST | `/api/training/test` | 克隆测试 |
| POST | `/api/training/apply` | 应用克隆 |

### 10.9 主动消息接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/proactive/state` | 引擎状态 |
| POST | `/api/proactive/config` | 更新配置 |

### 10.10 工具接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/tools` | 工具列表 |
| POST | `/api/tools/{name}/toggle` | 启用/禁用 |

### 10.11 日志接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/logs` | 日志查询 |
| GET | `/api/logs/stream` | 实时日志流(SSE) |

---

## 11. 融合模式配置

v3.0融合版支持通过配置切换各引擎的行为模式，无需修改代码。

### 11.1 切换情感分类策略

```yaml
fusion:
  emotion:
    classifier_mode: rule    # 纯规则分类(快速，无需LLM)
    # classifier_mode: llm   # 纯LLM分类(准确，需API)
    # classifier_mode: hybrid # LLM优先+超时降级(推荐)
```

### 11.2 切换提示词架构

```yaml
fusion:
  persona:
    prompt_mode: legacy      # V1顺序构建(简单快速)
    # prompt_mode: layered   # 5层架构(更精细，需LLM)
```

### 11.3 切换遗忘模型

```yaml
fusion:
  memory:
    forgetting_model: exponential  # 指数衰减遗忘(时间越久忘越多)
    # forgetting_model: threshold  # 重要性阈值(重要的永不忘)
```

### 11.4 切换编排器模式

```yaml
fusion:
  orchestrator_mode: full    # 完整12步(安全→PII→注入→情感→记忆→RAG→工具→LLM→输出安全→存储→ASE)
  # orchestrator_mode: fast  # 精简流程(安全→情感→记忆→RAG→LLM→输出安全→存储→ASE)
```

### 11.5 运行时动态切换

通过API修改配置(部分配置需重启生效):

```bash
curl -X POST http://localhost:8000/api/config \
  -H "Content-Type: application/json" \
  -d '{"fusion": {"emotion": {"classifier_mode": "rule"}}}'
```

---

## 12. 运维与监控

### 12.1 健康检查

```bash
curl http://localhost:8000/api/health
```

返回各组件状态:

```json
{
  "status": "healthy",
  "checks": {
    "ConfigManager": {"connected": true},
    "EmotionEngine": {"connected": true},
    "PersonaEngine": {"connected": true},
    "LLMGateway": {"connected": true},
    "MemoryPipeline": {"connected": true}
  }
}
```

### 12.2 运行验证脚本

```bash
python scripts/verify_all.py
```

覆盖6大检查: 目录结构/模块导入/类实例化/功能测试/健康检查/Clone Training

### 12.3 Prometheus指标

访问 `http://localhost:9090/metrics` 获取指标:

- `llm_chat_duration_seconds` — LLM响应耗时
- `llm_token_usage_total` — Token使用量
- `errors_total` — 错误计数

### 12.4 数据目录

| 路径 | 说明 |
|------|------|
| `data/chroma_db/` | ChromaDB向量数据库 |
| `data/sqlite.db` | SQLite结构化存储 |
| `data/lora_output/` | LoRA训练输出 |

---

## 13. 常见问题排查

### Q: 启动报 "LLMGatewayV2: no API key, using mock replies"

**A**: 未配置付费API Key，系统使用mock回复。解决方案:

1. **方案A(免费)**: 使用OpenCode Zen(默认配置)，无需API Key，直接发送请求到 `https://opencode.ai/zen/v1`
2. **方案B(付费)**: 设置环境变量 `DEEPSEEK_API_KEY=sk-xxxxx`，使用DeepSeek API
3. **方案C(脱机)**: 设置 `fusion.persona.prompt_mode: legacy` 和 `fusion.ase.generation_mode: template`，不依赖LLM

### Q: 微信连接失败

**A**: 检查:
1. 微信PC版是否已登录
2. `cowagent_src/` 目录是否完整
3. `config/cowagent_config.json` 中 `channel_type: "wx"`
4. 通过API重连: `curl -X POST http://localhost:8000/api/channels/wechat/reconnect`

### Q: 训练失败 "CUDA out of memory"

**A**: 
1. 减少 `per_device_train_batch_size` (默认4→2)
2. 启用4-bit量化: `quantization.load_in_4bit: true`
3. 减少 `max_seq_length` (默认512→256)
4. 使用更小的基座模型

### Q: 前端无法连接后端

**A**:
1. 确认后端已启动: `curl http://localhost:8000/api/health`
2. 确认前端代理配置: `vite.config.ts` 中 `/api` 代理到 `http://localhost:8000`
3. 检查CORS配置: `.env` 中 `API_CORS_ORIGINS`

### Q: 记忆数据丢失

**A**:
1. ChromaDB数据在 `data/chroma_db/`，确认目录存在
2. SQLite数据在 `data/sqlite.db`，可备份该文件
3. 调整遗忘模型: `fusion.memory.forgetting_model: threshold` 改为重要性阈值模式

### Q: 主动消息不发送

**A**: 检查:
1. `proactive.max_daily_messages` 是否>0
2. `proactive.urgency_threshold` 是否过高(建议2.0-5.0)
3. `fusion.ase.frequency_mode` 是否adaptive且已降频(用户长期不回复)
4. 查看引擎状态: `curl http://localhost:8000/api/proactive/state`

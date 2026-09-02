# 主动发消息模块完善方案

> **📋 文档状态卡**（2026-08-26 治理标注 · 决策链见 `docs/DECISION_LEDGER.md`）
> - **层级**：L2 决策档案 | **结局**：⏸️ 分级混合
> - **✅ 已实施**：P0.5 通道注册模式修复（07-27 reinit 落地）；指数退避重连（5s→300s）
> - **⚠️ 待核验阶段**：其余阶段需对照 07-28 编排器重构后的实况逐项判定，勿直接按本文开工
> - 判定基准：`CODE_GRAPH.md` proactive 区 + 实际 scheduler 行为。

## 一、项目概述

"十四"是一个基于大语言模型的AI伙伴系统，通过微信和Web渠道与用户进行有情感温度的对话。主动发消息是项目的核心特色功能之一。

技术栈：Python 3.12 + FastAPI + React/TypeScript + ChromaDB + SQLite + DeepSeek API

## 二、当前架构分析

### 2.1 主动消息系统架构图

```
触发层: ProactiveScheduler (APScheduler, 每5分钟tick)
    ↓
决策层: ASEEngine (六维紧迫度 + 频率控制 + 反省引擎)
    ↓
生成层: MessageGenerator (模板库3条/类 × 9类 + LLM双模式)
    ↓
发送层: send_message_func → ❌ 仅 print() 到控制台
    ↓
❌ WebSocket broadcast_proactive() — 已实现但未被调用
❌ GirlfriendBot.send_message() — 已实现但未被调用
❌ WeChatConnector._send_text() — 已实现但未被调用
```

### 2.2 核心组件说明

| 组件 | 文件 | 职责 |
|------|------|------|
| ASEEngine | proactive/ase_engine.py | 核心决策引擎：六维紧迫度计算、9种消息类型、反省引擎、双模式频率控制 |
| ProactiveScheduler | proactive/scheduler.py | APScheduler定时调度：5分钟ASE检查、早安08:00、晚安23:30、每日维护 |
| MessageGenerator | proactive/ase_engine.py (内嵌类) | 消息生成：模板模式 + LLM模式双通道 |
| FrequencyAdapter | proactive/ase_engine.py (内嵌类) | V2自适应频率：normal(8条/天) → low(3条/天) → minimal(1条/周) |
| FrequencyController | proactive/ase_engine.py (内嵌类) | 三重检查：每日限额 + 最小间隔(30分钟) + 回复后冷却(10分钟) |
| ReflectionEngine | proactive/ase_engine.py (内嵌类) | 反省引擎：每次对话后生成内心独白，影响紧迫度 |
| ContextAnalyzer | proactive/ase_engine.py (内嵌类) | 时段/场景分析：工作日/周末、早中晚、节假日 |
| WeChatProactiveMessenger | shisi/wechat/proactive_messenger.py | 微信增强：好感度感知的早安/晚安定制 |

### 2.3 六维紧迫度系统

```python
@dataclass
class UrgencyState:
    base: float = 0.0           # 基础紧迫度（每次tick +0.1，上限3.0）
    missing_bonus: float = 0.0  # 想念加成（距上次聊天小时数 × 0.5，上限5.0）
    event_bonus: float = 0.0    # 事件加成
    scene_bonus: float = 0.0    # 场景加成（早安/晚安/饭点 +1.0~1.5）
    emotion_bonus: float = 0.0  # 情感加成（伤心/生气 +1.5，撒娇/开心 +0.5）
    context_bonus: float = 0.0  # 上下文加成（周末 +0.5，晚上 +0.3）
```

紧迫度等级：>=8 非常想找你 / >=5 有点想你 / >=3 想找人说话 / <3 还好

### 2.4 消息类型（9种）

MORNING_GREETING(早安) / NIGHT_GREETING(晚安) / MISS_YOU(想你) / BORED(无聊) / CARE_WEATHER(天气关怀) / CARE_MEAL(饭点关怀) / JEALOUS(吃醋) / SHARE(分享) / WORRY(担心)

### 2.5 消息发送完整链路（当前状态）

**控制台模式（当前实际生效）**：
```
ProactiveScheduler._check_ase()
  → ase.tick(hours)
    → 频率检查通过 + 紧迫度达标
    → 生成消息内容
  → self._send(message)
    → main.py 中的 send_proactive() 函数
    → logger.info() + print()  // 仅打印到控制台！
```

**WebSocket推送（已实现未接通）**：
```
WebSocketServer.broadcast_proactive(content)
  → 所有连接的 WebSocket 客户端收到 {"type": "proactive", "content": "..."}
    → 前端 ProactiveToast 显示
```

**微信通道（已实现未接通）**：
```
GirlfriendBot.send_message(to_user, message)
  → cowagent_src.app._channel_mgr → weixin channel
    → channel.send(reply, context)
```

## 三、问题清单

| # | 严重度 | 问题 | 位置 | 影响 |
|---|--------|------|------|------|
| 1 | 致命 | send_message_func 只做 print()，消息无真实通道 | main.py:939-941, 1141-1143 | 主动消息功能完全无效 |
| 2 | 高 | 早安/晚安定时任务与ASE场景触发时间重叠 | scheduler.py vs ase_engine.py | 可能重复发送早安/晚安 |
| 3 | 高 | proactive.yaml 提示词模板未被ASE引擎使用 | config/prompts/proactive.yaml | LLM生成消息缺少角色设定 |
| 4 | 高 | GirlfriendBot.WECHAT_CONTACT_ID 为空 | girlfriend_bot.py:57 | 微信发送无目标用户 |
| 5 | 中 | 模板库每种类型仅3条，无去重机制 | ase_engine.py:48-94 | 消息重复率高 |
| 6 | 中 | 频率控制状态全在内存，重启丢失 | FrequencyAdapter/FrequencyController | 重启后频率限制失效 |
| 7 | 中 | 无用户在线状态感知 | 全局缺失 | 离线时可能造成打扰 |
| 8 | 中 | _hours_since_last_check() 传给ASE的值约0.083 | scheduler.py:217-221 | 紧迫度增长过慢 |
| 9 | 低 | shisi/wechat/proactive_messenger.py 未被集成 | shisi/wechat/proactive_messenger.py | 好感度增强功能闲置 |

## 四、完善方案

### 设计原则

1. **能复用不自造**：充分利用已有的 WebSocket 广播、GirlfriendBot 发送、shisi 增强等基础设施
2. **最小侵入**：不改变现有模块的公共接口，通过 send_message_func 统一出口改造
3. **渐进增强**：分阶段实施，每阶段可独立验证
4. **配置驱动**：所有行为参数可通过 system.yaml 配置

### 阶段一：打通发送通道（解决致命问题 #1）

**目标**：让主动消息真正到达用户（WebSocket + 微信）

**改动文件**：main.py（fast模式 + full模式）

**方案**：将 send_message_func 从纯 print 改造为多通道广播器

```python
def _create_proactive_sender(orchestrator=None, ws_server=None, wechat_connector=None):
    """创建主动消息发送器 — 多通道统一出口"""
    def send_proactive(msg: str):
        logger.info("[主动消息] %s", msg)
        print(f"\n💕 [十四主动] {msg}")

        # 通道1: WebSocket 推送到前端
        if ws_server:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(ws_server.broadcast_proactive(msg))
                else:
                    loop.run_until_complete(ws_server.broadcast_proactive(msg))
            except Exception as e:
                logger.warning("[主动消息] WebSocket推送失败: %s", e)

        # 通道2: 微信发送
        if wechat_connector:
            try:
                wechat_connector.send_text(msg)
            except Exception as e:
                logger.warning("[主动消息] 微信发送失败: %s", e)

    return send_proactive
```

**集成方式**：在 main.py 中，API 服务启动后获取 ws_server 实例，传入 _create_proactive_sender。

**验证标准**：
- WebSocket：前端 ProactiveToast 组件收到并显示主动消息
- 微信：微信用户收到主动消息文本
- 控制台：日志和打印正常输出
- 异常隔离：任一通道失败不影响其他通道

### 阶段二：修复重复触发和配置未使用（问题 #2, #3）

**目标**：消除早安/晚安重复发送，让 LLM 模式使用 proactive.yaml

**改动文件**：proactive/scheduler.py, proactive/ase_engine.py

**2.1 去除独立早安/晚安定时任务**

在 ProactiveScheduler.start() 中，移除 _morning_greeting 和 _night_greeting 两个 CronTrigger 注册。这些场景已由 ASEEngine._check_scene_triggers() 覆盖（早安7-9点，晚安22-24点）。

同时给 _check_scene_triggers() 添加每日一次限制，避免在场景时间窗口内每次 tick 都触发：

```python
def _check_scene_triggers(self) -> Optional[Dict[str, Any]]:
    now = datetime.now()
    hour = now.hour

    # 每日一次限制：检查今天是否已发送过该类型
    today = now.date()

    # 早安
    start, end = self._config["morning_hours"]
    if start <= hour < end:
        if self._last_morning_date != today:  # 新增检查
            self._last_morning_date = today
            msg = self._message_generator.generate_from_template(ProactiveType.MORNING_GREETING)
            self.urgency.scene_bonus = 1.5
            return {"type": "morning_greeting", "message": msg, "urgency": self.urgency.total}

    # 晚安同理...
```

**2.2 让 MessageGenerator 使用 proactive.yaml**

在 MessageGenerator.__init__() 中加载 proactive.yaml：

```python
def __init__(self, llm_gateway=None):
    self._llm = llm_gateway
    self._proactive_prompt = self._load_proactive_prompt()

def _load_proactive_prompt(self) -> str:
    """从 config/prompts/proactive.yaml 加载提示词模板"""
    try:
        import yaml
        prompt_path = Path(__file__).parent.parent / "config" / "prompts" / "proactive.yaml"
        if prompt_path.exists():
            with open(prompt_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return data.get("generation_prompt", "")
    except Exception as e:
        logger.warning("Failed to load proactive.yaml: %s", e)
    return ""
```

在 generate_with_llm() 中使用模板替代硬编码 prompt。

**验证标准**：
- 08:00 只收到一条早安消息（来自 ASE 场景触发）
- 23:30 只收到一条晚安消息
- LLM 模式生成的消息符合 proactive.yaml 中的角色设定

### 阶段三：增强消息多样性（问题 #5, #9）

**目标**：扩展模板库、集成好感度增强、添加去重

**改动文件**：proactive/ase_engine.py, config/prompts/proactive.yaml

**3.1 扩展模板库**

将每种消息类型从 3 条扩展到 8-10 条，按好感度等级分组：

```python
PROACTIVE_MESSAGES: Dict[str, Dict[str, List[str]]] = {
    "morning_greeting": {
        "low": [   # 好感度 0-3: 陌生人/认识/朋友
            "早安呀～今天又比我先醒",
            "早！新的一天开始了",
            "早上好，昨晚睡得好吗",
        ],
        "high": [  # 好感度 4-8: 好朋友/知己/暧昧/恋人/热恋/羁绊
            "早安～又梦到你了",
            "醒啦？今天也要想我哦",
            "早安呀，一睁眼就想找你",
        ],
    },
    # ... 其他类型同理
}
```

**3.2 集成 shisi 好感度增强**

在 ProactiveScheduler 中注入 WeChatProactiveMessenger，对早安/晚安消息进行好感度增强：

```python
# 在 scheduler._check_ase() 中
if msg_type in ["morning_greeting", "night_greeting"] and self._proactive_messenger:
    enhanced = self._proactive_messenger.enhance_proactive_message(message, character_id, emotion)
    message = enhanced["text"]
```

**3.3 添加去重机制**

在 ASEEngine 中添加已发送消息滑动窗口：

```python
def __init__(self, ...):
    self._recent_messages: Deque[str] = deque(maxlen=50)  # 最近50条

def _is_duplicate(self, message: str) -> bool:
    """检查消息是否在最近24小时内已发送过"""
    return message in self._recent_messages

def _record_proactive_sent(self) -> None:
    self._recent_messages.append(self._last_generated_message)
    # ... 原有逻辑
```

**验证标准**：
- 连续10条消息无完全重复
- 好感度高时早安/晚安消息更亲密
- 24小时内不发送相同文本

### 阶段四：状态持久化（问题 #6）

**目标**：频率控制状态重启后不丢失

**改动文件**：proactive/ase_engine.py

**方案**：添加 save_state()/load_state() 方法，将关键状态序列化到 JSON 文件：

```python
def save_state(self, path: str = "data/proactive_state.json"):
    state = {
        "daily_count": self._daily_message_count,
        "last_sent_time": self._last_proactive_time.isoformat() if self._last_proactive_time else None,
        "last_chat_time": self._last_chat_time.isoformat() if self._last_chat_time else None,
        "last_sent_type": self._last_sent_type,
        "affinity_level": self._affinity_level,
        "urgency": asdict(self.urgency),
        "freq_adapter": self._freq_adapter.to_dict() if self._freq_adapter else None,
        "freq_controller": self._freq_controller.to_dict() if self._freq_controller else None,
        "saved_at": datetime.now().isoformat(),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def load_state(self, path: str = "data/proactive_state.json"):
    if not Path(path).exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        state = json.load(f)
    # 恢复状态，检查日期有效性
```

在 scheduler 的每日重置任务中调用 save_state()，在 ASEEngine.__init__() 中调用 load_state()。

**验证标准**：
- 重启后 daily_message_count 恢复
- 重启后频率等级恢复
- 跨日自动重置计数

### 阶段五：时间计算修正（问题 #8）

**目标**：让紧迫度基于真实不活跃时长增长

**改动文件**：proactive/scheduler.py, main.py

**方案**：将 Orchestrator._last_chat_time 传入调度器：

```python
# main.py 中
scheduler = ProactiveScheduler(
    ase_engine=ase_engine,
    send_message_func=send_proactive,
    daily_maintenance_func=daily_maintenance,
    get_last_chat_time=lambda: orchestrator._last_chat_time,  # 新增
)

# scheduler.py 中
def _check_ase(self) -> None:
    if not self.ase:
        return
    try:
        if self._get_last_chat_time:
            last_chat = self._get_last_chat_time()
            if last_chat:
                hours = (datetime.now() - last_chat).total_seconds() / 3600
            else:
                hours = 99.0
        else:
            hours = self._hours_since_last_check()

        result = self.ase.tick(hours)
        # ...
```

**验证标准**：
- 用户2小时未聊天，紧迫度 missing_bonus = 1.0
- 用户24小时未聊天，紧迫度 missing_bonus = 5.0（上限）
- tick 间隔不再影响紧迫度计算

### 阶段六：在线状态感知（问题 #7）

**目标**：离线时降低发送频率

**改动文件**：proactive/scheduler.py, api/websocket_server.py

**方案**：

1. 在 WebSocketServer 中添加在线状态跟踪：

```python
class WebSocketServer:
    def __init__(self, ...):
        self._online_users: Set[str] = set()

    def is_user_online(self, user_id: str = "default") -> bool:
        return len(self._clients) > 0  # 简化版：有连接即为在线
```

2. 在 scheduler 中检查在线状态：

```python
def _check_ase(self) -> None:
    # 检查用户是否在线
    is_online = True
    if self._is_online_check:
        is_online = self._is_online_check()

    if not is_online:
        # 离线时仅允许场景消息（早安/晚安），且频率降低
        logger.debug("用户离线，跳过非场景主动消息")
        # 仍然调用 tick 更新紧迫度，但不发送
        self.ase.tick(hours, dry_run=True)
        return
```

3. 在 ASEEngine.tick() 中添加 dry_run 参数：

```python
def tick(self, hours_since_last_chat: float, dry_run: bool = False) -> Optional[Dict]:
    """dry_run=True 时只更新紧迫度，不发送消息"""
    # ... 更新紧迫度逻辑不变
    if dry_run:
        return None  # 不返回消息
    # ... 原有发送逻辑
```

**验证标准**：
- 用户在线时正常发送所有类型消息
- 用户离线时仅更新紧迫度，不发送
- 用户重新上线后紧迫度累积正确

## 五、验证计划

### 5.1 单元测试（每阶段完成后执行）

| 轮次 | 验证内容 | 测试方法 | 预期结果 |
|------|----------|----------|----------|
| 1 | WebSocket 推送通道 | 模拟 ASE 触发，检查 WebSocket 消息 | 前端收到 {"type":"proactive",...} |
| 2 | 微信发送通道 | 模拟 ASE 触发，检查微信消息 | 微信收到主动消息 |
| 3 | 异常隔离 | 断开 WebSocket/微信，触发 ASE | 其他通道正常，日志有 warning |
| 4 | 去除重复早安/晚安 | 等待 08:00 和 tick 重叠 | 只收到一条早安 |
| 5 | proactive.yaml 加载 | LLM 模式生成消息 | 消息符合 YAML 角色设定 |
| 6 | 模板扩展 | 连续生成 10 条 | 无完全重复 |
| 7 | 好感度增强 | 设置不同好感度值 | 高好感度消息更亲密 |
| 8 | 去重机制 | 24小时内触发相同类型 | 不发送相同文本 |
| 9 | 状态持久化 | 保存状态 → 重启 → 检查 | 频率等级和计数恢复 |
| 10 | 时间计算修正 | 模拟2小时/24小时不活跃 | 紧迫度正确增长 |
| 11 | 在线状态感知 | 断开 WebSocket → 触发 ASE | 离线时不发送非场景消息 |
| 12 | 端到端集成 | 完整流程测试 | 调度→决策→生成→发送→展示 全链路通畅 |

### 5.2 集成测试

端到端测试流程：
1. 启动系统（full 模式）
2. 连接 WebSocket
3. 等待 ASE tick 触发（或手动调用 /api/proactive/trigger）
4. 验证 WebSocket 收到 proactive 消息
5. 验证前端 ProactiveToast 显示
6. 验证控制台日志正确
7. 发送一条用户消息，验证紧迫度重置
8. 等待下一个 tick，验证频率控制生效
9. 重启系统，验证状态恢复
10. 断开 WebSocket，验证离线行为

## 六、外部参考

### 6.1 chatgpt-on-wechat (CowAgent) 插件体系

本项目基于 CowAgent 框架，其插件体系提供 ON_HANDLE_CONTEXT / ON_DECORATE_REPLY / ON_SEND_REPLY 三类事件钩子。主动发消息可参考其 SchedulerTool 的定时任务注入机制（agent_bridge.py 的 remember_scheduled_output 方法）。

### 6.2 ChatGPT Pulse 主动推送

OpenAI 的 ChatGPT Pulse 功能提供了主动消息的参考范式：每天晚上主动研究用户聊天记录，清晨推送个性化内容。本项目的 ASE 引擎已具备类似能力（反省引擎 + 场景触发），需要打通发送通道即可实现。

### 6.3 可复用的现有基础设施

| 基础设施 | 位置 | 复用方式 |
|----------|------|----------|
| WebSocket broadcast_proactive() | api/websocket_server.py:95-97 | 直接调用 |
| GirlfriendBot.send_message() | cowagent_adapter/girlfriend_bot.py:160-198 | 通过 send_message_func 调用 |
| WeChatConnector._send_text() | wechat_direct/connector.py:176-188 | 通过 send_message_func 调用 |
| WeChatProactiveMessenger | shisi/wechat/proactive_messenger.py | 注入到 scheduler |
| REST API /api/proactive/* | api/app_factory.py | 已有，无需改动 |
| 前端 ProactiveToast | frontend/src/components/chat/ProactiveToast.tsx | 已有，无需改动 |
| 前端 ProactiveEnginePanel | frontend/src/components/common/ProactiveEnginePanel.tsx | 已有，无需改动 |

## 七、风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| WebSocket 推送在同步上下文中失败 | 中 | 高 | 使用 asyncio.ensure_future + 异常隔离 |
| 微信发送频率过高被限制 | 中 | 中 | 频率控制已有，保持现有上限 |
| LLM 生成消息质量不稳定 | 中 | 低 | 模板回退机制已有 |
| 状态持久化文件损坏 | 低 | 中 | JSON 校验 + 异常回退到默认值 |
| APScheduler 时区问题 | 低 | 低 | 使用本地时间，不依赖时区设置 |

## 八、实施优先级

```
阶段一（打通通道）→ 阶段二（修复重复）→ 阶段五（时间修正）
       ↓                                        ↓
   立即见效                              紧迫度准确性

阶段三（消息多样性）→ 阶段四（状态持久化）→ 阶段六（在线感知）
       ↓                                        ↓
   用户体验                              高级功能
```

建议实施顺序：1 → 2 → 5 → 3 → 4 → 6

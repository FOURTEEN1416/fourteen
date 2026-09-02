# 📚 Proactive + Plugins 模块阅读报告

**读取进度**：7/7 文件 ✅ 全部穷举阅读 | **读取时间**：2026-08-26  
**模块定位**：主动搭话引擎（ASE）+ 插件系统  

---

## 🧠 proactive/ 核心组件概览（5文件）

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出 | 导出ASEEngine等核心组件 |
| `ase_engine.py` (37.3KB) | **ASE主动发言引擎融合版v2** | 融合V1反省+V2频率自适应+Optimized情境感知；9种ProactiveType枚举(morning/night_greeting/miss_you/bored/care_weather/care_meal/jealous/share/worry)；PROACTIVE_MESSAGES模板库(每类8-10条,按low/high好感度分组)；UrgencyState六维紧迫度(base/missing/event/scene/emotion/context)+total属性+level分级；ContextAnalyzer时段分析(weekend/evening/night加分)；MessageGenerator(template/llm双模式+proactive.yaml加载)；`_local_now()`时区修正(非UTC+8系统强制转北京时区) |
| `frequency.py` | 频率控制双方案 | **FrequencyAdapter**(自适应:normal→low→minimal三级降档,5次未回minimal/3次未回low,回复后恢复normal)；**FrequencyController**(固定:三重检查daily_limit/min_interval 30min/cooldown 10min)；两者均支持to_dict/from_dict持久化 |
| `reflection.py` | 反省引擎 | InnerMonologue数据类(thought/type/urgency_delta/created_at)；ReflectionEngine双模式：LLM模式(prompt生成内心独白,5类型miss_you/happy/worry/jealous/bored)/Rule模式(关键词规则:她|别人|女生→jealous 1.5,累|忙|加班→worry 0.8,>8h未聊→miss_you 2.0)；urgency映射表{miss_you:2.0,jealous:1.5,worry:0.8,bored:0.5,happy:0.3} |
| `scheduler.py` (15.4KB) | 定时调度器(APScheduler) | 5个定时任务：①ASE检查每5分钟②每日维护00:05③每日重置00:00④状态持久化每10分钟⑤通道健康检查每60秒；**多通道注册表**(register_channel:wechat/websocket/console,优先级投递,失败自动fallback)；免打扰时段23:00-07:00(`_is_quiet_hours`已修复旧版UTC硬编码+8偏移bug)；离线时dry_run只更新紧迫度不发送；每日维护集成情感时间衰减(apply_time_decay)+好感度衰减(DecayEngine经api.deps.shisi_reg调用)；通道重连修复：factory()返回None不算成功避免虚假日志 |

---

## 🧠 plugins/ 核心组件概览（2文件）

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出 | 仅导出WeatherPlugin |
| `weather.py` | 天气插件 | OpenWeatherMap API(免费版)；30分钟缓存TTL；check_trigger()场景触发：rain→带伞/snow→保暖/>35°C→喝水/<5°C→加衣；无API Key时_simulate()模拟数据(开发测试用)；httpx客户端10s超时 |

---

## 🔧 主要设计模式

### 1. 六维紧迫度模型
```
urgency.total = base(反省累积,上限3.0)
             + missing_bonus(失联时长×0.5,上限5.0,>0.5h起算)
             + event_bonus
             + scene_bonus(早安晚安1.5/吃饭1.0)
             + emotion_bonus(伤心生气1.5/撒娇开心0.5)
             + context_bonus(周末0.5/evening night 0.3)

触发阈值: total>=8→miss_you / >=6→miss_you|worry 
        / >=4→care系列|share / 场景触发需>=2.0
```

### 2. 三层消息生成降级
- LLM生成(generation_mode="llm") → 模板生成 → 模板fallback重试3次去重
- 去重机制：_recent_messages deque(maxlen=50)，重复即换模板

### 3. 场景每日一次限制
- _last_morning_date/_last_night_date/_last_meal_date 记录已触发日期
- night_hours跨零点处理(hour >= start or hour < 1,检查日期回退一天)

### 4. 状态全持久化
- JSON状态文件：daily_count/时间戳/紧迫度五维/最近50条消息/freq控制器状态
- 每10分钟定时保存 + stop时保存 + 启动时恢复

---

## ⚠️ 关键技术要点

### 时区处理（两处bug修复痕迹）
1. `_local_now()`：检测系统时区，偏差UTC+8超过1小时则强制转北京时间
2. scheduler `_is_quiet_hours()`：注释明确记录旧bug——硬编码+8且未取模导致UTC 16:00-23:00时段得到24-31非法小时

### 与其他模块的集成点
| 集成对象 | 方式 |
|---------|------|
| orchestrator | orchestrator._after_process调用ase.on_chat() |
| EmotionEngine | scheduler每日维护调用apply_time_decay(hours) |
| shisi_reg.affinity_enhancer | 每日好感度衰减apply_decay(cid)遍历所有角色 |
| api.deps | 通过deps.shisi_reg获取服务注册表 |

### 免打扰与在线感知
- 免打扰时段跳过非紧急消息（23:00-07:00）
- is_online_check回调：用户离线仅dry_run更新紧迫度

---

## 📋 已读文件列表（完整7个）

1. proactive/__init__.py
2. proactive/ase_engine.py
3. proactive/frequency.py
4. proactive/reflection.py
5. proactive/scheduler.py
6. plugins/__init__.py
7. plugins/weather.py
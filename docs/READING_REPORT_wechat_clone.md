# 📚 WeChat Direct + WeClone Adapter + Clone Training 模块阅读报告

**读取进度**：10/10 文件 ✅ 全部穷举阅读 | **读取时间**：2026-08-26  
**模块定位**：微信直连集成 + 风格克隆管线（WeChatMsg桥接 + 数据提取/清洗/风格分析）  

---

## 🧠 wechat_direct/ 核心组件（2文件）

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出 | 导出WeChatConnector |
| `wechat_connector.py` (33.8KB) | **直接微信连接器** | 直连微信API：扫码登录→收消息→传给小十→发回复；`DEFAULT_BASE_URL="https://ilinkai.weixin.qq.com"`；QR登录流程(fetch_qr→poll_status,超时480s,最多刷新5次,轮询间隔1s)；长轮询收消息(35s超时)；四种消息发送(text/voice/image/emoji,均base64传输)；context_token按用户管理+过期清理；凭据持久化`~/.weixin_cow_credentials.json`(load/save/clear)；状态持久化(_load_state/_save_state/_merge_state)；全局单例get_connector()；_shutdown_executor优雅关闭；_run_async_coro同步上下文跑协程；WeChatConnector类：login/run/_poll_loop/_handle_message/get_status/stop |

---

## 🧠 weclone_adapter/ 核心组件（3文件）

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出+架构说明 | **管线**：WeChatMsg导出(SQLite→JSON)→weclone_adapter(格式适配+清洗)→DataExtractor→StyleAnalyzer→风格注入ToneMimic提示词。⚠️注意：LoRA微调已移除（项目用外接API+RAG+提示词注入） |
| `adapter.py` | 克隆管线总控(无LoRA版) | WeCloneAdapter.clone(wxid)一键克隆：提取→分析→注入ToneMimic；quick_clone快速版；clone_from_file从已有JSON文件；extract(source参数支持wcf等)；_inject_to_tone_mimic将风格档案写入语气模仿器；health_check |
| `style_profiler.py` | 风格画像构建器 | 从CloneTraining的StyleProfile生成更丰富用户画像；用途：人性化System Prompt/few-shot示例库/ToneMimic数据增强；_build_few_shot构建示例对；_build_quick_tags快速标签 |

---

## 🧠 clone_training/ 核心组件（5文件）

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出+管线说明 | 管线三步：data_extractor提取→data_cleaner LLM Judge清洗→style_analyzer 12维分析；LoRA移除声明同上 |
| `data_extractor.py` (19.2KB) | **4种数据源提取器** | ①WeChatFerry(RPC实时查询微信进程内数据库)②WeChatMsg(LC044本地SQLite+解密)③手动导出txt/csv/json④wechat-decrypt微信4.x解密；统一输出`[{"user","reply","timestamp","is_self","source"}]`；single_combine_time_window=2分钟合并同一人连续消息；TXT解析双正则模式(A/B)+时间戳行兜底+说话人在下一行的处理；CSV/JSON格式自适应；系统消息过滤(你已添加了/撤回了一条消息/<msg等10种模式)；save_to_json落盘 |
| `data_cleaner.py` (9.5KB) | **LLM Judge数据质量评分** | QA对1-5分质量评分，accept_score=2过滤低质量；三层降级：LLM评分→启发式检查_heuristic_check→纯规则_rule_based_score；score_batch批量/score_from_dataset数据集级；clean()返回过滤后对话 |
| `style_analyzer.py` (14.4KB) | **12维风格分析器** | StyleProfile完整档案：句长分布(短≤10/中11-30/长31-80/超长>80)/标点习惯/语气词频率(的了呢吧嘛啊哦呀啦哈嗯哎12个)/表情颜文字频率+类型/口头禅提取(2-4字中文n-gram,排除常见填充词,出现≥2次)/情绪分布(正负面中性词典)/人称代词偏好(你我他她人家咱俺)/句类比例(提问感叹祈使陈述正则分类)/网络用语(yyds/绝绝子/栓Q等22个词典)/独特性打分(6因子加权:句长偏离0.15+emoji 0.15+kaomoji 0.10+语气词多样性0.15+网络用语0.15+情绪偏离0.15+口头禅0.15)；to_style_prompt()转LLM可用风格描述段 |
| `wechat_decrypt_source.py` (28KB) | **微信4.x数据库解密适配器** | 集成ylytdeng/wechat-decrypt(third_party/目录)；工作流：检测安装→检测微信运行(psutil查Weixin.exe,兜底tasklist)→密钥提取(find_all_keys_windows.py,需管理员权限)→数据库解密(decrypt_db.py -i增量模式,清理-shm/-wal残留)→读取消息；importlib.util动态加载mcp_server避免sys.path污染；联系人加载(contact.db:nick_name+remark,备注优先)+自wxid推断(config.json db_dir父目录)；目标解析(wxid精确→wxid_前缀→备注模糊→昵称模糊)；消息表查找：MD5(target_wxid)→Msg_{hash}表名,ThreadPoolExecutor并行8线程扫message_*.db；双路解码：mcp_server._format_message_text(正确处理WeChat 4.x二进制zstd压缩)→fallback手动zstandard解压+UTF-8解码+可见字符比例<50%判乱码丢弃；对话轮次构建：is_self切换成对,同人连续保留最新 |

---

## 🔧 主要设计模式

### 1. 四源统一提取
```
WeChatFerry(RPC) ─┐
WeChatMsg(SQLite) ─┤
txt/csv/json导出  ─┼→ 统一格式 [{"user","reply","timestamp","is_self","source"}]
wechat-decrypt    ─┘   → DataCleaner(LLM Judge) → StyleAnalyzer(12维) → ToneMimic注入
```

### 2. 三层评分降级
- LLM Judge评分（有gateway时）→ 启发式快速检查 → 纯规则兜底

### 3. 双路消息解码
- mcp_server解码（正确处理二进制+zstd）→ 手动zstandard+UTF-8降级 → 乱码丢弃

### 4. 架构决策记录
- ⚠️ **LoRA微调训练已移除**——项目使用外接API+RAG+提示词注入实现风格克隆，不再依赖本地模型训练（两处文档明确声明）

---

## ⚠️ 关键技术要点

### 合规相关（对应AGENTS.md硬约束）
- 微信连接器走官方ilinkai API（非逆向协议），降低封号风险
- 数据解密需用户本机管理员权限+微信在线运行（本地操作不外传）

### 与其他模块的集成点
| 集成对象 | 方式 |
|---------|------|
| my_character/tone_mimic.py | WeCloneAdapter._inject_to_tone_mimic() 注入风格档案 |
| orchestrator | scheduler.register_channel("wechat", ...) 注册投递通道 |
| third_party/wechat-decrypt | DecryptSource子进程调用find_all_keys+decrypt_db |

---

## 📋 已读文件列表（完整10个）

1. wechat_direct/__init__.py
2. wechat_direct/wechat_connector.py
3. weclone_adapter/__init__.py
4. weclone_adapter/adapter.py
5. weclone_adapter/style_profiler.py
6. clone_training/__init__.py
7. clone_training/data_cleaner.py
8. clone_training/data_extractor.py
9. clone_training/style_analyzer.py
10. clone_training/wechat_decrypt_source.py
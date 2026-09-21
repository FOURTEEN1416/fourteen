# 全量代码审查报告（2026-09-21）

> 方法：六路分域审计（wechat / proactive / memory / llm+knowledge / persona / api+tools+utils+security+observability）+ 主审独立通读每轮热路径（orchestrator→persona→memory→llm→tools）+ 全仓 AST 阻塞调用扫描。
> **每一条 finding 均由主审逐条对代码复核**（行号+实况），不采信注释与函数名；附「排除清单」= 审过但核实**不是**缺陷的候选。
> 边界声明：① persona 分域子审计已回收并**全量逐条对码核验**（其 P1×6 全证实、P2 修正两条、死代码清单逐项查调用面，见下 persona 域小节与排除清单）；② 服务器侧只读取证（如 `shisi/data/` 是否已生成脏库）未做，属生产核验另批。
> 测试基线注记：本报告为只读审查，零代码变更；角色卡 41 张在位、工作树含并行遗留 `M AGENTS.md`（未纳入本报告提交）。

---

## P0 — 每轮必现 / 数据销毁 / 隐私与隔离破口（9 项）

### P0-1 三层记忆检索全量执行、全量丢弃——生产 prompt 里没有任何长期记忆
- `orchestrator/optimized_orchestrator.py:854` `memory_context = raw_mem or ""` 为 **dict**（含 facts/episodic/reflections/user_key）。
- `:894` `apply_budget(memory_context=str(memory_context or ""))` → dict 被打成 **Python repr 字符串**；`apply_budget` 返回 str（`context_budget.py:147-183`）。
- `:945-952` 把该 str 传 `build_system_prompt(memory_context=...)`。
- `shisi/application/persona_service.py:105` `mem_ctx = memory_context if isinstance(memory_context, dict) else {}` → **`mem_ctx={}`** → 「记忆上下文」各段（我对你的观察/我记得的/相关回忆）永不渲染；`profile_key` 兜底取整段 repr 当查询键（:109-113）必然 miss。
- **后果**：每轮跑完 Chroma+SQLite 全量检索（成本照付）后结果整体蒸发。产品的核心卖点「记得你」在线上是假的。与 P0-2 叠加解释了「生日/军训乱编」症状至今仍在——v1.32/v1.33 的修复被这两处断链废掉。
- **修复方向**：`apply_budget` 保留 dict、只对内部文本字段限额；或 persona 改吃「已渲染字符串 + 显式 profile_key 参数」，消灭 isinstance 猜型。

### P0-2 用户画像库路径差三级，构造即抛、被 `except: pass` 吞死——画像功能整体失效
- `shisi/memory/legacy/user_profile.py::default_store`：`Path(__file__).resolve().parent.parent.parent / "data" / "sqlite.db"` → 解析为 `shisi/data/sqlite.db`（正确应为项目根 `data/sqlite.db`，仓库已有 `utils/project_paths.PROJECT_ROOT` 真源）。
- **执行证据**（本窗探针）：`default_store()` 实测抛 `sqlite3.OperationalError: unable to open database file`（目录不存在，构造期 `_ensure_schema` 即连库）。
- 消费点全部静默：`persona_service.py:117-126` `except Exception: pass`；`tools/builtin/profile_agent_tools.py:28`（LLM 工具链每次 `update_user_profile`/`query_profile` 同样落空）；orchestrator 侧调度也被 try 包裹只留 debug 日志。
- **后果**：`user_profile` 表在生产从未创建；LLM 智能体画像工具全部失败但返回被吞——「长出手脚改画像」（v1.33）实际一只手都没接上。
- **修复方向**：改 `PROJECT_ROOT / "data" / "sqlite.db"`；失败从 `except: pass` 升为 WARNING 留痕。

### P0-3 知识库「上传文档」= 整库覆盖，角色知识被永久销毁
- `api/routers/knowledge_routes.py:201-203`：`retriever.index(knowledge_chunks)`——`retriever.py:63-65` `index()` 是**替换语义**（`self._chunks = chunks`；追加正解是 `add_chunks()`，`:235-237` 内部 `self.index(self._chunks + chunks)` 反证）。随后 `:210 save_index` **落盘**。
- `_chunk_counts += len(...)` 使 `/knowledge/stats` 显示假数字直到重启。
- **后果**：一次上传 → 该角色卡全部锚点/示例对话块从索引和磁盘消失，RAG 从此只命中该文档。同文件 `:246` 删除路径用 `index(remaining)` 是正确替换用法，坐实语义。
- **修复方向**：改 `add_chunks`；计数以 `len(retriever._chunks)` 为准；补「上传后原块仍可检索」回归。

### P0-4 多用户隔离在读面/写面仍有硬破口（四例同族：隔离只做了检索键，没做接口键与投递键）
1. **`/api/memory/facts` 返回全库**：`misc_routes.py:235-243` `get_facts(category, limit)` 不传 `user_key`（`semantic_memory.py:159-170` 无 key 即无 WHERE），任一登录用户可读全站用户事实；`limit: Query(default=50)` 无上限。`/api/memory/diary`（:227）同理吐全部 `user_key|date`。dashboard `count_facts()` 同口径（:108-110）。
2. **`/api/rag/documents` 把文档写进全局事实库且写脏**：`safety_routes.py:124-132` 把整 chunk dict 作为第一位置参传 `add_fact`，而 `structured_memory.py:560 fact = str(fact or "")` → **整个 dict 的 repr 落进 fact 列**（confidence=1.0、user_key 空=legacy）；随即被 1) 的无过滤读面暴露、并被记忆检索注入他人 prompt。且 `add_fact` 每次全量扫该 key 的行做 near-dup → 一次上传 O(N²) 写。
3. **ASE 裸数字键 → 广播**：`proactive/scheduler.py:595-603` `_collect_ase_user_keys` 把 `get_all_users()` 的裸 `user_id`（如 `"4"`）当会话键建引擎；`on_chat` 只写完整键 `4:peer@im.wechat` → 裸键引擎 `_hours_since_last_chat()` 恒 99、紧迫度顶格，每过冷却就生成「想你」；投递 `_send_targeted(session_key="4")` → `api/run_api.py:219-229` 解析不出 `owner:peer` → **落入广播分支发给所有在线用户的 peer**，且 return True 记为「定向投递成功」。v1.29 隔离的直接反例。
4. **working deque 全员混写归档**：`memory_pipeline.py:426` 全局唯一 deque `self.working.add(...)`，`:488 should_archive` → `:1127 _archive_working_memory` 把**多用户交错的消息**整批落情景记忆并统一挂最后一个 `session_id`。读侧过滤（:517）只掩盖不消除。
- 另：`forget_facts`/`delete_fact` 按 id 删除无归属校验（`structured_memory.py:728 DELETE ... WHERE id=?`，user_key 只给回收站打标）——模型幻觉一个 id 即跨用户删事实（写侧越权，P1 下限，列此备查）。

### P0-5 WebSocket 认证绕开真源，生产默认 fail-open 匿名聊天入口
- `api/websocket_server.py:33` 自抄一份 `API_KEY_ENABLED` 解析、默认 `"false"`；真源 `api/runtime_config.py:47-52` 未设置时按 `is_production()` fail-closed。生产不设该变量：HTTP 面强制认证，`deploy/nginx-ai-girlfriend.conf:94-99` 反代的 `/ws/` **完全无认证**（`_verify_api_key` 在 `:83` 直接短路）。`yes/on` 类取值两侧解析结论相反——`runtime_config.py:39-45` 注释记载本仓曾因「三处复制该解析」出事故并收口，这是第 4 处漏网。
- 即便开启：只有一把全局静态 key，与 JWT 用户体系脱钩；`session_id` 客户端任意自报（:136-137）无归属。
- **后果**：匿名者经 `wss://域名/ws/` 消耗平台 LLM key、向记忆体系写数据。
- **修复方向**：WS 用 `resolve_api_key_enabled()` + 首帧 JWT，身份从 token 解出。

### P0-6 web 会话提醒「假送达」：协程未 await 就 return True
- `api/run_api.py:289-297 _ws_send`：`ws_server.broadcast_proactive(text)` 是 **async**（`api/websocket_server.py:213`，实测）——同步调用只创建协程对象即 `return True`（协程从未执行，刷 RuntimeWarning）。`proactive/reminder_delivery.py:116-122` 据此 `mark_reminder_result(True)` → 置 delivered、永不重试。
- **后果**：web 会话用户的到点提醒一条都收不到且系统认为已送达——正是 v1.18 根治的「说了会叫却没叫」在 web 通道复刻。测试全绿的原因：`test_reminder_intent_pipeline.py:395` 用同步 `list.append` 当 ws_sender，钉死了错误契约。
- **修复方向**：`_ws_send` 改 async 由投递任务 await，或 `run_coroutine_threadsafe` 投主循环。

### P0-7 四 worker 架构性互盲：通道宿主/调度器/缓存分属不同进程，投递与控制面打空
- 生产 `deploy/ai-girlfriend.service:11 --workers 4`。`wechat_direct/connector_registry.py:19-20` `_registry` 为**进程级**单例；scheduler 抢 `/tmp/ai-girlfriend-scheduler.lock`、通道抢 per-(user,slot) flock，**两把锁随机落在不同 worker**。
- 后果链（已逐点验码）：持调度锁的 worker `registry.all()` 看不到别的 worker 的 connector → 微信定向投递/提醒投递恒 miss → 提醒 3 次判死 failed、主动消息每条只留 warning；`/disconnect /reconnect force-disconnect` 被非持锁 worker 处理时返回 False/无效，真正轮询线程照跑（「时连时断」观感来源）。
- 同模式扩散：知识索引 `character_knowledge_service.py:120-124 ensure_index` 命中进程内 `_retrievers` 即返回、**不核对磁盘 mtime**——改卡只驱逐 1/4 worker，其余 worker 永久用旧索引（「改了卡时好时坏」）；`llm_provider/__init__.py:239 _user_gateways` 与 `misc_routes.py:359 reconfigure_llm` 同样只及单进程（用户换 key 后其余 worker 继续旧 backend）。
- **修复方向**：调度+通道钉进专用单 worker 进程（HTTP worker 经 DB/队列转发投递）；缓存条目记 `(path, mtime_ns)` 或卡文件 mtime 当版本号。

### P0-8 通道 disconnect 不释放 flock fd → 同进程重连必 429，只能重启整服务
- `connector_registry.py`：`start_login/restore_on_boot` 持锁 fd 攒进 `_poll_lock_fds`（注释自认「退出时由 OS 释放」，:236-241）；`disconnect`（:115-126）只 `stop()+pop`，**从不 close fd**。
- POSIX flock 按 open file description 判定：同进程旧 fd 仍持锁 → 重连新 `flock(LOCK_EX|LOCK_NB)` EWOULDBLOCK → `None` → `raise ChannelSlotError("通道正在被其他进程占用")`（Linux 生产必现；Windows 开发机走 `return 0` 分支掩盖，测试 monkeypatch 掉该函数——两层盲区）。
- **后果**：用户点「重新连接」对所有开机恢复的通道恒失败；「断开再连」这一基本操作不可用。
- **修复方向**：disconnect/remove 时按 (user,slot) 释放并 close 本进程锁 fd。

### P0-9 deepseek mock 无条件入 fallback 链、罐头回复被判成功并污染路由指针
- `llm_provider/multi_provider_gateway.py:287-290`：链中 `deepseek` 分支**跳过 key 检查**直接 `LLMGatewayV2()` 入链（其余 provider 有 `if not cfg.get("api_key"): continue`）；`llm_gateway.py:135-136` 无 key → `_mock_reply(query)` 返回**拟真角色台词**（"笨蛋，你终于来啦～…"），不含任何 `_ERROR_SENTINELS` 特征词 → `chat()` 判成功并 `_publish_current("deepseek")`。
- `config/llm_providers.json` 实测 `fallback_chain` 末位即 deepseek、tracked 配置各家 key 全空（真 key 走 env/local overlay）；`.env.example` 中 `DEEPSEEK_API_KEY` 为注释态 → 生产链尾大概率挂着一个恒 mock 的 provider。
- **后果**：前三家同时限流（zhipu 免费档限流是常态）→ 用户收到与关键词匹配的罐头句、日志记 success；且 `chat_stream`/`chat_with_tools` **只用 current_provider 不降级** → 恢复期内所有 SSE 与 tool_gate L1 终审全打 mock（提醒被静默吞）。类注释「配置了就插最前」为垃圾信息，代码无此逻辑。
- **修复方向**：deepseek 分支同规查 key（env+文件），无 key skip；或 mock 文案带错误哨兵。

---

## P1 — 功能失真 / 每轮性能与成本（按域）

### LLM 网关
1. **流式绕开 fallback 链且错误文案当 token 直发**：`multi_provider_gateway.py:460-464` `chat_stream` 只绑 `current_provider`；`openai_compatible_provider.py:353-356` 异常 `yield self._handle_error(e)`——「（zhipu API 请求失败，错误代码 401）」逐字推给前端、计入 full_reply、经 `_after_process` 写进 chat_history **污染下轮上下文**。流式空回复无角色化兜底（`_stream_mixin.py:317-324` yield 空串；非流式有 `get_fallback_line`——不对称）。SSE 内 200+error-body 逐行 `except KeyError: continue` 静默吞（`openai_compatible_provider.py:348-349`）。
2. **无 provider 级熔断，链首挂起饿死全链**：provider 客户端超时 60s（`openai_compatible_provider.py:126`）> 编排层整链预算 30s（`optimized_orchestrator.py:1237-1248` `wait_for(…, 30)`），`chat()` 每轮恒从链首重探（:373）→ 链首网络黑洞时每轮 30s 烧光、健康 provider 永远轮不到。
3. **百度 OAuth 三重坏**（`openai_compatible_provider.py:389-414`）：① async 函数里同步 `httpx.post(timeout=10)`——触发即冻结整个 worker 事件循环；② `MultiProviderGateway` 构造不传 `api_secret`（grep 零命中；只有 `__init__.py:110` 单 provider 路径传）→ auto 链里 secret 恒空 → token 端点必 400 → except 不置 `_oauth_expires_at` → **每次请求再阻塞一回**；③ 刷新成功只改 `self._headers` dict，httpx client 构造时已拷贝快照——token 轮换不生效。
4. **chat_sync 嵌套分支注释说谎**：`multi_provider_gateway.py:444-447` `pool.submit(_run).result()` 仍在调用方循环线程同步等待（注释称「避免阻塞」）；现网无 async 调用者，属潜伏陷阱。`_get_sync_loop` 发布竞态可漏建线程。
5. **双循环乒乓重建 httpx client**：`openai_compatible_provider.py:109-131` client 按 loop_id 缓存，uvicorn 主循环与常驻 sync loop 交替命中即整体弃建（TLS 重握手）；旧 client `aclose` 被调度到**新** loop 执行（跨 loop 关闭未定义）。ASE tick 与聊天交织时表现为周期尾延迟。

### 每轮成本结构（主审独立核算）
6. **每条消息多打一次无条件工具链 LLM**：`optimized_orchestrator.py:1110-1126` 每轮 `run_profile_sync_agent` 提交到**单 worker** `_bg_executor`（`:112-117`，注释自认「串行」），lambda 里再 `asyncio.run` 新建循环——与 after_chat 记忆写、画像正则排队互相阻塞；sync 调用经 provider 时又撞上 P1-5 的 loop 乒乓。当前该链整体死于 P0-2（白烧一次 LLM）。
7. **情感分类器假超时**：`my_character/emotion_engine.py:370-455` 单 worker executor + `future.result(timeout=0.5)`——超时**不取消**底层 LLM 调用（照跑几十秒占死 worker）；每轮 0.5s 后主链先行，分类结果常弃。
8. **会话摘要缓存键逐轮漂移**：`conversation_summarizer.py:45 cache_key=f"{session_id}:{older_count}"`，older_count 每轮 +1 → 缓存永不命中 → **51–80 条区间每轮一次同步 `_summarize` LLM**（chat_sync，`multi_provider_gateway` 常驻循环），且由 `optimized_orchestrator.py:866 get_chat_context` **直接在事件循环上**调用；越过 trigger 后 `startswith` 复用最早边界 → 摘要永久滞后 + `_cache` 无界。
9. **反思检索每轮扫全部 6 集合**：`reflection_engine.py:197 filter_dict={"type":"reflection"}`，而 `vector_memory.py:348 collection_map` 只有 episode/fact → 落「扫全部 collection」分支，每轮 O(6×k)。
10. **事件循环上的同步 DB/文件 IO**：`optimized_orchestrator.py:766 get_recent_context`、`:866 get_chat_context`（对照 `:785 retrieve_context` 已包 executor——同函数内两种口径）；`chat_routes.py:37-42` 每条 web 消息 `_resolve_character_id` → `get_active_character_id` 对 41 张卡逐个 `open+json.load`（`character_routes.py:82-87,202-212`，实测同步 IO 在 async 路由内）；`auth_routes` 登录/注册/改密 bcrypt（~0.2-0.5s CPU）同步在 async 路由（:152/:200/:394/:405/:429）——撞库时每错误请求冻结全站循环一次，等效单请求 DoS 放大器；`/knowledge/crawl`、`/enrich` 在 async 路由裸跑多源网络长链（`knowledge_routes.py:337-342,390-397`，实测 33.2s），**同仓 character_routes:219 已示范 to_thread 的同一函数**。dashboard `count_facts/count_chats_today` 同步直查记忆共享连接（`misc_routes.py:108-110`）。
11. **工具层双闸门缺失**：`tools/base_tool.py`——`dispatch` 从不消费 `self.timeout`（配置 `execution_timeout_seconds=10` 死配置，`_init_mixin.py:353-356` 明确传入）；挂死工具永久占 to_thread 槽（默认池有限，积少成多全服 to_thread 堵死）；重试路径同步 `time.sleep(0.5×n)`。速率限制按**工具名全局**共享（`_check_rate_limit:149-158`，默认 3/分钟）→ 第 4 个用户起收到 `Rate limit exceeded`，模型向用户转述「搜索不可用」，还记成工具故障污染统计；用户互耗配额。`profile_agent_tools.py:328` 每次自建新 ToolDispatcher（限速状态即抛）为同一缺口的第二个实例。

### 记忆 / 画像（P0 之外的独立缺陷）
12. **Chroma 事实向量通道整体空转**：`semantic_memory.py:63` `store=getattr(self._vm,"store_fact")` 拿到 **async** 方法（`vector_memory.py:222`）后同步位置调用 → 返回协程被丢弃（无 TypeError，兜底分支死码）→ 事实从不写 `user_facts` 集合，向量召回恒空，只剩 SQLite 结构化通道。即便 await，`store_fact` 的 meta 无 user_key（`vector_memory.py:227-231`）也会被读侧 `_meta_user_key` 过滤清零——该路径设计上就未通。
13. **冲突检测跨用户**：`conflict_detector.py:27 self._sem.search(new_fact, top_k=3)` 不传 user_key → A 的历史事实可让 B 刚抽取的事实被判冲突丢弃（写侧串扰，v1.28 只治了读侧）。
14. **`upsert_pending_intent` UPDATE 分支漏 commit**：`structured_memory.py:1082-1096` 直接 return（INSERT 分支 :1110 有 commit；`_conn` 上下文不自动提交，实测 :482-492）——澄清状态机的 ask_count/新问题依赖**下一次别的写操作顺带**刷盘，进程异常即静默丢。
15. **stream 路径 user_id 断供**：`_stream_mixin.py:242 _prepare_context(...)` 漏传 `user_id`（对照 `optimized_orchestrator.py:1202-1207` 传）→ 工具 `_meta.user_id=None`，web 流式下 calendar/set_reminder 归属丢失。
16. **after_chat 预写优化在生产失效**：`optimized_orchestrator.py:1064-1094` `history_already_written` 分支（`memory_service.py` 签名无此参，探针实证）与 `write_chat_history_sync`（service 无此属性，探针实证）双双永不命中——本轮回复要等后台 `after_chat` 落 DB 才对下轮可见，慢工具轮次下表现为「吞消息」竞态。测试只 grep orchestrator 源码文本，测不到运行时缺口。
17. **legacy 包双实现地雷**：`shisi/memory/legacy/__init__.py` 导出**无下划线**旧实现，而 pipeline 实际 import `_legacy_*`（实测 :28-31）；旧版 `CrossSessionReasoner.get_pending_events` 无 user 过滤（全局）——任何 `from shisi.memory.legacy import …` 都拿到未隔离版。`MemoryEnhancer(memory_ext)` 装配后全仓零消费者（grep 实证 components 无读方）；`recycle_bin_days` 配置无消费点；`_context_cache` 无界（仅日维护清，`memory_pipeline.py:611/706/756`）。

### proactive / ASE（v1.29/v1.13 之后仍开的口）
18. **ASEHub `__getattr__` 单引擎代理塌缩**（`proactive/ase_hub.py:141-147`，验码）：`save_state`（每 10 分钟 `_save_state`）、`reset_daily_count`、`set_quiet_hours`、`apply_runtime_config(paused/threshold/max_daily)` 全部只命中 OrderedDict 末位（=最近被 get 的）引擎——**重启后其他用户 daily_count 归零、30 分钟冷却丢失（可能连发）**；控制台「暂停主动消息」对其他用户无效；静默窗改后其余引擎照生成（v1.13 配额焚化炉在非默认窗复活）；LRU 淘汰（`ase_hub.py:92-97`）不 save 被踢引擎。`index.json` 只记不删、写无跨进程锁。
19. **手动发送「归还配额」写影子属性**：`training_routes.py:369/422-426` 读经代理（可能读错引擎）、finally **赋值**在 hub 实例上创建真实属性 → `__getattr__` 对该属性从此失效、引擎侧 +1 从未归还——09-18 修的「自测吃光 8 条配额」在 hub 化后原样回归。
20. **自适应降档整层死码**：`frequency.py:34 on_no_reply` 全仓仅测试调用（grep 实证）；生产 `frequency_mode: adaptive` 下对连续数日不回复的用户仍每天最多 8 条——骚扰/删好友风险，「normal→low→minimal」卖点名存实亡。
21. **main.py 微信形态主动消息必死**：`scheduler.py:792-804` async sender 分支**无** TypeError 回退（回退只挂同步分支），`main.py:397-410 _send(msg)` 不收 session_key → `await sender(message, session_key=...)` 抛 TypeError → except 把 wechat 实例置 None（通道反复瘫痪）→ 落 `_send_to_all`（instance=None 跳过）→ `python main.py` 形态主动消息零送达且每 tick 白烧 LLM；同步旧签名分支则静默转广播+记「定向成功」（A 私信发全员）。两个分支各坏一头。
22. **投递持续失败无退避**：v1.13 把记账移到投递成功后是对的，但失败侧无任何 backoff（`ase_engine.py:857-862` 的 min_interval 只在成功后置时间）→ 不可达用户每 5 分钟「生成→失败→再生成」，一天 288 次 LLM 零投递。
23. **调度线程 `asyncio.run` await 主循环资产**：`scheduler.py:769 asyncio.run(self._send_targeted(...))` → ws 连接/`asyncio.Lock` 属 uvicorn 主循环——跨循环未定义行为，轻则判未送达、重则 future 永不 resolve 占死 default executor worker（`ase_check` 无 max_instances）→ 定时任务集体停摆类事故隐患。
24. **重要日期时钟污染**：`scheduler.py:899/906` 把裸 `_dt.now()` 显式喂给 v1.21 刚收口为 `now_local` 的 `check_today`，dedup_key 也不同钟——UTC 主机上北京 00:00-07:59 命中算前一天（换机即静默失效类）。
25. **adaptive 回复冷却写死 30 分钟**：`ase_engine.py:722-723` on_chat 拨 `_last_proactive_time`、`:853-863` adaptive 分支硬编码 30——`cooldown_after_reply_minutes=10`、`min_interval_minutes` 两配置在该模式完全不生效，且 `get_runtime_config` 照实谎报。

### wechat_direct（P0 之外的并发/接线）
26. **消息处理无声丢失**：`wechat_connector.py:1128 _msg_executor.submit(self._handle_message,…)` future 丢弃（无 done_callback），而 `_handle_message` 前半段（去重、`_cleanup_context_tokens:1196-1206 迭代中 del、_save_context_tokens、图片解码）裸奔无锁（全文件只有 `_followup_lock`，实测 :715）——并发下 `RuntimeError: dictionary changed size during iteration`/`KeyError` 被 future 吞掉，**一条用户消息零日志蒸发**。owner 通道 max_workers=2，两条消息即可并发触发。
27. **每消息 `asyncio.run` 新循环 × 跨循环缓存 `asyncio.Lock`**：`wechat_connector.py:630-648` 每 worker 线程每消息一个新循环；`session_locks.py:50-60` 的锁按 session_id 缓存跨循环复用——无竞争走快路径侥幸能跑，**同用户连发第二条**进 waiter 路径即跨循环唤醒失灵 → 线程占死/poll 卡住/`bound to different event loop`。
28. **「回复『角色』自选」整链未接线**：`peer_character.py` 的 `try_handle_character_choice/build_character_menu` 生产零调用（grep 实证，仅路由展示菜单预览）——AGENTS v1.16 记载的功能实际不存在；`put_peer_character` 只写 DB 不热更（`_resolve_character_id` 仅实例首建执行、`_users` 永不过期）→ 控制台换角色不重启永不生效。
29. **state.json 无锁 RMW + 非原子写**：`wechat_connector.py:93-105 save_session_state` 直接 `open(path,"w")`（同文件 context_tokens 都做了 tmp+os.replace），poll/消息/followup 三线程交错 → 丢更新、半写 JSON 被读到 → 状态闪断「未连接」一帧并回写 DB。
30. **媒体发送留 `_last_user_id` 回退**：`send_text` 对 owner 通道拒发无目标（实测 :773-777），但 `send_voice/send_image/send_emoji`（:808/:836/:864）仍 `to_user or self._last_user_id`——漏传即发给同通道另一好友（隔离护栏缺一腿）。
31. **轮询侧无视 timeout 标记**：`_post_api` 超时返回 `{"ret":0,…,"timeout":True}`（:512-517），`_poll_loop` 只读 ret/errcode（:1080-1082）——半死连接零退避零告警，消息延迟接收监控无感。

### api / 前端承诺 vs 实际
32. **dashboard 微信状态恒「未连接」且缓存假结果**：`misc_routes.py:83-91` 绕过 FastAPI DI 直接 `await get_wechat_status()`——`credentials` 形参拿到 Security 标记默认值 → `_try_user_id` 抛 AttributeError 被 except 吞 → 恒 `{"connected": False}` 并写入 5s TTL 缓存反哺。
33. **删用户 → 该微信永久绑不上**：`admin_routes.py:226-227 db.delete(user)`；`wechat_bindings` 只有 DDL 级 `ondelete="CASCADE"`，而 users.db 连接**从未 `PRAGMA foreign_keys=ON`**（grep 实证仅 shisi/character/store.py 与 migrations.py 对其私有库设置）→ 孤儿行 + wxid unique → 真人重绑恒 409「已被其他账号绑定」且永远解不开。`User.sessions` ORM 级联**部分表干净消失**使此坑更隐蔽。
34. **PUT /characters/{id}/persona 不失效任何缓存**：对照 update_character（:401-406 有 `invalidate_character_persona_cache`+`_invalidate_knowledge_index`），persona 端点（:564-567）两者皆无——前端改人设「没反应」直到重启/activate。
35. **工具 toggle 单向死路**：`tools_routes.py:73-79` unregister 直接 pop 实例，enabled=true 回来时 `registry.get`→None→404，工具从管理界面消失直到重启。
36. **/api/training/apply 假成功端点**：`training_routes.py:127-137` 只返回 `{"status":"applied", path}` 什么都不做；`test_clone`（:117-120）每请求重建 ToneMimic（同步开 Chroma + ONNX DefaultEmbeddingFunction 首载）在 async 路由里执行，异常吞成 200+status:"error"。
37. **激活角色把其余卡 normalize 后回写磁盘**：`character_routes.py:459-463 _list_all_characters()` 默认 normalize=True → `_save_character` 回写清洗产物；`utils/character_helpers.py:57-76 _AUTHOR_KEYWORDS` 含**裸词 "by"** 无词边界、IGNORECASE 子串删除——英文卡 "maybe"→"maee"、"standby"→"stand"，且随激活操作**写进真源文件不可逆**。
38. **persona-card 端点写进第二存储**：`persona_card_routes.py:64-72` → `shisi/character/store.py` 的 sqlite `characters` 表，与对话真源 `config/characters/*.json` 分离——PUT 永远 200 但对聊天零影响。
39. **按用户日志过滤从未生效**：`logging_setup.py:129/173` 把 `UserContextFilter` 挂在 **root logger**——子 logger propagate 的记录不过 root filter → `record.user_id` 永不注入 → 普通用户 `/api/logs` 恒空（管理员侧正常故难察觉）。
40. **邀请码 check-then-mark 非原子**：`invite_routes.py` `is_valid()` 校验与 `used_by` 标记之间隔多次 await——并发同码双放行（一码一人可脚本绕过）。
41. **`<system_prompt 泄露>`类**：extra_tools 三件（MemoryTool 无 `_meta` 恒走无过滤分支= P0-4-1 的工具版；SchedulerTool 无主提醒永不投递且与 set_reminder 双轨；WebSummaryTool `requests.get(url)` 无 scheme/内网黑名单=SSRF，LLM 可被诱导打 `169.254.169.254`）——当前不在 `system.yaml builtin_tools` 白名单，**一拨即燃**（P2 潜伏，修复应与白名单同批评审）。同族：`profile_agent_tools.py:86` `_meta` 缺失时回落到 LLM 可控的 `kwargs["session_key"]`，违背「服务端注入归属」原则（`reminder_tool.py:48` 注释明文禁止），function-calling 夹带该键即可把画像写进他人会话。

### persona / 情感引擎（子审计回收后逐条对码证实）
42. **对话路径情感引擎不恢复亲密度 → 每轮把持久化好感度往下拽**：`optimized_orchestrator.py:653-690` 请求级 `EmotionEngine` 新建时 `affection_points=0.0`（`emotion_engine.py:294` 默认值）且**无** `_restore_affinity`——对照 `user_scheduler.py:130` 的引擎创建必调 `_restore_affinity`（:141-150）；对话尾 `:1132-1143` 把该引擎的 `affection_points` 喂 `AffinityMapper.sync`，`mapper.py:98-110` 按 `delta=target−last` 增量钳 `max_delta=3` → **每条消息把 shisi 侧已存好感度向 to_shisi(≈0) 拉低最多 3 分**（LRU 256 外的会话重启/淘汰后从 0 重来）。这是「聊得越久好感度越低」方向的系统性漂移。
43. **情感-风格耦合在生产恒失效**：`persona_engine.py:480-483` 把 `primary_emotion`（**Emotion 枚举对象**）直接当 `type` 传 `coupler.couple`；`emotion_style_coupler.py:182` `_emotion_matrix.get(primary)` 键为中文 str（`EMOTION_STYLE_MATRIX:52` + `config/emotion_style_matrix.yaml`）→ **恒 miss、情感调整量恒 0**，仅好感度分量生效。注入段 `[当前风格指导]` 因此永不含情绪色彩。
44. **system 里两份「# 当前状态」且第一份是英文枚举名**：`character_aggregate.py:162-163 `- 情感: {primary_emotion.name}``（NEUTRAL/LOVELY…）经 prompt_builder:35 进 base_prompt；`persona_engine.py:696-699`（中文+强度）经 persona_service:188-196 emotion_layer/emotion_style 再注入——同一条 prompt 两个状态块、口径互斥（英文枚举名对中文角色扮演是纯噪声）。
45. **情绪时间衰减整体打空**：`scheduler.py:822-823` 唯一调用 `apply_time_decay` 的对象是 `_init_mixin.py:151/278` 注入的**模板引擎** `components["emotion"]`；对话/调度各自用请求级与用户级引擎（状态互不相通）→ 能量恢复、强度衰减、per-day 亲密度衰减改的全是没人读的 state。**每用户情绪自然冷却这一功能实际不存在**（shisi 侧 `enhancer.decay_all` 只衰减好感度数值，不覆盖情绪层）。
46. **prompt 注入层失败=整层静默消失**：`persona_service.py:487-493` `_safe_engine_layer` 捕获一切异常仅 `logger.debug` → 情绪层/风格层/约束层任一抛错即从 system 无声缺件（生产 INFO 级连日志都没有），角色行为突变无从排查。
47. **ToneMimic 风格样例每轮检索、结果恒丢弃**：`knowledge_service.py:113-122` 每次 `retrieve` 同步跑 `retrieve_style_examples`（Chroma+ONNX 嵌入查询）→ 塞进 payload `"style_examples"` → `context_budget.rag_payload_to_text:45-74` **只读 `results/chunks`**，该键无人消费；`persona_service.py:199-201` 又固定以 `style_prompt=""、few_shot=None` 调 `build_style_layer` → 「## 风格参考」「## 相似历史对话参考」两节（`persona_engine.py:806-813`）**永不渲染**。花钱、耗 16GB 主机嵌入算力、产物进不了 prompt。且 `ToneMimic.add_conversation` 全仓零调用 → 聊天语料从不回流风格库，clone 上传之外的风格化是空转。

---

## P2 — 一致性 / 慢性 / 死码（择要）

- 知识检索双路交错的排序不变量在注入层被打回：`character_knowledge_service.py:180-201` score 覆写**索引内共享对象**（ext/base 量纲不可比），注入走 `RetrievalResult.get_top`（`retriever.py:30-31`）按 score 重排——v1.14「扩展路首位」修复只在 `search()` 返回值成立，测试恰好测不到断点。
- `shisi/knowledge/legacy/rag_engine.py:23` 中文 `\w+` 整句成 token → BM25 中文召回≈0（现仅 tests 引用）；`_init_mixin.py:368` 「use_legacy_rag=True 委托」注释为谎（全仓无该开关）。
- `shisi/ase/trigger_engine.py` 五类触发器生产零检查、`get_all_triggers` 返回 `[]` 桩——文档承诺的阶段/好感度/idle 触发台词永不发生（删或接线）。
- `llm_provider/prompt_template_manager.py` 零调用者死文件且 CWD 相对目录。
- 每轮两次 ToneMimic Chroma+ONNX 嵌入查询（`persona_engine.py:894` + `knowledge_service.py:116`）叠加 BM25——量级不大但在 16GB 主机上可测。
- `voice_detector.py` Tier5：`if pat == "说话"` 拿编译后正则比字符串恒 False（死分支，实际全走末尾 `return True`——结果碰巧近邻正确）。
- `orchestrator/_stream_mixin.py:39-120` 每条消息重建 PersonaConsistencyChecker/DynamicAnchorSystem；流式安全「抽检+全量」双层为已接受设计但 post-yield 过滤只发 `[内容已过滤]` 追加帧，前端已渲染文本不回滚。
- WS 正常完成发两条 `stream_end`（`websocket_server.py:161-176`）；`/api/training` 系前端向导第 3 步吃 P1-36 假成功。
- `proactive` 杂项：`_check_ase_global`/`get_recommended_type`/`_generate_proactive_message` 死实现；`event_bonus` 恒 0 假维度；`_reset_daily` 日志只反映随机一个引擎；`_important_dates_sent` set 只增（量小）。
- `memory_pipeline.retrieve_context_async`（:594-708）生产零调用但携 `_context_cache` 无界增长路径；`ForwardManager` 仅内存、重启丢。
- **persona 域 P2（核验后收录）**：① `verify_anchors`（`persona_engine.py:366-375`）对 `_anchor_hashes` 的每个键重算 `sha256(anchor)` 与存表自比——**永真**，锚点完整性校验是摆设（真防护需比对 `self._persona["core_anchors"]` 现值，未做）；② 一致性修正回路（`consistency_checker.py:367` 触发 `<0.4` 重生成）——三处构造点（:343-360、`_stream_mixin.py:96-101`、`persona_engine.py:286-290`）全都不传 `coupled_style`（style 维恒 0.9），修正分支需 anchor/emotion/persona 三维同时近下限才可达，**实际不可达**（子审计「恒 0.505」算法不严格——persona 维双违规+锚点全驳可压到 ≈0.385，但工程上等于没有）；③ **死配置两件**：`config/emotion.yaml` 只被 ConfigLoader 自读，生产 EmotionEngine（`_init_mixin.py:135`）不传 config → 全部走 `_default_config`；`prompt_mode`（shisi.yaml→`_init_mixin.py:146`→PersonaEngine）唯一分支点在死路 `build_system_prompt` 内，**传什么都不影响**；④ `/api/persona/profile` 与 `/api/persona/evolution-log` **必 500**：`personality_routes.py:103-119` 访问 `orch._persona.profile/.get_evolution_log`，而 `_persona` 是 PersonaService（`optimized_orchestrator.py:177`，无这两个属性、无 `__getattr__`）——前端 `useQueries.ts:155` 在消费；⑤ `CharacterCardAdapter`（`_init_mixin.py:400-412`）除 init 外**零读者**（`components["character_card"]/"card_mode"` 全仓无消费点，路由列表页各自直接扫目录），其目录缓存另有过期缺陷：仅以**目录 mtime** 失效（`integration.py:135-145`），原地改卡内容目录 mtime 不变 → 恒旧（好在无人读）；⑥ `_stream_mixin` 与 `check_and_correct_reply` 每条消息重建 checker+DynamicAnchorSystem（41 卡锚点重建，纯浪费）。
- **persona 域死码清单（逐项 grep 调用面证实）**：`persona_engine.build_complete_prompt`（零调用）及其下游整条 `build_system_prompt` enhanced/legacy 分支；`enhanced_prompt_engine.py` 全模块（仅被死分支引用；其内部 3 处缺陷随之无害）；`validate_response`/`auto_correct_response`/`check_anchor_consistency`（三公开方法零调用 → ConstraintValidator、EnhancedAnchorProtection 生产不生效）；`_style_enhancer_v2`（赋值后无使用）；`ContextualBehavior`（只喂死模块）；`EmotionMemorySystem`（仅类型注解）；`PersonaEvaluator`（全仓无实例化，仅 `__init__` 导出）；`auto_evolve`（零调用 → PersonaEvolutionEngine 不转）；`dynamic_anchor` 强化回路（`should_reinforce/generate_reinforcement/reinforcement_needed` 零消费者）；`EmotionEngine.get_style_modifiers`（零调用）；`ToneMimic.add_conversation`（零调用，见 P1-47）；`CharacterService.process_message`（类无实例化点）；`persona_service._build_chat_history`（测试反向钉死为「不得调用」的滞留函数）；`character_card/` 包生产面（除 self-引用与 init 挂线外零读者）。

---

## 排除清单（审过、核实**不是**缺陷——防下次误报）
1. `chat()` 失败不再改全局 `_current_index`（09-17 并发修复）语义正确；`_publish_current` 只在成功后发布。
2. `_is_error_reply` 7 个哨兵与各 provider `_handle_error` 文案逐一比对无误伤/漏判（deepseek mock 是「无哨兵可判」，归 P0-9）。
3. BM25 IDF 重建、`save/load_index` 全量序列化仅在建/换索引时发生，不在每轮路径。
4. ~~prompt_builder 每轮 BM25 检索~~——更正：`prompt_builder.py:89-92` 知识槽要求 `user_message` 非空，而 persona_service:152-159 恒传 `user_message=""` → **该检索每轮根本不发生**（v1.15「prompt_builder 为知识注入唯一 owner」为注释谎；知识实际全靠 persona_service:180-186 的 rag_context 兜底段，单份注入成立、但 orchestrator rag 任务失败时**无任何兜底**，角色知识当轮整体消失——记入 P2）。
5. `asyncio.timeout` 需 3.11 而 pyproject 写 3.10——生产 venv 固定 3.12，仅声明口径问题。
6. `_merge_attachments` 在已传 messages 时丢 attachments——主链从不同传两者，无现实触发。
7. 卡文件名/id 不一致（有 glob 兜底）、`persona_extractor` 可 await 性、init 期 `_run_async`、`ensure_index` 已加载短路——均核实无恙。
8. `frequency.can_send` 内 UTC 差值+本地日界混用为刻意设计（注释与代码一致）。
9. `reminder_delivery` 的 `_maybe_gc_intents` v1.27 修复完好（AST+行为双钉）。
10. 卡目录空键规则垃圾、CORS 默认、路径遍历防线（serve_file/sanitize_id/safe_join_path）、工具返回值 untrusted 信封、`_received_msgs` 封顶 10000、tracer TTL——防线有效。
11. `wechat_routes` 用 `def` 路由跑同步文件 IO（线程池执行，正确姿势）。
12. `memory_favorites` 表存在（shisi/migrations 同库建）。
13. 「after_chat 双插 chat_history」——核实只写一次（预写优化失效的另一面已列 P1-16）。
14. **persona 子审计两处误报（驳回，防下次误报）**：① 「`build_system_prompt` 调用面=0」——对 `CharacterAggregate.build_system_prompt` **不成立**，其经 `prompt_builder.py:35` ← persona_service:152 活着（正因活着才有 P1-44 状态块重复）；死的是 `persona_engine.build_system_prompt`，两者同名不同物；② 「`apply_time_decay` 无人调用」——有调用点但打在模板引擎上，已按实况改述为 P1-45 而非排除。
15. `EmotionEngine/LLMEmotionClassifier` 线程池：注释「类级别共享」为谎（实为实例级，`emotion_engine.py:385-386`），但 `close()` 级联正确释放（:454-459/:823-826）、请求级引擎 LRU 淘汰即关——**无泄漏**，只按注释失实记，不上功能缺陷。

## 复核实录（b76d6c6 → 3e96a7c · AX P2 批次增量复核）

> 复核对象：并行窗两提交 `b7cd513`（agent-plane P2 全量）+ `3e96a7c`（BOARD 收账），13 文件 +1022/−31。**逐文件逐条对码核验**，不采信提交信息与 BOARD「生产闭环」宣称。
> 工作树声明：复核时工作树含并行窗**未提交在制品** 7 文件（scheduler wait 门控、projection 按 event_type 拉取、runtime 种子判定等——恰与本复核新登记的部分 P1 同题，属他窗修复中），**不在本次口径内**；其提交后需再增量复核。

### 新增 P0-10 — agent-plane 全部端点无管理员门槛：任一用户 JWT 可跨用户读因果账本、可全库破坏性 curate
- `api/routers/agent_plane_routes.py`（新 153 行）6 端点（replay / profile / events / curate / probes，含 GET+POST）鉴权**全部只有 `Security(verify_api_key_dep)`**；而 `api/auth.py:44-74` 语义 = **任一有效用户 access token 即放行，role 不参与判定**。仓库已有 `api/auth_jwt.py:196-221 require_role("admin")` 未被使用。
- `replay/profile/events` 的 `session_key` 为**自由查询参数、无归属校验** → user4 传 user2 的会话键即可重放他人全部画像/工具/对话因果事件（P0-4 同族：隔离又只做了数据层键，没做接口层键）。
- `POST /curate` `apply=True` 为**破坏性**（delete_fact 进回收站 + 改写）；`session_key` 传空 → `run_curator_all_known` **全库所有用户**执行。前端 `frontend/src/api/system.ts:43-62` 已把该 destructive POST 暴露为普通客户端函数。
- **修复方向**：6 端点统一 `dependencies=[Security(require_role("admin"))]`（或至少 session_key 归属校验 + curate admin-only）。

### 新增 P1（AX P2 批次）
48. **persona_hint 注入链三处断、恒为空**：`proactive/llm_proactive.py::load_persona_hint` 本体正确，但调用侧拿不到 character_id——`_llm_proactive_one_user` 取 `eng._character_id`，**ASEEngine 无此属性**（真实属性是 `_knowledge_character_id`，`ase_engine.py:701`），异常/空被吞后传 `""`；即便走 hub 键推断，键形如 `N:peer` 无 `|` 分隔；`profile_projection` 产物也无 character_id 槽。**三源全空 → 每轮「人格提示」恒 `""`**，BOARD 宣称的 persona 闭环是空壳（文件解析、41 卡 glob 全跑，产物进不了 prompt）。
49. **`wait_minutes` 只入账不生效 + LLM 决策无静默前置闸 → 每用户约 288 次/天 LLM 空烧**：`ase_check` 为 5 分钟 IntervalTrigger（`scheduler.py:245-252`），`_llm_proactive_one_user` 每次调远端 LLM 决策；决策返回的 `wait_minutes` 仅写 ledger 事件，**无任何下一 tick 门控**；且 23:00–07:00 静默只在投递层硬闸（:570/:916/:965/:1039），LLM 调用发生在闸**之前** → 夜间照烧。消息不刷屏，token 恒流失。（他窗工作树在制品正在修 wait 门控，入账后本条应复核降级。）
50. **TOOL_RESULT 事件缺 turn_id → 回放/探针的 tool 维度是死壳**：`orchestrator/optimized_orchestrator.py:986-1003` append 时只带 session_key/payload，**不带 turn_id/character_id**；`replay(turn_id=…)` 按列切片永远命中 0 条 tool 事件；`scripts/ax_acceptance_probes` live 模式取最新 tool 事件作 turn 锚 → 恒 `no_turn_id_yet`。且该 append 为异步热路径内的同步 sqlite 写，失败仅 `logger.debug`。
51. **`data/agent_plane.db` 无保留策略**：`event_ledger.py` 只 append 不 prune，chat/tool/profile/web_disabled 全类型常驻 → 无界增长（`web_disabled` 在 enabled=false 时**每 tick 每用户写一行**，关闭态反而涨得最快）。
52. **CWD 相对路径两处回归（v1.8 教训同模式）**：① `load_persona_hint` 用 `Path("config/characters")` 且 cid 空时 **glob 解析全部 41 张卡 JSON**——每 5 分钟每用户一次；② `agent_plane_routes.curate` 全库扫描用 `Path("data/sqlite.db")`。工作目录非项目根的启动方式下双双静默失效。仓库真源是 `utils/project_paths.PROJECT_ROOT`。
53. **`write_config_file` 非原子读改写**（`scheduler.py:416-431`）：4 worker 下训练页每次保存配置都是全文件 RMW，无锁无临时文件 rename，并发写互相覆盖半文件风险；「下一 tick 生效」依赖各进程自行重读。

### 旧 P0 复核（该批次是否顺带修复）
- P0-1（dict→str 记忆蒸发）**未修**；P0-2（`user_profile.py:269` 画像库路径差三级）**未修**；P0-6（`run_api.py:296-303` 未 await 即 return True）**未修**；P0-8（`connector_registry.py:115-127` disconnect 不释放 flock fd）**未修**。

### 复核核实无恙（新代码中验过不是问题的）
- `curator.py` 对 `StructuredMemory` 的三处调用契约与真实签名逐一比对相符（`user_key_from_session:358`、`get_facts:603`、`delete_fact:694`），回收站语义用法正确（但 `memory_recycle_bin` 仍只增，归旧 P2）。
- `event_ledger.default_path()` 已正确锚定 `parents[2]/data/agent_plane.db`；每操作独立 `sqlite3.connect(timeout=30)`，无跨线程共享连接问题。
- `scripts/ax_clean_profiles.py` ROOT 锚定正确、默认 dry-run、`--apply` 显式开关，质量合格。

### 口径刷新
- 端点：`create_api_app` 内省（`AI_GF_ENV=dev`）**224 路由 / 190 唯一路径**（原 219/181，+5 路径组全为 agent-plane）。
- 本窗零代码变更，测试口径沿用 1455 收集 / 1451 通过 / 4 跳过、角色卡 41 张在位（**主检出工作树当前含他窗在制品**，上述数字对应 3e96a7c 提交态）。

## 完成声明四要素
- **验证证据**：本报告每条 P0/P1 附主审亲验行号与实况摘录；P0-2 含运行时探针实证（OperationalError）；AST 全仓扫描复核 async 内同步调用面；六路审计的「自查排除」段落逐条对码。复核实录（P0-10、P1-48~53）为 b7cd513/3e96a7c 变更带逐文件通读 + `create_api_app` 端点内省实跑（224/190）+ 属性存在性实测（`ASEEngine` 无 `_character_id`）。
- **边界检查**：只读审查零改动；未跑全量测试基线（零代码变更故沿用 1455/1451/4 口径并声明）；服务器侧运行态取证未做；六路子审计（含 persona）**全部回收并逐条对码**——证实者入册、误报者入排除清单（第 14 条）；增量复核覆盖 b76d6c6..3e96a7c 全部 13 文件，工作树他窗未提交在制品**未纳入**本口径。
- **已知限制**：P1 部分后果（如 zhipu 限流频率、bcrypt 时长、288 次/天为单用户满配间隔推算）依赖生产运行态，未在生产复现即定级者已注明触发条件；`大创赛…/user_data/` 镜像目录命中未计入正式清单（非活动代码）。
- **置信度**：P0 全部高（行级+运行时证据，含新增 P0-10）；P1 高（行级）个别中（成本量化）；P2 高；复核实录与「核实无恙」项均行级证据。

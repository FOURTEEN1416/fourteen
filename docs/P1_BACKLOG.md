# P1 Backlog — 唯一的你

**Updated**: 2026-11-08（v1.65.2 对抗验收修复批：UXV-1/2/5/6/7/8/9/10/18④ 共 10 项销账（内置默认卡裁决根治 UXV-10；复提记账根治 UXV-5；URL 防线根治 UXV-2）；UXV-3/4 主体随部署生效（决策 reason 措辞校验与 answered 记账两项增量仍开放）；UXV-12~17 与 UXV-18①②③⑤⑥⑦ 保留未决；测试污染真实卡库族已由 conftest CHARACTERS_DIR 沙箱根治；详见 `LOG.md` 2026-11-08 条目）

## 产品决策（待默默拍板；TOPIC-1 已实施待部署）

1. **[MM-V3] 表情识别（人脸）**：真空缺——`multimodal/` 仅通用视觉（VisionHandler 走 LLM `image_url`），非表情识别模型。需调研+选型；敏感生物特征，本地优先、不落盘原图。
2. **[MM-V4] 语音声学情绪**：真空缺——`voice/` 全是 TTS，ASRHandler 只取文本，声学信息被丢。需调研+选型。
3. **[Q4] 生物特征采集是否启用**：合规方向已调研（端侧只出特征、不上传原图），产品内启用与否归默默裁决。
4. **[OBS-2] 明文 HTTP（已裁决接受）**：2026-10-04 D13 裁决（DECISION_LEDGER）：生产维持裸 IP HTTP，不上 TLS，明文传输风险已知悉接受（微信扫码产品形态）。 nginx 模板已保留 certbot 升级路径与 X-Forwarded-Proto 头，若未来绑定域名可按注释直接启用；HSTS 仍严禁在纯 HTTP 下开启。
5. **[P1-11] GitHub 仓库名**：`FOURTEEN1416/fourteen.git` vs 项目名 `ai-girlfriend`，可选改名，需默默决策。
6. **[TOPIC-1] 话题状态机（已实施待部署）**：Q1–Q6 已裁决并全量落地——Q1-B 确定性状态机 + 每 4 轮稀疏 LLM（8s 硬超时、失败保留原状态）/ Q2 B+C 状态块 + 软化接续 / Q3-A parked 复提三重闸（≥6h + 非静默 + judge allow，closed 永不主动复提）/ Q4-B 全部状态 7 天 TTL / Q5 免费 API 不设每日 LLM 上限 / Q6-B 多 active 并行上限 3。落地：`utils/topic_state.py`（三表六 API）+ `proactive/topic_recall.py`（复提候选）+ 主链状态块 + 主动 `topic_plan` + 管理端三端点/前端「话题状态」区。实施结果见 `docs/reports/2026-10-27-话题状态机实施计划.md` §18。**测试 4,180 收集 / 4,174 通过 + 2 跳过 / 4 失败（既有）；前端 251/251；ruff 0；未部署；**默认开启**，回滚走管理端显式 `topic_state.enabled=false` / `llm_judge_enabled=false`）。

## 安全升级遗留（2026-10-04 P0 批后登记，来源=两轮攻击面审计 81 条发现中未入 P0 批的 confirmed 项）

> **2026-10-09 攻击面扫描批现状刷新**：六路对抗扫描对 SEC-1~11 逐项实地验证（见 `docs/verification/2026-10-09-attack-sweep.md`）——全部**未恶化**；SEC-6 补充证据（认证面 `/api/characters/{id}/favorites` 族 5 端点未与 shisi 面同规，其越权写面已由本批 PRIV-2 收口）、SEC-11 双锁漂移已实质发生（framer-motion/@tanstack/react-query 两生产依赖两锁版本分叉，FE-2 证据）；本批新确认暂缓项登记为 SEC-12~18。

6. **[SEC-1] 记忆/RAG/画像 untrusted 信封**（medium verified）：`persona_service.py:172-234` 记忆事实/画像块/RAG 知识以可信身份直入 system prompt，复核实测两个 payload 绕过全部正则并经 remember_facts 持久化。修法=套用 tool_gate 同款 `<context trust="untrusted">` 信封+入库前注入特征检测；prompt 行为变更需单独观察批次。
7. **[SEC-2] url_guard DNS rebinding TOCTOU**（medium verified，代码自登记遗留）：`tools/url_guard.py:55-86` check-then-connect 两次解析窗口。修法=transport 级固定解析 IP（自定义 adapter 按 IP 直连+Host/SNI 处理）。
8. **[SEC-3] 亲和 HTTP 面与对话键空间对齐**：P0 批收口后 HTTP 管理面键=`uid::cid`、对话路径键=`user_key::cid` 两套互不相通（前端零消费无现行破坏，docstring 已如实标注）；控制台要读对话积累的好感需会话键映射设计，另立批次。
9. **[SEC-4] 微信日志历史原文**：P0 批已改元数据化（AST 防复活钉），但轮转旧文件 `data/app.log.2` 仍存 37 条消息原文——自然过期（10MB×5 轮转）或服务器侧手动清理，归默默裁决。
10. **[SEC-5] EncryptionManager 接线**（medium verified）：BYOK llm_config 列与微信 bot 凭证明文落盘（生产实证 credentials.json 644）；掩码递归已修，静态加密未接。
11. **[SEC-6] shisi favorite/forward owner 列迁移**：P0 批过渡期已挂 admin 门；数据模型补 user_key 列+端点主体过滤待后续批次。
12. **[SEC-7] 公开仓 IP/SSH 泄露清理**（medium verified）：10 个 tracked 文件含生产 IP/端口 28222/root 通道；涉及 git 历史与 SSH 凭据轮换（§9.3 既有裁决要求），破坏性操作归默默裁决。
13. **[SEC-8] WS query 凭证通道收口**：nginx /ws/ access_log off 已做；`websocket_server.py:127-132` 的 URL query 传 token 路径未删（当前前端零 WS 消费者），改首帧认证优先需与未来 WS 客户端约定同批。
14. **[SEC-9] LLM 语义内容分类异步旁路**（low）：`SAFETY_LLM_CLASSIFY`/`PROMPT_INJECTION_LLM` 生产默认关（SSH 实证未设），规则层可绕过面在案；异步旁路方案（不阻塞主链）待做。
15. **[SEC-10] Python 依赖 lockfile**（medium verified）：无任何 lockfile，46 项开放下界约束，本地实测漂移三例（websockets 16.1.1<17.0、structlog/cloudscraper 未装）；引入 uv lock 或 pip-tools 哈希锁使三端可证可复现。CI 审计门（前端）已上线。
16. **[SEC-11] 低危卫生包**：chromadb 1.5.9 Critical 无补丁（嵌入式模式不可达，跟踪升级+禁 server 暴露规范）、pillow/cryptography/aiohttp/sentence-transformers 升级（devDependencies 域已清，Python 侧随常规周期）、memory 层零消费者全表接口收口或整删、UserContext WS 通道 user_id 注入、e2e 测试口令生产注册黑名单、bun.lock 与 package-lock 双锁维护纪律（每次依赖变更双写）。

## 安全升级遗留·二（2026-10-09 攻击面扫描批登记，来源=六路对抗扫描 30 发现中 confirmed 但暂缓的 P2 项；报告 `docs/verification/2026-10-09-attack-sweep.md`）

17. **[SEC-12] 账号锁定武器化（EXT-4）**：5 败锁 15 分钟无任何解锁通道（无邮箱验证/CAPTCHA/admin 解锁端点），攻击者经枚举 oracle 可对任意已知账号周期性续锁无限期拒绝其登录（锁定检查先于密码验证，正确密码也被 401 短挡）。修法需行为设计（解锁链路），与 EXT-3 oracle 已收口配套评估。
18. **[SEC-13] WS 未认证连接资源耗尽（EXT-6）**：`websocket_server.py` MAX_CLIENTS=1000 仅认证后检查，未认证连接每条挂 10s 协程/FD；nginx /ws/ 无 limit_conn/limit_req、systemd 无 LimitNOFILE。低门槛可用性攻击（非数据面）。修法=nginx /ws/ 连接限制 + 认证超时收紧。
19. **[SEC-14] web_enricher 抓取链零接入 url_guard（INJ-4）**：url_guard 自称唯一校验 owner 但 `persona_extractor/web_enricher.py` DirectScraper/Jina/Crawl4AI 全链裸 requests（自动跟随重定向、无内网校验）——潜伏 SSRF+守卫覆盖断链；现行利用需 SEO 操纵搜索结果，触发器=任何新调用方传入用户可控 URL。修法=三引擎统一收口 fetch_guarded 或前置 assert_public_http_url+禁自动重定向逐跳校验。
20. **[SEC-15] CI 前端依赖审计门即将红（FE-1）**：~~`ci.yml:79` npm audit --audit-level=high 与 dev 链新 advisory 冲突，下次 push CI 必红~~ **✅ 已根治销账（2026-10-11，commit `aea3cd0`）**：vitest ^3→^5.0.3 + @vitest/coverage-v8 同步（vitest 4 起 pool 重写移除 tinypool，2 critical 出树）+ npm audit fix 带 source-map-js 至修复版 + setup.ts 改 `@testing-library/jest-dom/vitest` 入口（vitest 5 断言类型双参数化适配）。验证：audit 0 vulnerabilities / tsc 0 / build ✓ / vitest 182/182 / **CI 全绿（run 38109894676，9/9 job）** / 三端部署复验全绿。**连带 FE-2 双锁漂移收敛**（framer-motion 两锁统一 12.43.0、react-query 统一 5.104.1，审计树=构建树）。
21. **[SEC-16] Content-Security-Policy 全缺（FE-3）**：nginx 模板与 index.html 双侧均无 CSP；当前 XSS sink 面窄（无 dangerouslySetInnerHTML）故为纵深防御缺口非现行漏洞，SPA 与 API 同源下 `default-src 'self'` 类策略零兼容成本。
22. **[SEC-17] crawl/enrich 长任务无超时无全局并发闸（ABUSE-5）**：`knowledge_routes.py` 裸 `asyncio.to_thread`（自证单次 33.2s）无 wait_for、限速为进程内计数（4 worker 实况放大 4 倍）；多账号可饱和默认线程池饿死全进程 to_thread 依赖（含提醒轮询）。修法=超时+专用有界 executor/信号量。
23. **[SEC-18] set_reminder 无每会话数量上限（ABUSE-7）**：攻击者可在自己会话内无界堆积 pending 提醒行，到期集中触发 LLM 文案生成+外发脉冲（对照 proactive 出站 8 条/天硬配额，提醒通道零配额）。修法=每会话 active 上限（如 50）+超限回提示。

## 环境 / 运维

24. **[OBS-3] systemd 服务以 root 运行**（真审计实证漂移）：生产 unit 实况 `User=root`+无 PrivateTmp+`--workers 4`；仓库模板基线 `User=www-data`+`PrivateTmp=true`（workers 已对齐 4 并加漂移注记）。降权涉及 data/ 属主迁移与停机窗口，归默默裁决；同机另两服务亦 root（其一共享本项目 venv）。
25. **[OBS-4] 备份产物权限代码保证**：P0 批 backup_manager 已全产物 0600+目录 0700（跨平台契约测试钉）；当前生产实存备份已收紧（09-26 手工），cron 后续产物由代码保证。

## 低优先级

26. **[P1-9] Proxy**：`http://127.0.0.1:7897` 在 `git push` 时偶发 connection reset（本地网络环境，非代码）。
27. **[P1-13] AGENTS.md PAT 认证**：与 opencode 内置认证可能冗余。
28. **[FE-ENV-1] Lighthouse 本机不可用**：headless Chrome 无法提交帧（NO_FCP），环境约束；性能验证固定改用 CDP `Performance.getMetrics` + 真机网络清单口径。

> 10/11 两项（[PY-DEAD-1] `ASEEngine.get_state()` 死码 / [FE-TYPE-1] `ProactiveEngineState` 前端死链）已于 2026-09-30 遗留待办批执行整删销账，见 `DELETION_LOG.md` 同日节。

## 纯新用户体验对抗验收登记（2026-11-07 v1.65.1 零代码验收批；来源=API 级沙箱旅程 + 界面审读 + 生产取证 + 代码审计，18 条全数 verified 未修复）

> **P10 话题意识复核裁定（2026-11-08 v1.65.2 后更新）**：q1_gap_bridging=partial——本地主病灶闭合（120min 锚点窗 + 状态块超窗降级）；残余 low 未清零（UXV-13/14/15）；生产 unsolved 直至部署。q2_repeat=**主体已闭合**——幻觉 URL 出站防线（UXV-2）与 parked 复提记账+冷却升级（UXV-5）已于 v1.65.2 落地（`test_p10_residual_guards.py` 7 例），随部署在生产生效；topic_plan 同题续期闸（UXV-12）仍开放。假链接案生产取证三根因：幻觉 URL 本体、时间感失真、「未闭环+≥6h 衔接窗」跨时段硬连——前两根因与周期复提均已修，部署后待生产复测。

31. **[UXV-3·部分销账 2026-11-08] 主体随部署生效；增量开放：决策 reason「刚/刚刚」措辞的时间锚定校验（v1.65.2 未做）—— 主动决策线时间感失真 + 「未闭环」跨时段硬连旧话题（medium verified，来源=生产取证，旧代码实录）**：event_ledger proactive_send 三条 reason 逐字核实——10-30T16:32:11Z 把 3.4h 前的提问写成「用户刚问过」（实问在 chat_history #3420 05:10:48Z）；20:37:26Z「用户 7.4 小时前问链接…符合 ≥6 小时自然衔接窗口，且该事未闭环」；次日 08:27:12Z「承接昨晚20:49用户指出假链接的未闭环事件」。机制根源=旧代码续聊锚点窗 24h + 状态块无超窗降级；新代码已改 120min 窗 + 超窗降级（`utils/topic_state.py:72`、`:546-555`、`orchestrator/optimized_orchestrator.py:1383-1393`），但生产未部署。修法=随本批部署生效；可再给决策 reason 的「刚/刚刚」措辞加与主链 `last_turn_age_seconds` 同款时间锚定校验。
32. **[UXV-4·部分销账 2026-11-08] 部署 P10 去重后复测；增量开放：主动线 answered 记账防跨天追问（v1.65.2 未做）—— 话题无节制重复追问与复读问候（medium verified，来源=生产取证，旧代码实录）**：#3991(11-06 08:09:37 UTC)「结果出来了吗，医生怎么说」与 #3999(11-07 07:49:51)「结果出来没，医生怎么说」跨 23.7h，且 #3989(11-06 02:20:50,user)「哎呀，还没出发呢」已作答；#2853-#2856 四条 proactive「(早上好，)你今天感觉怎么样？」02:36:53/03:41:54/08:14:56/09:19:53 UTC（08:14 UTC=北京 16:14 仍发「早上好」）；#3990 与 #4000 跨 30.6h 逐字相同；另取证材料 D3 组 10 分钟内逐字 ×3 等 ≥5 组。difflib 相似度数值（0.87/0.89~0.97）来自取证材料脚本未复跑（引文与时间戳已独立核实）。修法=部署本批 P10 去重（锚点 newest-first + 状态块 seen_titles）后复测；主动线可再对「用户已作答的话题」加 answered 记账防跨天追问。
40. **[UXV-12] topic_plan「上次决定的下一步」可被 upsert 反复续期无同题次数闸（low verified，来源=审计）**：`utils/topic_state.py:1336-1351` save_next_plan `ON CONFLICT ... DO UPDATE SET topic=excluded.topic...consumed_at=NULL`（:1339-1341）无同题比较与次数记账；`proactive/scheduler.py:2117-2130` skip 分支模型给 next_topic/intent 即 upsert 续期（wait≤240min 粒度）；take_next_plan due_only=`next_at <= now`（:1408-1421）。仅靠 judge 与出站硬闸把关是否真发出。修法=save_next_plan 对「next_topic 与库内未消费计划同题」加连续次数记账，超 N 次拒绝续期并落账本。
41. **[UXV-13] 跨会话尾巴 get_cross_session_tail 无任何时间窗（low verified，来源=审计）**：`shisi/memory/legacy/structured_memory.py:1466-1511` 按 id 取最近 8 行（:1488 无时间窗过滤，仅 :1495 滤「处理超时」行）；`orchestrator/optimized_orchestrator.py:1454-1490` 近史 <2 行时触发、limit=8——可把 >120min 前的用户原话原文注入 prompt（P10 同时段窗的残余缺口）。缓解已核：每行带（MM-DD HH:MM）北京墙钟前缀（:1501-1510）+ 注入前 scrub_history_rows 净化（:1480-1482）+ untrusted 信封。修法=增加 max_age_hours 参数（与锚点窗同源配置），窗内无行则返回空。
42. **[UXV-14] render_topic_state_block 超窗判定用裸 datetime.now()（low verified，来源=审计）**：`utils/topic_state.py:545` `wall_now = now or datetime.now()`，与本文件 :21-22「统一 now_local() 北京墙钟」规则及 last_active_at 口径不一致；生产快照路径 `:505` 不传 now 走裸墙钟（UTC 主机永不降级）；对照锚点路径 orchestrator:1392 已显式传 now_local。生产 TZ=Asia/Beijing 当前无实害。修法=默认值改 `utils.local_time.now_local()`（去 tzinfo），与模块规则对齐。
43. **[UXV-15] 两处时间窗 fail-open：解析失败行不降级（low verified，来源=审计）**：状态块 `utils/topic_state.py:549-553` `_parse_ts` 返回 None 的 active 话题保留「正在聊」；锚点窗 `utils/prompt_sanitize.py:347-358` `:348` `if ts and wall_now is not None:` 才进窗判定、:357-358 except pass 注释「解析失败按无时间窗行处理（fail-open，兼容旧数据）」。修法=状态块解析失败行剔除出「正在聊」并计 debug 日志；锚点窗 fail-open 若保留需补一条测试钉防止语义回漂。
44. **[UXV-16] 前端 UnifiedCharacter 把 created_at/updated_at/version 声明为必需而策展老卡缺三字段（low verified，来源=多处）**：`frontend/src/types/api.ts:508-521` 三者无 `?` 必需；确定性检查（`python -I -c` glob config/characters/*.json 逐卡读键）：43 张卡仅 3 张含全部三字段（注：当前盘面卡数 43，AGENTS 口径 41 系 10-31 快照，卡数本就是浮动变量）；grep RolesPage/CreateRole/RoleSettingsTabs 零消费 created_at（当前无渲染缺陷）。修法=TS 三字段改可选（?）或后端列表响应补齐字段，防未来消费即 undefined。
45. **[UXV-17] 业务错误的 error_code 恒 UNKNOWN + pydantic 422 直出英文（low verified，来源=多处）**：`api/app_factory.py:201-205` `status_code_map` 只覆盖 401/429/503/404/504/502，400/409/403 恒 UNKNOWN（`error_code = ... or status_code_map.get(exc.status_code, "UNKNOWN")`）；对照注册端点 409 特带 `X-Error-Code: REGISTRATION_CONFLICT`（`api/routers/auth_routes.py:191`）的机器码纪律；grep RequestValidationError 于 api/ 与 main.py 零命中 → 422 走 FastAPI 默认英文处理器（normalize.ts:21-34 仅拼 `loc: msg` 英文串；旅程实测 self_intro 601 字/reply_mode 非法值均英文 toast）。修法=status_code_map 补 400/409/403 机器码映射 + 加 localize 的 RequestValidationError 处理器转中文。
46. **[UXV-18·④ 已销账 2026-11-08（进度键改 title），①②③⑤⑥⑦ 保留] 前端打磨项集（七子项，均 low verified，来源=界面）**：① `CharacterSettingsPanel.tsx:190` 面板说明把 markdown 星号直渲为「**全部设置**」（无渲染器）；② `:145-153`「恢复出厂默认」一键 DELETE 全部 44 字段快照无二次确认且紧邻保存按钮（:355-363）；③ `:370` 保存成功后主按钮永驻「已保存（HH:MM）」（cfg 变更不回落保存态）；④ `HelpGuidePage.tsx:202` `key={item.to}` 与 :36/:41 两条 item.to 均为 '/roles'（进度互覆）；⑤ `Breadcrumb.tsx:33-39` tabLabels 无 proactive 键、:41 固定「系统设置」旧名（navGroups 现为「偏好与设置」）；⑥ `NotFoundPage.tsx:9-12`「返回仪表盘」指向 / 实落微信连接页且全站无仪表盘页；⑦ `LoginPage.tsx:105` 登录副标题「登录管理控制台」内部词汇观感错位。修法=七子项各按一处小改收口：去星号 / 加 ConfirmDialog / cfg 变更回落保存态 / key 与进度键改 title / 补 proactive 映射+统一组名 / 文案改「返回首页」 / 副标题改「进入你的控制台」。

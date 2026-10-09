# P1 Backlog — 唯一的你

**Updated**: 2026-10-05（安全升级批：新增 §安全升级遗留 11 项登记 + OBS-2 按 D13 裁决改口径 + OBS-3 补漂移细节；本文件只载真实未决项，历史处置记录见 git 历史与 `LOG.md`）

## 产品决策（待默默拍板）

1. **[MM-V3] 表情识别（人脸）**：真空缺——`multimodal/` 仅通用视觉（VisionHandler 走 LLM `image_url`），非表情识别模型。需调研+选型；敏感生物特征，本地优先、不落盘原图。
2. **[MM-V4] 语音声学情绪**：真空缺——`voice/` 全是 TTS，ASRHandler 只取文本，声学信息被丢。需调研+选型。
3. **[Q4] 生物特征采集是否启用**：合规方向已调研（端侧只出特征、不上传原图），产品内启用与否归默默裁决。
4. **[OBS-2] 明文 HTTP（已裁决接受）**：2026-10-04 D13 裁决（DECISION_LEDGER）：生产维持裸 IP HTTP，不上 TLS，明文传输风险已知悉接受（微信扫码产品形态）。 nginx 模板已保留 certbot 升级路径与 X-Forwarded-Proto 头，若未来绑定域名可按注释直接启用；HSTS 仍严禁在纯 HTTP 下开启。
5. **[P1-11] GitHub 仓库名**：`FOURTEEN1416/fourteen.git` vs 项目名 `ai-girlfriend`，可选改名，需默默决策。

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
20. **[SEC-15] CI 前端依赖审计门即将红（FE-1）**：`ci.yml:79` npm audit --audit-level=high 与 dev 链新 advisory（tinypool 2 critical 原型污染→RCE、source-map-js 1 high，均 devDependencies 不进生产 bundle）冲突，下次 push CI 必 fail 阻塞部署管线；唯一修复路径=vitest 3→5 breaking 升级。需主动排期。
21. **[SEC-16] Content-Security-Policy 全缺（FE-3）**：nginx 模板与 index.html 双侧均无 CSP；当前 XSS sink 面窄（无 dangerouslySetInnerHTML）故为纵深防御缺口非现行漏洞，SPA 与 API 同源下 `default-src 'self'` 类策略零兼容成本。
22. **[SEC-17] crawl/enrich 长任务无超时无全局并发闸（ABUSE-5）**：`knowledge_routes.py` 裸 `asyncio.to_thread`（自证单次 33.2s）无 wait_for、限速为进程内计数（4 worker 实况放大 4 倍）；多账号可饱和默认线程池饿死全进程 to_thread 依赖（含提醒轮询）。修法=超时+专用有界 executor/信号量。
23. **[SEC-18] set_reminder 无每会话数量上限（ABUSE-7）**：攻击者可在自己会话内无界堆积 pending 提醒行，到期集中触发 LLM 文案生成+外发脉冲（对照 proactive 出站 8 条/天硬配额，提醒通道零配额）。修法=每会话 active 上限（如 50）+超限回提示。

## 环境 / 运维

17. **[OBS-3] systemd 服务以 root 运行**（真审计实证漂移）：生产 unit 实况 `User=root`+无 PrivateTmp+`--workers 4`；仓库模板基线 `User=www-data`+`PrivateTmp=true`（workers 已对齐 4 并加漂移注记）。降权涉及 data/ 属主迁移与停机窗口，归默默裁决；同机另两服务亦 root（其一共享本项目 venv）。
18. **[OBS-4] 备份产物权限代码保证**：P0 批 backup_manager 已全产物 0600+目录 0700（跨平台契约测试钉）；当前生产实存备份已收紧（09-26 手工），cron 后续产物由代码保证。

## 低优先级

19. **[P1-9] Proxy**：`http://127.0.0.1:7897` 在 `git push` 时偶发 connection reset（本地网络环境，非代码）。
20. **[P1-13] AGENTS.md PAT 认证**：与 opencode 内置认证可能冗余。
21. **[FE-ENV-1] Lighthouse 本机不可用**：headless Chrome 无法提交帧（NO_FCP），环境约束；性能验证固定改用 CDP `Performance.getMetrics` + 真机网络清单口径。

> 10/11 两项（[PY-DEAD-1] `ASEEngine.get_state()` 死码 / [FE-TYPE-1] `ProactiveEngineState` 前端死链）已于 2026-09-30 遗留待办批执行整删销账，见 `DELETION_LOG.md` 同日节。

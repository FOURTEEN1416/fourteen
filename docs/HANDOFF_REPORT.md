# 唯一的你·十四 — 新窗口交接（HANDOFF 单一真源）

> 本文只载**当前状态**与**现行纪律**：该改就改、该删就删，不留历史快照堆叠。
> 历史批次全过程见 `LOG.md`（唯一只追加不覆盖文件）；决策拍板见 `docs/DECISION_LEDGER.md`；删除见 `docs/DELETION_LOG.md`。
> 2026-09-29 文档清理批：原五层历史快照与旧窗口（09-19/09-26）交接正文移除，信息由 `LOG.md` 与 git 历史承载。

## 当前状态快照（2026-10-15 质量回调批刷新）

- **三端**：本地 = origin = 服务器 **`1df4921`**（质量回调批：agnes 超时 8→15s 误杀根治 + 主动消息上下文统一真源 `proactive/proactive_context.py` + ASE 生成线角色化 + 决策线画像/否认双源升级 + 熔断期主动消息不降级）；10-15 代码链：`5576e9d`（对抗验收）→`1df4921`（本批）。工作树文档同步中（LOG/AGENTS/README/HANDOFF 随后单独提交）。
- **终验口径（Python/前端 = 2026-10-15 质量回调批终验 `LOG.md`）**：四分块 **3233 收集 / 3232 通过 / 1 跳过 / 0 失败**（**216 测试文件**、**42 卡**在位）+ ruff 0 + vitest **182/182**（30 文件）+ 生产冒烟（人设 465 字含通用禁令/回复示例、沉浸约束含禁客服腔+非共处、`primary_provider_status` 双态正确、agnes timeout=15、scheduler 模型闸在位）；徽章 **3414**。
- **主动消息上下文统一真源（2026-10-15 现行要点）**：`proactive/proactive_context.py` 是两条主动线（决策 `llm_proactive` / 生成 `ase_engine`）与祝福润色共用的唯一人设/约束构造器——`load_full_persona_hint`（default→persona.yaml 全量 description+通用禁令；卡→五段拼装≤1200 字；**动态读零缓存**，切角色即时生效+用户隔离天然成立）、`immersive_proactive_constraints`（沉浸/小说双模式：禁括号动作/非共处/禁客服腔三连问/话锋对齐/10~60 字）；画像唯一读入口 = `shisi.agent_plane.runtime.get_profile_prompt_block`（主链同款，含 preferences）；否认称呼双源 = `denied_names_for`（窗口检测+ledger 持久，用户 30 天 prune 后仍生效）。**旧 `llm_proactive.load_persona_hint`（400+200 裁剪版）仅存兼容，新代码一律走 proactive_context**。
- **模型闸现行要点（2026-10-15）**：`multi_provider_gateway.primary_provider_status()` 暴露链首熔断态；scheduler 决策链在静默闸后、等待窗前检查——首选（agnes）熔断开闸期**主动消息跳过不降级**（账本 `model_degraded` 每用户每日一条，init/load/persist/purge 四处同步）；agnes `request_timeout=15s`（8s 低于健康分布 3.8~19.8s 中位，误杀是 82% 流量降级智谱的根因；**下调超时前必须复核该分布**）。ASE 生成线角色化：`ASEEngine.character_id_resolver` 闭包绑定 user_key（`_init_mixin` 注入，默认 default 回落）；`config/prompts/proactive.yaml` 已降级为可选风格覆盖位（generation_prompt 空，主路径为角色卡驱动）。
- **异常回复守卫现行要点**：终审守卫 `terminal_reply_guard`（`utils/prompt_sanitize.py`）是发送与落库前最后一道门——错误哨兵（`（所有 LLM 提供商均不可用…）` 等）/表情复读开头/只剩角色名退化回复拦截→角色化兜底；流式 `_stream_mixin` 同接；主动链 `_deliver` 统一兜底（决策线/手动发送线/重要日期祝福线三路径）。表情能力保留（用户裁决）：`EMOJI_USE_RULE` 只做「禁复读」全局守卫，数量/风格/时机归角色卡唯一 owner；`strip_emoji_echo` 只剥机械复读，系统占位 `[已消毒]`/`[blocked:xx]` 不误伤（`_is_emoji_token` 纯中文/数字/emoji 判据）。
- **主链身份锚现行要点**：`USER_IDENTITY_RULE` + 【被否认称呼】段进主链 prompt（前置，对抗历史注意力惯性）；**亲密呼语「笨蛋/傻瓜」显式豁免**（禁令对象是名字不是语气，防拟人化弱化）；「真实记得的事要自然地用」；`detect_denied_nicknames` 补「X是谁」语序（「十七是谁」此前漏检）。
- **画像否决门现行要点**：`write_profile_from_tool` 写侧拒绝被否认称呼+存量自愈清空；`_denied_names` 双源（全量用户消息 + `profile_correct.denied_name` 持久记录，**用户消息 30 天 prune 后否决仍生效**）；读侧守卫 `format_profile_prompt_block`（脏数据不注入 prompt）。生产净化：画像「十七」→空、否决记录落账（actor=denial_gate）、残留事实 6 条按回收站+水位语义清、哨兵行 2 条删（备份在案）。
- **重复注入根治**：引擎约束层删硬编码长度表（长度唯一 owner=reply_mode）；卡文自带身份拷问剧本时引擎关通用段（`include_identity_defense`）；内置卡通用禁令删表情/长度重复款。
- **对话节奏现行纪律（D14）**：下一轮对话何时开口唯一 Owner = proactive 决策链（LLM 决策 + 双闸：主动间隔≥60min / 回复后冷却≥15min / 未回复连击≤4）；**任何定时追问机制不得再加回**；拆条上限 2 条。遗留：`proactive/runtime_plane.py` followup_budget 表待后续批清（DELETION_LOG 在案）；agnes-3.0-flash 抖动（2026-10-15 已处置：超时 15s 治误杀 + 熔断期主动消息跳过不降级——剩余抖动=供应商侧，观察）。
- **通道产品语义现行纪律（D16，2026-10-12）**：**谁扫码谁聊**——一个注册账号=一个用户，扫码人微信 id（凭证 `ilink_user_id`）即用户本人化身；非扫码人消息=异常拒绝；**角色选择唯一入口=控制台 activate**（同步扫码人绑定），任何「好友/代聊/访客」类概念不得再引入；主动节奏按账号聚合（`account_pacing_state`）；**一人一 bot 一条通道**（`MAX_CHANNELS_PER_USER=1`，2026-10-12 补裁 `7ac7418` 取代 09-19「一人两条」）。
- **跨通道身份组（D15 机制保留、在用关联已拆）**：`user_identity_map` 显式声明机制仍在（`scripts/link_identities.py`，本批起 link/unlink/purge 全程审计留痕）；2026-10-12 D16 取证后账号 2↔4 关联已 unlink（两账号=两不同扫码人，此前合并属越权；备份 `sqlite.db.bak-unlink-20261012-1850`，现 `user_identity_map` 0 行）。chat_history 原始历史与情景层不跨通道合并（边界在 D15 在案）。
- **生产**：health/ready 200、4 worker、迁移并发竞态 P1 已根治（`run_migrations` 包 `BEGIN IMMEDIATE` 单写事务 + WAL 有限重试）、websocket 出站通道已修复（`client_count` @property 误调用）。
- **10 月安全线**：SEC-15 已销账（vitest tinypool 双 critical 根治 + FE-2 双锁收敛，run `38109894676` 审计门复绿）；SEC-12~14/16~18 维持登记（P2 暂缓）；SEC-1~11 遗留（凭据轮换 / prompt 行为观察 / git 历史清理等）待裁决——见 `docs/P1_BACKLOG.md` 安全区段。
- **活跃工作**：W1 论文/CS 实证线（`大创赛报名以及后期发展/论文-唯一的你十四/`；《心理学进展》稿件 1136831 **已拒稿**——2026-09-30 通知，编务按"个案研究超收稿范围"退稿，附两条审稿意见（业界对照缺失 / AGIL 映射与失衡界定需理论化）；处理走 09-25 `recon/` 重构台账，main-v2 已合稿，首发目标=中国科技论文在线；CS 实验待跑项见 `cs_experiment/STATUS.md` §2）。
- **待办真源**：`docs/P1_BACKLOG.md`（**28 个编号条目**：产品决策 5（MM-V3 / MM-V4 / Q4 / OBS-2 / P1-11）+ 安全升级遗留 SEC-1~11 + 攻击面批次 SEC-12~18 + 环境/运维 2（OBS-3 / OBS-4）+ 低优先级 3（P1-9 / P1-13 / FE-ENV-1）；V3 表情识别 / V4 语音声学情绪选型、生物特征采集裁决、HTTPS、systemd root、仓库改名等）。
- **窗口登记**：`docs/board/BOARD.md`（只载活跃窗口；已完成窗口终态由 `LOG.md` 承载）。

## 接手必守（现行纪律，违反即事故）

1. **nginx 入口冻结**：线上 `listen 80; server_name 139.199.199.174` **不得改动域名/端口**；仓库模板 `deploy/nginx-ai-girlfriend.conf` 已改 `${DOMAIN}` 占位符与线上不一致——**勿用模板重新生成线上配置**。
2. **多会话并行**：主检出常有其他会话在写，**禁 `git add -A` / `git commit -a`**；`AGENTS.md`/`README.md`/`LOG.md` 是混批高发区，提交前 `git diff --numstat` 确认改动归属。
3. **白名单提交**：只精确 `git add` 本窗文件；产物落盘即提交；测试基线不可回归（引用基线必须同时声明卡数，`config/characters/` 为 gitignored）。
4. **部署纪律**：A 档（代码）push 后走正典 `git fetch + merge --ff-only` + `deploy/remote_deploy.sh` + health 核验 + 改动 blob `git hash-object` 三端抽验（跨端同一性不能比 md5，CRLF/LF 必不同）；B 档（纯文档）不上服务器。
5. **真源文档单写**：`AGENTS.md`/`README.md`/`LOG.md`/`BOARD.md`/本文件/`P1_BACKLOG.md` owner=主控；其他窗口跨窗信息一律追加 `BOARD.md` 追加区。
6. **参赛材料不入库**：`大创赛报名以及后期发展/` 全目录 gitignore（宪法 §3 三不入）。
7. **文档时效纪律（2026-09-29 默默裁决）**：除 `LOG.md` 只追加不覆盖外，其余文档该改就改、该删就删——禁止删除线+注记的备注式清除，禁止历史快照分层堆叠。

## 指路

- 为什么这么改 → `LOG.md`｜决策拍板 → `docs/DECISION_LEDGER.md`｜删了什么 → `docs/DELETION_LOG.md`｜改了什么（机器审计）→ git 历史｜功能清单 → `docs/FUNCTION_INVENTORY.md`｜代码实况 → 根 `CODE_GRAPH.md`｜文档入口 → `docs/README.md`

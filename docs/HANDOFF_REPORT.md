# 唯一的你·十四 — 新窗口交接（HANDOFF 单一真源）

> 本文只载**当前状态**与**现行纪律**：该改就改、该删就删，不留历史快照堆叠。
> 历史批次全过程见 `LOG.md`（唯一只追加不覆盖文件）；决策拍板见 `docs/DECISION_LEDGER.md`；删除见 `docs/DELETION_LOG.md`。
> 2026-09-29 文档清理批：原五层历史快照与旧窗口（09-19/09-26）交接正文移除，信息由 `LOG.md` 与 git 历史承载。

## 当前状态快照（2026-10-12 跨通道事实共享批刷新）

- **三端**：本地 = origin = 服务器 `/opt/ai-girlfriend` **`c0244b2`**（2026-10-12 跨通道事实共享批：D15 显式身份组真共享；部署复验 health 200、重启窗口 0 ERROR、默默两通道关联已建合并池 217）。10-12 代码一致链：`26df08b`→`926c2fd`→`e0eb5cd`→`a2a597b`→`c0244b2`。
- **终验口径（Python/前端 = 2026-10-12 跨通道事实共享批终验 `LOG.md`）**：四分块存量+新文件 **3016 收集 / 3015 通过 / 1 跳过 / 0 失败**（**195 测试文件**、**42 卡**在位）+ ruff 0 + vitest **182/182**（30 文件）+ 突变验红 3/3；徽章 **3197**。
- **对话节奏现行纪律（D14）**：下一轮对话何时开口唯一 Owner = proactive 决策链（LLM 决策 + 双闸：主动间隔≥60min / 回复后冷却≥15min）；**任何定时追问机制不得再加回**；拆条上限 2 条。遗留：`proactive/runtime_plane.py` followup_budget 表待后续批清（DELETION_LOG 在案）；agnes-3.0-flash 抖动观察项。
- **跨通道身份组现行纪律（D15）**：同一真人多通道事实共享走 `user_identity_map` **显式声明**（`scripts/link_identities.py`，绝不按 owner/peer 猜测）；未声明键隔离行为 100% 不变；删除=组内等价副本全歼+全键水位；默默两通道已关联（组 igrp_bfc87dd2321a69a3）。chat_history 原始历史与情景层（diary/reflections）不跨通道合并（边界在 D15 在案）。
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

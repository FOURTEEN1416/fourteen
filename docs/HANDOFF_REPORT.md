# 唯一的你·十四 — 新窗口交接（HANDOFF 单一真源）

> 本文只载**当前状态**与**现行纪律**：该改就改、该删就删，不留历史快照堆叠。
> 历史批次全过程见 `LOG.md`（唯一只追加不覆盖文件）；决策拍板见 `docs/DECISION_LEDGER.md`；删除见 `docs/DELETION_LOG.md`。
> 2026-09-29 文档清理批：原五层历史快照与旧窗口（09-19/09-26）交接正文移除，信息由 `LOG.md` 与 git 历史承载。

## 当前状态快照（2026-09-29 刷新）

- **三端**：本地 = origin git HEAD **`2132ade`**（09-28 后两个纯文档批 `2d805cf` 遗留收口 / `2132ade` 磁盘巡检已入库）；三端**代码一致点 `2ebfc61`**（服务器 `/opt/ai-girlfriend`，纯文档批不上服务器）。
- **终验口径（09-28 服务器同步批）**：全量五分块 **2652 收集 / 2651 通过 / 1 跳过 / 0 失败**（174 测试文件，41 卡零净增、注册流沙箱在位）+ ruff 0 + vitest 177/177；GitHub CI run `36402213711` @ `2ebfc61` 全绿；徽章 **2828**。
- **生产**：health/ready 200、4 worker、迁移并发竞态 P1 已根治（`run_migrations` 包 `BEGIN IMMEDIATE` 单写事务 + WAL 有限重试）、websocket 出站通道已修复（`client_count` @property 误调用）。
- **活跃工作**：W1 论文/CS 实证线（`大创赛报名以及后期发展/论文-唯一的你十四/cs_experiment/`；论文已投稿《心理学进展》稿件 1136831 审稿中；CS 实验待跑项见其 `STATUS.md` §2）。
- **待办真源**：`docs/P1_BACKLOG.md`（9 项：V3 表情识别 / V4 语音声学情绪选型、生物特征采集裁决、HTTPS、systemd root、仓库改名等）。
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

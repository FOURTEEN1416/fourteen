# LOG — 项目操作日志（L2 留痕层）

> 创建：2026-08-28 | 依据：歆歆操作约定 §4（三层留痕）+ 治理调研 P3 落地
> **分工不重复**：为什么改（过程与原因）→ 本文件；决策拍板 → `docs/DECISION_LEDGER.md`；删了什么 → `docs/DELETION_LOG.md`；改了什么（机器审计）→ Git 历史；当前状态/交接 → `docs/HANDOFF_REPORT.md`
> **纪律**：每个工作会话收尾必须追加一条（无日志 = 会话未闭环）；条目粗粒度按任务计，不写流水账；追加式，禁删改旧条目；禁写入密钥/隐私。
> **档位说明**：hook 自动化经查当前 ZCode 宿主（`~/.zcode/cli/config.json` 仅 mcp/model 键）不可用，先执行纯约定档（用户 08-28 裁决 D2：能 hook 则 hook，不能则约定）；宿主未来支持 hooks 后升级。

---

## 2026-08-28 — 文档与代码图谱治理（P0~P3 全量落地）

**任务**：用户点名 5 痛点（文档混杂/方向不明/代码不反映现状/治理三任务/操作无日志），要求方法论先行 → 调研 → 增量治理。

**动作与原因**：
1. 方法论调研（GitHub-First 合规）：Diátaxis/MADR/Keep a Changelog/C4/Aider Repo Map/pydeps/Cline Memory Bank/PVB/OWASP Logging，报告入 `docs/reports/2026-08-28_文档与代码图谱治理方法论调研.md`。
2. Skills 固化：新建 `~/.zcode/skills/project-governance/SKILL.md`（治理方法论 + 融合歆歆操作约定）+ 补充 sliver-vibe-coding（Existing Projects 增"代码现状文档永不从零重建"条款）。
3. D1 裁决执行——Demo 全删：删 `api/routers/demo_routes.py` + `app_factory.py` 挂载与 import + 两测试文件 demo 用例；与 08-27 前端删除合并完成全链路（原因：用户 D1"全删，后续改为产品介绍页"，推翻 08-26 驳回结论）。
4. P0 增量重建 CODE_GRAPH v3.2→v3.3.0：变更带提取（08-01..HEAD 20 commits）+ 实扫校准 + 差异回写（端点 204→199、页面 19→15、API 模块 14→12、测试 1104→1089）+ 消除 §1.2 三处时间戳矛盾（artifact.json 06-30/5983 节点为权威，543277c 的 6771 声明废弃）。CODEMAPS/INDEX 关键指标同步。
5. D1 真源写回：DECISION_LEDGER（SP-3 翻案附4）、FEATURE_MAP（F-02 作废/F 区处置记录/头部刷新）、DELETION_LOG（[2026-08-28] 条目）、VISION（基线数字 1089/15 页/199 端点 + PVB 愿景板骨架）、HANDOFF_REPORT（08-28 接管批注）、AGENTS.md（测试基线 1089 ×3 处 + v1.3 修订行）。
6. P1 `docs/README.md` 唯一入口建立：真源四件套 + Diátaxis 象限 + 生命周期三态 + 新文档准入规则。
7. P2 VISION 愿景板：检索确认设计文档无目标用户/商业目标自有表述 → 骨架入 VISION，缺口待用户口述（D3）。
8. P3 本文件建立（hook 不可用，降级纯约定档）。

**验证**：`pytest -q` → 1030 passed + 1 skipped（107.26s）；`vitest run` → 59 passed/11 files；`create_api_app` 实扫 199 端点；demo 引用 grep 全零。

---

## 2026-08-28（续）— 图谱工具修复 + 感染源清零 + 分组提交

**任务**：用户指示①修复 codebase-memory 图谱工具 工具并重索引；②核查文档治理完整性、清除感染源；③愿景板按口述落笔；④按文件分组 git 提交。

**动作与原因**：
1. 工具修复：GitHub 定位 `DeusData/codebase-memory 图谱工具`（★40.9k，MIT，纯 C 原生可执行）→ pip 安装 v0.10.8 → CLI `index_repository` 重索引成功（**7706 节点 / 32367 边**，schema v2，commit 对齐 c32af54，artifact.json 回写验证；旧 06-30 快照 5983/24923 已覆盖）→ search_graph 查询验证通过。CODE_GRAPH §1.2/头部/更新记录三处回写。
2. 感染源清零（两轮 grep 全库）：README 根（徽章 1104→1089、19 页→15、demo 子路由行、API 模块 14→12、routers 13→20、pytest 注释）；CODEMAPS/BACKEND+FRONTEND+MODULES（demo 行删除、204/17→199/16）；DECISION_LEDGER §四"1104 不可回退"→1089；VISION SP-3 标记已执行移出候选池。
3. 删除 `docs/DOCUMENTATION_GOVERNANCE_REPORT.md`（.venv 污染口径不可修复，有效信息收编 docs/README.md），DELETION_LOG 登记。
4. 愿景板落笔（用户口述）：目标用户=广泛用户；商业目标=完全免费开源 MIT、无订阅不商业化。
5. git 分组提交×4：`feat!` Demo 全链路删除（8 文件）/ `feat(clone)` 解密剥离收尾（5 文件）/ `docs(truth)` 真源同步（13 文件）/ `docs(governance)` 治理基础设施（3 新文件）。

**发现并上交裁决（未提交）**：工作区存在 8 个来历不明的删除——start_all/start_backend/start_frontend.cmd、deploy_ai_girlfriend.bat/.ps1、weclone_adapter/ 三件套。证据：DELETION_LOG 无登记；08-27 日志记载 adapter.py 为"重写（206→230 行）"而非删除；HANDOFF 仍列其为现存；`main.py:186` 无保护 import WeCloneAdapter（删除将破坏克隆管线）、`training_routes.py:74` 引用。疑似误删，建议 `git checkout -- <paths>` 恢复或用户确认意图后另行提交。

**验证**：重索引 status=indexed + artifact.json 实读；两轮感染源 grep 零残留（除有意保留的历史快照标注）；4 commits 落地；工作区仅剩上交裁决的 8 个删除。

**未验证/待办**：① 8 个不明删除待裁决；② 产品介绍页立项未启动；③ 提交未 push（用户未要求）。

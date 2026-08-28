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

**未验证/待办**：① codebase-memory 图谱工具 图谱库重索引（MCP 工具本会话不可用，artifact.json 仍 06-30 快照）；② 愿景板目标用户/用户需求/商业目标待口述；③ 产品介绍页新立项未启动；④ 本会话改动未提交 git（待用户验收后按文件提交）。

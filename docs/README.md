# docs/ 文档体系唯一入口

> 创建：2026-08-28（治理行动 P1，方法论：Diátaxis 四象限 + 生命周期三态）
> **规则**：进入 docs/ 找信息，先看本文；新文档入 docs/ 必须先在本文登记（象限 + 生命周期），否则不入。
> 真值裁决优先级（AGENTS.md §1.3）：**代码实况 > 现行文档 > 历史文档**；本文只声明归属，不复制内容。

---

## 一、真源四件套 + 登记簿（改动须随代码变更同步，过期即修）

| 文档 | 管辖（唯一权威范围） | Diátaxis 象限 | 生命周期 |
|------|--------------------|--------------|---------|
| [VISION.md](VISION.md) | 终态宣言：产品本质/既成事实基线/候选池/Non-Goals/成功判据 | explanation | **truth** |
| [FUNCTION_INVENTORY.md](FUNCTION_INVENTORY.md) | 功能清单（页-功能点编号，商讨协议定位基准；08-28 重制自代码实况×历史意图） | reference | **truth** |
| [DECISION_LEDGER.md](DECISION_LEDGER.md) | 决策生死账（真决策清单 + 挂起池 SP-*） | reference（决策记录） | **truth** |
| 根目录 [CODE_GRAPH.md](../CODE_GRAPH.md) | 代码实况：端点/模块/热路径/指标（当前 v3.3.0） | reference | **truth** |
| [history/INDEX.md](history/INDEX.md) | 漂移登记簿 + 历史文档演进索引 | reference | **truth** |

配套操作文档（how-to）：

| 文档 | 用途 | 生命周期 |
|------|------|---------|
| [HANDOFF_REPORT.md](HANDOFF_REPORT.md) | 接手必读：上一窗口交接了什么、未完成什么（含 08-28 接管批注） | truth（每次接管刷新） |
| [DELETION_LOG.md](DELETION_LOG.md) | 删除类变更逐条留痕（文件/原因/验证），与 git 历史互补 | truth（追加式） |
| 根目录 [LOG.md](../LOG.md) | L2 操作日志：每会话一行块（日期/动作/原因/结果），会话收尾必追加 | **truth（追加式，禁删改旧条目）** |

## 二、分层地图（reference，代码视角分册）

`docs/CODEMAPS/`：[INDEX.md](CODEMAPS/INDEX.md)（入口）· ARCHITECTURE · BACKEND · FRONTEND · DATABASE · MODULES。数字口径以 CODE_GRAPH 为准（INDEX 头部有漂移声明机制）。

## 三、决策与设计（explanation / 决策记录）

| 位置 | 内容 | 生命周期 |
|------|------|---------|
| [adr/](adr/) | 10 篇架构决策记录（ADR-0001~0011，MADR 体例） | truth（增补式） |
| [designs/](designs/) | 集成方案设计（如 MiMo TTS） | derived（落地后由真源收编） |
| [plans/](plans/) | 落地方案（极致拟人化等） | derived |
| [architecture/](architecture/) | 设计原则 + 知识图谱方法说明 | derived |

## 四、历史与归档（archive，只读禁改禁删）

| 位置 | 内容 |
|------|------|
| [history/](history/) | 4+ 份历史设计文档（05-19 立项、05-24 多用户设计等）+ INDEX.md 演进索引 |

## 五、一次性报告（derived，仅供追溯，不再维护）

| 位置 | 内容 | 说明 |
|------|------|------|
| [reports/](reports/) | 调研/评审报告（治理调研/SP-5 诊断/对齐验证/拟人化研究四件套——均有状态卡标注） | derived |
| [P1_BACKLOG.md](P1_BACKLOG.md) | P1 待办（07 月重写版） | 部分 SP-* 与 DECISION_LEDGER 挂起池重叠，以 DECISION_LEDGER 为准 |
| ~~FEATURE_MAP.md~~ | 已删除（08-28 用户裁决：严重错误） | 由 FUNCTION_INVENTORY.md 替代 |
| ~~READING_REPORT_*.md（16 份）~~ | 已删除（08-28 穷举清理：过期一次性快照，结论已收编 FUNCTION_INVENTORY/CODE_GRAPH） | — |
| ~~inventory/file-inventory.md~~ | 已删除（08-28：文件清单快照口径过时，可再生成） | — |
| [inventory/file-inventory.md](inventory/file-inventory.md) | 文件清单快照 | 口径见其头部声明 |
| [superpowers/](superpowers/) | 4 份设计/计划工作产物（glass 视觉 spec=现行视觉源头、framework-v1=SP-1 方案输入、create-role 方案A=SP-2 决策链、frontend-rewrite=已执行历史计划） | derived（被 DECISION_LEDGER 引用） |
| [visual-map/](visual-map/) | 前端页面实拍图册（index.html 本地打开） | 重拍：`frontend/visual-tour.mjs` |

## 六、新文档准入规则

1. 先问归属：内容属于哪个真源的管辖范围？→ **写进真源，不新建**（单一真源）。
2. 确属新类别 → 在本文登记：象限（tutorial/how-to/reference/explanation）+ 生命周期（truth/derived/archive）。
3. 一次性报告一律 derived + 日期前缀，落 `reports/`。
4. 🚫 禁止：与真源同主题的平行文档；删除 archive；未经用户批准重组目录结构。

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
| 根目录 [CODE_GRAPH.md](../CODE_GRAPH.md) | 代码实况：端点/模块/热路径/指标（当前 **v3.8.34**，2026-10-21 八项裁决批 + 2026-10-28 话题状态机增量注记；**235 端点 / 197 唯一路径 / 20 include_router**（`len(app.routes)`=239 含 4 条框架路由，2026-10-28 内省实测）、**4256 通过 + 2 跳过 / 4 失败（骰子闸时区族，根因已由 `165504f` 根治，五文件新鲜复跑 128/128）**（收集 4263 / 266 测试文件）、徽章 4508；图谱库快照仍为 2026-09-02 二次重索引 7997/33187 @ef328a2，待下次重索引） | reference | **truth** |
| [history/INDEX.md](history/INDEX.md) | 漂移登记簿 + 历史文档演进索引 | reference | **truth** |

配套操作文档（how-to）：

| 文档 | 用途 | 生命周期 |
|------|------|---------|
| [HANDOFF_REPORT.md](HANDOFF_REPORT.md) | 接手必读：上一窗口交接了什么、未完成什么（当前状态单一真源） | truth（每次接管刷新） |
| [DELETION_LOG.md](DELETION_LOG.md) | 删除类变更逐条留痕（文件/原因/验证），与 git 历史互补 | truth（追加式） |
| 根目录 [LOG.md](../LOG.md) | L2 操作日志：每会话一行块（日期/动作/原因/结果），会话收尾必追加 | **truth（追加式，禁删改旧条目）** |
| [board/BOARD.md](board/BOARD.md) | **跨窗口看板**（宪法 §8 载体）：当前窗口登记 / 追加区 / 阻塞 / 漂移告警；已完成窗口的登记行已于 09-29 清理批移除，终态见追加区各日条目 | truth（追加式） |

## 二、分层地图（reference，代码视角分册）

`docs/CODEMAPS/`：[INDEX.md](CODEMAPS/INDEX.md)（入口）· ARCHITECTURE · BACKEND · FRONTEND · DATABASE · MODULES。数字口径以 CODE_GRAPH 为准（INDEX 头部有漂移声明机制）。

## 三、决策与设计（explanation / 决策记录）

| 位置 | 内容 | 生命周期 |
|------|------|---------|
| [adr/](adr/) | 12 篇架构决策记录（编号 0001–0007 + 0011–0015；0008–0010 空缺未使用；**编号冲突已解决（2026-09-02 用户裁决更名）**：JWT 认证体系回填为 `ADR-0007-用户认证与权限体系.md`，成就体系保留 `ADR-0014-achievement-system.md`） | truth（增补式） |
| [designs/](designs/) | 集成方案设计（如 MiMo TTS） | derived（落地后由真源收编） |
| [plans/](plans/) | 落地方案（极致拟人化等） | derived |
| [plans/](plans/) **2026-09-20 收敛设计** | [小凌架构收敛与双向映射设计](plans/2026-09-20_小凌架构收敛与双向映射设计.md)——把小凌转写架构补成自洽规范（公式三版本消歧 / 参数域 spec / 出生前后分工契约）并双向映射到本项目既有 owner（逐行 `file:line`）：**已有且接线 6 项 · 部分有 7 项 · 真缺口 8 项**；含 **D1-D8 八项落地设计** + **B1-B6 六条新发现真实缺陷**（死配置/UTC 潜伏缺陷/遗忘硬删除/回忆不强化/四套刻度）+ 对基线① P0-P4 的修订建议 + 6 项待裁决 | derived（提案未获批；零代码改动） |
| [architecture/](architecture/) | 设计原则 + 知识图谱方法说明 | derived |

## 四、历史与归档（archive，只读禁改禁删）

| 位置 | 内容 |
|------|------|
| [history/](history/) | 历史设计文档（05-19 立项、05-24 多用户设计等）+ **已执行完毕的历史交接书**（HANDOFF_REPORT-2026-08-28 / -2026-09-14、HANDOFF_2026-09-15 / -09-20 包ABC / -09-21 AX×2 / -09-22 六域、NEW_WINDOW_PROMPT-09-22；09-29 归档批移入）+ INDEX.md 演进索引 |

## 五、一次性报告（derived，仅供追溯，不再维护）

| 位置 | 内容 | 说明 |
|------|------|------|
| [reports/](reports/) | 调研/评审报告（治理调研/SP-5 诊断/对齐验证/拟人化研究四件套——均有状态卡标注） | derived |
| [reports/](reports/) **2026-09-19 三件** | [经历因果升级机制研究](reports/2026-09-19_经历因果升级机制研究.md)（小凌架构→本项目升级机制，P0-P4 路线图）/ [情感真源收敛审查](reports/2026-09-19_情感真源收敛审查.md)（P0 前置审计：情感状态族无持久化闭环 + 幽灵 vital_signs，R1-R6/S1-S6）/ [WrenWen 伴侣架构精读](reports/2026-09-19_WrenWen伴侣架构精读.md)（W1-W28 机制条目+踩坑映射） | derived（零代码改动纯研究；方案均为提案未获批） |
| [reports/](reports/) **2026-09-20 交接** | [经历因果研究交接与深研任务包](reports/2026-09-20_经历因果研究交接与深研任务包.md)——交接给后续窗口的深化研究任务包：已耕区域勿重复清单 + W-A~W-G 七个可并行工作包（小凌一手素材深掘/公式学术对照/GitHub 补深/P0-P4 方案细化/评测体系/竞品/作者动态追踪）+ 并行安全矩阵与产出规范 | derived（任务包；新窗口认领走 BOARD 追加区） |
| [reports/](reports/) **2026-09-20 W-A 产出** | [小凌一手素材深掘](reports/2026-09-20_小凌一手素材深掘.md)——W-A 工作包产出（21 作品全量一手素材深掘）：**找到「出生前参数表」实屏**（initial/min/max/plasticity + 「出生前参数设计说明 V0.3」出生前/出生后分工契约）/ **`forget.py` 代码级曝光**（λ_d=0.36·λ_e=0.034·λ_f=0.29 + sigmoid 检索权重，且自陈为**反解拟合非数据标定**）/ **激活值公式 3 种表述**（手稿 5 项 vs 口播 6/7 项）/ 完整发帖时间线与承诺-交付对照（1 条承诺未兑现）/ 素材实况勘误 4 条 / 术语表与 10 条对标增补 / 6 条待裁决建议 | derived（零代码改动纯研究；建议均提案未获批） |
| [reports/](reports/) **2026-10-27/28 五件** | [话题状态机设计与研判](reports/2026-10-27-话题状态机设计与研判.md)（已裁决并实施：两阶段 B→C——确定性话题状态 + 每 4 轮稀疏 LLM；§4 设计稿参数已被 §5 Q1–Q6 最终裁决覆盖）/ [话题状态机实施计划](reports/2026-10-27-话题状态机实施计划.md)（Q1–Q6 裁决逐字固化 + §18 实施结果，2026-10-28 收口）/ [DSH 生态补充调研](reports/2026-10-27-DSH生态补充调研.md)（11 项目源码级补充：人格蒸馏/工作流 Skills 4 项、Agent 记忆/情绪插件 3 项、主动/子代理/语音/QQ 桥 4 项；adopt 分 now/later/no）/ [BreezeTTS2 本地部署设计](reports/2026-10-28-BreezeTTS2本地部署设计.md)（技术预研·未实施·纯文档：本地部署前提下的许可/硬件账/接口契约/灰度回滚/成本对比/迁移步骤，遵守「暂不引入」裁决）/ [本仓语音链路与拟人机制测绘](reports/2026-10-27-本仓语音链路与拟人机制测绘.md)（`voice/` 合成链 + 全仓拟人化机制只读测绘，代码坐标高置信，HEAD `9a027a1` 快照） | derived（话题状态机两件已随 `3fe2771` 落地三端；BreezeTTS2 为预研非引入；DSH/测绘为调研输入） |
| [reports/](reports/) **2026-10-27 同批六件（2026-10-28 补记，落盘时漏登记）** | [DSH 专项调研](reports/2026-10-27-DSH专项调研.md)（DSH=DeepSeek 官方开源 Agent Harness，「仿真人化插件」前提不成立；可借 `dsh-im-humanize` 机制不借运行时）/ [仿真人化插件生态调研](reports/2026-10-27-仿真人化插件生态调研.md)（SillyTavern/AstrBot/MaiBot/LangBot/NoneBot/DSH 横向生态；有效「像人」多为 R/T/L/E/M/A/I 七层工程机制而非 prompt）/ [仿真人化引入方案](reports/2026-10-27-仿真人化引入方案.md)（七层机制引入设计与取舍，纯设计零代码）/ [BreezeTTS2 调研](reports/2026-10-27-BreezeTTS2调研.md)（本体/非商用权重许可/自托管 12–24GB GPU 事实底稿）/ [BreezeTTS2 引入设计](reports/2026-10-27-BreezeTTS2引入设计.md)（托管 API 方案，后被本地部署设计以本地前提重写）/ [系统性升级调研汇总](reports/2026-10-27-系统性升级调研汇总.md)（四路调研 + 两份引入设计的索引） | derived（均为调研/提案，未获批落地） |
| [P1_BACKLOG.md](P1_BACKLOG.md) | P1 待办（只载真实未决项） | 部分 SP-* 与 DECISION_LEDGER 挂起池重叠，以 DECISION_LEDGER 为准 |
| READING_REPORT_*.md（14 份，docs 根） | **模块深度档案**：08-26 全库 200+ 文件穷举阅读的结构化记录（端点全景/机制细节/设计模式），含 CODE_GRAPH 未收录的深度内容 | derived·长期有效（08-28 全文复读改判保留；voice/clone 两份含已删模块，作历史档案）。⚠️ 09-15 注记：wechat_clone/voice/memory_context_multimodal 三份的守卫/silk/图片接线细节已被 W3 收编更新，现行口径以 CODE_GRAPH v3.6.0 + FUNCTION_INVENTORY N-IMG-1 为准（三份文件头部已加注记）。⚠️ 09-17 注记：本轮代码变更波及 8 份（my_character/orchestrator/proactive_plugins/api/shisi/llm_provider/wechat_clone/tests_root），各头部已加 09-17 时效注记并指向 CODE_GRAPH **v3.7.0**；未波及 6 份（character_card/memory_context_multimodal/persona_extractor/security_observability/tools_utils_scripts_cache/voice） |
| [inventory/file-inventory.md](inventory/file-inventory.md) | 05-31 文件清单历史快照（自带 08-26 状态卡：已失效判断已标注） | archive |
| [superpowers/](superpowers/) | 4 份设计/计划工作产物（glass 视觉 spec=现行视觉源头、framework-v1=SP-1 方案输入、create-role 方案A=SP-2 决策链、frontend-rewrite=已执行历史计划） | derived（被 DECISION_LEDGER 引用） |
| ~~visual-map/~~ | 前端页面实拍图册（目录已删；可用 `frontend/visual-tour.mjs` 重建） | — |

## 六、新文档准入规则

1. 先问归属：内容属于哪个真源的管辖范围？→ **写进真源，不新建**（单一真源）。
2. 确属新类别 → 在本文登记：象限（tutorial/how-to/reference/explanation）+ 生命周期（truth/derived/archive）。
3. 一次性报告一律 derived + 日期前缀，落 `reports/`。
4. 🚫 禁止：与真源同主题的平行文档；删除 archive；未经用户批准重组目录结构。

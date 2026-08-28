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

## 2026-08-28（八）— 配色迁移 + FEATURE_MAP 清除 + 功能清单重制（SP-10 方法纠正）

用户三裁决：①配色换暖黄/海盐蓝/薄荷青浅色系（弃马卡龙粉/蓝/绿）；②SP-10 禁止实拍分析法，直接读代码出功能清单；③FEATURE_MAP 严重错误彻底清除，历史设计文档（用户原始想法）为意图基准。

**执行**：
1. 配色：全库类名迁移 macaron-pink→macaron-yellow(暖黄 #FDE68A/#D97706)、macaron-green→macaron-mint(薄荷青 #99F6E4/#0D9488)、macaron-blue→海盐蓝(#BAE6FD/#0284C7)；@theme 三族+primary(暖黄系)+accent(海盐蓝系)+bg 渐变+success(同薄荷青) 重定义；ParticleCanvas 粒子色同步。验证：tsc 0 错、vitest 59 绿。
2. 删 docs/FEATURE_MAP.md（git rm，DELETION_LOG 待补条目与本条合并登记）；真源链同步：AGENTS §1.3 商讨协议坐标系改指 FUNCTION_INVENTORY、VISION 分工声明、docs/README 索引、DECISION_LEDGER SP-2/SP-10 引用修正。
3. SP-10 重制：新建 **docs/FUNCTION_INVENTORY.md**（truth，页-功能点两级编号）——App.tsx×15 页组件×api 消费逐页读出功能点（LOGIN-1~LOGS-1 共 40+ 条，全部带文件证据），§0 全局能力对照历史六特性，产出 **GAP-1~5 差距清单**（企微通道未实现/记忆三层前端呈现薄/消息统计占位/两 tab 开发中/状态中心缺丰富化）。

**验证**：tsc --noEmit 0；vitest 59 passed。配色为纯 token 层变更，pytest 不受影响（未重跑，后端零改动）。

**未验证/待办**：① GAP-1（企业微信）需用户裁决是否立项；② SP-5 修复清单（诊断报告 P0-P2）仍待批；③ 本批未部署云端（纯前端 token+docs，下次部署随批）。

---

## 2026-08-28（七）— 语音域 MiMo-only 收敛（裁决 A）

用户选定 A 口径（全域 MiMo-only，保留 fallback_local）。删除 4 provider + voice_training + shisi 训练 API(4端点) + EmotionVoiceMapper；tts_manager 单引擎重写；**fallback_local 重写为 Windows SAPI 本地合成**（原四级链已删，避免 ImportError 炸降级路径；pywin32 入可选组 win-tts-fallback）；voice_routes/shisi/config/pyproject/测试全链对齐。验证：pytest 1015+1、vitest 59、194 端点实扫、代码层旧引擎零命中。前端 SettingsVoice（克隆/合成唯一入口）本就 MiMo-only，零改动。

---

## 2026-08-28（六）— 下一阶段队列登记

用户确认「前置处理已完成」，点名后续四项：塑料感消除（=SP-5）、页面设计对齐核查、产品介绍页、全面测试。已按商讨协议坐标登记为 **SP-10（设计对齐核查：visual-tour 重拍+逐页比对）/ SP-11（产品介绍页，接替 F-02 门面职责）/ SP-12（全面测试：1089 基线+E2E 冒烟+对齐复跑）**，连同既有 SP-5 构成下一阶段队列（DECISION_LEDGER「下一阶段队列」表）。启动均待用户逐项点名。仅文档变更，本地+GitHub 同步，云端无需部署。

---

## 2026-08-28（五）— 三端同步：GitHub 推送 + 云服务器部署（附两个部署链阻断修复）

**任务**：用户问"云服务/GitHub/本地三端同步了吗"。核实：本地✅、GitHub 落后 17 commit、云服务器停在 67f000e（约 07-28）落后一个月。随后执行同步。

**GitHub**：`git push origin main`（60263e0→388bad8，17 commit）。此后每次提交即推。

**云服务器（deploy@→root@139.199.199.174，/opt/ai-girlfriend）**：git archive 直推 + remote_deploy.sh（pip -e / npm ci / build / systemctl restart）。过程中修掉两个部署链阻断 bug：

1. **CRLF 阻断**：deploy/ 全部 8 个脚本/配置在仓库 blob/归档输出中带 CRLF，Linux 端报 `$'\r': command not found`。根因 = git archive 按 core.autocrlf 导出 CRLF。修复：新增 `.gitattributes`（*.sh/*.conf/*.service 强制 eol=lf，属性优先级高于 autocrlf），物理转 LF 提交（6f4a1ce）。
2. **npm lockfile 漂移**：本地 bun 管包导致 package-lock.json 与 package.json 失同步，服务器 npm ci 报 EUSAGE。修复：lockfile-only 重生成（83e3270）。
3. **归档推送不删文件**：tar 只增不删，服务器残留幽灵层三页/DemoPage/users.ts 等 29 个已删文件导致 tsc 失败。修复：`git diff --diff-filter=D 67f000e..HEAD` 生成删除清单 + 服务器 xargs rm 对齐。

**云端终验**：service active；/api/health ok（v3.1.0 production）；`/api/demo/*` 404（已删）✓；`/api/training/extract` 404（已删）✓；前端 HTTP 200；uvicorn 4 worker 绑 127.0.0.1:8000（OBS-1 保持）。HTTPS 仍为 OBS-2 待办（域名），与本次无关。

**沉淀**：三端同步规程——本地 commit → push origin → `git archive HEAD | ssh root@139.199.199.174 "tar -x -C /opt/ai-girlfriend"` → 若有删除：`git diff --name-only --diff-filter=D <服务器旧commit>..HEAD | ssh ... xargs rm` → `bash deploy/remote_deploy.sh` → 健康核验。

**未验证/待办**：① 服务器 git 索引仍指 67f000e（archive 同步不更新 git 元数据，下次同步以本次 LOG 记录的 83e3270 为基线）；② HTTPS/域名 OBS-2 未动。

---

## 2026-08-28（四）— main.py 屎山治理 + 克隆智能体代跑 + 前后端对齐验证

**任务**：用户指示①克隆教程改为"智能体代跑"模式（导出工具=用户 GitHub 的 wechat-decrypt，本地最新版 C:\Users\FOUR\wechat-decrypt）；②main.py 屎山检查优化；③前后端对齐验证；④同步 CODE_GRAPH。

**动作与原因**：
1. 导出程序定位：`FOURTEEN1416/wechat-decrypt`（本地最新版含 AGENTS.md 智能体手册 + `--users` 单联系人导出）；平台 upload 端点原生兼容其 `is_self/text/message_type` 导出格式。
2. 智能体任务书：权威版 `docs/guides/微信克隆-智能体任务书.md` + 前端内嵌 `constants/cloneAgentGuide.ts`（一键复制）。任务书做薄桥接——解密流程指向工具仓库自己的 AGENTS.md 冷启动决策树，自身只补 unique-you 特有段（格式校验/50MB 分片/上传）。CreateRole 克隆 tab 改三步流（OpenCode 推荐→复制任务书→上传 JSON），移除旧三工具卡片。用户流程：装 OpenCode（免费模型）→ 复制任务书喂智能体 → 收 JSON 上传。
3. main.py 屎山治理：`run_console_chat`（复杂度 24 单体，CODE_GRAPH §7 热点）迁出为 `orchestrator/console_chat.py`（命令处理函数+分派表）；main.py 494→409 行；清 docstring 重复/函数内 import 遮蔽/banner 死替换/未用形参/contextlib。外部契约 `from main import OptimizedOrchestrator/UserManager` 不变。
4. 前后端对齐验证：create_api_app 实扫 198 端点 × 前端 api/*.ts 静态解析——**前端调用缺后端 0**（无断链）、消费 118/198（60%）、80 零消费均为已知开放面（shisi 域/psych 幽灵能力/运维）。报告 `docs/reports/2026-08-28_前后端对齐验证.md`。
5. CODE_GRAPH v3.3.0→v3.4.0（§1.1/§4.1/§4.9/§7/§13）；FEATURE_MAP F-05 措辞同步。

**验证**：tsc --noEmit 0 错误；vitest 59 passed；pytest 1030 passed + 1 skipped（全量）。提交 3 个（73fffb6 / c69db0b / 258c440）。

**未验证/待办**：① wechat-decrypt 仓库为私有——对外发布前用户需自行公开或提供分发渠道（任务书里的 clone URL 才对终端用户可用）；② 产品介绍页立项未启动；③ 提交未 push。

---

## 2026-08-28（三）— 微信克隆收敛 + 用户删除确认提交

**任务**：用户确认 8 项删除为其本人操作并指示提交；裁决微信克隆方向——解密必须在用户登录微信的本地环境进行，服务器只收 JSON。

**动作与原因**：
1. 克隆链路调查：CreateRole 克隆 tab 已是「三工具教程→本地运行说明→JSON 上传」完整形态（前端零改动）；死代码为服务端提取路径。
2. 摘除服务端提取管线：`main.py` run_clone_pipeline + --clone 三参数（574→494 行）；`training_routes.py` /api/training/extract（唯一 weclone API 依赖）；前端 `trainingExtract()` 死封装（零消费）。
3. 用户删除补登记 + 提交：weclone_adapter/ 三件、start_*.cmd ×3、deploy_ai_girlfriend.bat/.ps1；同步 AGENTS §0 启动方式（python main.py / uvicorn / npm run dev；部署走 deploy/）、L9 教训标记失效。
4. 真源同步：CODE_GRAPH（198 端点/main.py 494 行/§7 run_clone_pipeline 已删/§13 行）、FEATURE_MAP B-07 重写、DECISION_LEDGER 08-28 三行、VISION 198、HANDOFF 批注④、CODEMAPS/INDEX。
5. 感染源治理口径（用户问询后确立）：**真源文档=物理修正（错误数字/失效陈述直接改删）；派生报告=备注+归档（历史快照仅供追溯）；既污染且不可修复=物理删除（如 DOCUMENTATION_GOVERNANCE_REPORT）**——与 docs/README.md 的 truth/derived/archive 生命周期一一对应。

**修复事故**：main.py 批量替换曾留下孤儿 `return`（L361 IndentationError），py_compile 定位后修复，全量测试复绿。

**验证**：`create_api_app` 实扫 **198 端点**；pytest **1030 passed + 1 skipped**；vitest **59 passed**；`grep weclone`（py/ts/tsx）全零。

**未验证/待办**：① 产品介绍页立项未启动；② 提交未 push。

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

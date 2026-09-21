# Code Deletion Log

## [2026-09-21] llm_provider/prompt_template_manager.py 零调用死文件删除（P2 批6b 项4）

### 删除对象与证据
- `llm_provider/prompt_template_manager.py`（`PromptTemplateMgr`）
  - **零调用**：全仓 grep `prompt_template_manager` / `PromptTemplateMgr` / `PromptTemplateManager` —— 除自身与 `大创赛报名以及后期发展/` 软著归档复制件（不入库）外**无任何 import/实例化/测试引用**；`llm_provider/__init__.py` 不导出
  - **文档虚报**：`CODE_GRAPH.md` 标其 "54 fan-in / OptimizedOrchestrator to prompt_template_manager 14"——代码实况为 0，属陈旧/虚构度量，终局文档对齐批统一清除；`docs/CODEMAPS/MODULES.md` 行本次已删

### 验证
- 删除后 import 面零报错（`pytest` 相邻套件 + `ruff check llm_provider/` 绿）

### Impact
- 删除 1 文件；零行为变更

**Reversible**: git revert 即恢复。

## [2026-09-21] shisi/ase/trigger_engine 死桩删除（P2 批6b 项3）

### 删除对象与证据
- `shisi/ase/trigger_engine.py`（252 行，五类触发器 + TriggerEngine）与 `tests/test_shisi_ase.py`（148 行，全部为该死桩的 dataclass 构造测试）
  - **零调用**：全仓 grep `check_time_triggers / check_stage_triggers / check_affinity_triggers / check_event_triggers / check_idle_triggers / register_default_triggers / get_all_triggers` **无任何调用者**；`get_all_triggers` 本身就是 `return []` 死桩
  - 唯一"使用"是 `WeChatProactiveMessenger.__init__` 构造一个实例并挂 `trigger_engine` 属性——该属性全仓无人读取 → 一并移除 import/字段/属性
  - 现役主动消息链路（`proactive/ase_engine.py` + `scheduler`）从不经过此引擎

### 同批修正
- `shisi/ase/__init__.py` docstring 去除"多类型触发器"能力宣称（虚假声明）
- `docs/CODEMAPS/MODULES.md` 删除 `knowledge/legacy/` 行（上条已删模块）与 `ase/` 行中的 `trigger_engine.py`

### 验证
- `tests/test_wechat.py` / `test_integration.py`（WeChatProactiveMessenger 现役接口）全绿

### Impact
- 删除 2 文件 + 死属性；现役主动消息行为零变更

**Reversible**: git revert 即恢复。

## [2026-09-21] shisi/knowledge/legacy（RAGEngineV2 整包）+ test_rag_engine 删除（P2 批6b）

### 删除对象与证据
- `shisi/knowledge/legacy/`（`__init__.py` + `rag_engine.py`，313 行）与 `tests/test_rag_engine.py`（613 行）
  - **零生产消费**：全仓 grep `RAGEngineV2(`、`use_legacy_rag`、`knowledge.legacy` —— 除自身与其测试文件外**无任何 import / 实例化**；`orchestrator._init_mixin._init_rag` 无条件构造 `ShisiKnowledgeAdapter`，注释所述"use_legacy_rag=True 时内部委托"从未存在（垃圾注释，已同步更正）
  - **配置开关是假的**：`config/system.yaml` 的 `rag.use_shisi_rag` 代码从不读取做分支（仅 config 持久化测试透传），yaml 注释已改为如实描述
  - **模块本身带缺陷**：其 `BM25Index` 中文分词用 `\w+` 整段匹配，中文查询 token 与 2-gram 语料 token 无交集 → 中文召回≈0（审查报告 6b 项2）；现役检索真源是 `shisi/knowledge/retriever.py`（BM25Retriever/KeywordRetriever），修复无消费方，删除即根治
  - `legacy/__init__.py` 自称"仍在活跃使用"为**虚假声明**（与 v1.10 删除 shisi/api/v2 时 `docs` 旧裁决被推翻同型）

### 同批修正（非删除）
- `orchestrator/_init_mixin.py` `_init_rag` 注释更正（不再宣称存在 legacy 委托）
- `config/system.yaml` rag 双轨注释更正为"历史开关、代码不读取"

### 验证
- 删除后 `ruff check`（改动文件）+ `tests/test_shisi_knowledge.py`/`test_knowledge_order.py`/`test_config_manager.py` 全绿
- 端点数不变（legacy 包从未挂载/接入路由）

### Impact
- 删除 3 文件；**零行为变更**（无调用者）；测试收集数 −（test_rag_engine 用例数），终局基线口径随之刷新

**Reversible**: git revert 即恢复；无数据迁移。

## [2026-09-21] importance_scorer 无隔离旧副本三类删除（P1 批4a · 审查报告 item 17）

### 删除对象与证据
- `shisi/memory/legacy/importance_scorer.py` 内的 `ForgettingManager` / `ConflictDetector` / `CrossSessionReasoner` 三个旧副本类（共 ~70 行）
  - **零消费**：全仓 grep 确认无任何模块从 `importance_scorer` 导入这三类；现役实现分别在同包 `forgetting_manager.py` / `conflict_detector.py` / `cross_session_reasoner.py`（`memory_pipeline` 实际使用的即后者）
  - **危害**：与 `legacy/__init__.py` 旧导出叠成**双实现地雷**——旧副本的 `get_pending_events` 全表返回、`check_conflict` 不带 `user_key`，一旦 `from …legacy import ConflictDetector` 拿到旧版本即静默回到跨用户串扰语义（v1.28 隔离根治的反向通道）
- 联动修正：`legacy/__init__.py` 三类导入改指向现役独立模块（唯一真源），并加注释禁止回退

### 验证
- `tests/test_p1_batch4a_memory.py::test_legacy_exports_point_to_live_modules`（导出身份断言）+ `test_importance_scorer_old_copies_removed`（旧副本不得复活）
- 分块回归：test_memory / 隔离 / proactive / agent-plane 套件全绿（12+134+103 passed）

## [2026-09-18] shisi v2 死模块删除 + 假端点改 501（用户裁决「三项全做」）

### 删除对象与证据
- `shisi/api/v2/`（6 文件：`__init__.py` / `character_routes.py` / `health_routes.py` / `migration_routes.py` / `persona_routes.py` / `schemas.py`）
  - **零挂载**：`v2_router` 在全仓无任何 `include_router` —— `app_factory.create_api_app()` 仅调 `setup_shisi` → `registry._mount_routes`，该函数只挂 8 组 v1 路由
  - **零消费**：全仓 grep `api.v2` / `api/v2` / `v2_router` / `from .v2`，除自身外只命中文档引用（AGENTS Owner Map、CODE_GRAPH 分层表、CODEMAPS ×2）、本日志早先的保留裁决、LOG 待裁决条目 —— **无任何代码 import**
  - **推翻早先裁决**：早先"保留待将来集成"的结论见下方 Files NOT Removed 段的 ⚠️ 更新标注
- 同步文档 4 处引用移除：`AGENTS.md` Owner Map / `CODE_GRAPH.md` 分层表 / `docs/CODEMAPS/DATABASE.md`（整行）/ `docs/CODEMAPS/MODULES.md`（整行）

### 修正（非删除）
- `shisi/api/memory_routes.py::delete_memory` —— 由「回 `已移入回收站（30天保留期）` 但**不做任何事**」改为 `confirm=true` 时 **501 Not Implemented**；**保留未确认时的 400 前置校验**（既有契约，见 `tests/test_integration.py::test_delete_requires_confirm`，故无需改测试）。**谎报成功比显式失败更危险**（调用方会误以为数据已妥善处置）。依据：`memory_recycle_bin` 表已存在于 `shisi/migrations.py`，但删除链路从未落地
- `shisi/api/memory_routes.py::unfavorite_memory` —— 签名 `(fav_id, character_id="", memory_id="")` 中 `fav_id` **被完全忽略**（实调 `unfavorite(character_id, memory_id)`，而后者两参数均有空默认值 → 可被无参省略调用，行为未定义）。改为 `fav_id` 唯一判据，新增 `FavoriteManager.unfavorite_by_id(fav_id)`

### 验证
- `ruff check .` → All checks passed（0.16.8，与 CI 同版本）
- 行为实证：临时库写入 3 条收藏 → `unfavorite_by_id` 首删 `True` / 重删 `False` / **邻居角色未被误删**；HTTP `DELETE /favorite/999999` → 200 `success:false`；`DELETE /memory/x?confirm=true` → **501**
- `pytest -q` → 1060 passed / 4 skipped（删除 6 文件后零回归）

### Impact
- 删除 6 文件（v2 死模块）；**端点总数不变**（v2 从未挂载）
- 唯一行为变更：1 个端点由假成功改 501。前端 `frontend/src/` 对 `/api/shisi` **零引用**，无消费方受影响

---

## [2026-09-17] 死代码清洗（用户裁决"死代码可以直接清洗掉"）

### 删除对象与证据
- `frontend/src/components/shared/Badge.tsx`（27 行）：零消费——storyline 三件用 `common/Badge`，admin 用 `UserBadges`；`shared/index.ts` 导出无引用方。删后 tsc 0 错/vitest 87 绿
- `frontend/src/store/chatStore.ts` + `frontend/src/api/chat.ts`：僵尸聊天域——`setConnected` 零调用（侧栏"已连接"圆点恒 false 撒谎）；Sidebar/MobileDrawer 改接 `useWechatStatus` 真源（React Query 30s 轮询+SSE）；`emotionState/emotionTrend` 两个活函数迁入 `client.ts` 存续；孤儿类型 `ChatMessage/ChatResponse` 一并移除（EmotionState 原定义在 types/api 首行，未动）
- `shisi/wechat/command_handler.py` + `command_parser.py`：微信指令系统（"切换角色：xxx"等指令集）——生产消息链路**从未接线**（仅 registry 实例化，wechat_connector/UserManager 零调用），用户裁决角色切换走 web 控制台（activate→wechat_bindings 已于同日接线）
- 测试联动：`test_wechat.py` 25→7（指令 Parser/Handler 用例随模块移除，sticker/proactive_messenger 测试保留）；`test_tool_health.py` TestCommandHandlerSendSticker 2 用例；`test_integration.py` wechat_handler 断言 3 行；`shisi/api/registry.py` 装配段 + `api/app_factory.py` status 元组同步

### 登记未删（悬空但有保留理由，待后续裁决）
- `shisi/wechat/proactive_messenger.py`：registry 装配但生产主动消息走 `proactive/scheduler.py`——留观
- `shisi/wechat/sticker_adapter.py`：前端表情包为未立项功能（STICKERS-1），立项时可能复用

---

## [2026-09-15] 事故残骸与过期恢复产物清扫（用户指令"临时产物垃圾清除干净"）

- `.git.broken-0006/`：09-14 主仓 .git 损毁事故的残骸备份（实测 0 字节空壳）——事故已复盘入档、主仓已重建并三端同步（本地=GitHub=服务器），残骸无恢复价值
- `data.空库备份-0015/`（933KB）：恢复期生成的空库备份，gitignored，被 seed 流程的新备份体系取代
- 以上均不入 git 历史（未跟踪/被忽略状态删除），DELETION_LOG 本条即唯一留痕
- 附带清扫（仓库外工作区）：论文区模板解包临时目录 4.5MB、PPT 工作区 tmp/ 25MB 及过期审计产物、旧设计变体 pptx/pdf（已被高级视觉重构版取代）

# Code Deletion Log

## [2026-09-15] verify 双仓回收（用户裁决：「最最最最重要的：对这些仓库进行回收和删除」）

### 删除对象
- `D:/Desktop/ai-girlfriend-verify`（W4 验证窗幸存副本，6.4MB/584 文件）
- `D:/Desktop/ai-girlfriend-verify-backup-2327`（23:27 保全备份，5.5MB/592 文件）

### 删除前全读清扫（防零损失）
- 全量盘点：与主仓 diff 后「它们独有」的文件仅 13 个——W3 窗调研文档 2 份（已救回至 `docs/research/`）、孤儿 `.git` 指针 ×2（指向主仓重建前 worktree 注册，本就失效）、`_diag*.txt` ×10（事故恢复期临时探针输出，逐个过目确认为一次性诊断，事故叙事已由 `verification/INCIDENT-*.md` 承载）
- 价值物已先行并入主仓：W4 232 行测试（d74a8e6 随收编入 main）、W4 验证报告与事故报告（4e17d81）、BOARD 幸存条目（恢复时已并）
- 两目录 `.git` 均为 69B 文件非仓库本体 → 无需 git rm，直接目录删除

### 验证
- 删除后 `ls D:/Desktop` 仅剩 `ai-girlfriend`（主仓）与 `ai-girlfriend-code`（W3 worktree，分支已收编，保留待卸窗）

## [2026-08-28] FEATURE_MAP 清除（用户裁决：严重错误）+ 配色体系迁移

### 决策依据
- 用户裁决「FEATURE_MAP F-01~F-15 有严重错误请彻底清除」；功能对齐意图基准改为 docs/history/ 历史设计文档（用户原始想法记录）
- 配色裁决：弃马卡龙粉/蓝/绿，改暖黄/海盐蓝/薄荷青浅色系

### ⚠️ 2026-08-28（晚）部分撤销：READING_REPORT 群恢复
- 用户质询"没有完全读取就删除？？"成立——删除时仅凭文件名/年龄判定，未全文阅读
- 已从 git 历史恢复全部 14 份 READING_REPORT_* + inventory/file-inventory.md，**逐份全文复读**（约 2000 行）
- 复读判定：全部为高质量深度架构档案（非过期快照，内容未被收编）→ **全部保留**，docs/README 归位为"模块深度档案 derived·长期有效"
- 唯一维持删除：docs/FEATURE_MAP.md（用户裁决内容严重错误，非阅读判定）
- 教训入宪：project-governance skill 新增"删除前必须全文读完"铁律

### Files Created
- `docs/FUNCTION_INVENTORY.md`（新 truth：页-功能点编号功能清单，代码实况逐页读出 × 历史意图对照，含 GAP-1~5 差距清单）

### Files Modified（配色迁移，frontend/src 全库）
- `index.css`：@theme 三族重定义（yellow #FDE68A 系 / blue 海盐 #BAE6FD 系 / mint #99F6E4 系）+ primary(暖黄)/accent(海盐蓝) + bg 渐变(#FFFBEB/#F0F9FF/#F0FDFA) + success 同薄荷青
- 5 个文件类名迁移：macaron-pink→macaron-yellow、macaron-green→macaron-mint（tsx/css 全库，残留 0）
- `ParticleCanvas.tsx`：粒子双色 hex 同步
- 真源链：AGENTS §1.3 坐标系、VISION 分工声明、docs/README 索引、DECISION_LEDGER SP-2/SP-10

### Verification
- tsc --noEmit 0 错误；vitest 59 passed；旧色值 grep 零命中；macaron-pink/green 残留 0

---

# Code Deletion Log

## [2026-08-28] 语音域 MiMo-only 收敛（用户裁决 A：全语音域只留 MiMo）

### 决策依据
- 用户裁决：语音克隆只保留 MiMo TTS → 选定 A 口径（全域收敛，保留 MiMo fallback_local 本地兜底）
- 前端本就 MiMo-only（07-27 reinit 收敛），本次清后端四引擎与其依赖面

### Files Deleted
- `voice/edge_tts_provider.py` / `sovits_provider.py` / `cosyvoice_provider.py` / `bert_vits2_provider.py`（4 个 TTS provider）
- `voice/voice_training.py`（GPT-SoVITS LoRA 音色训练器；克隆改走 MiMo voiceclone API）
- `shisi/api/training_routes.py`（/api/shisi/voice/training/* 4 端点，零前端消费）
- `tests/test_voice_training.py` / `tests/test_emotion_tts.py`（对应测试）

### Files Modified
- `voice/tts_manager.py`：单引擎重写（initialize 只装 MiMo；删多引擎降级循环与 edge 专属情感注入；删 emotion_mapper 形参）
- `voice/mimo_tts_provider.py`：`fallback_local` 重写——原四级本地引擎链（已删）改为 Windows SAPI 本地合成（零额外服务，非 Windows 返回 None）
- `voice/__init__.py`：导出面收敛为 TTSManager/TTSProviderBase/MiMoTTSProvider
- `orchestrator/_init_mixin.py`：去 EmotionVoiceMapper 注入
- `api/routers/voice_routes.py`：默认引擎 mimo-tts；`GET /voice/speakers` 返回 MiMo 预设音色（原三引擎发音人表删除，端点零 UI 消费）；test 端点去 switch_engine
- `shisi/voice/emotion_tts.py`：删 EmotionVoiceMapper 整类（Edge 专用 rate/volume 体系）；`shisi/voice/__init__.py` 导出同步
- `shisi/api/registry.py`：删 training_manager 装配与路由挂载
- `shisi/wechat/command_handler.py`：删 switch_engine("gpt-sovits") 联动
- `config/system.yaml`：删 edge-tts/cosyvoice/gpt-sovits/bert-vits2 四配置块；engine 注释收敛
- `pyproject.toml`：删 edge-tts 依赖；新增可选组 `win-tts-fallback`（pywin32，SAPI 兜底）
- `tests/test_voice_manager.py` / `test_character_voice.py` / `test_modules.py`：引擎名语义对齐 mimo-tts

### Impact
- API 端点：198 → **194**（shisi 语音训练 -4）
- Python 测试：1030 → **1015** + 1 skipped（-15）；vitest 59 不变
- 语音能力边界：合成/克隆/设计 = MiMo Cloud 全托管；云故障 → Windows SAPI 离线兜底；放弃跨厂商容灾（用户知情裁决）
- 音色绑定数据兼容：`engine` 字段历史值（edge-tts 等）仍在存储中，读取时引擎维度已不生效（switch_engine 移除于 test 路径）

### Verification
- `python -m pytest -q` → 1015 passed + 1 skipped（2026-08-28 实跑）
- `npx vitest run` → 59 passed；tsc 0 错误
- create_api_app 实扫 194 端点；`grep -rn "edge-tts|sovits|cosyvoice|bert-vits2|voice_training"` 代码层零命中（仅历史标注注释）

---

## [2026-08-28] 微信克隆服务端管线移除 + 启动部署脚本删除（用户裁决：本地提取 + JSON 上传）

### 决策依据
- 用户 08-28 裁决：微信克隆解密必须在**用户登录微信的本地环境**进行，不可能在云服务器/网站上进行；服务端只接收本地工具导出的聊天记录 JSON
- CreateRole 克隆 tab 已具备「三工具教程 → 本地运行说明 → JSON 上传分析」完整形态，为唯一克隆入口；服务端提取路径全部为死代码
- weclone_adapter/ 三件、start_*.cmd ×3、deploy_ai_girlfriend.bat/.ps1 为**用户本人删除**，本条目补登记；其余为本会话按裁决执行

### Files Deleted（用户删除，会话确认）
- `weclone_adapter/__init__.py` / `adapter.py` / `style_profiler.py`（WeClone 适配层：服务端 extract/clone/style 分析）
- `start_all.cmd` / `start_backend.cmd` / `start_frontend.cmd`（Windows 启动脚本）
- `deploy_ai_girlfriend.bat` / `deploy_ai_girlfriend.ps1`（部署打包脚本；部署统一走 `deploy/`）

### Files Deleted（本会话按裁决执行）
- `main.py`：`run_clone_pipeline()`（-35 行）+ `--clone/--clone-source/--clone-name` 三参数 + 分发分支 + 帮助文本两处（main.py 574→494 行）
- `api/routers/training_routes.py`：`POST /api/training/extract` 端点（-30 行，唯一 weclone_adapter API 依赖）
- `frontend/src/api/training.ts`：`trainingExtract()` 封装（零 UI 消费）

### Files Modified
- `api/app_factory.py`：training_router 注释 11→8 端点；头部 199→198 端点
- `tests/test_api_routes.py`：training 计数 9→8、子路由总贡献 73→72
- 真源同步：CODE_GRAPH §1.1/§4.1/§4.2/§7/§13、AGENTS §0 启动方式+Owner Map+L9 失效、FEATURE_MAP B-07 重写、DECISION_LEDGER 08-28 三行决策、VISION A 区 198、HANDOFF 批注④、CODEMAPS/INDEX 指标

### Impact
- API 端点：199 → **198**（create_api_app 实扫）
- main.py：574 → 494 行
- 服务端从此**零微信数据提取路径**：克隆数据仅经 `/api/clone/upload`（JSON 文件）进入
- 测试基线不变：1030 Python + 59 前端（提取路径本就无专属测试）

### Verification
- `grep -rn "weclone" --include="*.py"` + 前端 ts/tsx → 0 命中
- `python -m pytest -q` → 1030 passed + 1 skipped（2026-08-28 实跑）
- `npx vitest run` → 59 passed / 11 files（2026-08-28 实跑）

---

## [2026-08-28] 文档感染源清理（治理会话第二阶段，用户授权"清理删除"）

### 决策依据
- 用户 2026-08-28 指示：文档治理须"清除感染源，修正相关说法……进行相关文档的清理删除"
- `docs/DOCUMENTATION_GOVERNANCE_REPORT.md` 为 08-26 一次性活动报告（derived），含无法修复的污染口径："19598 个 Python 文件"（.venv 污染）、"前端测试 1034 passed"（实为 Python 数错标）、"SP-3 Demo 删除驳回"（已被 08-28 D1 裁决推翻）；有效信息（阅读报告清单）已收编 `docs/README.md` §五

### Files Deleted
- `docs/DOCUMENTATION_GOVERNANCE_REPORT.md`（144 行）

### Files Modified（同批感染源修正，非删除）
- `README.md`（根）：徽章与结构树 1104→1089、19 页→15 页、demo 子路由行移除、14 API 模块→12、routers 13→20、pytest 注释 1025→1030
- `docs/DECISION_LEDGER.md`：§四"1104 基线不可回退"→"1089（v1.3 重测，旧基线随删除自然缩减）"
- `docs/VISION.md`：§B 候选池 SP-3 标记"已执行（D1）"移出冻结池；愿景板目标用户（广泛用户）与商业目标（完全免费开源 MIT）按用户口述落笔
- `docs/CODEMAPS/BACKEND.md`：漂移声明指向 199 端点；demo_routes 行移除
- `docs/CODEMAPS/FRONTEND.md`：demo.ts/DemoPage/UsersPage/UserWorkspace/BindingDetailPage/api users.ts 条目移除，pages 19→15
- `docs/CODEMAPS/MODULES.md`：路由模块 22→21（demo 移除）、挂载 17/204→16/199
- `docs/HANDOFF_REPORT.md`：三处对已删报告的引用改为"已删除+收编"批注

### 保留说明
- 16 份 READING_REPORT_*.md 保留为 derived（历史通读产物，docs/README.md §五 已标注"仅供追溯"，其历史数字随日期快照有效）；HANDOFF_REPORT 内 1025/1033/1035 等中间数字同理保留（头部已有 08-28 接管批注）

---

## [2026-08-28] Demo 后端全删（D1 裁决：全删，后续改为产品介绍页）

### 决策依据
- 用户 2026-08-28 治理会话裁决 D1「全删」：推翻 08-26「SP-3 立论崩塌撤回」结论，与 08-27 前端下线（见上条）合并完成 Demo 全链路移除
- 后端 demo_routes.py 原「保留供未来复用」终止——产品介绍页为静态展示，不走对话/记忆链路，demo 后端无复用价值

### Files Deleted
- `api/routers/demo_routes.py`（4 端点：POST /api/demo/chat/stream、GET /api/demo/memory/recall、GET /api/demo/memory/visualization、POST /api/demo/exit）

### Files Modified
- `api/app_factory.py`：
  - 删除 `from api.routers.demo_routes import router as demo_router`（原 line 26）
  - 删除 `app.include_router(demo_router)` 挂载行（原 line 239）
  - 头部注释更新：17 include_router/204 端点 → 16 include_router/199 端点（2026-08-28 实扫）
- `tests/test_production_hardening.py`：parametrize 移除 `/api/demo/memory/recall`、`/api/demo/memory/visualization` 两行（保留 /api/mimo/status 鉴权用例）
- `tests/test_connection_lifecycle.py`：删除 `test_sse_demo_stream_closes_generator_on_disconnect`（与上方 `/api/chat/stream` 同链路用例重复覆盖）

### Impact
- API 端点：204 → **199**（create_api_app 实扫）
- include_router：17 → 16
- DECISION_LEDGER SP-3 翻案登记（附4）；FEATURE_MAP F-02 作废；CODE_GRAPH v3.3.0 同步

### Verification
- `grep -rn "demo_routes\|from api.routers.demo" api/ tests/ main.py` → 0 命中
- `python -m pytest -q` → **1030 passed + 1 skipped**（2026-08-28 实跑，107.26s）
- `npx vitest run` → **59 passed / 11 files**（2026-08-28 实跑）

### 后续计划
- 产品介绍页（原 SP-3b 设想）：公开路由静态页，展示产品定位/玩法/邀请入口，不依赖对话后端——新立项，未启动

---

## [2026-08-27] Demo 页面下线（SP-3 裁决：直接删除，系统门面后续改造为产品介绍页）

### 决策依据
- 用户 2026-08-27 明确裁决「直接删除 Demo 页面，系统门面后续做成产品介绍页面」
- SP-3（Demo 删除）属于产品功能去重，非核心价值路径
- `docs/visual-map/index.html` F-02 已确认 Demo 为独立公开页，无内部依赖

### Files Deleted
- `frontend/src/pages/DemoPage.tsx`（~250 行 Demo 体验页）
- `frontend/src/api/demo.ts`（~85 行 demo 4 个端点封装：chat/stream、memory/recall、memory/visualization、exit）

### Files Modified
- `frontend/src/App.tsx`：
  - 删除 `const DemoPage = lazy(() => import('./pages/DemoPage'))`（line 28）
  - 删除 `<Route path="/demo" ...>` 路由（line 116）
  - 更新公开路由注释：「公开路由：登录页 + Demo 体验」→「公开路由：登录页」
- `frontend/src/pages/LoginPage.tsx`：
  - 删除"Demo 入口"按钮（line 219-228，连同其 `navigate('/demo')` 调用）
  - 登录页底部无外部跳转入口
- `docs/visual-map/index.html` 后续：F-02 卡片应标记为已废弃（视觉地图静态产物，不在本次范围内）

### Impact
- 前端净删除：~335 行
- 路由数：原 19 个公开+受保护路由 → 18 个（删除 /demo）
- API 端点：原 5 个 demo 端点（`/api/demo/*`）→ 0 个；后端对应实现（`api/routers/demo_routes.py`）未触碰（保留供未来复用，无需迁移）
- 安全性：消除未鉴权公开访问入口（虽然 Demo 体验页本身不暴露敏感数据）

### Verification
- `grep -r "DemoPage\|/demo\|api/demo" frontend/src/` → 0 命中
- `npm run build` / `tsc --noEmit` 待跑（前端测试不在 Python pytest 范围）
- 后端测试基线 1033 passed, 1 skipped 无变化

### 后续计划
- 「系统门面改造为产品介绍页」作为新独立任务（暂命名 SP-3b）
- 目标：在原 /demo 路径（公开页）上做产品介绍/导航/快速演示
- 入口可能从 LoginPage 底部或 Sidebar 顶部提供

---

## [2026-08-27] 微信本地解密项目剥离（用户 08-27 批准「先把这个剥离出来」）

### 根因
微信克隆的解密程序（依赖微信进程 + Windows API）必须运行在用户本机电脑，放到云服务器上是逻辑硬伤。
虽然 `api/routers/clone_routes.py` 已在 7-27 重构时仅保留 `/api/clone/upload`，但 `clone_training/`、
`weclone_adapter/`、`voice/clone_data_manager.py` 仍保留了"调用本地解密"的旁路（wcf/wechatmsg/decrypt）。
本轮彻底剥离，确保云端 100% 不可能触发任何本地解密路径。

### Files Deleted
- `clone_training/wechat_decrypt_source.py`（300+ 行，`DecryptSource` 类 + `DecryptSourceError` + `wechat-decrypt` 适配层）

### Files Rewritten (剥离死分支)

#### `clone_training/data_extractor.py` (509 → 252 行)
**删除方法**：
- `extract_from_wcf`（来源1：WeChatFerry RPC，需本机微信进程）— 70 行
- `extract_from_wechatmsg`（来源2：WeChatMsg SQLite，已解密数据库）— 100+ 行
- `extract_from_decrypt`（来源4：wechat-decrypt 4.x 解密，调用已删除的 `DecryptSource`）— 40+ 行
- `_process_wcf_messages`（WCF 辅助）
- `_build_conversations_from_rows`（SQLite 辅助）
- `_date_to_timestamp`（辅助）— **注**：仍需保留在 `extract_from_txt` 中？检查后实际未删除

**保留方法**：
- `extract_from_export`（来源3：txt/csv/json 文件导入）— 云端可用
- `_extract_from_json / _csv / _txt`
- `_process_raw_messages / _is_system_message / _empty_result / save_to_json`

#### `weclone_adapter/adapter.py` (206 → 230 行)
**改动**：
- `clone()` 的 `source` 默认值：`"wcf"` → `"auto"`
- `_extract()` 移除 `wcf / wechatmsg / decrypt` 三个分支
- 拒绝调用：source 不在 `("txt", "csv", "json", "auto")` 时 logger.error + 返回 `[]`
- `health_check()` 增加 `wechat_local_decrypt_stripped: True` 与 `supported_sources: ["txt", "csv", "json", "auto"]`

#### `voice/clone_data_manager.py` (378 → 354 行)
**改动**：
- 移除 `_get_contacts_from_decrypt` 方法（line 73-86，含 `from clone_training.wechat_decrypt_source import DecryptSource`）
- 移除 `get_contacts` 中的"优先从解密数据库获取"逻辑
- 移除 `import time`（仅在已删除的缓存逻辑中使用）
- docstring 标注"剥离历史"

#### `tests/test_request_context_isolation.py` (141 → 120 行)
**删除测试**：
- `test_clone_preview_uses_injected_local_extractor` — 该函数已随 Option B 后端清理删除，测试现已是孤立代码

#### `tests/test_api_routes.py`
**测试断言更新**（不是删除，是更新数字）：
- `(clone_routes, 9, "clone/*")` → `(clone_routes, 8, "clone/* (2026-08-27 剥离 /api/clone/preview 死路径)")`
- `test_total_contribution_is_74` → `test_total_contribution_is_73`（73 = 11+11+9+7+9+6+12+8）

### Files Intentionally NOT Touched
- `wechat_direct/` 整个目录 — 这是微信消息收发通道（不是解密），保留
- `wechat_direct/wechat_connector.py`（line 22 logger）— 消息通道
- `main.py`（line 295 `from wechat_direct import WeChatConnector`）— 启动消息通道
- `api/deps.py`、`api/run_api.py`、`api/routers/chat_routes.py`、`api/routers/misc_routes.py` — 全部是消息收发，与解密无关
- `tests/test_wechat_connector.py` — 测试消息收发，不是解密

### Impact
- **代码精简**：约 -300 行（wechat_decrypt_source.py 整体 + data_extractor.py 减半 + adapter.py 微调）
- **剥离原则**：100% 云端可用的克隆路径只支持文件导入（txt/csv/json/auto），不依赖本机微信进程
- **安全性**：杜绝任何代码路径触发本机微信内存密钥提取
- **向后兼容**：API `/api/clone/upload`（已存在）保持不变，仍是生产路径
- **测试基线**：1033 passed, 1 skipped（无新增失败；2 个测试因 Option B 调整数字，已更新）

### Verification
- `python -c "from clone_training.data_extractor import DataExtractor; print([m for m in dir(DataExtractor()) if 'extract' in m])"` → `['extract_from_export']`
- `python -m pytest tests/test_api_routes.py` → 15/15 passed
- `python -m pytest tests/test_request_context_isolation.py` → 3/3 passed
- `python -m pytest tests/` → **1033 passed, 1 skipped**（全量回归无失败）

### 后续待办
- `third_party/wechat-decrypt/` 目录已 .gitignore 忽略，无需操作
- 旧数据集中标记 `source: "decrypt"` / `source: "wcf"` / `source: "wechatmsg"` 的条目仍存在（已 JSON 落盘），仅影响 `_detect_source` 返回值显示，不影响功能

---

## [2026-08-27] 接管基线收尾 — clonePreview 死代码 + 幽灵层三页（SP-9 裁决：直接删除）

### Dead Exports Removed (Frontend)
- `src/api/clone.ts` — Removed `clonePreview()` 函数。对应后端 `POST /api/clone/preview` 端点已于克隆下线（Option B）时删除，前端零调用。`ClonePersonaPreview` 接口保留（`cloneUpload` 仍在使用）。

### Ghost Pages Deleted (SP-9, 用户 08-27 批准「直接删除」)
三页互链完整但路由已全部摘除（App.tsx 零挂载），属微信↔角色绑定功能的半成品：
- `src/pages/UsersPage.tsx` + `src/tests/components/UsersPage.test.tsx`
- `src/pages/BindingDetailPage.tsx` + `src/tests/components/BindingDetailPage.test.tsx`
- `src/pages/UserWorkspace.tsx` + `src/tests/components/UserWorkspace.test.tsx`

### Transitively Dead Code Removed (跟随幽灵层失去全部消费者)
- `src/api/users.ts` — 整文件删除（listUsers/getUserDetail/getUserChatHistory/getUserEmotion/setUserRole/resetUser/deleteUser/toUserDisplay 及相关接口；消费者仅 UsersPage 与 client 聚合导出）。注意：管理后台 AdminUsersPage 使用独立的 `api/admin.ts`，不受影响。
- `src/api/wechat.ts` — 删除绑定管理区块（bindWechat/listMyBindings/updateBinding/unbindWechat + WechatBindingDTO）。后端 `/api/wechat/bind*` 端点保留未动，未来复用无需迁移。
- `src/hooks/useQueries.ts` — 删除零消费者的 `useWechatBindings()` hook。
- `src/api/client.ts` — 同步清理 users 组导入/导出与绑定函数聚合。
- `src/tests/components/WeChatPage.test.tsx` — 移除 mock 工厂中的 bindWechat/unbindWechat 字段及过时注释。

### Impact
- 前端净删除约 -1000 行；活页 WeChatPage/AdminUsersPage 零影响
- 后端无任何改动
- 验证：tsc --noEmit 通过 + vitest 全量通过（数字见 commit 时点）

---

## [2026-07-14] Routing Fix — Tombstone & Dead Code Cleanup

### Dead Endpoints Removed
- `api/routers/misc_routes.py` — Removed shadowed `/api/health` endpoint (3 lines). This was superseded by `api/health_routes.py` which is mounted first in `app_factory.py`. Having two `/api/health` routes caused confusion during debugging.

### Dead Imports Removed
- `api/app_factory.py` — Removed unused `Limiter`, `SlowAPIMiddleware`, `get_remote_address` imports from slowapi. Only `RateLimitExceeded` is used (for exception handler). The custom fallback rate limiter serves as the actual enforcement mechanism.
- `api/main_routes.py` — Removed unused `APIRouter` import and dead `router = APIRouter(tags=["main"])` variable (tombstone from when `health_router` was extracted to `health_routes.py`). The `router` variable was never imported by any file.

### Test Artifacts Deleted
- `tests/_test_invite.db` — Leftover SQLite test database artifact from invite code tests.

### Documentation Updated
- `docs/CODEMAPS/BACKEND.md` — Added `health_routes.py` and `runtime_config.py` to architecture tree; updated `misc_routes.py` endpoint count (10→9); updated `main_routes.py` description to reflect it no longer contains a router instance.

### Files NOT Removed (Intentionally Retained)
- `shisi/api/v2/health_routes.py` — Defines a `/health` route in the v2 API namespace. The entire `shisi/api/v2/` module (`v2_router`) is never mounted in `app_factory.py`. However, this is part of the shisi v2 API layer and may be activated in future integration work. Left intact to avoid breaking import chains.
  - ⚠️ **2026-09-18 更新：本保留裁决已被推翻** —— `shisi/api/v2/` 全 6 文件按用户裁决删除（见本文件首条）。"避免破坏 import 链"的顾虑经全仓 grep 证伪：**零代码 import，仅存在文档引用**。
- `shisi/memory/legacy/` — Despite the "legacy" name, these modules are actively imported by `shisi/application/memory_service.py` and covered by `tests/test_memory.py` + `tests/test_memory_pipeline.py`. Not dead code.
- `shisi/knowledge/legacy/` — Despite the "legacy" name, `rag_engine.py` is actively imported by `shisi/knowledge/legacy/__init__.py` and tested by `tests/test_rag_engine.py`. Not dead code.

### Impact
- Lines removed: ~15 (dead code + dead imports)
- No functional changes — all removed code was either shadowed or never executed
- Tests: 77/77 passed after cleanup (test_api_routes + test_production_hardening + test_ops_lifecycle + test_invite_codes + test_connection_lifecycle + test_p0_fixes + test_config_permissions)

---

## [2026-06-03] Dead Code Cleanup Session

### Unused Dependencies Removed
- 
echarts@^2.15.0 - No imports in any source file; manualChunks entry in vite.config.ts also cleaned up
- @testing-library/user-event@^14.6.1 - No imports in any source or test file

### Unused Files Deleted — Frontend

**Unused common/ components (6 files):**
- src/components/common/Card.tsx - Only used by ProactiveEnginePanel (also dead, transitively dead)
- src/components/common/EmptyState.tsx - Only used by MessageList (unused chat component)
- src/components/common/ProactiveEnginePanel.tsx - No external consumers
- src/components/common/SensitiveInput.tsx - No external consumers
- src/components/common/Skeleton.tsx - No external consumers (shared/Skeleton.tsx kept — used by pages)
- src/components/common/UrgencyBadge.tsx - No external consumers

**Unused shared/ duplicates (6 files):**
- src/components/shared/AnimatedNumber.tsx - No consumers; only in barrel export
- src/components/shared/DangerButton.tsx - No consumers; only in barrel export
- src/components/shared/ParallaxTilt.tsx - No consumers; only in barrel export
- src/components/shared/ProgressBar.tsx - No consumers; only in barrel export
- src/components/shared/StaggerContainer.tsx - No consumers; only in barrel export
- src/components/shared/Tabs.tsx - No consumers; only in barrel export
- src/components/shared/Tooltip.tsx - No consumers; only in barrel export

**Unused chat components (5 files):**
- src/components/chat/ChatInput.tsx - No external imports
- src/components/chat/MessageBubble.tsx - Only used by MessageList (also dead)
- src/components/chat/MessageList.tsx - No external imports
- src/components/chat/ProactiveToast.tsx - No external imports
- src/components/chat/TypingIndicator.tsx - No external imports

**Unused hooks (4 files):**
- src/hooks/useDashboardData.ts - No external imports
- src/hooks/useSmartPoll.ts - Only used by useDashboardData (also dead)
- src/hooks/useSSE.ts - No external imports
- src/hooks/useWebSocket.ts - No external imports

**Unused stores (2 files):**
- src/store/logStore.ts - No imports anywhere
- src/store/settingsStore.ts - No imports anywhere

**Unused types (1 file):**
- src/types/sticker.ts - No imports anywhere

**Unused page (1 file):**
- src/pages/AdminInvitesPage.tsx - Not imported in App.tsx or any other file

**Unused API module (1 file):**
- src/api/invites.ts - Only used by AdminInvitesPage (also dead)

### Barrel Files Cleaned Up
- src/components/common/index.ts — Removed 6 dead re-exports (Card, Skeleton, EmptyState, UrgencyBadge, ProactiveEnginePanel, SensitiveInput)
- src/components/shared/index.ts — Removed 9 dead re-exports (Select, ProgressBar, Skeleton, Toast, EmptyState, Badge, Tabs, Tooltip, AnimatedNumber, StaggerContainer, ParallaxTilt, DangerButton). Note: Select, Skeleton, EmptyState, Badge were RESTORED after discovering pages import them via barrel.

### Pre-existing Bugs Fixed
- src/api/client.ts:256,275 — Added missing imports of psychProfile, psychSnapshots, psychReset, psychMentalHealth, psychLiwc from ./system (caused TS2304 errors)

### Impact
- Files deleted: 25
- Dependencies removed: 2
- Lines of code removed: ~3,800 (estimated)
- Bundle size reduction: recharts removed from manualChunks (~45 KB gzip savings potential)
- TypeScript errors fixed: 10 (pre-existing psych* import bugs)

### Testing
- 	sc --noEmit — 0 errors
- ite build — passed (2186 modules, 1.20s)
- itest run — 4/4 frontend tests passed
- pytest -x -q — 625/625 Python tests passed (1 skipped)

### Notes
- common/EmptyState.tsx and common/Skeleton.tsx were kept restored through shared/ because pages import them via barrel
- common/Badge.tsx kept (used by KnowledgePreview, StorylineEditor, StorylineIndicator)
- shared/Select, Skeleton, EmptyState, Badge RESTORED after initial deletion — pages use them via barrel imports
- Legacy backend pi/_*_routes.py files NOT removed — they coexist with pi/routers/ in app_factory.py; only a full endpoint diff can confirm redundancy
- 
eact-window and 
eact-virtualized-auto-sizer kept — used via 
equire() in MessageList.tsx even though MessageList is unused

## 2026-09-18 — 用户裁决批次：双 _monologues 收敛 + extract_intent 死方法删除

**Deleted**:
- `proactive/ase_engine.py` `ASEEngine._monologues`（deque 定义+注释 5 行 + on_chat/reflect 两处 append，共 8 行）——存储对象与内部 `ReflectionEngine.reflect()` 返回值完全相同（`self._reflection` 于 `__init__` 持有），全仓零读取点；独白记录唯一 owner=ReflectionEngine（`get_latest_monologue` 真接口 + D26 正向测试守卫）。`InnerMonologue` import 保留（582/652 返回类型注解在用）。
- `security/prompt_injection.py` `extract_intent` 方法（21 行）——全仓零调用；内部直调 `chat_sync`，若将来在 async 上下文接线会同步阻塞事件循环（潜伏雷，与生产 64 触发 0 送达同族）。模块在用部分 `detect`/`sanitize`（`_init_mixin.py:112` 生产启用）原样保留。

**Verification**: `ast.parse` 双文件过；grep 残留双零（`_monologues` in ase_engine=0、`extract_intent` 全仓=0）；ruff 两文件 All checks passed；全量 pytest 零回归（数字见 LOG 五十五）。

**Reversible**: 单提交 `git revert` 即可整体恢复。

## 2026-09-18 — 双角色库收敛（裁决①）：旧角色库删除 + 双库同步脚本删除

**Deleted**:
- `data/characters/` 全部 53 张旧卡（本地与服务器同步删除）——49 张为 config/characters 权威库 24 角色的旧版本（persona_* 时间戳版/裸名版/序号版，逐张按 name 归组核对）；4 张无对应孤立卡（人设重度病娇by诗/修仙妹3.0/茉莉/纯对话版纯爱百合性转萝莉仙尊-银子著）经用户裁决废弃。**备份**：`data/archive/characters-data-backup-20260918.tar.gz`（53 张全量；data/ 不入 git，tar 为唯一回滚手段）。
- `config/characters/222cdb5a.json`（本地孤立卡，name=林晚星，服务器权威版 c907dc57）——备份 `data/archive/222cdb5a-localconfig-backup.json`。
- `scripts/sync_character_files.py`（config→data 双库同步脚本，**双库分歧的制度化源头**）——目标库已删，用途终结；全文阅读确认后删除。

**Verification**: 旧路径引用 grep 归零（仅存 knowledge_routes 注释一条）；本地 config 25 张 JSON 校验 25/25 过；全量 pytest **1060 passed / 4 skipped**（+48 = 25 卡 × test_persona_injection 每卡 2 参数化用例全覆盖，0 失败）；ruff 7 文件全过。

**Reversible**: 代码单提交 revert；卡数据解包 tar 即恢复。

---

## [2026-09-20] 删除死代码 `persona_utils.build_time_context`（墙钟时区缺陷修复批次附带）

### 删除对象与证据
- `my_character/persona_utils.py::build_time_context()`（13 行：函数体 + docstring）
  - **零调用者**：全仓 grep `build_time_context`，除本定义处外**零命中**（含 `tests/`、`shisi/`、`orchestrator/`）
  - **它是"看着像接好的线"**：函数体只做一件事 —— `TimeContext.now()`，而该实现原有 UTC 墙钟缺陷（本次同批修复）。留着会误导后续窗口以为"时间上下文已接线"
  - 替代路径：需要 `TimeContext` 的调用方**直接** `TimeContext.now()`（现为本地时钟，见 `my_character/persona_engine.py:418`）

### 同批修复（非删除）
- `shisi/memory/legacy/memory_pipeline.py` 4 处墙钟判定由 UTC 改本地：`after_chat` 深夜情感加权（`:286`）、`daily_maintenance` 日记日期键（`:582`）、`get_formatted_context` 当日摘要查询键（`:686`）、`_do_fact_extraction` 的 `should_store_as_fact` 入参（`:778`）
- `my_character/enhanced_prompt_engine.py::TimeContext.now()` 由 UTC 改本地
- 新增公共真源 `utils/local_time.py::now_local()`；`proactive/ase_engine._local_now` 改为委托它（**保留函数名**，`proactive/scheduler.py` 既有 import 与"共用时钟源"注释继续成立）
- **保留不动**：`memory_pipeline` 中 `session_id` 生成（`:206`）与 `_apply_forgetting` 的 `updated_at`/`days_old` 时间差运算（`:738-739`）—— 这两类**必须**用 UTC

### 验证
- `ruff check`（0.16.8，CI 同版本）→ All checks passed
- 新增 `tests/test_local_time.py`（13 用例）+ `tests/test_memory_pipeline.py` 3 用例；**突变验红已做**：把 `after_chat` 改回 `datetime.now(tz=timezone.utc)` → `test_mp_after_chat_feeds_local_clock_to_late_night` 与静态防护 `test_no_wall_clock_utc_regression_in_fixed_sites` **同时变红**，还原后全绿（两用例均与运行时刻无关，无墙钟巧合）
- 分块全量 pytest 见 LOG 同批次条目

### Impact
- 删除 1 个死函数（13 行）；**无行为变更**（零调用者）
- 修复：UTC+8 部署下"深夜情感记忆加权"由错位在本地 07:00–13:59 恢复为真正的 23:00–05:00；日记/当日摘要按本地日期切分
- 副作用（已登记）：`diary_summaries` 中修复前写入的行仍以 **UTC 日期**为键，修复后当日查询键为本地日期 → **历史行存在一次性键错位**，不迁移、自然过期（旧摘要仍可经 `detect_mood_trend` 全量读取）

**Reversible**: 代码单提交 revert 即恢复；死函数无调用方，删除不影响任何路径。

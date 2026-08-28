# 项目交接报告

**项目**：unique-you — 唯一的你·十四 — 基于 LLM 的智能情感陪伴系统  
**版本**：v1.1（2026-08-27 交接版）  
**交接日期**：2026-08-27  
**项目根目录**：`D:\Desktop\ai-girlfriend`  
**Python 版本**：3.10+（见 `pyproject.toml`）

> ⚠️ **2026-08-28 接管批注**（本报告为 08-27 快照，数字以 CODE_GRAPH v3.3.0 为准）：
> ① Demo 已按用户裁决 D1 **全链路删除**（前端 08-27 + 后端 08-28，端点 204→199），"改造为系统门面"的旧预期由"产品介绍页"新立项替代，见 DECISION_LEDGER 附4 与 DELETION_LOG [2026-08-28]；
> ② 测试基线当日实测 **1030 passed + 1 skipped（pytest）/ 59（vitest）= 1089**；
> ③ 本报告中 1025/1033/1035 等中间数字为历史快照，AGENTS.md 基线已同步为 1089。


---

## 1. 项目概览

### 1.1 产品形态
微信扫码即用的 LLM 智能情感陪伴系统，扫码登录后控制台调角色与语音。

### 1.2 核心能力（见 `README.md`）
- 微信聊天（扫码登录，文字/语音，多用户独立）
- 角色系统（每用户绑角色卡，性格/风格/口头禅可调）
- 情感引擎（亲密度、情感阶段变化）
- 主动搭话（不全是被动等待）
- 语音合成（MiMo 云 / Edge-TTS / 本地模型）
- 记忆系统（三层：短期 + 情景 + 长期）
- 工具（天气、日历、提醒、搜索）
- 剧情线（支线、进度追踪）
- 邀请码注册 + 管理控制台（19 个页面）

### 1.3 技术栈
- 后端：Python 3.10+ / FastAPI / PEP 8 + 类型注解
- 前端：React 19 + Vite 8 + TypeScript 6 + Tailwind 4 + Zustand 5
- 测试：1025 Python 测试 + 79 前端测试 = 1104（**实测 1035 待登记漂移**）
- 部署：`start_all.cmd` / `deploy_ai_girlfriend.bat` / `deploy_ai_girlfriend.ps1`
- 远程仓库：`https://github.com/FOURTEEN1416/fourteen.git`

### 1.4 项目结构（顶层）
```
D:\Desktop\ai-girlfriend\
├── main.py / start_all.cmd / start_backend.cmd / start_frontend.cmd
├── deploy_ai_girlfriend.bat / .ps1
├── api/（41文件）           — FastAPI 路由层
├── character_card/（6文件） — SillyTavern V2/V3 角色卡编解码
├── clone_training/          — 克隆训练管线（已剥离本地解密）
├── config/                  — 角色/全局配置
├── context/                 — 上下文构建
├── data/                    — 运行时数据
├── frontend/                — React 19 前端
├── llm_provider/（5文件）   — LLM 部署/路由
├── logs/                    — 运行日志
├── memory_ext/              — 记忆系统
├── multimodal/              — 多模态
├── my_character/（21文件）  — 角色子系统
├── observability/（部分）   — 可观测性
├── orchestrator/（6文件）   — 流程编排
├── persona_extractor/（13文件）— 人格抽取
├── plugins/                 — 插件
├── proactive/（部分）       — 主动搭话
├── security/（部分）        — 安全策略
├── shisi/（124文件）        — 核心算法（legacy/active 双形态）
├── tests/（65+）            — 测试
├── third_party/             — 外部仓库（.gitignore，已含 wechat-decrypt）
├── tools/ utils/ scripts/ deploy/ cache/  — 工具链
├── voice/（11文件）         — 语音合成
├── wechat_direct/ weclone_adapter/ clone_training/ — 微信集成
└── docs/                    — **本文档所在目录**
```

---

## 2. 本次交接已完成的工作

### 2.1 7.5 火爬虫授权改造 → Crawl4AI（已完成）

**问题**：原 `web_enricher.py` (37.9KB) 依赖 Firecrawl SDK，需 `FIRECRAWL_API_KEY` 环境变量，属授权硬伤。

**方案**：用免授权的开源库 **Crawl4AI** 完整替代 Firecrawl。

**文件变更**：
- `persona_extractor/web_enricher.py`：
  - 删除了 `FirecrawlSource` 类
  - 新增 `Crawl4AISource` 类（提供同名的 `search()` / `scrape()` 接口）
  - 修复：使用了正确的 Crawl4AI API（`arun` 而非 `scrape_url`，`config` 而非 `browser_config`）
  - 修复：变量 `docs` 必须在 try 块外初始化（避免 `UnboundLocalError`）
  - `WebPersonaEnricher.__init__()`：移除 `firecrawl_api_key` 参数
  - `_detect_sources()`：可用源从 `firecrawl` 改为 `crawl4ai`
  - `add_url()` 抓取链路：`DirectScraper → Jina Reader → Crawl4AI`（原 `→ Firecrawl`）
  - `search_all_sources()` / `_collect_docs()` Phase 2：Firecrawl 替换为 Crawl4AI
- `scripts/enrich_persona_web.py`：
  - 更新帮助文案，Firecrawl 标记为"免授权替代"
  - CLI 模式从 `🔥 Firecrawl` 改为 `🕷️ Crawl4AI`

**实测结果**：
- `python scripts/enrich_persona_web.py --id test123 --urls "URL"` ✅ 抓取 61604 字符，源标记为 `crawl4ai`
- `python scripts/enrich_persona_web.py --id test123 --all-sources` ✅ 运行通过

**意义**：彻底绕开 Firecrawl 授权环节，所有爬取能力免费。

### 2.2 微信克隆功能彻底下线 Option B（后端 + 微信解密项目剥离，全部完成）

**问题**：微信克隆的解密程序（依赖微信进程 + Windows API）必须运行在用户本机，放到云服务器是逻辑硬伤。

**方案（Option B - 彻底下线）**：
1. 后端删除云上不可能工作的端点
2. 整个 `clone_training/` / `weclone_adapter/` / `voice/clone_data_manager.py` 中所有调用本地解密的旁路全部剥离
3. **云端 100% 不可能触发任何本地解密路径**

**后端变更 — `api/routers/clone_routes.py`**：
- 删除 `ClonePreviewRequest` 类
- 删除 `_build_clone_preview` 函数
- 删除 `POST /api/clone/preview` 端点
- 删除 `from pydantic import BaseModel, Field` 导入
- 保留 `_build_clone_preview_from_conversations`（upload 端点仍依赖）
- 保留 `/api/clone/upload`（生产路径：JSON → StyleAnalyzer → 人设预览）
- 保留所有其他端点（contacts / datasets / batch-delete / stats）
- **最终**：clone_routes 共 8 个端点（原 9 个）

**微信本地解密项目剥离**（`docs/DELETION_LOG.md` 完整记录）：

| 文件 | 操作 | 说明 |
|------|------|------|
| `clone_training/wechat_decrypt_source.py` | **整文件删除** | 300+ 行，wechat-decrypt 适配层 |
| `clone_training/data_extractor.py` | **重写** | 删除 `extract_from_wcf` / `extract_from_wechatmsg` / `extract_from_decrypt`（来源 1/2/4），仅保留 `extract_from_export`（来源 3：txt/csv/json 文件导入）。509 → 252 行 |
| `weclone_adapter/adapter.py` | **重写** | `_extract()` 移除 wcf/wechatmsg/decrypt 分支，source 仅支持 `(auto, txt, csv, json)`；`health_check` 增加 `wechat_local_decrypt_stripped: True` |
| `voice/clone_data_manager.py` | **重写** | 移除 `_get_contacts_from_decrypt` 方法与 `import time`，联系人来源仅保留从已有克隆数据 `*_raw.json` 提取 |

**测试代码更新**：
- `tests/test_request_context_isolation.py`：删除 `test_clone_preview_uses_injected_local_extractor`（孤立测试）
- `tests/test_api_routes.py`：更新断言（`clone_routes: 9 → 8`，`total: 74 → 73`）

**前端变更 — `frontend/src/pages/CreateRole.tsx`**：
- 删除 `{/* ═══ 步骤 1：下载工具（三选一） ═══ */}` 整块（97 行 UI 卡片）
- 保留：步骤 2（在本地电脑运行工具）+ 步骤 3（上传并分析）

**保留的本地流程**（云端 100% 安全）：
1. 用户在本地电脑用 WeChatMsg / PyWxDump / wechat-decrypt 提取聊天记录
2. 导出 JSON / CSV / TXT 文件
3. 通过前端 `WeChatCloneTab` 选中并上传
4. 服务器 `/api/clone/upload` 调用 `StyleAnalyzer` 分析
5. 返回人设预览，前端填入角色卡

**影响统计**：
- 代码精简：约 -300 行（wechat_decrypt_source.py 整文件 + data_extractor.py 减半 + adapter.py 微调）
- 测试基线：1033 passed, 1 skipped（全量回归无失败）
- 安全性：100% 云端隔离，杜绝任何代码路径触发本机微信内存密钥提取

### 2.3 项目源码穷举阅读 100% 完成

- 穷举了项目内全部 **358 个 Python 源文件**（剔除了 `.venv` 19,236 个第三方依赖）
- 生成了 12 份 `docs/READING_REPORT_*.md` 结构化阅读报告 + 1 份综合治理报告（08-28 治理时因 .venv 污染口径无法修复而删除，有效信息收编入 `docs/README.md`）
- 实测 `pytest --collect-only` 收集 **1035 tests**（基线 1025 已在 INDEX.md 登记漂移）
- 发现的关键漂移点：测试基线 1025 → 1035（+10）、文件口径"19,598"实际为 .venv 污染

---

## 3. 待您确认的 4 项 SP 任务

按商讨协议（AGENTS.md §1.3），以下 4 项任务需要您交代**“当前真实状态”**（人话），我将基于此继续完成后续实现。

> **2026-08-27 补充**：用户 2026-08-27 明确要求**AI 主动查项目中的代码和设计文档**而非等待人话解释。本节已自动从 `docs/adr/`、`docs/architecture/`、`docs/visual-map/`、`docs/plans/` 抽取起点信息。

### 起点信息自动抽取（基于项目现有文档）

| 任务 | 起点（从项目文档中抽取） |
|------|---------------------------|
| **SP-1 状态中心并集** | `docs/architecture/knowledge-graph.md` L51 / L67：原设计 = **4 统计 + 情绪分布 + 成就 + 亲密度趋势**；当前实现 = `frontend/src/pages/StatusCenter.tsx`（3 卡片：当前情绪/亲密等级/记忆条目 + 最近记忆列表）。`docs/visual-map/index.html` F-07 标注："**已知差距 G-01：较 v10 设计缺情绪分布图、成就、亲密度趋势**"。**任务核心**：补齐 G-01 三个缺失维度。`docs/adr/ADR-0011-unified-design-framework.md`（Superseded）原定侧边栏导航方案已被废止。 |
| **SP-3 Demo 删除** | `frontend/src/pages/DemoPage.tsx` 是**无鉴权公开演示对话页**（路由 `/demo`），无内部业务依赖，仅 `/api/demo/*` 4 端点独立封装。**2026-08-27 已直接删除**。**后续**：原 `/demo` 路径将改造成"产品介绍/系统门面"页（待立项 SP-3b）。 |
| **SP-4 知识库入口复活** | `docs/architecture/knowledge-graph.md` L186：`shisi/knowledge/retriever.py` → 知识库（`data/knowledge/` + `llm_gateway.py`）→ 消费方 `api/routers/chat_routes.py`。**当前状态**：知识库后端完整在用；前端**没有独立的知识库管理页面**（知识注入通过 `frontend/src/pages/CreateRole.tsx` 的"文件导入" Tab 间接完成，`character_card/` 写死）。**任务核心**：在前端提供独立的"知识库入库"管理界面。 |
| **SP-5 塑料感补课** | `frontend/src/index.css` L8-16：定义了 **`--color-macaron-{pink,blue,green}-{light,deep}`** 三色系马卡龙糖果色；L81-113：**`Glass Morphism` 毛玻璃** (.glass-card / .glass-pink / .glass-blue / .glass-green)；L222 **`Macaron Button` 按钮**。**塑料感源头**：糖果色 + 毛玻璃 + 三色渐变（`#FDF2F8 → #EFF6FF → #ECFDF5`）。`docs/architecture/design-principles.md` 无视觉原则约束；`docs/adr/ADR-0003-纯Tailwind样式体系.md` 与 `ADR-0004-LightOnly主题.md` 是仅有的样式 ADR。**任务核心**：替换马卡龙色系 + 减少毛玻璃滥用 + 统一色板/间距/字体。 |

**回复格式示例**：`1✅ 2调整 3❌ 4✅` 或分批确认（先 1/3 后 4/5）。

### 2026-08-27 处置记录

- **SP-3 Demo 删除**：✅ **已完成**（详见 `docs/DELETION_LOG.md` 顶部条目），等待 SP-3b（产品介绍页）立项
- **SP-1/4/5**：✅ **起点信息已自动抽取**，等待您对起点确认后进入实施

---

## 4. 已知问题与技术债务

| # | 问题 | 影响范围 | 建议处理 |
|---|------|----------|----------|
| L1 | `web_enricher.py` 的 `Crawl4AISource` 搜索功能受外部 `r.jina.ai` 服务可用性影响 | 搜索模式可能因网络原因返回空结果 | 已有 bilibili_api / jina_reader 多源兜底 |
| L2 | 前端 `CreateRole.tsx` 仍保留 `clonePreview` API 的 TypeScript 类型导出（未被任何组件调用） | 构建时可能产生未使用警告 | 下次重构可顺手清理 |
| L3 | `AGENTS.md` 中测试基线 1025 实际为 1033（+8 漂移） | 文档与实际不一致 | 需更新 AGENTS.md §4.3 |
| L4 | 旧克隆数据集中标记 `source: "decrypt"` / `source: "wcf"` / `source: "wechatmsg"` 的条目仍存在（已 JSON 落盘） | `_detect_source` 返回值显示 | 仅影响显示标签，不影响功能；可保留作为历史 |
| L5 | 后续若有"使用本地解密"的需求，需从 `git log` 找回 `wechat_decrypt_source.py` 历史版本 | 未来复用 | git reflog 可恢复 |

---

## 5. 关键决策摘要

详细决策由来见 `DECISION_LEDGER.md`。本节摘录与本次交接最相关的决策：

| 决策 | 日期 | 关键内容 |
|------|------|----------|
| 微信本地解密项目彻底剥离 | 2026-08-27 | 用户要求"先把这个剥离出来"；云端 100% 杜绝本地解密路径；删除 wechat_decrypt_source.py + data_extractor 三种本地提取 + adapter 旁路 + clone_data_manager 解密路径 |
| 7.5 爬虫授权改造 | 2026-08-27 | 用 Crawl4AI 替代 Firecrawl，绕开授权 |
| 微信克隆功能下云 | 2026-07-27 | wechat-decrypt 必须在本机；服务器仅接受 JSON 上传 |
| 微信克隆 Option B 端点精简 | 2026-08-27 | 删 /api/clone/preview 死端点；前端删三选一工具卡片 |
| 测试基线漂移登记 | 2026-08-27 | 1025 → 1033（+8），已记录到 INDEX.md |
| LoRA 移除决策 | 2026-08-26 | 风格克隆 = 外接 API + RAG + 提示词注入 |

---

## 6. 项目文档导航（接手人必备）

本节列出了 `docs/` 目录下的所有关键文档，并标注每个文档的作用，帮助接手人快速了解项目。

### 6.1 核心总览类（必读）

| 文档 | 作用 | 阅读优先级 |
|------|------|-----------|
| ~~`DOCUMENTATION_GOVERNANCE_REPORT.md`~~ | 已删除（2026-08-28 治理：污染口径不可修复，内容收编 docs/README.md） | — |
| **`HANDOFF_REPORT.md`** | 项目交接报告（本文档），含已完成工作、待办、问题、下一步 | ⭐⭐⭐ |
| **`VISION.md`** | 项目最顶层的愿景文档，含产品形态、技术栈、核心能力 | ⭐⭐⭐ |
| **`FEATURE_MAP.md`** | 全局功能映射（编号：F-xx/B-xx/G-xx），含每个功能的当前真实行为 | ⭐⭐⭐ |
| **`P1_BACKLOG.md`** | P1 优先级待办 | ⭐⭐⭐ |

### 6.2 模块阅读报告（共 12 份）

| 报告文件 | 对应模块 | 关键路径 |
|----------|----------|----------|
| `READING_REPORT_api.md` | API 服务 | `api/` (35 文件) |
| `READING_REPORT_character_card.md` | 角色卡 | `character_card/` + `my_character/` |
| `READING_REPORT_llm_provider.md` | LLM 提供商 | `llm_provider/` |
| `READING_REPORT_orchestrator.md` | 编排器 | `orchestrator/` |
| `READING_REPORT_voice.md` | 语音 | `voice/` |
| `READING_REPORT_persona_extractor.md` | 人格抽取（含本次改造的 web_enricher） | `persona_extractor/` |
| `READING_REPORT_proactive_plugins.md` | 主动搭话/插件 | `proactive/` + `plugins/` |
| `READING_REPORT_wechat_clone.md` | 微信克隆（含本次下线影响） | `wechat_direct/` + `weclone_adapter/` + `clone_training/` |
| `READING_REPORT_memory_context_multimodal.md` | 记忆/多模态 | `memory_ext/` + `context/` + `multimodal/` |
| `READING_REPORT_security_observability.md` | 安全/可观测 | `security/` + `observability/` |
| `READING_REPORT_tools_utils_scripts_cache.md` | 工具/脚本/部署/缓存 | `tools/` + `utils/` + `scripts/` + `deploy/` + `cache/` |
| `READING_REPORT_shisi.md` | shisi 核心（含 legacy 路径澄清） | `shisi/` (124 文件) |
| `READING_REPORT_tests_root.md` | 测试根/根目录启动器 | `tests/` + `main.py` + `user_scheduler.py` |

### 6.3 决策与历史类

| 文档 | 作用 |
|------|------|
| **`DECISION_LEDGER.md`** | 自项目 inception 以来的关键决策记录，含权衡、推理过程 |
| **`DELETION_LOG.md`** | 已废弃/删除的功能、代码路径、原因 |

### 6.4 其它资产目录

| 目录 | 内容 |
|------|------|
| `adr/` | 架构决策记录（轻量） |
| `architecture/` | 架构图 |
| `audits/` | 安全/质量审计报告 |
| `CODEMAPS/` | 代码结构图 |
| `designs/` | 设计文档 |
| `history/` | 历史设计文档归档（含 INDEX.md 漂移登记簿） |
| `inventory/` | 资产清单 |
| `plans/` | 规划文档 |
| `reports/` | 报告 |
| `superpowers/` | 技能/工具 |
| `visual-map/` | 可视化映射 |

---

## 7. 下一步推进计划

### 7.1 立即可做（无需 SP 起点）

| 任务 | 预计耗时 | 优先级 |
|------|----------|--------|
| 更新 `AGENTS.md` §4.3 测试基线 1025 → 1035 | 5 分钟 | 中 |
| 清理前端 `clonePreview` 未使用类型导出 | 10 分钟 | 低 |
| 重生成 12 份 `READING_REPORT_*.md` 中提及 7.5 改造、Option B 的影响 | 30 分钟 | 中 |

### 7.2 需 SP 起点后做

#### SP-1 状态中心并集
- **前置**：用户交代"状态中心当前长什么样、几个小模块、需合并内容"
- **预计实现**：基于起点回答，合并状态中心各小模块

#### SP-3 Demo 删除（建议驳回）
- **前置**：用户交代"Demo 页面当前真实行为、驳回后应变成什么样的系统门面"
- **预计实现**：将 Demo 改造为"系统门面"（产品介绍/导航/快速演示）

#### SP-4 知识库入口复活
- **前置**：用户交代"知识库入口原来是怎么回事、为什么搁置、复活后需提供什么功能"
- **预计实现**：复活知识库入口 UI + 后端 API

#### SP-5 塑料感补课
- **前置**：用户交代"塑料感指什么、补课内容有什么"
- **预计实现**：根据"塑料感"定义，补足相应逻辑/数据/界面

### 7.3 长期路线

| 阶段 | 内容 |
|------|------|
| **短期**（1-2周） | 完成 4 项 SP 任务；清理 L4/L5 文档漂移 |
| **中期**（1-2月） | shisi/ 模块解耦（legacy 路径拆分）；前端 19 页面统一设计语言 |
| **长期**（3月+） | 角色系统深度学习增强；多模态扩展；集群部署 |

---

## 8. 接手人入门路径（最小阅读集）

**15-20 分钟**了解 80% 项目状态：

1. `docs/README.md` — 文档体系入口（原全局状态报告已删除）
2. `HANDOFF_REPORT.md` — 交接重点（本文档）
3. `FEATURE_MAP.md` — 功能实现概览
4. `P1_BACKLOG.md` — 当前最紧急任务
5. `VISION.md` — 产品定位与技术栈

**第一周计划**：

| 时间 | 任务 |
|------|------|
| 第 1 天（前 2 小时） | 读上述 5 个总览类文档 |
| 第 1 天（剩余时间） | 读 `FEATURE_MAP.md` + `P1_BACKLOG.md` |
| 第 2 天 | 根据 SP 任务确认起点，开始 SP-1/SP-3/SP-4/SP-5 实施 |
| 第 3-4 天 | 按模块阅读感兴趣的 `READING_REPORT_*.md` |

---

## 9. 验证基线

- **测试基线**：实测 `pytest` **1033 passed, 1 skipped**（剥离后基线；原文档 1025 需更新）
- **代码规模**：357 个 Python 源文件（剥离了 `wechat_decrypt_source.py`；不含 `.venv`）
- **模块数**：14 个 Owner 模块 + shisi 核心 124 文件
- **核心路由数**：8 个 sub-routers 共 73 个端点（剥离后；原 74）
- **最近一次验证时间**：2026-08-27

---

## 10. 联系方式

- 远程仓库：`https://github.com/FOURTEEN1416/fourteen.git`
- 详细规则：见项目根 `AGENTS.md`
- 漂移登记簿：`docs/history/INDEX.md`
- 删除日志：`docs/DELETION_LOG.md`

---

**文档结束。**

**后续待办**：
1. 用户确认 4 项 SP 任务的"起点人话"（SP-1/3/4/5）—— 已部分确认（SP-3: 直接删除 Demo 页面，系统门面后续做；其余三项等起点人话）
2. 用户确认是否需要把剥离的 `wechat_decrypt_source.py` 等代码独立成独立子项目（git 保留历史）
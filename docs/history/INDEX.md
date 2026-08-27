# 产品意图演进索引 — 真值裁决表

> 创建：2026-08-24 | 维护者：随重大产品决策更新
> 用途：解决「设计文档多次迭代，哪版是正确的」问题。
>
> **裁决标准（冲突时按此优先级）：**
> 1. **代码实况**（以 `CODE_GRAPH.md` + 实际路由/端点为准）
> 2. **现行文档**（README.md v3.x / AGENTS.md / docs/P1_BACKLOG.md）
> 3. **历史文档**（本目录 `docs/history/`，仅供追溯意图）

---

## 演进时间线

### 📜 阶段一：单用户 AI 伴侣「十四」（2026-05-19）

**文档**：`history/2026-05-19-PROJECT_OVERVIEW.md`

原始开发目的：基于 LLM 的 AI 虚拟伴侣，情感模拟 + 三层记忆 + 主动交互 + 风格克隆，微信单用户接入。

| 设计要素 | 当前状态 |
|---------|---------|
| 情感引擎（8 级好感度阶梯） | ✅ 现行有效 |
| 三层记忆（工作/情景/语义） | ✅ 现行有效（shisi/memory/） |
| ASE 主动消息引擎 | ✅ 现行有效（proactive/） |
| 12 维风格分析 + LoRA 训练管线 | ⚠️ 部分取代：07-30 起 LoRA 本地训练移除，改为「本地提取→上传→服务器分析」（commit 3545643） |
| V1/V2 双版本架构（main.py / main_v2.py） | ⚠️ 已取代：07-28 双模式合并进 main.py（_init_mixin 唯一初始化真相源） |
| CowAgent 微信通道 | ⚠️ 已取代：现为 wechat_direct/ 直连方案 |

### ✅ 阶段二：多用户架构转向（2026-05-24）

**文档**：`history/2026-05-24-multi-user-design.md`

GirlfriendManager 按 user_id 隔离（记忆/情感/角色卡独立，LLM/安全/RAG/TTS 共享）。

**状态：✅ 现行架构，完全生效**（user_scheduler.py 生产运行中，多用户隔离是硬约束 AGENTS.md L3）。

### 📜 阶段三：v10 导航设计稿与差距分析（2026-05-29）

**文档**：`history/2026-05-29-design-vs-current-gap.md`

逐页对比设计稿与前端实现的差距清单（P0×3 / P1×5 / P2×3）。

| 当年差距项 | 当前状态 |
|-----------|---------|
| P0-1 SettingsSecurity 内容错位 | ✅ 已修复（现为真实内容安全面板） |
| P0-2 三层级动态侧边栏 | ❌ **用户裁决：不需要**（2026-08-24，架构已扁平化） |
| P0-3 面包屑导航 | ✅ 已实现（components/layout/Breadcrumb.tsx） |
| P1 状态中心丰富化（4 卡片+情绪分布+成就+趋势） | ⏳ 部分差距仍在（当前 3 卡片+最近记忆） |
| P1 创建角色缺实时预览/快速设置子 tab | ⏳ 差距仍在（当前为三创建方式 tab） |
| P1 微信控制台卡片 vs 表格布局 | ➖ 已被后续迭代自然演化，按现状为准 |

### ✅ 阶段四：品牌更名 + 认证体系（2026-06-01 ~ 06-04）

**文档**：`history/2026-06-04-HANDOFF.md`

- 品牌：「AI Girlfriend」→「唯一的你 / unique-you」，定位词「AI伙伴」；物理目录 ai-girlfriend 与 GitHub 仓库 fourteen.git 保留旧名
- 认证双体系：JWT 用户鉴权（管理控制台）+ X-API-Key 内部鉴权（服务间），**✅ 现行有效**
- P0 安全修复史（JWT_SECRET fail-fast、accessToken 内存闭包等）

**状态：✅ 有效，P0 修复均已在位。**

### ⭐ 阶段五：当前态（2026-08-24 生效）

**真值文件**：`AGENTS.md` v1.1（宪法）/ `README.md` v3.1.0 / `CODE_GRAPH.md`（08-01）/ `docs/P1_BACKLOG.md`（08-24 重写）/ `docs/FEATURE_MAP.md`（功能现状地图）

产品形态：**多用户微信陪伴系统**——扫码即用 + 19 页管理控制台 + 204 API 端点 + 1104 测试基线。

### 🏛️ 阶段六：文档治理体系建立（2026-08-26 生效）

**触发**：用户批复「仅批准文档治理」，要求严谨科学治理——追踪决策演变、筛选真决策、提炼终极状态，拒绝暴力删除/简单归档。

**产出三层治理体系**：

| 层 | 文件 | 职责 |
|----|------|------|
| 决策生死 | `docs/DECISION_LEDGER.md` | 时间轴总账 + C1-C4 裁决 + 挂起池 SP-1~8 |
| 终态合成 | `docs/VISION.md` | 既成事实基线 / 候选池 / 非目标 三分法 |
| 本文件 | `docs/history/INDEX.md` | 阶段叙事与漂移登记 |

**同批落盘动作**：ADR-0011/0013 标注 Superseded；L2 决策档案 ×10 加状态卡；L3 快照 ×4 加历史头；CODEMAPS×3 数字漂移声明；`.superpowers/brainstorm/create-role-schemes.html` 按 ADR-0012 处置——**归档为本目录**（保留三案决策链考古价值，非裸删）。

---

## 已知真值漂移登记簿

| 漂移描述 | 发现日期 | 状态 |
|---------|---------|------|
| README 引用不存在的 `.triad-navigation/` 与 `8-layer-code-map.md` | 08-24 | ✅ README 已修，历史地图见 `docs/history/` |
| CODE_GRAPH 称「19 页面全部注册路由」 | 08-24 | ⚠️ 实为 16 挂载 + 3 孤儿（UsersPage/UserWorkspace/BindingDetailPage），详见 FEATURE_MAP F 区 |
| progress-tracker / design-expectation 比赛文档过期 | 08-24 | ✅ 已删除（比赛信息源清理 commit 893a906） |
| `config/shisi.yaml` L148 `default_tts: "edge-tts"` 与 system.yaml `engine: "mimo-tts"` 及 reinit BREAKING 决策冲突 | 08-26 | ✅ **已修**（08-26 用户批准后改为 mimo-tts；reinit 六项验证欠账同步补验：4 项 API 层全绿 + 2 项部分验证）|
| CODEMAPS/INDEX「21 注册路由」、FRONTEND「19 含孤儿」、BACKEND 端点数偏差 | 08-26 | ✅ 三份头部均已加漂移声明，权威数字指向 CODE_GRAPH |
| `.superpowers/brainstorm/` 未按 ADR-0012 清除 | 08-26 | ✅ HTML 已归档至本目录（2026-06-29-create-role-brainstorm.html），原目录已删 |
| 「创建角色方案A未实施」「Demo 是可删残留」「知识库功能深埋」三项判断与代码实况不符 | 08-26 晚 | ✅ **前端 src 全文通读后全部修正**（FEATURE_MAP F 区/H 区重写）：方案A主体已在位；Demo=公开获客门面（登录页有直通入口）；知识库组件 KnowledgePreview 完整存在但零挂载。同时发现幽灵层：UsersPage/UserWorkspace/BindingDetailPage 三页互链无路由 + 6 个零引用 hooks（含 useEmotionTrend）。详见 DECISION_LEDGER SP 区修订 |
| AGENTS.md 测试基线「1025 Python 测试」与实收不符 | 08-26 | ⚠️ **pytest --collect-only 实测 1035 collected**（+10 漂移，后续新增测试未同步文档）；建议基线更新为 1035 Python + 79 前端 |
| 后端 Python 文件总量口径 | 08-26 | ✅ **全量普查澄清**：项目实际代码 ~358 py 文件（此前「19,598 个」口径被 .venv 第三方依赖 19,236 个污染）；核心 49 + 非核心 307 已全部穷举阅读，产出 docs/READING_REPORT_*.md 共 12 份 |

---

## 全库源码穷举阅读记录（2026-08-26 完成）

**覆盖**：除 .venv 外全部 Python 源码（358/358 = 100%）
**产出**：`docs/READING_REPORT_*.md` ×12（my_character / llm_provider / character_card / orchestrator / voice / persona_extractor / proactive_plugins / wechat_clone / memory_context_multimodal / security_observability / tools_utils_scripts_cache / api / shisi / tests_root）

**关键新发现摘要**：
1. `persona_extractor/web_enricher.py` — 网络人设增强四内容源架构（DirectScraper/AgentReach/Firecrawl/13平台Channels），SP 相关的「7.5 火爬虫」即此处 Enricher 实例化验证
2. `proactive/scheduler.py` — 两处时区 bug 修复痕迹在案（_local_now 强制 UTC+8、_is_quiet_hours 未取模历史）
3. `api/auth_jwt.py` — P0-2 修复在案：生产环境 JWT_SECRET<32 字符直接拒绝启动
4. `shisi/memory/legacy/` 与 `shisi/knowledge/legacy/` — docstring 双声明「legacy=历史命名仍在活跃使用」，与 AGENTS.md Owner Map 一致
5. `clone_training/wechat_decrypt_source.py` — 微信 4.x 数据库解密链路（密钥提取→增量解密→zstd 二进制解码），需管理员权限本机运行
6. LoRA 训练移除决策在 weclone_adapter/__init__.py 与 clone_training/__init__.py 双处文档化为「外接 API + RAG + 提示词注入」

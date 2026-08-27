# 项目交接报告

**项目**：unique-you — 唯一的你·十四 — 基于 LLM 的智能情感陪伴系统  
**版本**：v1.0（2026-08-27 交接版）  
**交接日期**：2026-08-27  
**交接人**：ai-girlfriend 项目协调者 (AI内部)  
**联系方式**：见 AGENTS.md §0 项目身份

---

## 1. 项目概况

### 1.1 产品形态
微信扫码即用的 LLM 智能情感陪伴系统，扫码登录后控制台调角色与语音。

### 1.2 核心能力（见 README.md）
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
- Python 3.10+ / React 19 / Vite 8 / TypeScript 6 / Tailwind 4 / Zustand 5
- 后端：FastAPI，Python 3.10+，PEP 8 + 类型注解
- 前端：React 19 Composition API + TypeScript 6
- 部署：Windows 优先，`start_all.cmd` / `deploy_ai_girlfriend.bat`

---

## 2. 本次交接已完成的工作

### 2.1 7.5 火爬虫授权改造 → Crawl4AI（已完成实测）

**问题**：原方案依赖 Firecrawl SDK，需要 FIRECRAWL_API_KEY 环境变量，属授权硬伤。

**方案**：改用 Crawl4AI 免授权库替代，核心改动如下：

| 文件 | 变更要点 |
|------|----------|
| `persona_extractor/web_enricher.py` | 替换 `FirecrawlSource` → `Crawl4AISource`，删除了对 `FIRECRAWL_API_KEY` 的依赖 |
| `scripts/enrich_persona_web.py` | 更新帮助文案，将 Firecrawl 相关 CLI 参数改为 Crawl4AI |
| `WebPersonaEnricher` 类 | 移除 `firecrawl_api_key` 参数，改用 `self.crawl4ai = Crawl4AISource()`，`_detect_sources()` 中将 `firecrawl` 改为 `crawl4ai` |

**实测结果**：
- `python scripts/enrich_persona_web.py --id test123 --urls "URL"`：✅ 成功抓取、写入知识库，source 标记为 `crawl4ai`（37.9KB 的 `web_enricher.py` 现已精简）
- `python scripts/enrich_persona_web.py --id test123 --all-sources`：✅ 正常运行，使用 bilibili_api / jina_reader 等其他源，crawl4ai source 已列出

**效果**：原本需要申请 Firecrawl 服务并配置 API Key 的功能，现在直接使用 Crawl4AI 免授权即可工作，彻底绕开了授权环节。

### 2.2 微信克隆功能彻底下线（Option B）（后端完成，前端进行中）

**问题**：微信克隆的解密程序必须在用户的电脑中进行，但这个程序被放到了云服务上，这是逻辑硬伤。且前端页面提供导入聊天记录的 JSON 文件功能。

**方案**（Option B - 彻底下线）：
- 删除了 `POST /api/clone/preview` 端点（云服务器上必然报错，前端已零调用）
- 删除了 `POST /api/training/extract` 中的 `wcf` 和 `wechat` 两个死选项，白名单精简为 `^(csv)$`，仅保留纯文件导入路径
- 前端 `CreateRole.tsx` 的 `WeChatCloneTab`：删除了“步骤 1：下载工具（三选一）”卡片，保留“步骤 2：在本地电脑运行工具提取数据”+“步骤 3：上传提取的数据到服务器分析”的完整流程

**文件变更**：
- `api/routers/clone_routes.py`：删除了 `ClonePreviewRequest` 类、`_build_clone_preview` 函数、`/api/clone/preview` 端点、`from pydantic import BaseModel, Field` 导入；保留了 `_build_clone_preview_from_conversations`（upload 端点仍用得着）和所有其他端点
- `frontend/src/pages/CreateRole.tsx`：删除了“步骤 1：下载工具（三选一）”整块 UI 卡片；保留了下面的“步骤 2/3”完整流程

**保留的核心功能**：
- `/api/clone/upload`：上传 JSON → StyleAnalyzer → 人设预览（生产路径）
- 前端 `WeChatCloneTab`：只要用户有 JSON 文件，直接点“选择 JSON 数据文件” → “上传并分析”即可

### 2.3 项目源码穷举阅读 100% 完成

- 穷举了项目内全部 358 个 Python 源文件（剔除了 .venv 19,236 个第三方依赖）
- 生成了 12 份 `docs/READING_REPORT_*.md` 结构化阅读报告 + 1 份 `docs/DOCUMENTATION_GOVERNANCE_REPORT.md` 综合治理报告
- 实测 `pytest --collect-only` 收集 1035 tests（基线 1025 已在 INDEX.md 登记漂移）

---

## 3. 待交接的 4 项 SP 任务（需要您提供起点）

以下 4 项任务需要您交代**“当前真实状态”**（人话），我将基于此继续完成后续实现。请逐条回复✅/❌/调整。

| 任务编号 | 任务名称 | 需要您交代的起点（人话） |
|----------|----------|---------------------------|
| **SP-1** | 状态中心并集 | 目前状态中心长什么样？有几个小模块？需要合并什么内容？ |
| **SP-3** | Demo 删除（建议驳回） | Demo 页面现在的“真实行为”是什么？驳回后应该变成什么样的“系统门面”？ |
| **SP-4** | 知识库入口复活 | 知识库入口原来是怎么回事？为什么被搁置？复活后需要提供什么功能？ |
| **SP-5** | 塑料感补课 | “塑料感”在该项目里指什么？补课内容目前有什么（或是空白）？ |

**请直接回复这四句人话**（格式示例：`1✅ 2调整 3❌ 4✅` 或分批确认），我基于此继续完成这四项任务。

---

## 4. 已知问题与技术债务

| # | 问题 | 影响范围 | 建议 |
|---|------|----------|------|
| L1 | `clone_routes.py` 中 `_build_clone_preview_from_conversations` 仍依赖 `clone_training.style_analyzer.StyleAnalyzer` 正常工作，但 `DecryptSource` 已被删除 | upload 端点仍正常工作 | 无影响，保持现状 |
| L2 | `web_enricher.py` 的 `Crawl4AISource` 搜索功能受外部 `r.jina.ai` 服务可用性影响 | 搜索模式可能因网络原因返回空结果 | 已在 `scripts/enrich_persona_web.py` 中有 bilibili_api / jina_reader 等多源兜底 |
| L3 | 前端 `CreateRole.tsx` 仍保留 `clonePreview` API 的 TypeScript 类型导出（未被任何组件调用） | 构建时可能产生未使用警告 | 下次重构可顺手清理 |

---

## 5. 下一步计划

1. **请回复四句 SP 任务的起点人话**（或表明想跳过其中若干项）
2. **根据批复结果**：
   - 完成 SP-1（状态中心并集）实现
   - 完成 SP-3（Demo 删除/驳回）
   - 完成 SP-4（知识库入口复活）
   - 完成 SP-5（塑料感补课）
3. **后续可选**：
   - 进一步清理前端 `clonePreview` 相关的 TypeScript 类型定义
   - 完善 `web_enricher.py` 的错误处理与降级策略
   - 更新 `AGENTS.md` 中的测试基线（1035 vs 1025）

---

**文档末尾**

如需查看更细节的代码 diff，请参考 `docs/READING_REPORT_*.md` 系列报告或 `docs/DOCUMENTATION_GOVERNANCE_REPORT.md`。本报告旨在为项目交接提供清晰的状态快照与下一步行动指引。
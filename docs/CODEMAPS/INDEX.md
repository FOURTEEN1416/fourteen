# 代码地图索引

> **✅ 2026-09-28 历遍修复批刷新**：三端一致 **`ffa6d68`**；GitHub CI 三轮全绿（`36333194107`/`36333947881`/`36334364744`）；测试口径 **2567 收集 / 2566 通过 / 1 跳过 / 0 失败**（41 卡；全量口径必含 `tests/core/`）+ 前端 **135**（徽章 **2701**）；端点 **229 APIRoute / 195 唯一路径 / 19 include_router**（`len(app.routes)`=233）；活跃 ADR **12** 不变；.py 总量 **477**；服务器 Linux 复跑 W3 域 54/54（本机 WAL flaky 归因闭环）。权威数字以 `CODE_GRAPH.md` **v3.8.25** 为准。09-27 刷新留档：⚠️ 当时 CI 红（backend 1 + E2E 4，已由 `dceb331` 根治）；测试口径 2565/2564/1/0 + 前端 135（徽章 2699）。更早刷新留档（09-20/21 全仓历遍与战役终局、ADR 11→12 补 ADR-0015）见 git 历史与 `docs/history/INDEX.md`。

**最近更新:** 2026-09-28
**项目版本:** 3.1.0
**项目规模:** **477** 个 Python 文件（模块 283 + 根级 2 + `scripts/` 18 + `tests/` 171 + `deploy/` 3；**2026-09-27 find 实测**；**不含** `frontend/` 与内嵌 `大创赛报名以及后期发展/`） + **~108** TS/TSX 文件 | 当前分支: `main`
**架构框架:** FastAPI (后端) + React/Vite (前端) + SQLite/ChromaDB (数据)

---

## 地图导航

| 地图 | 描述 | 适合谁 |
|------|------|--------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 整体架构概览，组件关系，数据流 | 新加入者，架构评审 |
| [BACKEND.md](BACKEND.md) | FastAPI 路由层，API 端点清单，认证系统 | API 开发者，后端开发者 |
| [FRONTEND.md](FRONTEND.md) | React 前端结构，页面路由，组件体系，状态管理 | 前端开发者，UI 设计师 |
| [DATABASE.md](DATABASE.md) | 数据模型，存储层，迁移 | 全栈开发者，数据工程师 |
| [MODULES.md](MODULES.md) | 后端 Python 业务模块（shisi/RAG/TTS/安全等） | 后端开发者，架构师 |

---

## 快速参考

### 技术栈

| 层 | 技术 | 版本/端口 |
|----|------|-----------|
| **前端** | React 19 + Vite 8 + TypeScript 6 + Tailwind CSS 4 | `:5173` |
| **后端** | Python ≥3.10 + FastAPI + Uvicorn | `:8000` |
| **数据库** | SQLite (主, aiosqlite) + ChromaDB (向量) | `data/users.db` |

### 项目目录结构（2026-09-19 实测）

```
unique-you/
├── api/                    # FastAPI 路由层 (45 py 文件：app_factory/run_api/22 routers/
│                           #   achievement_engine/database/auth/auth_jwt/deps/
#                           #   health_routes/main_routes/qrcode_store/websocket_server/state/...)
├── shisi/                  # DDD 核心域 (116 py 文件，v2 死模块删除后口径)
├── my_character/           # 情感引擎 (21 py 文件)
├── frontend/               # React 前端 SPA (17 pages / 13 api 模块 / 3 store)
├── orchestrator/           # 编排包 (9 文件：主类+init/stream mixin+session_locks+voice_detector
│                           #   +console_chat+tool_gate+context_budget)
├── persona_extractor/      # 人格提取 (13 文件)
├── tools/                  # 工具系统 (10 py 文件, 12 内置工具)
├── utils/                  # 公共工具 (11 文件：local_time/fallback_lines/affinity_state/
│                           #   reply_mode/async_utils/important_dates/...)
├── observability/          # 可观测性 (9 py 文件)
├── security/               # 安全模块 (4 模块 + __init__)
├── llm_provider/           # LLM 供应商接入 (5 py 文件)
├── voice/                  # MiMo TTS 语音合成 (5 模块 + __init__)
├── cache/                  # 缓存层 (LLM 缓存 + Redis)
├── character_card/         # 已删除（批6b 项10，零读者死码包，见 docs/DELETION_LOG.md）
├── clone_training/         # 克隆训练 (4 文件：清洗/提取/风格分析)
├── context/                # 上下文 (世界书)
├── memory_ext/             # 记忆扩展 (mem0 后端)
├── proactive/              # 主动消息 (6 文件：ase_engine/scheduler/frequency/reflection/
│                           #   reminder_delivery/ase_hub)
├── multimodal/             # 多模态 (image_attachment + multimodal_processor)
├── wechat_direct/          # 微信直连 (5 文件：每人独立通道 registry/paths/peer + connector)
├── plugins/                # 插件系统 (2 文件)
├── config/                 # YAML/JSON 配置 + config/characters/ 角色卡库（gitignore；现役 41 张）
├── tests/                  # 测试 (1611 Python 通过 + 4 跳过 / 98 前端)
└── docs/                   # 文档
    ├── CODEMAPS/           # ← 本目录
    ├── adr/                # 架构决策记录 (12 篇)
    ├── architecture/       # 架构文档
    ├── reports/            # 报告文档
    └── designs/            # 设计文档
```

> `weclone_adapter/` 已于 08-28 删除（克隆收敛为本地提取+JSON 上传，DECISION_LEDGER 08-28 行）。

### 关键指标（2026-09-21 实测，批6b 项10 死码清除后）

| 指标 | 值 |
|------|-----|
| API 端点 | **220 业务端点 / 186 唯一路径**（19 include_router + setup_shisi，`create_api_app` 内省实扫；⚠️ `len(app.routes)=224` 含 4 条框架路由；较 09-20 口径 +5 = agent-plane 路由组） |
| 前端页面 | 17 页面文件（全部挂载；幽灵层三页+DemoPage 已删） |
| 测试用例 | **1709 = 1611 Python 通过（4 跳过）+ 98 前端通过**（2026-09-21 全量审查修复战役终局实跑全绿，收集 1615；⚠️ 随 `config/characters/` 卡数浮动 = 2 × 卡数 + 7，现役 41 张） |
| 活跃 ADR | **12**（0001–0007 + 0011–**0015**；0008–0010 空缺未使用） |
| Fitness Functions | 17 (12 CI + 5 手动) |
| 总线因子 | 1 (唯一开发者: 默默) |

### 启动命令

```powershell
# 后端
python -m uvicorn api.run_api:app --reload --host 0.0.0.0 --port 8000

# 前端
npm run dev
```

---

## 演化阶段

| 阶段 | 时间 | 内容 |
|------|------|------|
| Phase 1-4 | ~2026-05-28 | 初始框架、安全模块、前端设计、架构重构 |
| Phase 5-8 | 2026-05-29~30 | 三体导航体系、前端重构、统一设计框架 |
| Phase 9-11 | 2026-05-31~06-01 | 全面审计、用户认证模块 (JWT)、LoginPage |
| Phase 12-13 | 2026-06-01 | Auth 全面验证 + main_routes.py 重构 (1313→95行) |
| Phase 14 | 2026-06-02 | P0 投产准备：邀请码系统 + 测试基线 625 + Vitest/Playwright |
| Phase 15 | 2026-06-03 | 品牌清洗「唯一的你」+ E2E 可靠性修复 |
| Phase 16 | 2026-06-03 | P0 全面修复 6/8 + CI 加固（all blocking）+ 品牌标签清洗 |

---

## 相关文档

- [ADR 目录](../adr/) — 架构决策记录 (**12 篇**，含 ADR-0015 系统提示词分层与按需注入)
- [架构文档](../architecture/) — 设计原则与知识图谱
- [报告文档](../reports/) — 研究与评审报告

# 代码地图索引

> **✅ 2026-09-20 全仓历遍刷新**：`orchestrator` 7→**9** 文件 / `proactive` 5→**6** / `shisi` 115→**116** / 新增 **`utils/`** 行；测试口径为 **1436 收集 / 1432 通过 / 4 跳过**（现役角色卡 **41 张**；⚠️ 卡目录被 gitignore、内容不随 git 复现，用例数 = 2 × 卡数 + 7）；活跃 ADR 11→**12**（补 **ADR-0015**）。历史漂移声明留档见 `docs/history/INDEX.md`。权威数字以 `CODE_GRAPH.md` **v3.8.16** 为准。

**最近更新:** 2026-09-20
**项目版本:** 3.1.0
**项目规模:** **389** 个 Python 文件（模块 283 + 根级 2 + `scripts/` 11 + `tests/` 92 + `deploy/` 1；**不含** `frontend/` 与内嵌 `大创赛报名以及后期发展/`） + **~108** TS/TSX 文件 | 当前分支: `main`
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
├── character_card/         # 角色卡 (6 文件)
├── clone_training/         # 克隆训练 (4 文件：清洗/提取/风格分析)
├── context/                # 上下文 (世界书)
├── memory_ext/             # 记忆扩展 (mem0 后端)
├── proactive/              # 主动消息 (6 文件：ase_engine/scheduler/frequency/reflection/
│                           #   reminder_delivery)
├── multimodal/             # 多模态 (image_attachment + multimodal_processor)
├── wechat_direct/          # 微信直连 (5 文件：每人独立通道 registry/paths/peer + connector)
├── plugins/                # 插件系统 (2 文件)
├── config/                 # YAML/JSON 配置 + config/characters/ 角色卡库（gitignore；现役 41 张）
├── tests/                  # 测试 (1432 Python 通过 + 4 跳过 / 98 前端)
└── docs/                   # 文档
    ├── CODEMAPS/           # ← 本目录
    ├── adr/                # 架构决策记录 (12 篇)
    ├── architecture/       # 架构文档
    ├── reports/            # 报告文档
    └── designs/            # 设计文档
```

> `weclone_adapter/` 已于 08-28 删除（克隆收敛为本地提取+JSON 上传，DECISION_LEDGER 08-28 行）。

### 关键指标（2026-09-20 实测）

| 指标 | 值 |
|------|-----|
| API 端点 | **215 业务端点 / 181 唯一路径**（18 include_router + setup_shisi，`create_api_app` 内省实扫；⚠️ `len(app.routes)=219` 含 4 条框架路由） |
| 前端页面 | 17 页面文件（全部挂载；幽灵层三页+DemoPage 已删） |
| 测试用例 | **1530 = 1432 Python 通过（4 跳过）+ 98 前端通过**（2026-09-20 全仓历遍实跑全绿，收集 1436；⚠️ 随 `config/characters/` 卡数浮动 = 2 × 卡数 + 7，现役 41 张） |
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

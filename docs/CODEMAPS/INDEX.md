# 代码地图索引

> **✅ 2026-11-14 文档清理批口径刷新**：本地 HEAD **`83e8321`**（v1.72；工作区含并行域在制品；`origin/main` **`528644e`**，本地领先 **6 提交**，未 push/未部署）；生产 **`cbb82aa`**（v1.70，2026-11-09 三端部署）。端点 **249 业务 APIRoute / 207 唯一路径 / `len(app.routes)`=253**（含 4 条框架路由；v1.69.1 内省实测，v1.70–v1.72 零新增）；Python tracked **671 文件 / 189,870 行**（磁盘剔除 `frontend/` 与内嵌 `大创赛报名以及后期发展/` 后 **673 文件**；含该目录共 833）；TS/TSX **147**；前端 **18 页面 / 18 API 模块 / 4 stores**；测试 **317 文件 / 5028 通过 + 2 跳过 / 收集 5030 / 0 失败**；前端测试 **283 用例 / 42 文件**；角色卡 **45**；活跃 ADR **12** 不变。权威数字以 `CODE_GRAPH.md` 为准。

**最近更新:** 2026-11-14
**项目版本:** 3.1.0
**项目规模:** **673** 个 Python 文件（磁盘剔除 `frontend/` 与内嵌 `大创赛报名以及后期发展/`，2026-11-14 实测；`git ls-files '*.py'` tracked **671**） + **147** TS/TSX 文件（frontend/src，2026-11-14 find 实测） | 当前分支: `main`
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

### 项目目录结构（2026-11-14 实测）

```
unique-you/
├── api/                    # FastAPI 路由层 (52 py 文件：app_factory/run_api/audit/27 routers/
│                           #   achievement_engine/database/auth/auth_jwt/deps/
#                           #   health_routes/main_routes/qrcode_store/websocket_server/state/...)
├── shisi/                  # DDD 核心域 (116 py 文件，v2 死模块删除后口径)
├── my_character/           # 情感引擎 (12 py 文件)
├── frontend/               # React 前端 SPA (18 pages / 18 api 模块 / 4 stores)
├── orchestrator/           # 编排包 (9 文件：主类+init/stream mixin+session_locks+voice_detector
│                           #   +console_chat+tool_gate+context_budget)
├── persona_extractor/      # 人格提取 (13 文件)
├── tools/                  # 工具系统 (13 py 文件, 17 内置工具·默认注册 14
│                           #   = config/system.yaml builtin_tools，2026-10-21 R3 批 +memory)
├── utils/                  # 公共工具 (31 文件：local_time/fallback_lines/affinity_state/
│                           #   reply_mode/async_utils/important_dates/topic_state(话题状态机三表
│                           #   唯一 owner)/stream_guard(主链增量出站守卫)/...)
├── observability/          # 可观测性 (8 py 文件)
├── security/               # 安全模块 (4 模块 + __init__)
├── llm_provider/           # LLM 供应商接入 (4 py 文件)
├── voice/                  # MiMo TTS 语音合成 (7 模块 + __init__)
├── cache/                  # 缓存层 (LLM 缓存 + Redis)
├── character_card/         # 已删除（批6b 项10，零读者死码包，见 docs/DELETION_LOG.md）
├── clone_training/         # 风格提取（训练管线：数据清洗/风格提取/风格分析；目录名保留 clone_training）
├── context/                # 上下文 (世界书)
├── memory_ext/             # 已删除（2026-09-28 历遍批 W18 整包出库，第二套 RAG 记忆真源，见 docs/DELETION_LOG.md）
├── proactive/              # 主动消息 (14 文件：ase_engine/ase_hub/scheduler/frequency(零 token 骰子闸)/
│                           #   reflection/reminder_delivery/llm_proactive/runtime_plane/runtime_assembly
│                           #   /topic_recall(parked 复提候选))
├── multimodal/             # 多模态 (image_attachment + multimodal_processor)
├── wechat_direct/          # 微信直连 (6 文件：每人独立通道·D16 谁扫码谁聊 registry/paths + connector)
├── plugins/                # 插件系统 (2 文件)
├── config/                 # YAML/JSON 配置 + config/characters/ 角色卡库（gitignore；本地实测 45 张
│                           #   = 41 策展卡 + 2026-11-08 用户自建 4 张「陈末」等）
├── tests/                  # 测试 (test_*.py **317** 文件；后端 5028 通过 + 2 跳过 / 收集 5030；前端 283 用例/42 文件)
└── docs/                   # 文档
    ├── CODEMAPS/           # ← 本目录
    ├── adr/                # 架构决策记录 (12 篇)
    ├── architecture/       # 架构文档
    ├── reports/            # 报告文档
    └── designs/            # 设计文档
```

> `weclone_adapter/` 已于 08-28 删除（克隆收敛为本地提取+JSON 上传，DECISION_LEDGER 08-28 行）。

### 关键指标（2026-11-14 复测）

| 指标 | 值 |
|------|-----|
| API 端点 | **249 业务端点 / 207 唯一路径**（v1.69.1 `create_api_app` 内省实测，AI_GF_ENV=dev；v1.70–v1.72 零新增；⚠️ `len(app.routes)`=253 含 4 条框架路由） |
| 前端页面 | 18 页面文件（全部挂载；`/templates` `/psych` 已重定向） |
| 测试用例 | 前端 **283 用例 / 42 测试文件** + 后端 test_*.py **317 测试文件**（5028 通过 + 2 跳过 / 收集 5030 / 0 失败，v1.72 实测） |
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

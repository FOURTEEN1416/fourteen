# 代码地图索引

> **✅ 2026-10-11 次级文档对齐刷新**：三端一致 **`aea3cd0`**（2026-10-11 SEC-15 根治批，GitHub CI run `38109894676` 全绿 9/9 job，`gh run view` 复核 success）；测试口径 **2874 收集 / 2873 通过 / 1 跳过 / 0 失败**（2026-10-09 攻击面扫描批五分块实跑，`LOG.md` 10-09 条；全量口径必含 `tests/core/`）+ 前端 **182**（30 文件，vitest ^5.0.3，徽章 **3055**）；端点 **229 APIRoute / 195 唯一路径 / 19 include_router**（`len(app.routes)`=233；2026-10-11 内省复测不变）；活跃 ADR **12** 不变；.py 总量 **495**。权威数字以 `CODE_GRAPH.md` **v3.8.28** 为准。更早刷新（09-27/09-28/09-29）见 git 历史与 `docs/history/INDEX.md`。

**最近更新:** 2026-10-11
**项目版本:** 3.1.0
**项目规模:** **495** 个 Python 文件（模块 284 + 根级 2 + `scripts/` 17 + `tests/` 189 + `deploy/` 3；**2026-10-11 find 实测**；**不含** `frontend/` 与内嵌 `大创赛报名以及后期发展/`） + **128** TS/TSX 文件（frontend/src） | 当前分支: `main`
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
├── api/                    # FastAPI 路由层 (48 py 文件：app_factory/run_api/24 routers/
│                           #   achievement_engine/database/auth/auth_jwt/deps/
#                           #   health_routes/main_routes/qrcode_store/websocket_server/state/...)
├── shisi/                  # DDD 核心域 (116 py 文件，v2 死模块删除后口径)
├── my_character/           # 情感引擎 (12 py 文件)
├── frontend/               # React 前端 SPA (19 pages / 15 api 模块 / 3 store)
├── orchestrator/           # 编排包 (9 文件：主类+init/stream mixin+session_locks+voice_detector
│                           #   +console_chat+tool_gate+context_budget)
├── persona_extractor/      # 人格提取 (13 文件)
├── tools/                  # 工具系统 (13 py 文件, 12 内置工具)
├── utils/                  # 公共工具 (19 文件：local_time/fallback_lines/affinity_state/
│                           #   reply_mode/async_utils/important_dates/...)
├── observability/          # 可观测性 (8 py 文件)
├── security/               # 安全模块 (4 模块 + __init__)
├── llm_provider/           # LLM 供应商接入 (4 py 文件)
├── voice/                  # MiMo TTS 语音合成 (7 模块 + __init__)
├── cache/                  # 缓存层 (LLM 缓存 + Redis)
├── character_card/         # 已删除（批6b 项10，零读者死码包，见 docs/DELETION_LOG.md）
├── clone_training/         # 克隆训练 (4 文件：清洗/提取/风格分析)
├── context/                # 上下文 (世界书)
├── memory_ext/             # 已删除（2026-09-28 历遍批 W18 整包出库，第二套 RAG 记忆真源，见 docs/DELETION_LOG.md）
├── proactive/              # 主动消息 (10 文件：ase_engine/ase_hub/scheduler/frequency/reflection/
│                           #   reminder_delivery/llm_proactive/runtime_plane/runtime_assembly)
├── multimodal/             # 多模态 (image_attachment + multimodal_processor)
├── wechat_direct/          # 微信直连 (5 文件：每人独立通道 registry/paths/peer + connector)
├── plugins/                # 插件系统 (2 文件)
├── config/                 # YAML/JSON 配置 + config/characters/ 角色卡库（gitignore；本地实测 42 张
│                           #   = 41 default 策展卡 + 1 张 user_id=1 林挽夏运行时克隆副本 99c89c39，
│                           #   2026-10-09 落盘；与 LOG「41 卡」差异注记见 CODEMAPS/DATABASE.md）
├── tests/                  # 测试 (2873 Python 通过 + 1 跳过 / 182 前端；189 个 .py 文件)
└── docs/                   # 文档
    ├── CODEMAPS/           # ← 本目录
    ├── adr/                # 架构决策记录 (12 篇)
    ├── architecture/       # 架构文档
    ├── reports/            # 报告文档
    └── designs/            # 设计文档
```

> `weclone_adapter/` 已于 08-28 删除（克隆收敛为本地提取+JSON 上传，DECISION_LEDGER 08-28 行）。

### 关键指标（2026-10-11 复测）

| 指标 | 值 |
|------|-----|
| API 端点 | **229 业务端点 / 195 唯一路径**（111 GET / 82 POST / 16 PUT / 20 DELETE；19 include_router + setup_shisi，`create_api_app` 内省实扫 2026-10-11 复测一致；⚠️ `len(app.routes)=233` 含 4 条框架路由） |
| 前端页面 | 19 页面文件（全部挂载；2026-09-28 新增 TemplatesPage / SettingsAccount） |
| 测试用例 | **3055 = 2873 Python 通过（1 跳过）+ 182 前端通过**（2026-10-09 攻击面扫描批全量五分块实跑全绿，收集 2874，见 `LOG.md` 10-09 条；前端 182/182 为 2026-10-11 SEC-15 根治批复验；⚠️ 随 `config/characters/` 卡数浮动 = 2 × 卡数 + 13，本地实测 42 张 = 41 策展 + 1 运行时克隆副本，差异注记见 `DATABASE.md`） |
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

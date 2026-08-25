# 代码地图索引

> **⚠️ 数字漂移声明**（2026-08-26 治理标注）：本文「21 注册路由」口径与实测不符——实际为 **16 挂载 + 3 孤儿页面**（UsersPage/UserWorkspace/BindingDetailPage）。权威数字以 `CODE_GRAPH.md` 为准，详见 `docs/history/INDEX.md` 漂移登记簿。

**最近更新:** 2026-08-01
**项目版本:** 3.1.0
**项目规模:** ~356 Python 文件 + ~106 TS/TSX 文件 | 当前分支: `main`
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
| **Node** | D:\node.exe v24.14 | 前端构建 |
| **Bun** | v1.3.12 | 前端包管理/测试 |

### 项目目录结构

```
unique-you/
├── api/                    # FastAPI 路由层 (36 文件)
├── shisi/                  # 核心业务逻辑 (96 文件)
├── frontend/               # React 前端 SPA
├── config/                 # YAML/JSON 配置
├── security/               # 安全模块 (5 文件)
├── llm_provider/           # LLM 供应商接入 (6 文件)
├── voice/                  # TTS 语音合成 (11 文件)
├── persona_extractor/      # 人格提取 (~14 文件)
├── tools/                  # 工具系统 (10 文件, 12 内置工具)
├── orchestrator/           # 编排包 (optimized_orchestrator.py + init/stream mixin, 5 文件)
├── cache/                  # 缓存层 (LLM 缓存 + Redis)
├── character_card/         # 角色卡 (6 文件)
├── clone_training/         # 克隆训练 (6 文件)
├── context/                # 上下文 (世界书)
├── memory_ext/             # 记忆扩展 (mem0 后端)
├── proactive/              # 主动消息 (5 文件)
├── observability/          # 可观测性 (9 文件)
├── plugins/                # 插件系统 (2 文件)
├── multimodal/             # 多模态 (2 文件)
├── wechat_direct/          # 微信直连 (2 文件)
├── weclone_adapter/        # 微信克隆适配 (3 文件)
├── my_character/           # 我的角色 (自定义角色)
├── tests/                  # 测试 (1025 Python 测试通过)
└── docs/                   # 文档
    ├── CODEMAPS/           # ← 本目录
    ├── adr/                # 架构决策记录
    ├── architecture/       # 架构文档
    ├── reports/            # 报告文档
    └── designs/            # 设计文档
```

### 关键指标

| 指标 | 值 |
|------|-----|
| API 端点 | 204 (17 include_router, create_api_app 实扫) |
| 前端页面 | 19 页面文件 / 21 注册路由 |
| 测试用例 | 1025 Python 测试 + 79 前端测试 = 1104 |
| 活跃 ADR | 10 |
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

- [ADR 目录](../adr/) — 架构决策记录 (10 篇)
- [架构文档](../architecture/) — 设计原则与知识图谱
- [报告文档](../reports/) — 研究与评审报告

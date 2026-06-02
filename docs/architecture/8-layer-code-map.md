# 8 层代码地图

> 基于 Triad Navigation 方法论，描述项目当前架构状态。
> L1-L4 自动派生，L5-L8 季度审查。

---

## L1：项目表面

| 项目 | 状态 | 备注 |
|------|------|------|
| README | ✅ 已更新 | 2026-05-27 重写：新增架构文档入口、MiMo TTS、实际目录树 |
| LICENSE | ✅ MIT | `LICENSE` 文件存在 |
| CI badge | 🟡 待配置 | CI 已完善（7 blocking jobs + schedule），badge 待加 |
| 贡献指南 | ❌ 无 | 单人项目，暂不需要 |

## L2：结构架构

```
unique-you/                          ← 根（Python 后端 + React 管理台）
├── api/                                ← FastAPI 路由层
│   ├── routers/                        ← 子路由（领域拆分）
│   ├── auth.py                         ← 认证
│   ├── deps.py                         ← 依赖注入
│   ├── main_routes.py                  ← 主路由（~75 端点）
│   └── websocket_server.py             ← WebSocket
├── shisi/                              ← 旧架构（十四模块）
│   ├── api/                            ← 旧 API 路由（49 端点）
│   │   └── v2/                         ← 新版路由（9 端点）
│   ├── character/                      ← 角色管理
│   ├── affinity/                       ← 亲密度
│   ├── emotion_stage/                  ← 情感阶段
│   ├── memory/                         ← 记忆（收藏/转发）
│   ├── sticker/                        ← 表情包
│   ├── voice/                          ← 语音
│   ├── wechat/                         ← 微信集成
│   ├── storyline/                      ← 剧情线
│   ├── knowledge/                      ← RAG 知识库
│   ├── stats/                          ← 统计
│   ├── vital_signs/                    ← 生理指标
│   └── infrastructure/                  ← 持久化（SQLite）
├── config/                             ← 14 个配置文件
├── security/                           ← 安全模块（内容过滤/PII/加密）
├── tools/                              ← 工具系统
├── voice/                              ← 语音合成引擎
├── my_character/                       ← 情感引擎
├── observability/                      ← 可观测性（日志/指标/链路）
├── rag_engine/                         ← RAG 引擎
├── proactive/                          ← 主动消息
├── plugins/                            ← 插件系统
├── frontend/                           ← React 管理台 UI
│   ├── src/
│   │   ├── api/                        ← 按域拆分的 API 客户端
│   │   ├── pages/                      ← 22 页面
│   │   ├── components/                 ← 共享组件
│   │   ├── hooks/                      ← React Query hooks
│   │   ├── stores/                     ← Zustand stores
│   │   └── types/                      ← TypeScript 类型
│   └── package.json
├── tests/                              ← 37 测试文件 / ~525 测试函数
└── main.py                             ← 主入口
```

## L3：行为架构（数据流）

```
用户/微信 → main.py
        → orchestrator.py (12步流程)
            → 安全层 (ContentSafetyFilter + PIIAnonymizer + InjectionDetector)
            → 情感引擎 (EmotionEngine)
            → LLM 网关 (MultiProviderGateway)
            → 记忆系统 (MemoryExtractor)
            → 工具系统 (ToolDispatcher)
            → 响应生成

API 请求 → FastAPI (app_factory.py)
        → 认证 (X-API-Key)
        → 路由 (main_routes / routers)
        → 依赖 (deps.py 全局单例)
        → 响应 (JSON / SSE / Streaming)

前端管理台 → React Query (hooks/)
          → api/ (client.ts 编排层)
          → 后端 API
          ← 数据缓存 + 自动失效
```

## L4：配置与环境

| 配置源 | 优先级 | 说明 |
|--------|--------|------|
| `.env` 文件 | 最高 | API Keys / 环境开关（已 gitignored） |
| 环境变量 | 高 | 运行时覆盖 |
| `config/system.yaml` | 中 | 主配置 |
| `config/shisi.yaml` | 低 | 旧版配置（逐步废弃） |
| `config/llm_providers.json` | 中 | LLM 供应商参数 |

依赖：
- Python 3.10+：`pyproject.toml`（49 项依赖）
- Node/TypeScript：`frontend/package.json`（管理台 UI）

## L5：风险热点

| 热点 | 级别 | 说明 |
|------|------|------|
| 三套 API 并行 | 🟡 | shisi v1->统一 API 迁移接近完成（ADR-0002） |
| 两套 YAML 配置重叠 | 🟡 | 优先级规则已定义（system.yaml 优先） |
| 前端 0 测试 | 🔴 | 管理台改动无法自动验证（TS 类型检查可部分缓解） |
| 无集成/E2E 测试 | 🔴 | 后端 525 测试全为单元级 |
| pyproject.toml vs requirements.txt 版本冲突 | 🟡 | httpx 版本不一致 |
| CI 刚建立 | 🟢 | 已完善：7 个 blocking job + 周期层 schedule |
| Bus Factor = 1 | 🟡 | 已建立缓解计划（`bus-factor.md`） |

## L6：演化历史

| 时间段 | 事件 |
|--------|------|
| 2025 Q4 | 初始版本：微信直连 + 基本 LLM 对话 |
| 2026 Q1 | V2 重构：可观测性 + 安全层 + 工具系统 + RAG |
| 2026 Q2 | V3 优化：并行初始化 + 结构化日志 + 双编排器模式 |
| 2026-05 | 前端 API 拆分（arch/client-split-4A） |
| 2026-05 | 全面审计（前后端 7 维度扫描） |
| 2026-05 | 三体导航体系建立（ADR/原则/CI/FF） |

## L7：归属分工

| 模块 | 负责人 | Bus Factor |
|------|--------|-----------|
| 全项目 | 默默 | 1（唯一开发者） |

说明：单人项目，无 Code Owner 体系。建议至少找一个模块让 AI Agent 能独立维护。

## L8：约定标准

| 维度 | 标准 |
|------|------|
| Python 风格 | ruff（select=E,F,W,I,N,UP,B,SIM） |
| 类型检查 | mypy（warn_return_any, no_implicit_optional） |
| 前端框架 | React 18 + TypeScript |
| 样式 | 纯 Tailwind（无自定义 CSS 类） |
| 状态管理 | Zustand（UI 状态）+ React Query（服务端数据） |
| 提交规范 | 常规 commit message |
| ADR 格式 | MADR（`docs/adr/ADR-{编号}-{标题}.md`） |
| 测试框架 | pytest（后端）/ 无（前端待建） |

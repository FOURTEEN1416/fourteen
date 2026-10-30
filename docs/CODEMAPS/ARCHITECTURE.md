# 架构地图

**最近更新:** 2026-10-30
**演进阶段:** Phase 14 (投产准备) → Phase 15 (品牌清洗) → Phase 16 (P0 全面修复 + CI 加固) → Phase 17 (全仓性能/正确性扫描) → Phase 18 (每人独立微信通道) → Phase 19 (D16 通道语义对齐：谁扫码谁聊、一人一 bot `MAX_CHANNELS_PER_USER=1`、扫码人守卫、好友自选角色退役) → Phase 20 (聊天质量根治线：R4 真流式 + v1.57 拟人化 + v1.58 话题状态机 + v1.59 四项跟进批含 4C 骰子闸) → Phase 21 (前端体验批1–7 本地链（代码尾 `9815c27`，其后文档收口/验收修正/e2e 基础设施提交；精确链尾以 `git log -1` 为准）：admin 数据面/首日旅程/组件库收编/设计 token/用户列表排序全页化)
**数据口径:** 端点与模块数均为 **2026-10-30 亲测**（`create_api_app()` 内省 AI_GF_ENV=dev + find 文件扫描），非文档估算值。**✅ 2026-10-30 复测**：端点 **249 APIRoute / 205 唯一路径 / `len(app.routes)`=253**（118 GET / 90 POST / 18 PUT / 23 DELETE；批6 净增 14，批7 零新端点）；api **51** 文件 / **26 routers**（+announcement_routes·批4）、前端 **20 pages / 16 api 模块 / 6 stores**、orchestrator 主类 **2464 行**。生产 @ **`c3ede77`**（批1–7 本地链未部署）。详见 `CODE_GRAPH.md`（10-28 口径 235/197 与更早见 git 历史）

---

## 系统总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户层 (用户/微信)                        │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                   前端 SPA (React + Vite)                        │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ 页面组件     │  │ API 客户端    │  │ 状态管理              │  │
│  │ (20 pages)  │──│ (16 modules) │──│ (Zustand stores x6)  │  │
│  └─────────────┘  └──────┬───────┘  └───────────────────────┘  │
└───────────────────────────┼─────────────────────────────────────┘
                            │ /api/* (Vite proxy → :8000)
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                    FastAPI 路由层 (:8000)                        │
│                                                                  │
│  ┌──────────────┐  ┌────────────────┐  ┌────────────────────┐  │
│  │ 26 路由模块  │  │ 249 endpoints  │  │ 认证层             │  │
│  │ (api/routers)│  │ (create_api_app│  │ JWT 优先 + X-API-Key│  │
│  │ + shisi/api  │  │ │  10-30 实扫) │  │ （JWT=用户，Key=机器）│  │
│  └──────┬───────┘  └───────┬────────┘  └────────────────────┘  │
└─────────┼──────────────────┼───────────────────────────────────┘
          │                  │
┌─────────▼──────────────────▼───────────────────────────────────┐
│                  核心业务层 (Python 模块)                        │
│                                                                  │
│  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐  │
│  │ shisi/   │ │ LLM     │ │ 安全     │ │shisi/    │ │shisi/ │  │
│  │(116 文件) │ │ Provider│ │ (5 文件)  │ │knowledge │ │memory │  │
│  └──────────┘ └─────────┘ └──────────┘ └──────────┘ └───────┘  │
│  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐  │
│  │ TTS 语音 │ │ 人格提取 │ │ 工具系统  │ │ 编排器    │ │ 缓存  │  │
│  └──────────┘ └─────────┘ └──────────┘ └──────────┘ └───────┘  │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ proactive / plugins / multimodal / clone_training ...   │    │
│  └────────────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────┐
│                    数据层                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ SQLite       │  │ ChromaDB     │  │ 文件系统             │  │
│  │ (主存储)      │  │ (向量/RAG)    │  │ (角色卡/知识库)       │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

> **D16 通道语义（2026-10-12）**：微信通道「谁扫码谁聊、一人一 bot」——注册用户扫码绑定 bot，bot 即其本人微信内的一对一角色化身；**非扫码人消息拒绝**，角色在网页控制台自由切换（`MAX_CHANNELS_PER_USER=1`）。
>
> **话题状态机（v1.58，2026-10-27/28，默认开启）**：确定性状态机每轮先行（`utils/topic_state.py` 三表 `topic_state/topic_plan/topic_meta` 唯一 owner）+ 稀疏 LLM judge 每 4 轮一次（8s 硬超时、失败保留原状态）；主链三接线：prompt 注入状态块（`render_topic_state_block` ≤160 字）→ `update_after_turn`（`optimized_orchestrator.py:1805`）→ 满轮触发 `run_llm_judge`（`:1785`）；parked 话题 ≥6h 经三重闸（≥6h+非静默+judge allow）由 `proactive/topic_recall.py` 复提；主动 `topic_plan` 全周期（决策线 skip 也存 `llm_proactive.py:875` → `scheduler.py` 到点 take → 发送成功才消费）。回滚走管理端显式 `topic_state.enabled=false`（关闭态零 DB）。
>
> **零 token 骰子闸（4C，2026-10-28，默认关闭+dry_run 默认开）**：主动消息掷骰先于 LLM——`proactive/frequency.py` 纯函数 owner（`evaluate_dice_gate`），`scheduler.py:1580` live 决策前判定；配置走 `scheduler_config.json` 的 `dice_gate` 块（管理端 `/api/proactive/config` GET/POST 承载，无独立端点）；回退 `DICE_GATE_PRODUCTION_WIRED=False`。`scheduler.py` 的接线已由用户裁决接受为例外。

---

## 编排器架构

> **注:** 编排逻辑位于 `orchestrator/` 包，包含 **9 个文件**（含 `__init__.py`）：
> - `optimized_orchestrator.py` (**2464 行**，2026-10-30 wc 实测) — 主类 `OptimizedOrchestrator`：`__init__` / 会话锁 / `_prepare_context` / `process_message` / `_after_process` / `health_check`；**v1.58 话题状态机三接线**：`get_topic_snapshot`（`:1156`，prompt 状态块）/ `run_llm_judge`（`:1785`，满 4 轮稀疏触发）/ `update_after_turn`（`:1805`，每轮确定性转换）
> - `_init_mixin.py` (**589 行**，2026-10-30 wc 实测) — `_InitPhasesMixin`：`initialize` 直接调用 8 个 `_init_*` 阶段；其中 `_init_memory_and_rag` 再级联 `_init_ase_and_scheduler` / `_init_tools` / `_init_rag`，共 11 个阶段方法
> - `_stream_mixin.py` (**607 行**，2026-10-30 wc 实测) — `_StreamPipelineMixin`：**按句边真流式下发**（R4，逐 token 喂 `utils.stream_guard.IncrementalReplyGuard`，完整句立即下发、不完整尾巴永不下发，流末 `_finalize_reply` 唯一全文终裁；`chat.stream_mode: buffered` 为旧路径回滚开关）
> - `session_locks.py` (175行) — 会话锁管理（`SessionLockManager`）
> - `voice_detector.py` (108行) — 语音活动检测
> - `console_chat.py` (134行) — 控制台聊天通道
> - **`tool_gate.py` (387行，09-20)** — 三级工具意图管线（L0 晋级线 / L1 LLM 终审 / 防假承诺）+ 工具结果 untrusted 信封
> - **`context_budget.py` (666行，09-20)** — 上下文预算与去重、跨会话尾巴信封、工具结果注入位次
>
> `OptimizedOrchestrator` 继承 `_InitPhasesMixin` + `_StreamPipelineMixin`，通过 `self.components` 共享状态。公共 API 100% 兼容。
>
> `tools/` 模块提供 17 个内置工具（BaseTool 子类，`grep -E '^class .*BaseTool' tools/builtin/*.py`
> 实数 17），默认注册 **14** 个 = `config/system.yaml` `builtin_tools`（weather / search /
> calendar / calculator / set_reminder / query_reminders / manage_reminder /
> time_awareness / character_card / update_user_profile / remember_facts /
> forget_facts / query_profile / **memory**——2026-10-21 R3 批启用，`_init_mixin.py:370`
> 按 `name in enabled_tools` 注册；scheduler / web_summary / image_gen 已实现未默认注册），
> 由编排器在流水线中调度执行。
>
> `cache/` 模块（`llm_cache.py` + `redis_client.py`）提供 LLM 响应缓存层。

---

## 数据流

### 用户请求流 (聊天)

```
微信用户消息
  → wechat_direct/wechat_connector.py（扫码登录 + 收发，指数退避重连；D16 谁扫码谁聊——扫码人即用户本人，非扫码人消息拒绝，角色控制台自由切换）
    → orchestrator (optimized_orchestrator.py, process_message 流水线)
        1. 安全过滤器 (ContentSafety)
        2. PII 匿名化 (PIIAnonymizer)
        3. 提示注入检测 (PromptInjection)
        4. 情感引擎 (EmotionEngine)
        5. LLM 路由 (MultiProviderGateway，默认链 agnes→zhipu→xunfei→baidu)
        6. 记忆提取 (MemoryExtractor)
        7. 工具调度 (ToolDispatcher)
        8. RAG 检索（角色知识库 BM25，top_k=8 + 查询扩展）
        9. 响应生成（R4 按句边真流式：完整句过 IncrementalReplyGuard 即下发，未发余量流末 _finalize_reply 唯一全文终裁）
        10. 人格注入
        11. 消息记录
        12. 响应格式化
        ↳ v1.58 话题状态机（每轮）：update_after_turn 确定性转换（:1805）→ prompt 注入
          render_topic_state_block ≤160 字（:1156）→ 满 4 轮 run_llm_judge 稀疏修正（:1785，
          8s 硬超时失败保留原状态）
  → 回复 (+ 可选 MiMo TTS 语音) → 微信发出
```

> **主动线（决策与生成分离）**：`proactive/llm_proactive.py` 决策（skip 也存
> `topic_plan`，`:875`）→ `scheduler.py` 到点 take → 发送成功才消费；决策前先过
> **零 token 骰子闸**（`frequency.py::evaluate_dice_gate`，`scheduler.py:1580`，默认关闭）；
> parked 话题复提候选由 `proactive/topic_recall.py` 组装（三重闸：≥6h + 非静默 + judge allow）。

> **注**：聊天主链路在微信侧。控制台前端无聊天页（chatStore/api/chat.ts 已于
> 09-17 死代码清洗删除）；`/api/chat/*` 11 端点保留供 API 消费。

### 管理后台流

```
管理员
  → 前端 AdminInvitesPage → admin.ts API → GET/POST /api/admin/*
    → FastAPI admin_routes.py（**8 端点**：users CRUD + global-view×2 + audit；批7 NEW-22
      用户列表 `sort`/`sort_dir` 白名单排序全页化）
      → JWT 验证 (admin role)
        → 邀请码 CRUD / 用户管理 / 操作员审计 / 全局运维视图
```

### 认证流

```
用户
  → LoginPage → auth.ts API → POST /api/auth/login
    → auth_routes.py → auth_jwt.py (bcrypt + JWT)
      → authStore (Zustand) 保存 token
        → AuthGuard 保护路由
```

---

## 安全架构

```
┌───────────────────────────────────────────────────┐
│                  安全层                            │
│                                                    │
│  外部网关: X-API-Key (内部服务间认证)               │
│  路由层:   JWT Bearer Token (用户认证)              │
│  应用层:   ContentSafety                           │
│            PIIAnonymizer                           │
│            PromptInjection                         │
│            Encryption                              │
│  数据层:   bcrypt 密码哈希                          │
│            JWT 签名 (HS256)                        │
└───────────────────────────────────────────────────┘
```

---

## 前后端分离

```
前端 (React SPA)                   后端 (FastAPI)
┌────────────────┐               ┌──────────────────┐
│ pages/         │               │ /api/auth/*       │
│ components/    │── JSON/SSE ──▶│ /api/chat/*       │
│ hooks/         │               │ /api/characters/* │
│ (React Query)  │◀─────────────│ /api/storyline/*  │
│ api/           │               │ /api/admin/*      │
│ (axios client) │               │ /api/voice/*      │
└────────────────┘               └──────────────────┘
```

**关键原则:**
- 前端只做 UI 渲染 + 状态管理
- 所有业务逻辑在后端
- 前端使用 TanStack Query 管理服务端状态
- Zustand 管理客户端状态

---

## 路由层级

```
全局 (global)                     角色 (role)
┌────────────┐                   ┌───────────────────┐
│ /intro     │                   │ /roles            │
│ /wechat    │                   │ /templates        │
│ /settings  │                   │ /roles/create     │
│ /login     │                   │ /roles/:roleId/   │
│ /psych     │                   │   settings[/:tab] │
│ /help      │                   │ /roles/:roleId/   │
│ /admin/*   │                   │   status          │
└────────────┘                   │ /roles/:roleId/   │
                                 │   storyline       │
                                 └───────────────────┘
```

> **注:** 路由已从 `/users/:userId/roles/:roleId/...` 重构为 `/roles/:roleId/...`，
> 移除了用户层级嵌套，角色直接挂在根路径下。`/demo` 已删（08-28 D1 裁决），
> 公开门面为 `/intro`（SP-11 产品介绍页）；`/` 按登录态分流 `/wechat` 或 `/intro`；
> `/help` 为批4 V2-4 教学导引页（HelpGuidePage，13 模块卡+进度持久化）；`/templates` 为 W12 角色模板页。

---

## 关键依赖

| 依赖 | 用途 | 版本约束 |
|------|------|----------|
| FastAPI | Web 框架 | ≥0.100 |
| SQLAlchemy | ORM | ≥2.0 |
| python-jose | JWT | ≥3.3 |
| bcrypt | 密码哈希 | ≥4.0 |
| openai | LLM 客户端 | ≥1.0 |
| ChromaDB | 向量数据库 | 嵌入版 |
| React | 前端框架 | 19.x |
| TanStack Query | 服务端状态 | 5.x |
| Zustand | 客户端状态 | 5.x |
| Tailwind CSS | 样式 | 4.x |
| Vite | 构建工具 | 8.x |
| TypeScript | 类型系统 | 6.x |

---

## 相关地图

- [BACKEND.md](BACKEND.md) — 详细 API 端点清单
- [FRONTEND.md](FRONTEND.md) — 前端页面和组件详情
- [DATABASE.md](DATABASE.md) — 数据模型
- [MODULES.md](MODULES.md) — 业务模块详情

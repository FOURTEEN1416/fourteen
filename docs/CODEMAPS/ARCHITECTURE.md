# 架构地图

**最近更新:** 2026-09-18
**演进阶段:** Phase 14 (投产准备) → Phase 15 (品牌清洗) → Phase 16 (P0 全面修复 + CI 加固) → Phase 17 (全仓性能/正确性扫描)
**数据口径:** 端点与模块数均为 2026-09-17 实测（`create_api_app()` 内省 + 文件扫描），非文档估算值

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
│  │ (17 pages)  │──│ (13 modules) │──│ (Zustand stores x3)  │  │
│  └─────────────┘  └──────┬───────┘  └───────────────────────┘  │
└───────────────────────────┼─────────────────────────────────────┘
                            │ /api/* (Vite proxy → :8000)
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                    FastAPI 路由层 (:8000)                        │
│                                                                  │
│  ┌──────────────┐  ┌────────────────┐  ┌────────────────────┐  │
│  │ 21 路由模块  │  │ 204 endpoints  │  │ 认证层             │  │
│  │ (api/routers)│  │ (create_api_app│  │ JWT + X-API-Key   │  │
│  │ + shisi/api  │  │ │  实扫)       │  │                    │  │
│  └──────┬───────┘  └───────┬────────┘  └────────────────────┘  │
└─────────┼──────────────────┼───────────────────────────────────┘
          │                  │
┌─────────▼──────────────────▼───────────────────────────────────┐
│                  核心业务层 (Python 模块)                        │
│                                                                  │
│  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐  │
│  │ shisi/   │ │ LLM     │ │ 安全     │ │shisi/    │ │shisi/ │  │
│  │(121 文件) │ │ Provider│ │ (5 文件)  │ │knowledge │ │memory │  │
│  └──────────┘ └─────────┘ └──────────┘ └──────────┘ └───────┘  │
│  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐  │
│  │ TTS 语音 │ │ 人格提取 │ │ 工具系统  │ │ 编排器    │ │ 缓存  │  │
│  └──────────┘ └─────────┘ └──────────┘ └──────────┘ └───────┘  │
│  ┌────────────────────────────────────────────────────────┐    │
│  │ proactive / plugins / multimodal / character_card ...   │    │
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

---

## 编排器架构

> **注:** 编排逻辑位于 `orchestrator/` 包，包含 7 个文件（含 `__init__.py`）：
> - `optimized_orchestrator.py` (1013行) — 主类 `OptimizedOrchestrator`：`__init__` / 会话锁 / `_prepare_context` / `process_message` / `_after_process` / `health_check`
> - `_init_mixin.py` (510行) — `_InitPhasesMixin`：`initialize` 调用 10 个 `_init_*` 阶段；其中 `_init_memory_and_rag` 再级联 `_init_ase_and_scheduler` / `_init_tools` / `_init_rag`，共 13 个阶段方法
> - `_stream_mixin.py` (373行) — `_StreamPipelineMixin`：`process_message_stream` SSE 真流式/伪流式降级
> - `session_locks.py` — 会话锁管理（`SessionLockManager`）
> - `voice_detector.py` — 语音活动检测
> - `console_chat.py` — 控制台聊天通道
>
> `OptimizedOrchestrator` 继承 `_InitPhasesMixin` + `_StreamPipelineMixin`，通过 `self.components` 共享状态。公共 API 100% 兼容。
>
> `tools/` 模块提供 12 个内置工具（weather / search / calendar / calculator /
> time_awareness / character_card / web_summary / image_gen +
> set_reminder / query_reminders / memory / scheduler），
> 由编排器在流水线中调度执行。
>
> `cache/` 模块（`llm_cache.py` + `redis_client.py`）提供 LLM 响应缓存层。

---

## 数据流

### 用户请求流 (聊天)

```
用户消息
  → 前端 ChatInput → chat.ts API → POST /api/chat (Vite proxy)
    → FastAPI api/routers/chat_routes.py
      → Orchestrator (orchestrator/optimized_orchestrator.py, 12 级流水线)
        1. 安全过滤器 (ContentSafety)
        2. PII 匿名化 (PIIAnonymizer)
        3. 提示注入检测 (PromptInjection)
        4. 情感引擎 (EmotionEngine)
        5. LLM 路由 (MultiProviderGateway)
        6. 记忆提取 (MemoryExtractor)
        7. 工具调度 (ToolDispatcher)
        8. RAG 检索
        9. 响应生成
        10. 人格注入
        11. 消息记录
        12. 响应格式化
  → SSE/流式返回 → 前端 MessageBubble 渲染
```

### 管理后台流

```
管理员
  → 前端 AdminInvitesPage → admin.ts API → GET/POST /api/admin/*
    → FastAPI admin_routes.py
      → JWT 验证 (admin role)
        → 邀请码 CRUD / 用户管理
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
│ /wechat    │                   │ /roles            │
│ /roles     │                   │ /roles/create     │
│ /settings  │                   │ /roles/:roleId/   │
│ /login     │                   │   settings        │
│ /demo      │                   │ /roles/:roleId/   │
│ /admin/*   │                   │   status          │
└────────────┘                   │ /roles/:roleId/   │
                                 │   storyline       │
                                 └───────────────────┘
```

> **注:** 路由已从 `/users/:userId/roles/:roleId/...` 重构为 `/roles/:roleId/...`，
> 移除了用户层级嵌套，角色直接挂在根路径下。

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

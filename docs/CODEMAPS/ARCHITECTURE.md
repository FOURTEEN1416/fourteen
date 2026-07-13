# 架构地图

**最近更新:** 2026-07-13
**演进阶段:** Phase 14 (投产准备) → Phase 15 (品牌清洗) → Phase 16 (P0 全面修复 + CI 加固)

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
│  │ (15 pages)  │──│ (12 modules) │──│ (Zustand stores x6)  │  │
│  └─────────────┘  └──────┬───────┘  └───────────────────────┘  │
└───────────────────────────┼─────────────────────────────────────┘
                            │ /api/* (Vite proxy → :8000)
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│                    FastAPI 路由层 (:8000)                        │
│                                                                  │
│  ┌──────────────┐  ┌────────────────┐  ┌────────────────────┐  │
│  │ 8 旧路由     │  │ 12 新路由模块   │  │ 认证层             │  │
│  │ (71 endpoints)│  │ (75+ endpoints)│  │ JWT + X-API-Key   │  │
│  └──────┬───────┘  └───────┬────────┘  └────────────────────┘  │
└─────────┼──────────────────┼───────────────────────────────────┘
          │                  │
┌─────────▼──────────────────▼───────────────────────────────────┐
│                  核心业务层 (Python 模块)                        │
│                                                                  │
│  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐  │
│  │ shisi/   │ │ LLM     │ │ 安全     │ │shisi/    │ │shisi/ │  │
│  │ (96 文件) │ │ Provider│ │ (5 文件)  │ │knowledge │ │memory │  │
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
│  │ PostgreSQL 15 │  │ SQLite       │  │ Chroma (向量)        │  │
│  │ (主存储)      │  │ (缓存/本地)   │  │ (RAG 嵌入)           │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 编排器架构

> **注:** 根目录 `orchestrator.py` 已合并为薄包装层，实际编排逻辑位于
> `orchestrator/optimized_orchestrator.py`。`orchestrator/` 包还包含
> `session_locks.py`（会话锁）和 `voice_detector.py`（语音检测）。
>
> `tools/` 模块提供 12 个内置工具（搜索、天气、日历、提醒、时间感知等），
> 由编排器在流水线第 7 步调度执行。
>
> `cache/` 模块（`llm_cache.py` + `redis_client.py`）提供 LLM 响应缓存层。

---

## 数据流

### 用户请求流 (聊天)

```
用户消息
  → 前端 ChatInput → chat.ts API → POST /api/chat (Vite proxy)
    → FastAPI _chat_routes.py
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

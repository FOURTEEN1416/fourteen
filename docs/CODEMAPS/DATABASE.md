# 数据库地图

**最近更新:** 2026-08-01
**数据库:** SQLite (主, aiosqlite) + ChromaDB (向量) + 文件系统 (角色卡/知识库)

---

## 数据存储概览

```
┌─────────────────────────────────────────────────────────┐
│                     数据存储架构                           │
│                                                          │
│  SQLite (aiosqlite)            ChromaDB (向量)           │
│  ┌────────────────────┐   ┌──────────────────────┐      │
│  │ 用户 & 会话 (主库)  │   │ RAG 嵌入向量存储      │      │
│  │ data/users.db      │   │ data/chroma_db/      │      │
│  │ User               │   │ - 文档嵌入            │      │
│  │ UserSession        │   │ - 知识库索引          │      │
│  │ llm_config (JSON)  │   │ - 记忆向量            │      │
│  └────────────────────┘   └──────────────────────┘      │
│                                                          │
│  文件系统                                                 │
│  ┌────────────────────┐   ┌──────────────────────┐      │
│  │ 角色卡              │   │ 知识库数据            │      │
│  │ character_card/    │   │ data/knowledge/      │      │
│  │ my_character/      │   │ config/characters/   │      │
│  └────────────────────┘   └──────────────────────┘      │
└─────────────────────────────────────────────────────────┘
```

---

## SQLite 模型 (api/database.py)

> 数据库连接由 `api/runtime_config.py:get_database_url()` 解析，默认 `sqlite+aiosqlite:///data/users.db`。
> 若设置 `DATABASE_URL` / `APP_DATABASE_URL` 环境变量，可切换为 PostgreSQL/MySQL（同步驱动自动转异步）。

### User

| 字段 | 类型 | 描述 |
|------|------|------|
| id | Integer, PK | 用户 ID |
| username | String(50), Unique | 用户名 |
| email | String(120), Unique | 邮箱 |
| password_hash | String(128) | bcrypt 哈希密码 |
| role | String(20), default='user' | 角色 (user/admin) |
| is_active | Boolean, default=True | 是否激活 |
| llm_config | JSON, nullable | 用户级 LLM 配置（API Key 隔离） |
| created_at | DateTime | 创建时间 |
| updated_at | DateTime | 更新时间 |

### UserSession

| 字段 | 类型 | 描述 |
|------|------|------|
| id | Integer, PK | 会话 ID |
| user_id | Integer, FK→User.id | 用户 ID |
| token_jti | String(36), Unique | JWT ID |
| refresh_token | String(255) | 刷新令牌 |
| is_active | Boolean, default=True | 是否有效 |
| expires_at | DateTime | 过期时间 |
| created_at | DateTime | 创建时间 |
| last_used_at | DateTime | 最后使用时间 |

### shisi 业务模型

| 模型 | 文件 | 说明 |
|------|------|------|
| User | api/database.py | ✅ 活跃（含 llm_config 字段） |
| UserSession | api/database.py | ✅ 活跃 |
| affinity/emotion_stage/persona 等 | shisi/api/v2/ | DDD 核心 plane，异步 SQLite 访问 |

> **注意:** `shisi/` 子系统使用独立 SQLite 异步访问（DDD 分层: affinity/emotion_stage/persona/stats/vital_signs）。
> `api/database.py` 中的 User/UserSession 是用户认证主模型。

---

## 文件型存储

### 人设卡模块 (character_card/)

```
character_card/
├── __init__.py           ← 模块入口
├── integration.py        ← 集成接口
├── models.py             ← 数据模型
├── parser.py             ← 解析器
├── prompt_builder.py     ← 提示词构建
└── validator.py          ← 校验器
```

### 角色配置 (config/characters/)

```
config/characters/
├── {character_id}.json   ← 每个角色的独立配置文件
└── ...
```

### 知识库 (data/knowledge/)

```
data/knowledge/
├── {character_id}.json   ← 每个角色的知识库数据
├── char_vault_*.json     ← 角色知识库
└── sys_001.json          ← 系统知识库
```

---

## 向量存储 (ChromaDB)

| 用途 | 集合 | 嵌入模型 |
|------|------|----------|
| RAG 知识检索 | knowledge_chunks | sentence-transformers + rank-bm25 (本地) |
| 记忆检索 | memory_vectors | sentence-transformers + rank-bm25 (本地) |

ChromaDB 以持久化模式运行，数据存储在 `data/chroma_db/`。

---

## 缓存层

| 缓存类型 | 位置 | 用途 |
|----------|------|------|
| Python dict | memory/ | 运行时内存缓存 |
| React Query | frontend/ | 前端 API 响应缓存 |
| cache/llm_cache.py | cache/ | LLM 响应缓存 |
| cache/redis_client.py | cache/ | Redis 缓存（可选） |

---

## 数据流模式

### 用户注册/登录
```
LoginPage
  → POST /api/auth/register|login
    → auth_routes.py
      → auth_jwt.py (bcrypt 验证 + JWT 生成)
        → database.py (User, UserSession)
          → SQLite (data/users.db)
```

### 聊天消息
```
ChatInput
  → POST /api/chat
    → routers/chat_routes.py
      → Orchestrator
        → 记忆 (memory/) → ChromaDB
        → RAG (rag_engine/) → ChromaDB + data/knowledge/
        → 角色配置 → config/characters/{id}.json
        → LLM 响应
```

---

## 相关地图

- [BACKEND.md](BACKEND.md) — API 端点如何访问数据
- [MODULES.md](MODULES.md) — 各模块的数据管理

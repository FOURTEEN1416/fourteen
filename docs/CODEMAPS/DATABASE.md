# 数据库地图

**最近更新:** 2026-07-13
**数据库:** PostgreSQL 15 (主) + SQLite (缓存/本地) + ChromaDB (向量)

---

## 数据存储概览

```
┌─────────────────────────────────────────────────────────┐
│                     数据存储架构                           │
│                                                          │
│  PostgreSQL 15 (:5432)          SQLite                   │
│  ┌────────────────────┐   ┌──────────────────────┐      │
│  │ 用户 & 会话         │   │ 本地缓存              │      │
│  │ User               │   │ data/users.db        │      │
│  │ UserSession        │   │ - 会话缓存            │      │
│  │ 未来扩展: 角色/消息  │   │ - 临时数据            │      │
│  └────────────────────┘   └──────────────────────┘      │
│                                                          │
│  ChromaDB (向量)             文件系统                     │
│  ┌────────────────────┐   ┌──────────────────────┐      │
│  │ RAG 嵌入向量存储    │   │ 知识库数据            │      │
│  │ data/chroma_db/    │   │ data/knowledge/      │      │
│  │ - 文档嵌入          │   │ config/characters/   │      │
│  │ - 知识库索引        │   │ config/prompts/      │      │
│  └────────────────────┘   └──────────────────────┘      │
└─────────────────────────────────────────────────────────┘
```

---

## PostgreSQL 模型

### User

| 字段 | 类型 | 描述 |
|------|------|------|
| id | Integer, PK | 用户 ID |
| username | String(50), Unique | 用户名 |
| email | String(120), Unique | 邮箱 |
| password_hash | String(128) | bcrypt 哈希密码 |
| role | String(20), default='user' | 角色 (user/admin) |
| is_active | Boolean, default=True | 是否激活 |
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

### 遗留 SQLAlchemy 模型

| 模型 | 文件 | 说明 |
|------|------|------|
| User | api/database.py | ✅ 活跃 |
| UserSession | api/database.py | ✅ 活跃 |
| (更多 shisi 模型) | shisi/ | 通过异步 SQLite 访问 |

> **注意:** 项目正在从 shisi (异步 SQLite) 向 PostgreSQL 统一迁移。
> 新功能应使用 `api/database.py` 中的 SQLAlchemy 模型。

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
| SQLite | data/users.db | 本地会话缓存 |
| Python dict | memory/ | 运行时内存缓存 |
| React Query | frontend/ | 前端 API 响应缓存 |
| ChromaDB | data/chroma_db/ | 向量嵌入缓存 |

---

## 数据流模式

### 用户注册/登录
```
LoginPage
  → POST /api/auth/register|login
    → auth_routes.py
      → auth_jwt.py (bcrypt 验证 + JWT 生成)
        → database.py (User, UserSession)
          → PostgreSQL
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

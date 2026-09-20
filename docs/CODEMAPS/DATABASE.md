# 数据库地图

**最近更新:** 2026-09-20
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
│  │ 8 表 (见下)         │   │ - 文档嵌入            │      │
│  │                    │   │ - 情景记忆 (单 collection， │
│  │                    │   │   隔离缺口见 FUNCTION_      │
│  │                    │   │   INVENTORY 差距表)        │
│  └────────────────────┘   └──────────────────────┘      │
│                                                          │
│  文件系统                                                 │
│  ┌────────────────────┐   ┌──────────────────────┐      │
│  │ 角色卡              │   │ 知识库数据            │      │
│  │ config/characters/ │   │ data/knowledge/      │      │
│  │ (gitignore；现役     │   │ BM25 索引 (42 文件，   │      │
│  │  41 张，2026-09-20  │   │ 约 1750 块)           │      │
│  │  自服务器逐字节恢复) │   │                      │      │
│  └────────────────────┘   └──────────────────────┘      │
│  ┌────────────────────┐                                 │
│  │ 微信通道凭证         │                                 │
│  │ data/wechat_       │                                 │
│  │ sessions/<uid>/    │                                 │
│  │ slotN/ (每用户独立) │                                 │
│  └────────────────────┘                                 │
└─────────────────────────────────────────────────────────┘
```

---

## SQLite 模型 (api/database.py，8 表)

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
| created_at / updated_at | DateTime | 时间戳 |

### UserSession

| 字段 | 类型 | 描述 |
|------|------|------|
| id | Integer, PK | 会话 ID |
| user_id | Integer, FK→User.id | 用户 ID |
| token_jti | String(36), Unique | JWT ID |
| refresh_token | String(255) | 刷新令牌 |
| is_active | Boolean, default=True | 是否有效 |
| expires_at / created_at / last_used_at | DateTime | 时间戳 |

### 其余 6 表

| 模型 | 表名 | 说明 |
|------|------|------|
| InviteCode | invite_codes | 邀请码注册（内测准入） |
| ConsentRecord | consent_records | 用户同意记录（W2-consent） |
| WechatBinding | wechat_bindings | 微信 wxid ↔ 角色绑定（**微信人设真源**，09-17 起 web"设为活跃"实时同步） |
| **WechatChannelSession** | wechat_channel_sessions | **每人独立微信通道会话**（09-19）：(user_id, slot 0/1) 一人多条；status=idle/waiting_qr/scanned/connected/error；bot_id/nickname/messages_today/last_error（凭证本体在文件系统，不落库） |
| **WechatPeerPreference** | wechat_peer_preferences | **通道内好友自选角色**（09-19）：(owner_user_id, peer_wxid) → character_card_id；与 wechat_bindings 区分——binding 表达「wxid↔注册用户」身份，本表表达「在 U 的通道里 F 选了哪张卡」 |
| CharacterAchievement | character_achievements | 角色成就（ADR-0014，10 成就×4 类，幂等重算） |

### shisi 业务表 (shisi/migrations.py，12 张)

`characters` / `characters_v2` / `affinity_records` / `affinity_unlocks` / `affinity_audit` / `emotion_stage_state` / `stickers` / `character_stickers` / `vital_signs_state` / `memory_favorites` / `memory_recycle_bin` / `shisi_schema_version`

> **注意:** `shisi/` 子系统使用独立 SQLite 异步访问（DDD 分层: affinity/emotion_stage/persona/stats/vital_signs）。
> `api/database.py` 中的 **8 表**是用户认证与控制面模型（`users` / `user_sessions` / `invite_codes` /
> `consent_records` / `wechat_bindings` / `wechat_channel_sessions` / `wechat_peer_preferences` / `character_achievements`）。
>
> ⚠️ **口径自纠（2026-09-20 全仓历遍）**：本节此前写「`api/database.py` 中的 **6 表**」，与本文首段
> 「SQLite 模型 (api/database.py，**8 表**)」自相矛盾。以实测 `data/users.db` 的 8 张表为准。

### `data/sqlite.db` 全量表清单（26 张，2026-09-20 实测）

`shisi/migrations.py` 只建 12 张（上表）。同库另由 `structured_memory.py` / `working_memory.py` /
`_legacy_diary_summarizer.py` 等建表，**合计 26 张**（不含 `sqlite_sequence` 与 `user_facts_fts*` FTS 影子表）：

| 来源 | 表 |
|------|-----|
| `shisi/migrations.py`（12） | `characters` / `characters_v2` / `affinity_records` / `affinity_unlocks` / `affinity_audit` / `emotion_stage_state` / `stickers` / `character_stickers` / `vital_signs_state` / `memory_favorites` / `memory_recycle_bin` / `shisi_schema_version` |
| `structured_memory.py` 等（14） | `chat_history`（**对话上下文真源，09-20 起按 session 隔离读取**） / `user_facts`（+`user_facts_fts` 全文索引） / `sessions` / `working_memory` / `daily_summaries` / `emotion_trajectory` / `reflections` / `tool_call_log` / `trace_log` / `persona_evolution_log` / `pending_events` / **`pending_intents`**（09-20 澄清状态机：槽位合并 / 两轮上限 / 15min TTL） / **`reminders`**（09-20 迁移 +session_key/user_id/status/delivered_at/fail_count） / `affinity_log` |

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

> ⚠️ **该目录被 `.gitignore:117` 忽略**，内容由本地/部署私有投递，**卡数不可跨检出复现**。
> 2026-09-20 状态：**现役 41 张**（本批途中曾为空 → 已按用户指令从服务器
> `/opt/ai-girlfriend/config/characters/` **逐字节恢复**，逐文件 `sha256sum` **41/41 一致**、
> JSON 全可解析，含 v1.14 的 16 张文学导入卡）。
> **测试基线随之浮动**：`tests/test_persona_injection.py` 用例数 = **2 × 卡数 + 7**
> ——0 卡时参数化整体塌缩（本批途中实测收集数从 1436 掉到 1356）。
> **引用测试基线必须同时声明卡数**（见 `AGENTS.md` §4.3）。

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
微信消息 → wechat_direct → Orchestrator
        → 记忆 (shisi/memory) → ChromaDB
        → RAG (shisi/knowledge/) → BM25 索引 data/knowledge/{id}.json
        → 角色配置 → config/characters/{id}.json
        → LLM 响应
```

---

## 相关地图

- [BACKEND.md](BACKEND.md) — API 端点如何访问数据
- [MODULES.md](MODULES.md) — 各模块的数据管理

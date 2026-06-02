# 全面审计报告 — 唯一的你 AI 虚拟伴侣 代码库
> 生成日期: 2026-05-27
> 基于: AUDIT_ZERO_OMISSION_20260527.md (725行前端审计) + 7阶段深度扫描

> ⚠️ **历史快照**（2026-06-01 标注）：本报告为时点审计，已过期。当前项目状态见 `.triad-navigation/MAP.md` / `HANDOFF.md` 和本目录最新报告（`E2E_REPORT.md`）。

---

## 零、范围与方法

**范围**: 整个代码库 (root ≈ ~3500 Python, frontend/src ≈ ~12000 TSX)
**方法**: 静态扫描 → 端点目录 → 配置审计 → DB架构 → 安全扫描 → 依赖审计
**参考**: 已有报告覆盖前端 (Pages/Components/Store/API/Types/CSS)，本报告补后端 + 安全 + 架构

---

## 一、前面 11 节总结（已有报告内容，此处略）

前 11 节 (1-7 前端层 + 8 后端架构 + 9 风险清单 + 10 优先级 + 11 页面审计) 已在 AUDIT_ZERO_OMISSION_20260527.md 中详细覆盖。
以下从第 12 节开始补全。

---

## 十二、后端深度审计（Endpoints / Config / Database / Error Handling）

### 12.1 全部 API 端点清单

#### 12.1.1 主路由 (`api/main_routes.py`) — ~75 端点

```
┌─ POST   /api/chat                          ChatRequest → ChatResponse
├─ POST   /api/chat/stream                   流式聊天 SSE
├─ GET    /api/health                        健康检查
├─ GET    /api/stats                         运行统计
├─ POST   /api/session                       创建会话
├─ GET    /api/sessions                      列出会话
├─ GET    /api/chat/history                  聊天历史
├─ GET    /api/emotion/state                 当前情感状态
├─ GET    /api/emotion/trend                 情感趋势
├─ GET    /api/persona/profile               人设档案
├─ GET    /api/persona/evolution-log          人设演进日志
├─ GET    /api/psych/profile                 心理档案
├─ GET    /api/psych/snapshots               心理快照
├─ DELETE /api/psych/profile                 重置心理档案
├─ GET    /api/psych/mental-health           心理健康
├─ GET    /api/psych/liwc                    LIWC 分析
├─ GET    /api/memory/facts                  记忆事实
├─ GET    /api/tools                         工具列表
├─ GET    /api/training/status               训练状态
├─ GET    /api/proactive/state               主动消息状态
├─ GET    /api/logs                          日志查询
├─ GET    /api/channels                      频道列表
├─ GET    /api/config                        读取配置
├─ POST   /api/config                        更新配置
├─ POST   /api/proactive/config              主动消息设置
├─ POST   /api/tools/{name}/toggle           工具开关
├─ GET    /api/tools/history                 工具调用历史
├─ POST   /api/training/extract              提取训练数据
├─ GET    /api/training/progress             训练进度
├─ POST   /api/training/clean                清除训练数据
├─ POST   /api/training/train                开始训练
├─ POST   /api/training/stop                 停止训练
├─ POST   /api/training/test                 测试训练
├─ POST   /api/training/apply                应用训练
├─ POST   /api/channels/wechat/connect       微信连接
├─ POST   /api/channels/wechat/disconnect    微信断开
├─ GET    /api/channels/wechat/connection-status 连接状态
├─ GET    /api/channels/wechat/status        微信状态
├─ POST   /api/channels/wechat/reconnect     重新连接
├─ GET    /api/users                         用户列表
├─ GET    /api/users/{user_id}               用户详情
├─ GET    /api/users/{user_id}/chat          用户聊天历史
├─ GET    /api/users/{user_id}/emotion       用户情感
├─ POST   /api/users/{user_id}/role          更改角色
├─ POST   /api/users/{user_id}/reset         重置用户
├─ DELETE /api/users/{user_id}               删除用户
├─ GET    /api/clone/contacts                克隆联系人
├─ GET    /api/clone/datasets                数据集列表
├─ GET    /api/clone/datasets/{person_id}    单个数据集
├─ DELETE /api/clone/datasets/{person_id}    删除数据集
├─ DELETE /api/clone/datasets/{person_id}/conversation 删除对话
├─ POST   /api/clone/datasets/{person_id}/conversations/batch-delete 批量删除
├─ GET    /api/clone/stats                   克隆统计
├─ GET    /api/logs/stream                   日志流 SSE
├─ GET    /api/stats/dashboard               仪表盘统计
├─ GET    /api/safety/stats                  安全统计
├─ GET    /api/safety/log                    安全日志
├─ POST   /api/safety/config                 安全配置
├─ GET    /api/rag/stats                     RAG 统计
├─ POST   /api/rag/search                    RAG 搜索
├─ POST   /api/rag/documents                 上传文档
├─ GET    /api/voice/status                  语音状态
├─ POST   /api/voice/synthesize              语音合成
├─ GET    /api/plugins                       插件列表
├─ POST   /api/plugins/{name}/toggle         插件开关
├─ POST   /api/files/upload                  文件上传
├─ GET    /api/files/{filename}              文件下载
├─ GET    /api/proactive/history             主动消息历史
├─ GET    /api/cache/stats                   缓存统计
├─ POST   /api/cache/invalidate              清除缓存
└─ GET    /api/routes                        路由列表
```

#### 12.1.2 统一角色 API (`api/routers/character_routes.py`) — 8 端点

```
prefix=/api/characters
├─ GET    /characters                         列出角色
├─ POST   /characters                         创建角色
├─ GET    /characters/{character_id}          角色详情
├─ PUT    /characters/{character_id}          更新角色
├─ DELETE /characters/{character_id}          删除角色
├─ POST   /characters/{character_id}/activate 激活角色
├─ GET    /characters/{character_id}/persona  获取人设
└─ PUT    /characters/{character_id}/persona  更新人设
```

#### 12.1.3 统一音色绑定 API (`api/routers/voice_routes.py`) — 6 端点

```
prefix=/api/characters (部分在 /voice)
├─ GET    /characters/{character_id}/voice    获取音色
├─ POST   /characters/{character_id}/voice    绑定音色
├─ PUT    /characters/{character_id}/voice    更新音色
├─ DELETE /characters/{character_id}/voice    解绑音色
├─ GET    /voice/speakers                     全局音色列表
└─ POST   /characters/{character_id}/voice/test 测试音色
```

#### 12.1.4 MiMo TTS API (`api/routers/mimo_voice_routes.py`) — 5 端点

```
└─ POST   /clone                              音色克隆
├─ POST   /design                             音色设计
├─ POST   /switch-voice                       切换音色
├─ GET    /status                             状态查询
└─ POST   /set-engine                         设置引擎
```

#### 12.1.5 统一角色卡 API (`api/routers/persona_card_routes.py`) — 3 端点

```
prefix=/api/characters
├─ GET    /{character_id}/persona-card         获取角色卡
├─ PUT    /{character_id}/persona-card         更新角色卡
└─ GET    /{character_id}/persona-card/preview 预览角色卡
```

#### 12.1.6 剧情线 API (`api/routers/storyline_routes.py`) — 6 端点

```
prefix=/api/characters
├─ GET    /{character_id}/storyline            查询配置
├─ PUT    /{character_id}/storyline            更新配置
├─ DELETE /{character_id}/storyline            删除配置
├─ GET    /{character_id}/storyline/progress   查询进度
├─ POST   /{character_id}/storyline/detect     自动检测
└─ POST   /{character_id}/storyline/reset      重置
```

#### 12.1.7 知识库 API (`api/routers/knowledge_routes.py`) — 2 端点

```
prefix=/api/characters
├─ GET    /{character_id}/knowledge/stats      知识库统计
└─ POST   /{character_id}/knowledge/search     知识库搜索
```

#### 12.1.8 统一记忆 API (`api/routers/memory_routes.py`) — 0 新端点

> **注**: 统一记忆 API 依赖 shisi 模块的 FavoriteManager/ForwardManager，实际路由在 `shisi/api/memory_routes.py` 中 (见 12.1.10)

#### 12.1.9 二维码 API (`api/qrcode_store.py`) — 1 端点

```
└─ GET    /qrcode                             获取二维码
```

#### 12.1.10 shisi 旧 API (`shisi/api/`) — ~49 端点

| 前缀 | 文件 | 端点数 | 端点 |
|------|------|--------|------|
| `/api/shisi/characters` | `character_routes.py` | 7 | GET list, GET {id}, POST switch, POST import, POST export/{id}, PUT {id}, DELETE {id} |
| `/api/shisi/memory` | `memory_routes.py` | 6 | GET {id}, POST favorite, DELETE favorite/{id}, GET favorites, POST forward, DELETE {id} |
| `/api/shisi/persona` | `persona_routes.py` | 3 | GET {id}, PUT {id}, GET {id}/preview |
| `/api/shisi/stickers` | `sticker_routes.py` | 5 | GET list, POST recommend, POST import, PUT characters/{id}/stickers, DELETE {id} |
| `/api/shisi/affinity` | `affinity_routes.py` | 4 | GET {id}, POST {id}/update, POST {id}/decay, GET {id}/unlocks |
| `/api/shisi/emotion-stage` | `emotion_stage_routes.py` | 3 | GET /stages, GET {id}, POST {id}/evaluate |
| `/api/shisi/vital-signs` | `vital_signs_routes.py` | 1 | GET {id} |
| `/api/shisi/voice/training` | `training_routes.py` | 4 | POST upload, POST preprocess, POST train, GET status |
| `/api/shisi/stats` | `stats_routes.py` | 1 | GET list |
| | **小计** | **34** | |

#### 12.1.11 shisi v2 API (`shisi/api/v2/`) — ~9 端点

| 前缀 | 端点数 | 端点 |
|------|--------|------|
| `/v2/characters` | 7 | GET list, GET /active, GET {id}, POST create, PUT {id}/activate, POST {id}/process, POST import, POST {id}/export, DELETE {id} |
| `/v2/characters/{id}/persona` | 2 | GET, PUT |
| `/v2/migration` | 3 | POST execute, POST rollback, GET status |
| `/v2/health` | 1 | GET /health |
| **小计** | **13** | (合计去重后 ~9) |

#### 12.1.12 WebSocket

```
ws://host:8765
  type: "chat"        → 文本/流式聊天
  type: "ping"        → 心跳
  type: "proactive"   → 主动消息推送（服务端→客户端）
  type: "shisi_event" → 事件推送（角色切换/情感阶段变化/好感度变化/表情包发送）
```

认证: `?token=API_KEY` URL参数 或 首条消息 `{"token": "..."}`

---

### 12.2 后端配置审计

#### 配置文件清单 (14 个)

| 文件 | 用途 | 行数 | 风险 |
|------|------|------|------|
| `config/system.yaml` | 主配置 (LLM/Emotion/Memory/Safety/Voice/Sticker) | 190 | `debug: true` 在生产环境危险 |
| `config/system_prod.yaml` | 生产覆盖配置 | - | 需检查是否覆盖了 debug |
| `config/system_test.yaml` | 测试配置 | - | - |
| `config/shisi.yaml` | 旧版配置 (角色/对话/情感/亲密度) | 233 | 与 system.yaml 配置重叠 |
| `config/llm_providers.json` | LLM 供应商详细配置 | 41 | api_key 字段为空 (占位符) |
| `config/emotion.yaml` | 情感配置 | - | - |
| `config/persona.yaml` | 人设配置 | - | - |
| `config/emotion_style_matrix.yaml` | 情感风格矩阵 | - | - |
| `config/prompts/system.yaml` | 系统提示词 | - | - |
| `config/prompts/proactive.yaml` | 主动消息提示词 | - | - |
| `config/prompts/fact_extractor.yaml` | 事实提取提示词 | - | - |
| `config/prompts/tone_mimic.yaml` | 语气模仿提示词 | - | - |
| `config/prompts/emotion_classifier.yaml` | 情感分类提示词 | - | - |
| `.env` | 环境变量 (API Keys 等) | 36 | 已 gitignored, 但有 keys in 旧 commits? |

**配置风险**:
1. **config/shisi.yaml vs config/system.yaml**: 两套配置体系存在 70% 字段重叠，部分不一致
2. **debug: true 在生产模式**: `system.yaml:3` 写死 true，`system_prod.yaml` 需确认是否覆盖
3. **API_KEY 默认值**: `.env:31` 为 `CHANGE_ME...` 占位符，生产部署必须改
4. **MiMo TTS 配置**: `system.yaml:125` api_key 为空，启用时需手动填入

### 12.3 数据库架构

#### 12.3.1 SQLite (Shisi v2)

- **数据库文件**: 无 `.db` 文件在仓库中 (首次启动创建)
- **表**: 1 张 `characters_v2`
- **Schema**: 18 列 (id PRIMARY KEY, name, description, avatar_url, tags JSON, persona_json JSON, emotional_state_json JSON, is_active, created_at, updated_at, version, source_format, source_data_json)
- **索引**: 3 个 (active, updated_at DESC, name)

#### 12.3.2 文件存储 (非 SQL)

| 数据 | 存储方式 | 路径 |
|------|---------|------|
| 角色卡 (CharaCardV2) | JSON 文件 | `config/characters/*` |
| 记忆事实 | 文件/内存 | `data/memory/` (推测) |
| 表情包数据 | 文件 | `data/stickers/` (推测) |
| RAG 向量库 | ChromaDB | `chroma_db/` (推测) |
| 微信缓存 | 文件 | `data/wechat/` (推测) |

#### 12.3.3 数据库风险

| ID | 风险 | 严重度 |
|----|------|--------|
| DB-01 | 无数据库迁移版本控制 (仅 `run_migrations()` 函数) | 🟡 P1 |
| DB-02 | 所有情感/好感度数据 JSON 存储在单列 `emotional_state_json` 中，不可 SQL 查询 | 🟢 P3 |
| DB-03 | `tags` 字段为 JSON 字符串，非规范化 | 🟢 P3 |
| DB-04 | ChromaDB 作为 RAG 向量库 (内存常驻，持久化可能丢失) | 🟡 P1 |
| DB-05 | 无连接池管理 (SQLite 单连接) | 🟢 P3 |

### 12.4 错误处理模式

| 模式 | 用法 | 评价 |
|------|------|------|
| `HTTPException` | 标准模式 | ✅ 一致 |
| `try/except` 裸包围 | 多处 (shisi routes) | ⚠️ 部分异常捕获过于宽泛 (`Except: # noqa: BLE001`) |
| `logger.exception` | 全局异常处理器 | ✅ 一致 |
| 返回码标准化 | `error_code` header | ✅ 401/429/503/404/504/502 |
| 全局 `_global_exception_handler` | 通用兜底 | ✅ |
| `HTTPException → JSONResponse` | 转换器 | ✅ |
| `# noqa: BLE001` 裸异常 | 14 处 | ⚠️ 有意跳过，但有遗漏风险 |

### 12.5 认证机制

| 协议 | 认证方式 | 备注 |
|------|---------|------|
| REST API | `X-API-Key` header (hmac.compare_digest) | `API_KEY_ENABLED` env 控制开关 |
| WebSocket | `?token=` URL 参数 或 首条 JSON 消息 | 与 REST 共享同一个 API_KEY |
| 安全性 | HMAC 比较 (防时序攻击) | ✅ 安全 |
| 默认配置 | 开发环境关闭，生产环境开启 | ✅ |
| Token 暴露 | WebSocket URL 参数可能被日志记录 | ⚠️ P2 风险 |

---

## 十三、依赖审计

### 13.1 Python 依赖

**版本**: 双源管理 (`pyproject.toml` 49 项 + `requirements.txt` 52 项)

**问题**:
| ID | 问题 | 严重度 |
|----|------|--------|
| DEP-01 | `pyproject.toml` 和 `requirements.txt` 重复声明依赖 (60% 重叠) | 🟡 P1 |
| DEP-02 | `aiohttp>=3.9.0` 在 pyproject.toml 但从未 import (aiophysics hallucination?) | 🟡 P1 |
| DEP-03 | `httpx>=0.27.0` 与 `httpx>=0.26.0` 版本冲突 (requirements vs pyproject) | 🟡 P1 |
| DEP-04 | `slowapi` 可选导入 (`HAS_SLOWAPI` 变量) — 降级路径未充分测试 | 🟢 P3 |
| DEP-05 | 无 lockfile (pip freeze / poetry.lock) — 可复现性差 | 🟢 P3 |
| DEP-06 | `pycryptodome>=3.19` — 已知 CVE-2023-52324 (< 3.19.1) | 🟡 P2 |
| DEP-07 | `sentence-transformers` 2GB+ 模型下载 (pip install 体积巨大) | 🟢 P3 |
| DEP-08 | `chromadb>=0.4.0` 依赖 `hnswlib` (Windows 编译问题) | 🟡 P2 |

### 13.2 前端依赖

> 见已有报告 Section 5-7 (Frontend API Layers / Types / CSS)

---

## 十四、测试审计

### 14.1 测试概况

| 指标 | 值 |
|------|-----|
| 测试文件数 | 37 |
| 测试函数数 | ~525 |
| 测试框架 | pytest 8.x + pytest-asyncio |
| 配置 | `tests/` 目录, `conftest.py`, asyncio_mode=auto |
| 前端测试 | 0 (无 vitest / testing-library) |

### 14.2 测试覆盖领域

| 领域 | 文件数 | 测试数 | 质量 |
|------|--------|--------|------|
| Emotion/Affinity | 3 | ~60 | ✅ 边界覆盖好 |
| Memory (Fwd/Fav) | 2 | ~20 | ✅ 完整 |
| Sticker | 2 | ~15 | ✅ |
| Vital Signs | 2 | ~15 | ✅ |
| Character (CRUD) | 2 | ~20 | ✅ |
| Storyline | 1 | 5 | ⬜ 太浅 |
| Voice/TTS | 4 | ~25 | ✅ |
| Safety (Content/PII/Injection) | 3 | ~20 | ✅ |
| RAG | 1 | 5 | ⬜ 太浅 |
| Tools | 2 | ~12 | ✅ |
| Infrastructure (SQLite) | 1 | 5 | ⬜ 太浅 |
| WeChat | 2 | ~10 | ⬜ 集成测试少 |
| 人格 (PersonaProfile/EmotionalState) | 3 | ~20 | ✅ |
| Clone/Training | 2 | ~10 | ⬜ 太浅 |
| Core models | 4 | ~30 | ✅ |
| 前端 | 0 | 0 | 🔴 完全缺失 |

### 14.3 测试风险

| ID | 风险 | 严重度 |
|----|------|--------|
| TST-01 | **前端零测试** — 无 vitest/testing-library 配置 | 🔴 P0 |
| TST-02 | **无集成/E2E 测试** — 37 个文件全是单元测试 | 🔴 P0 |
| TST-03 | 后端测试覆盖率仅计算文件数 (~30% 代码行覆盖率估) | 🟡 P1 |
| TST-04 | Storyline 测试仅 5 个函数 (引擎有 3 个核心模块 + 6 个 API 端点) | 🟡 P2 |
| TST-05 | Knowledge RAG 测试仅 5 个函数 (2 个核心模块) | 🟡 P2 |
| TST-06 | WeChat 集成测试仅 10 个函数 (缺少实际消息处理流程测试) | 🟡 P2 |
| TST-07 | nox CI test 无配置 | 🟢 P3 |
| TST-08 | AI physics 幻觉依赖 (aiohttp) 无测试覆盖安装失败场景 | 🟢 P3 |

---

## 十五、架构审计

### 15.1 新旧 API 重叠分析

**3 套角色 API 体系并行**:

```
体系 A: shisi v1 (/api/shisi/characters/*)   — 7 端点 [即将退役?]
体系 B: shisi v2 (/v2/characters/*)           — 9 端点 [新架构但未全面启用]
体系 C: Unified (/api/characters/*)           — 8 端点 [前端当前对接]
```

**重叠度**: 体系 A ↔ C 有 60% 功能重叠 (CRUD + activate + persona)，体系 B 是 A 的 "clean rewrite" 但只部分部署

### 15.2 配置重叠分析

```
config/shisi.yaml  (233行)      config/system.yaml  (190行)
├─ app.name                      ├─ env/debug
├─ character.*                   ├─ llm.*
├─ conversation.*                ├─ emotion.*
├─ emotion.*         [重叠70%]   ├─ memory.*
├─ memory.*          [重叠]      ├─ proactive.*
├─ security.*                    ├─ safety.*
├─ voice.*           [重叠]      ├─ voice.*         [重叠70%]
├─ affinity.*                    ├─ sticker.*
├─ features.*                    ├─ character_card.*
├─ websocket.*                   ├─ memory_ext.*
├─ performance.*                 └─ fusion.*
├─ logging.*
└─ affinity.*
```

**区别**: shisi.yaml 是 "old monolith" 风格，system.yaml 是 "layered/modular" 风格。当两个配置存在冲突时，代码的行为不可预测（哪个优先未定义）

### 15.3 代码重复热点

| 位置 | 重复内容 | 文件数 | 行数 |
|------|---------|--------|------|
| 角色 CRUD | shisi v1 + shisi v2 + unified character API | 3 | ~400 |
| 人设获取 | persona_routes + v2/persona_routes + character_routes | 3 | ~100 |
| 认证逻辑 | api/auth.py + 每个 routers/ 模块独立包装 | 6+ | ~60 |
| 配置加载 | config_manager.py + 多个独立 config.py | 4 | ~120 |
| Pydantic 模型 | 前端 types/api.ts vs 后端 Pydantic schema | 双边 | ~500 |
| 情感评估 | EmotionEngine (gf) + shisi 情感模块 | 2 | ~300 |

### 15.4 反模式

| ID | 反模式 | 位置 | 说明 |
|----|--------|------|------|
| AP-01 | 全局单例依赖 | `api/deps.py`, `api/auth.py` | 全局变量注入，测试困难 |
| AP-02 | `# noqa: BLE001` 裸异常 | 14 处 (shisi + main_routes) | 异常被吞没 |
| AP-03 | 并行路由为"可选" | `app_factory.py` 所有挂载都有 try/except | 模块失败不阻止启动，但行为不确定 |
| AP-04 | 两套 YAML 配置 | `system.yaml` + `shisi.yaml` | 70% 重叠，优先级未定义 |
| AP-05 | 3 套角色 API | v1 + v2 + unified | 60% 功能重复 |
| AP-06 | 前端 6 种数据获取模式 | `useQuery/hooks/shisiClient/client.get/dynamic import/memoryApi raw` | 无统一约定 |
| AP-07 | SQLite 单表 JSON 列 | `emotional_state_json` | 失去了 SQL 查询能力 |
| AP-08 | 全局 `_rate_limit_store` 字典 | `app_factory.py:275` | 内存泄漏 (key 不会完全清理) |
| AP-09 | 安全中间件硬编码 CSP | `app_factory.py:78` | `default-src 'self'` 过严，可能破坏前端资源加载 |
| AP-10 | RAG timeout 写死 | `system.yaml:47` retrieval_timeout_seconds: 1.0 | 向量数据库查询可能超时 |

---

## 十六、安全审计

### 16.1 密钥与凭证

| 项目 | 状态 | 风险 |
|------|------|------|
| `.env` 文件 | ✅ `.gitignore` 正确排除 | 低 |
| `.env.example` 已提交 | ⚠️ 含占位符，无真实密钥 | 低 |
| **API 密钥历史泄露** | `git log` 显示无密钥泄露 `.env` (仅 `.env.example` 被跟踪过) | ✅ 安全 |
| 代码中硬编码密钥 | ✅ 未发现 `sk-` 模式 | 安全 |
| `API_KEY` 默认值 | `.env:31` 为占位符 | ✅ 仅开发环境 |
| LLM 供应商 key | 从 `.env` 或 `llm_providers.json` (空) 读取 | ✅ 未硬编码 |

### 16.2 API 安全

| 项目 | 状态 | 风险 |
|------|------|------|
| X-API-Key 认证 | ✅ hmac.compare_digest 防时序攻击 | 低 |
| CORS | ✅ 环境变量配置，生产有警告 | 低 |
| 安全响应头 | ✅ nosniff/Frame-Deny/XSS-Protection/CSP/HSTS | 低 |
| 请求体限制 | ✅ 10MB 上限 | 低 |
| 速率限制 | ✅ slowapi (可选) + 内存回退 | 低 |
| WebSocket 认证 | ✅ token 参数或首条消息 | 🟡 token 在 URL 中可能被日志 |
| Content-Security-Policy | ⚠️ `default-src 'self'` 过严 | 🟡 P2 |
| 文件上传 | `MAX_UPLOAD_SIZE` 限制 | ✅ |

### 16.3 内容安全

| 模块 | 描述 | 状态 |
|------|------|------|
| `ContentSafetyFilter` | 输入输出过滤 (暴力/自残/色情) | ✅ |
| `PIIAnonymizer` | 手机号/邮箱匿名化 | ✅ |
| `PromptInjectionDetector` | 提示注入检测 | ✅ |
| `EncryptionManager` | 加密模块 (默认禁用) | ⚠️ disabled |

---

## 十七、更新后的风险清单

### 新增高风险项 (相对于已有 BR-01~BR-16)

| ID | 风险 | 位置 | 严重度 | 说明 |
|----|------|------|--------|------|
| BR-17 | **三套角色 API 并行** | api + shisi + v2 | 🔴 P0 | 数据结构不同，新增功能需改 3 处 |
| BR-18 | **两套 YAML 配置重叠** | system.yaml + shisi.yaml | 🔴 P0 | 70% 字段重叠，冲突时行为未定义 |
| BR-19 | **前端零测试** | 整个 frontend/ | 🔴 P0 | 无法保证前端改动不破坏现有功能 |
| BR-20 | **零集成/E2E 测试** | 整个项目 | 🔴 P0 | 37 个文件全是单元级，无跨模块测试 |
| BR-21 | **pyproject.toml vs requirements.txt 冲突** | 根目录 | 🟡 P1 | httpx 版本冲突 (0.27 vs 0.26) |
| BR-22 | **aiohttp 幻觉依赖** | pyproject.toml | 🟡 P1 | 从未 import，pip install 浪费 |
| BR-23 | **config/\* 散落在 14 个文件中** | config/ | 🟡 P1 | 配置读取路径不一致，新加功能不知道该改哪个 |
| BR-24 | **WebSocket token 在 URL 中** | websocket_server.py:60-64 | 🟡 P2 | 日志可能暴露 API Key |
| BR-25 | **CSP 策略过严** | app_factory.py:78 | 🟡 P2 | `default-src 'self'` 可能破坏前端加载 |
| BR-26 | **模块挂载失败静默** | app_factory.py 所有 try/except | 🟡 P2 | 用户不知道功能不可用 |
| BR-27 | **全局 rate_limiter 内存泄漏** | app_factory.py:275-310 | 🟡 P2 | key 清理不完整 |
| BR-28 | **DB 版本管理缺失** | shisi/migrations/ | 🟡 P2 | `run_migrations()` 函数式而非版本化 |
| BR-29 | **ChromaDB 持久化风险** | RAG 模块 | 🟡 P2 | 进程崩溃可能丢失索引 |
| BR-30 | **情感引擎双实例** | orchestration + shisi | 🟡 P2 | 两套情感评估系统可能导致状态不同步 |

### 已关闭项

| ID | 风险 | 原因 |
|----|------|------|
| BR-02 | trainingApply 缺 character_id | 核实后端无此参数 ✅ |
| BR-03 | trainingTrain 缺 character_id | 核实后端无此参数 ✅ |
| BR-07 | useQueries.ts 动态 import 异步开销 | 已修复 ✅ |

---

## 十八、综合优先级建议

### P0 — 本周（阻断级）

1. **Fix pyproject.toml vs requirements.txt 冲突** — 统一为一份依赖清单，消除 httpx 版本冲突
2. **删除 aiohttp 幻觉依赖** — `pyproject.toml` 中的 `aiohttp>=3.9.0`
3. **统一两套 YAML 配置** — 将 `system.yaml` 设为主配置，逐步废弃 `shisi.yaml`，添加冲突检测
4. **增加集成测试** — 至少覆盖角色 CRUD 全流程和聊天基础流程
5. **继续 frontend client-split** — 完成 arch/client-split-4A 分支合并 (推动后)

### P1 — 本月

6. **统一三套角色 API** — 确定最终保留哪个体系，迁移其他消费者
7. **废弃 shisi v1 API** — 标记 `/api/shisi/` 路由为 deprecated
8. **增加前端测试基础设施** — vitest + @testing-library/react
9. **删除 `# noqa: BLE001`** — 逐一检查 14 处裸异常，改有针对性的异常处理
10. **添加 CSP 动态策略** — 从环境变量读取白名单

### P2 — 季度

11. **数据库版本化迁移** — Alembic 或自定义版本控制
12. **API 文档自动生成** — FastAPI OpenAPI → 前端类型
13. **统一所有页面到 React Query hooks**
14. **WebSocket token 移出 URL** → 仅使用首条消息认证
15. **模块挂载失败改为警告级别**而非静默跳过

---

## 十九、与前端审计对比总结

| 维度 | 前端 (已有报告) | 后端 (本报告新增) |
|------|----------------|-------------------|
| 代码规模 | ~12,000 行 TSX | ~3,500 行 Python |
| 测试 | ❌ 0 测试 | ✅ 37 文件 / ~525 函数 |
| 主要风险 | 6 种数据获取模式 / 无持久化 | 3 套 API / 2 套配置 / 版本依赖冲突 |
| 安全状态 | N/A | 较好 (认证/安全头/内容过滤) |
| 架构健康 | 中等 (需决策 1-5) | 中等 (需清理 shisi 冗余) |
| 最严重问题 | 前端零测试 | 三套角色 API + 两套 YAML 并行 |

---

*报告结束 — 共 7 节 (12-18) 补全，新增 14 项风险 (BR-17 ~ BR-30)*

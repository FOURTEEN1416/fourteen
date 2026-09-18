# 后端地图

> **✅ 2026-09-19 全量刷新**：端点统计已按 `create_api_app()` 内省实测重写（**204 业务端点 / 171 唯一路径**；`len(app.routes)=208` 含 4 条框架路由）。逐模块端点数以本图模块级清单 + `CODE_GRAPH.md` v3.8.2 §4.2（按 tag 分布，权威）为准。

**最近更新:** 2026-09-19
**版本:** 3.1.0
**入口文件:** `api/run_api.py`, `api/app_factory.py`, `main.py`, `orchestrator/`

---

## 架构分层

```
api/ (44 py 文件)            ← FastAPI 路由层
├── run_api.py              ← 启动入口 (uvicorn + flock 调度器单例/微信连接自动恢复)
├── app_factory.py          ← APP 工厂 (create_api_app) — 唯一构造入口
├── auth.py                 ← X-API-Key 认证依赖
├── auth_jwt.py             ← JWT 认证 (bcrypt + python-jose)
├── database.py             ← SQLAlchemy 模型 (User/InviteCode/UserSession/
│                             ConsentRecord/WechatBinding/CharacterAchievement)
├── achievement_engine.py   ← 成就引擎 (ADR-0014，10 成就×4 类幂等重算)
├── deps.py                 ← 依赖注入
├── health_routes.py        ← 健康检查路由 (/api/health, /api/ready) — 无需认证
├── main_routes.py          ← 共享 Pydantic 模型与 Helper（无端点）
├── runtime_config.py       ← 运行时环境检测 (is_production, get_database_url)
├── websocket_server.py     ← WebSocket 服务
├── qrcode_store.py         ← 二维码存储
├── session_manager.py      ← 会话管理
├── state/                  ← safety_log / tool_history / training_state
│
└── routers/                ← 路由模块 (21 个，不含 __init__.py)
    ├── character_routes.py   (21 endpoints，tag=character)
    ├── misc_routes.py        (16，含 /api/user/llm-config GET/POST、日记种子)
    ├── training_routes.py    (13，training/* + proactive/*)
    ├── safety_routes.py      (12，safety/rag/voice/files/cache)
    ├── chat_routes.py        (11，chat/session + wechat channels)
    ├── personality_routes.py (10，emotion/persona/psych)
    ├── wechat_routes.py      (9，wechat/*；另有 qrcode_store 1 端点独立挂载)
    ├── clone_routes.py       (8)
    ├── auth_routes.py        (8)
    ├── knowledge_routes.py   (8，knowledge/* + enrich)
    ├── users_routes.py       (7)
    ├── voice_routes.py       (6，character/voice/*)
    ├── mimo_voice_routes.py  (6，mimo/*)
    ├── storyline_routes.py   (6)
    ├── llm_providers_routes.py (6，admin)
    ├── invite_routes.py      (4)
    ├── memory_routes.py      (4，桥接 shisi Favorite/ForwardManager)
    ├── emotion_routes.py     (2，emotion/params/*)
    ├── persona_card_routes.py (3)
    ├── admin_routes.py       (5)
    └── tools_routes.py       (6，system/tools + plugins/*)
│
└── shisi/api/ (11 py 文件)  ← DDD 核心 plane，setup_shisi(app) 装配，31 端点
    （affinity 4 / character 7 / emotion_stage 3 / memory 6 / persona 3 /
      stats 1 / sticker 5 / vital_signs 1 / health* — 均已纳认证 31/31）
```

> demo_routes 已于 2026-08-28 删除（D1 裁决）；`shisi/api/v2/` 死模块已于 2026-09-18 删除。

---

## API 端点清单（模块级，2026-09-19 内省实测）

| 模块 (tag) | 端点数 | 路径前缀 |
|------|------|------|
| character | 21 | /api/characters/*, /api/presets/*（含 .png 导入/导出） |
| misc | 16 | /api/stats, /api/dashboard, /api/memory/facts, /api/logs*, /api/config, /api/user/llm-config, /api/channels, /api/routes, /api/memory/diary* |
| training | 13 | /api/training/*, /api/proactive/*（config/send/pause/history） |
| safety-infra | 12 | /api/safety/*, /api/rag/*, /api/voice/*, /api/files/*, /api/cache/* |
| chat | 11 | /api/chat/*, /api/session/*, /api/wechat/status |
| personality | 10 | /api/emotion/*, /api/persona/*, /api/psych/* |
| memory | 10 | api memory_routes(4) + shisi memory_routes(6) |
| wechat | 9 | /api/wechat/*（另有 qrcode 1 端点独立挂载） |
| clone | 8 | /api/clone/*（含 /api/clone/upload） |
| auth | 8 | /api/auth/*（register/login/refresh/logout/me GET/PUT/DELETE/password/check-invite） |
| knowledge | 8 | /api/characters/{id}/knowledge/*, /api/characters/{id}/enrich |
| users | 7 | /api/users/*（admin only） |
| characters (shisi) | 7 | /api/shisi/characters |
| tools | 6 | /api/system/tools*, /api/plugins/* |
| voice | 6 | /api/character/voice/* |
| mimo-tts | 6 | /api/mimo/* |
| storyline | 6 | /api/storyline/* |
| llm-providers | 6 | /api/llm-providers/*（admin） |
| stickers | 5 | /api/shisi/stickers |
| admin | 5 | /api/admin/* |
| affinity | 4 | /api/shisi/affinity |
| invite | 4 | /api/auth/register-invite, /api/admin/invites |
| emotion-stage | 3 | /api/shisi/emotion-stage |
| persona (shisi) | 3 | /api/shisi/persona |
| persona-card | 3 | /api/persona-card/* |
| health | 2 | /api/health, /api/ready（无认证，探活） |
| emotion | 2 | /api/emotion/params/* |
| vital-signs | 1 | /api/shisi/vital-signs |
| stats (shisi) | 1 | /api/shisi/stats |
| (untagged) | 1 | /api/wechat/qrcode |
| **合计** | **204** | 95 GET / 74 POST / 20 DELETE / 15 PUT |

---

## 认证体系

```
┌─────────────────────────────────────────────────────────┐
│                    双轨认证系统                            │
│                                                          │
│  JWT Bearer Token (用户认证)      X-API-Key (内部服务)     │
│  ┌──────────────────────┐      ┌──────────────────────┐  │
│  │ /api/auth/login      │      │ 请求头: X-API-Key   │  │
│  │ ↓ access_token (15m) │      │ 用于服务间通信        │  │
│  │ ↓ refresh_token (7d) │      │ 不经过用户身份验证     │  │
│  │ ↓ bcrypt 密码哈希     │      │                      │  │
│  └──────────────────────┘      └──────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 核心业务流程

### Orchestrator 12 级流水线

> **注意:** 根目录 `orchestrator.py` 已删除，编排逻辑统一由 `orchestrator/` 包提供。
> `orchestrator/optimized_orchestrator.py` 中的 `OptimizedOrchestrator` 是实际实现。

```
main.py/orchestrator/
┌─────────────────────────────────────────────────────────┐
│  1. ContentSafetyFilter    ← 内容安全过滤                 │
│  2. PIIAnonymizer          ← PII 匿名化                  │
│  3. PromptInjectionDetector← 提示注入检测                 │
│  4. EmotionEngine          ← 情感引擎                    │
│  5. MultiProviderGateway   ← LLM 多供应商网关             │
│  6. MemoryExtractor        ← 记忆提取                    │
│  7. ToolDispatcher         ← 工具调度                    │
│  8. RAG Retriever          ← RAG 检索                   │
│  9. ResponseGenerator      ← 响应生成                    │
│ 10. PersonaInjector        ← 人格注入                    │
│ 11. MessageLogger          ← 消息记录                    │
│ 12. ResponseFormatter      ← 响应格式化                  │
└─────────────────────────────────────────────────────────┘
```

---

## 配置体系

| 文件 | 用途 | 优先级 |
|------|------|--------|
| `.env` | API Keys / 密钥 (gitignored) | 最高 |
| `config/system.yaml` | 系统配置 | 高 |
| `config/shisi.yaml` | 核心业务配置 | 高 |
| `config/llm_providers.json` | LLM 供应商配置 | 高 |
| `config/emotion.yaml` | 情感配置 | 中 |
| `config/emotion_style_matrix.yaml` | 情感风格矩阵 | 中 |
| `config/persona.yaml` | 人格配置 | 中 |
| `config/characters/` | 角色特定配置 | 运行时 |

---

## 相关地图

- [ARCHITECTURE.md](ARCHITECTURE.md) — 整体架构
- [MODULES.md](MODULES.md) — 业务模块详情
- [DATABASE.md](DATABASE.md) — 数据模型
- [FRONTEND.md](FRONTEND.md) — 前端 API 客户端

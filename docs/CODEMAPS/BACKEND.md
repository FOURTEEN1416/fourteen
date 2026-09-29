# 后端地图

> **✅ 2026-09-29 全仓历遍重排**：端点统计按 `create_api_app()` 内省**按 handler 所属模块**重排（**229 业务端点 / 195 唯一路径**；111 GET / 82 POST / 16 PUT / 20 DELETE；`len(app.routes)=233` 含 4 条框架路由）。较 09-21 口径 220/186 净增 9 = W9 自服务 +5（consent/withdraw、consent/status、account/delete、account/export(+chats)）+ W12 模板 +2 + /api/metrics +1 + api memory 转发面 +1（W4）；wechat_routes 旧表计 9、本批内省两次均为 8（旧表计数偏差，该文件近期无变更）。权威口径以 `CODE_GRAPH.md` **v3.8.27** §1.1 为准。更早增量（09-20/21）见 git 历史。

**最近更新:** 2026-09-29
**版本:** 3.1.0
**入口文件:** `api/run_api.py`, `api/app_factory.py`, `main.py`, `orchestrator/`

---

## 架构分层

```
api/ (45 py 文件)            ← FastAPI 路由层
├── run_api.py              ← 启动入口 (uvicorn + flock 调度器单例/微信连接自动恢复)
├── app_factory.py          ← APP 工厂 (create_api_app) — 唯一构造入口
├── auth.py                 ← X-API-Key 认证依赖
├── auth_jwt.py             ← JWT 认证 (bcrypt + python-jose)
├── database.py             ← SQLAlchemy 模型 (User/InviteCode/UserSession/
│                             ConsentRecord/WechatBinding/CharacterAchievement)
├── achievement_engine.py   ← 成就引擎 (ADR-0014，10 成就×4 类幂等重算)
├── deps.py                 ← 依赖注入
├── health_routes.py        ← 健康检查路由 (/api/health, /api/ready) + /api/metrics（W10 多 worker 聚合）— 前两者无需认证
├── main_routes.py          ← 共享 Pydantic 模型与 Helper（无端点）
├── runtime_config.py       ← 运行时环境检测 (is_production, get_database_url)
├── websocket_server.py     ← WebSocket 服务
├── qrcode_store.py         ← 二维码存储
├── session_manager.py      ← 会话管理
├── state/                  ← safety_log / tool_history / training_state
│
└── routers/                ← 路由模块 (24 个，不含 __init__.py)
    ├── character_routes.py   (21 endpoints，tag=character)
    ├── misc_routes.py        (16，含 /api/user/llm-config GET/POST、日记种子、follow_up/reply_mode 配置)
    ├── training_routes.py    (13，training/* + proactive/*)
    ├── auth_routes.py        (13，register/login/refresh/logout/me/change-password/admin reset + consent 状态机 + W9 自服务 consent/status|withdraw、account/delete|export(+chats))
    ├── safety_routes.py      (12，safety/rag/voice/files/cache)
    ├── chat_routes.py        (11，chat/session + wechat channels；旧全局微信端点已收敛 admin 兼容面)
    ├── wechat_channel_routes.py (11，本人通道 9 + admin-wechat 2；双 router 同文件，JWT)
    ├── personality_routes.py (10，emotion/persona/psych)
    ├── wechat_routes.py      (8，wechat/*；另有 qrcode_store 1 端点独立挂载)
    ├── clone_routes.py       (8)
    ├── knowledge_routes.py   (8，knowledge/* + enrich)
    ├── users_routes.py       (7)
    ├── tools_routes.py       (6，system/tools + plugins/*)
    ├── voice_routes.py       (6，character/voice/*)
    ├── mimo_voice_routes.py  (6，mimo/*)
    ├── storyline_routes.py   (6)
    ├── llm_providers_routes.py (6，admin)
    ├── agent_plane_routes.py (5，agent-plane/*，admin 门槛)
    ├── memory_routes.py      (5，/api/characters/{id}/favorites* 收藏/转发回执，桥接 shisi Favorite/ForwardManager)
    ├── admin_routes.py       (5，admin/users* CRUD，delete 接 lifecycle)
    ├── invite_routes.py      (4，register-invite 独立路由+独立 TokenResponse + admin/invites*)
    ├── persona_card_routes.py (3)
    ├── emotion_routes.py     (2，emotion/params/*)
    └── character_template_routes.py (2，/api/character-templates 清单/克隆，W12)
│
└── shisi/api/ (11 py 文件)  ← DDD 核心 plane，setup_shisi(app) 装配，31 端点
    （affinity 4 / character 7 / emotion_stage 3 / memory 6 / persona 3 /
      stats 1 / sticker 5 / vital_signs 1 / health* — 均已纳认证 31/31）
```

> demo_routes 已于 2026-08-28 删除（D1 裁决）；`shisi/api/v2/` 死模块已于 2026-09-18 删除。
> **每人独立微信通道（2026-09-19）**：`/api/wechat/channel/*` 仅 JWT 本人
> （状态/list/connect/qrcode/disconnect/reconnect/好友选角 peers+characters）；
> `/api/admin/wechat/*` 为 admin 运维面（全局通道摘要/强制下线）。

---

## API 端点清单（模块级，2026-09-29 内省实测，按 handler 所属文件）

| 模块文件 | 端点数 | 路径 |
|------|------|------|
| character_routes | 21 | /api/characters/*, /api/presets/*（含 .png 导入/导出） |
| misc_routes | 16 | /api/stats, /api/dashboard, /api/memory/facts, /api/logs*, /api/config, /api/user/llm-config, /api/channels, /api/routes, /api/memory/diary* |
| training_routes | 13 | /api/training/*, /api/proactive/*（config 含 follow_up/reply_mode、send/pause/history） |
| auth_routes | 13 | /api/auth/*：register/login/refresh/logout/me/change-password/admin reset + consent(AGREEMENT_VERSION) + W9 自服务 consent/status、consent/withdraw、account/delete、account/export、account/export/chats |
| safety_routes | 12 | /api/safety/*, /api/rag/*, /api/voice/*, /api/files/*, /api/cache/* |
| chat_routes | 11 | /api/chat/*, /api/session/*, /api/wechat/status |
| wechat_channel_routes | 11 | /api/wechat/channel/*（本人 9：status/list/connect/qrcode/disconnect/reconnect/peers/characters 等）+ /api/admin/wechat/*（admin 2：摘要/强制下线；双 router 同文件） |
| personality_routes | 10 | /api/emotion/*, /api/persona/*, /api/psych/* |
| clone_routes | 8 | /api/clone/*（含 /api/clone/upload） |
| knowledge_routes | 8 | /api/characters/{id}/knowledge/*, /api/characters/{id}/enrich |
| wechat_routes | 8 | /api/wechat/*（另有 qrcode_store 1 端点独立挂载，admin 兼容） |
| users_routes | 7 | /api/users/*（admin only） |
| shisi/api/character_routes | 7 | /api/shisi/characters |
| tools_routes | 6 | /api/system/tools*, /api/plugins/* |
| shisi/api/memory_routes | 6 | /api/shisi/memory（含 favorites；09-28 路由遮蔽修复后 `GET /favorites` 静态路由可达） |
| voice_routes | 6 | /api/character/voice/* |
| mimo_voice_routes | 6 | /api/mimo/* |
| storyline_routes | 6 | /api/storyline/* |
| llm_providers_routes | 6 | /api/llm-providers/*（admin） |
| agent_plane_routes | 5 | /api/agent-plane/*（replay/profile/events/curate/probes，admin 门槛） |
| shisi/api/sticker_routes | 5 | /api/shisi/stickers |
| memory_routes (api) | 5 | /api/characters/{id}/favorites*（收藏增删查 + 转发回执） |
| admin_routes | 5 | /api/admin/users*（用户 CRUD；delete 接 lifecycle） |
| shisi/api/affinity_routes | 4 | /api/shisi/affinity |
| invite_routes | 4 | /api/auth/register-invite（独立路由+独立 TokenResponse）+ /api/admin/invites* |
| health_routes | 3 | /api/health, /api/ready（无认证探活）+ /api/metrics（W10 多 worker 聚合） |
| shisi/api/emotion_stage_routes | 3 | /api/shisi/emotion-stage |
| shisi/api/persona_routes | 3 | /api/shisi/persona |
| persona_card_routes | 3 | /api/persona-card/* |
| character_template_routes | 2 | /api/character-templates（W12：清单/克隆） |
| emotion_routes | 2 | /api/emotion/params/* |
| shisi/api/vital_signs_routes | 1 | /api/shisi/vital-signs |
| shisi/api/stats_routes | 1 | /api/shisi/stats |
| app_factory（直挂） | 1 | /api/shisi/status（shisi 域合计 31 = 30 路由 + 本端点） |
| qrcode_store | 1 | /api/wechat/qrcode |
| **合计** | **229** | 111 GET / 82 POST / 16 PUT / 20 DELETE |

---

## 认证体系

```
┌─────────────────────────────────────────────────────────┐
│              认证体系（2026-09-19 起 JWT 优先）            │
│                                                          │
│  JWT Bearer Token (用户认证，优先)   X-API-Key (机器)     │
│  ┌──────────────────────┐      ┌──────────────────────┐  │
│  │ /api/auth/login      │      │ 请求头: X-API-Key   │  │
│  │ ↓ access_token (15m) │      │ 用于服务间通信/脚本/  │  │
│  │ ↓ refresh_token (7d) │      │ E2E；不经过用户身份   │  │
│  │ ↓ bcrypt 密码哈希     │      │                      │  │
│  └──────────────────────┘      └──────────────────────┘  │
│                                                          │
│  verify_api_key_dep：有效 Bearer JWT → 直接放行（控制台    │
│  用户无需携带全局 API Key，前端 bundle 不含 Key 明文）；   │
│  否则 API_KEY_ENABLED 且 X-API-Key 匹配 → 放行            │
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

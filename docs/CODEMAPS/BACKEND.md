# 后端地图

**最近更新:** 2026-06-03
**入口文件:** `api/run_api.py`, `api/app_factory.py`, `main.py`, `orchestrator.py`

---

## 架构分层

```
api/                        ← FastAPI 路由层
├── run_api.py              ← 启动入口 (uvicorn)
├── app_factory.py          ← APP 工厂 (create_api_app)
├── auth.py                 ← X-API-Key 认证依赖
├── auth_jwt.py             ← JWT 认证 (bcrypt + python-jose)
├── database.py             ← SQLAlchemy 模型 (User, UserSession)
├── deps.py                 ← 依赖注入
├── websocket_server.py     ← WebSocket 服务
├── qrcode_store.py         ← 二维码存储
├── session_manager.py      ← 会话管理
│
├── _misc_routes.py         ← [旧] 杂项 (10 endpoints)
├── _chat_routes.py         ← [旧] 聊天 (10 endpoints)
├── _personality_routes.py  ← [旧] 人格 (9 endpoints)
├── _users_routes.py        ← [旧] 用户 (7 endpoints)
├── _training_routes.py     ← [旧] 训练 (11 endpoints)
├── _tools_routes.py        ← [旧] 工具 (5 endpoints)
├── _safety_routes.py       ← [旧] 安全 (12 endpoints)
├── _clone_routes.py        ← [旧] 克隆 (7 endpoints)
│
├── routers/                ← 新路由模块 (12 个)
│   ├── auth_routes.py      ← 认证注册登录 (9 endpoints)
│   ├── admin_routes.py     ← 管理员 CRUD (6 endpoints)
│   ├── invite_routes.py    ← 邀请码 (4 endpoints)
│   ├── character_routes.py ← 角色管理 (53 endpoints)
│   ├── voice_routes.py     ← 语音 (16 endpoints)
│   ├── mimo_voice_routes.py← MiMo 语音 (10 endpoints)
│   ├── storyline_routes.py ← 故事线 (27 endpoints)
│   ├── wechat_routes.py    ← 微信集成 (10 endpoints)
│   ├── emotion_routes.py   ← 情感 (10 endpoints)
│   ├── memory_routes.py    ← 记忆 (4 endpoints)
│   ├── knowledge_routes.py ← 知识库 (7 endpoints)
│   └── persona_card_routes.py ← 人设卡 (3 endpoints)
│
└── main_routes.py          ← 重构后的主路由 (仅 95 行, 0 endpoints)
                             包含 6 个 Pydantic 模型 + 4 个 Helper + sanitize_config
```

---

## API 端点清单

### 认证模块 (auth_routes.py) — 9 endpoints

| 方法 | 路径 | 描述 | 认证 |
|------|------|------|------|
| POST | /api/auth/register | 用户注册 | 无 |
| POST | /api/auth/login | 用户登录 | 无 |
| POST | /api/auth/refresh | 刷新 token | Bearer |
| POST | /api/auth/logout | 退出登录 | Bearer |
| GET | /api/auth/me | 获取当前用户 | Bearer |
| PUT | /api/auth/me | 更新当前用户 | Bearer |
| PUT | /api/auth/password | 修改密码 | Bearer |
| DELETE | /api/auth/me | 注销账户 | Bearer |
| POST | /api/auth/check-invite | 检查邀请码 | 无 |

### 管理员模块 (admin_routes.py) — 6 endpoints

| 方法 | 路径 | 描述 | 认证 |
|------|------|------|------|
| GET | /api/admin/users | 用户列表 | Admin |
| GET | /api/admin/users/{id} | 用户详情 | Admin |
| PUT | /api/admin/users/{id} | 更新用户 | Admin |
| DELETE | /api/admin/users/{id} | 删除用户 | Admin |
| PUT | /api/admin/users/{id}/role | 更改角色 | Admin |
| GET | /api/admin/stats | 系统统计 | Admin |

### 邀请码模块 (invite_routes.py) — 4 endpoints

| 方法 | 路径 | 描述 | 认证 |
|------|------|------|------|
| POST | /api/invites | 创建邀请码 | Admin |
| GET | /api/invites | 列出邀请码 | Admin |
| DELETE | /api/invites/{id} | 删除邀请码 | Admin |
| GET | /api/invites/check/{code} | 验证邀请码 | 无 |

### 角色模块 (character_routes.py) — 53 endpoints

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | /api/characters | 角色列表 |
| POST | /api/characters | 创建角色 |
| GET | /api/characters/{id} | 角色详情 |
| PUT | /api/characters/{id} | 更新角色 |
| DELETE | /api/characters/{id} | 删除角色 |
| ... | /api/characters/* | +48 更多 (含配置/导入/导出等) |

### 故事线模块 (storyline_routes.py) — 27 endpoints

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | /api/characters/{id}/storyline | 故事线列表 |
| POST | /api/characters/{id}/storyline | 创建章节 |
| PUT | /api/characters/{id}/storyline/{sid} | 更新章节 |
| DELETE | /api/characters/{id}/storyline/{sid} | 删除章节 |
| ... | 更多 | 分支管理/进度追踪等 |

### 语音模块 (voice_routes.py + mimo_voice_routes.py) — 26 endpoints

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | /api/voice/voices | 语音列表 |
| POST | /api/voice/tts | 文本转语音 |
| ... | /api/mimo/* | MiMo 语音 API |

### 微信模块 (wechat_routes.py) — 10 endpoints

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | /api/wechat/status | 微信状态 |
| POST | /api/wechat/send | 发送消息 |
| ... | /api/wechat/* | 联系人/群聊等 |

### 情感/记忆/知识模块 — 21 endpoints

| 模块 | 路径前缀 | 数量 |
|------|----------|------|
| emotion_routes.py | /api/emotion/* | 10 |
| memory_routes.py | /api/memory/* | 4 |
| knowledge_routes.py | /api/knowledge/* | 7 |
| persona_card_routes.py | /api/persona-card/* | 3 |

### 旧路由模块 (api/_*_routes.py) — 71 endpoints

| 模块 | 路径前缀 | 数量 | 说明 |
|------|----------|------|------|
| _misc_routes | /api | 10 | 杂项 (系统状态等) |
| _chat_routes | /api | 10 | 聊天 |
| _personality_routes | /api | 9 | 人格 |
| _users_routes | /api | 7 | 用户 |
| _training_routes | /api | 11 | 训练 |
| _tools_routes | /api | 5 | 工具 |
| _safety_routes | /api | 12 | 安全 |
| _clone_routes | /api | 7 | 克隆 |

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

```
main.py/orchestrator.py
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
| `config/system_prod.yaml` | 生产配置 | 中 |
| `config/system_test.yaml` | 测试配置 | 低 |
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

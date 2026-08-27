# 📚 API 模块阅读报告

**读取进度**：41/41 文件 ✅ 全部穷举阅读 | **读取时间**：2026-08-26  
**模块定位**：FastAPI REST API 层（应用工厂+17路由域204端点+WebSocket+认证+数据库）  

---

## 🧠 核心基础设施（15文件）

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包声明 | 最小导出 |
| `app_factory.py` (21.3KB) | **REST应用工厂** | 仅负责FastAPI实例创建+中间件配置+子路由挂载（docstring明确：17个include_router共204端点,实扫2026-07-30）；UserContextMiddleware从Authorization提取用户ID写日志上下文(ring_buffer按用户隔离)；MAX_REQUEST_SIZE 10MB；slowapi可选速率限制+**回退自实现限速器**(defaultdict按IP+路径,60s窗口60次,300s清理过期,429返回RATE_LIMIT错误码)；shisi模块挂载(/api/shisi/status探测13个子管理器可用性)；_mount_required_router关键路由挂载记录到deps.route_mounts(readiness防静默404)——characters/character_voice/character_memory/persona_card/storyline/knowledge/wechat/emotion_params八组必挂 |
| `run_api.py` (13.7KB) | **API-Only启动入口** | uvicorn api.run_api:app或python直接运行；全局orchestrator单例+UserManager；_app_lifespan生命周期(_init_and_preload预热)；_ensure_scheduler_singleton调度器去重；双发送通道工厂：_websocket_sender_factory+_wechat_sender_factory注册给ProactiveScheduler；_autostart_wechat_connector自动拉起微信连接；_migrate_retired_providers退役provider迁移；atexit清理 |
| `deps.py` | **全局依赖中心** | _APIDeps容器：orch/health/config/sessions/gf核心五件套+三状态管理器(TrainingStateManager/ToolHistoryManager maxlen1000/SafetyLogManager maxlen2000)+微信状态缓存(5s TTL)+shisi_reg+route_mounts+延迟初始化clone_mgr/character_voice_mgr(双重检查锁)；get_rag/get_tts从orchestrator.components取 |
| `database.py` (8.6KB) | **SQLAlchemy异步数据模型** | async引擎(aiosqlite默认,pool_pre_ping)+async_sessionmaker；**四模型**：User(email/username唯一索引/hashed_password/role三级admin-editor-viewer/is_active/is_verified/**llm_config JSON列实现多用户API Key隔离**)、InviteCode(16字符主键/created_by/used_by/expires_at/is_valid()含时区修正/内测注册控制)、UserSession(refresh_token_hash SHA256存储/device_name/ip_address/user_agent/is_expired naive datetime修正)、WechatBinding(wxid唯一/user_id级联删除/**character_card_id绑定角色卡**)；init_db建表/get_db会话依赖/close_db释放 |
| `auth.py` | **统一API Key认证** | verify_api_key_dep全路由共享(Security依赖)；X-API-Key header优先→query参数兜底(EventSource无法自定义header场景)；hmac.compare_digest防时序攻击；运行时可update_auth_key热更新；未启用时放行但生产环境高强度警告 |
| `auth_jwt.py` (8.2KB) | **JWT认证+密码安全** | **P0-2修复记录**:旧弱默认secret已废,生产环境JWT_SECRET<32字符直接RuntimeError拒绝启动(openssl rand -base64 48建议)；dev兜底_DEV_ONLY_JWT_SECRET+高强度警告；bcrypt直调(绕过passlib 1.7.4与bcrypt 5.0不兼容)；access token 30min/refresh token 7天,jti防重放,type字段区分；hash_refresh_token SHA256入库；get_current_user_id/get_current_user/require_role("admin")工厂三级依赖 |
| `path_security.py` | 路径遍历防护 | sanitize_id仅留[A-Za-z0-9_-]截128字符；safe_join_path resolve后前缀校验越界抛异常 |
| `session_manager.py` | 会话管理 | session_id格式`{user_id}:{channel}:{uuid8}`；线程锁保护字典；create/get/end/list active/active_count |
| `qrcode_store.py` | 微信二维码存储 | data/wechat_qrcode.json文件持久化；600秒过期；_generate_qr_image用qrcode库现场生成PNG base64；GET /api/wechat/qrcode端点 |
| `runtime_config.py` | 运行时配置工具 | is_production():AI_GF_ENV>APP_ENV>ENV三级检测prod/production；get_database_url():APP_DATABASE_URL>DATABASE_URL>默认sqlite+aiosqlite:///data/users.db；同步转异步驱动映射(postgresql→asyncpg/mysql→aiomysql/sqlite→aiosqlite),已含+驱动原样返回 |
| `health_routes.py` | 健康检查路由 | GET /api/health轻量存活探针(无认证,200+version 3.1.0+environment)；GET /api/ready就绪检查(200/503)：orchestrator组件级(llm/memory/emotion/persona四组件非空校验)+**route_mounts能力矩阵**(路由静默缺失=未就绪)+health_checker状态 |
| `main_routes.py` | 共享常量+Pydantic模型 | SENSITIVE_FIELDS七敏感键+五后缀；_sanitize_config递归掩码(保留max_tokens等无害字段)；ChatRequest(message≤10000字/message_type四选一pattern校验/character_id≤128)；ChatResponse(reply/trace_id/emotion)；ConfigUpdateRequest/ProactiveConfigRequest/ToolToggleRequest；UPLOAD_DIR+MAX_UPLOAD_SIZE 50MB(上限100MB)/MAX_RAG_UPLOAD_SIZE 10MB |
| `state/training_state.py` (3KB) | 训练状态管理 | RLock保护11字段状态(status/progress/loss/eta等)；ThreadPoolExecutor单worker后台执行submit；stop_event协作式停止；__del__兜底shutdown |
| `state/safety_log.py` | 安全日志 | deque(maxlen=2000)+锁；append带user_id注入；get_recent按用户过滤；get_stats按category聚合(近200条统计) |
| `state/tool_history.py` | 工具历史 | deque(maxlen=1000)简单环形 |

---

## 🧠 WebSocket服务器

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `websocket_server.py` (11.8KB) | **WebSocket推送服务** | websockets库可选(HAS_WEBSOCKETS降级)；MAX_CLIENTS=1000；端口8765(127.0.0.1)；**P0: API Key认证**(_verify_api_key,API_KEY_ENABLED环境变量控制)；_handler客户端消息处理；六种广播：broadcast_proactive主动消息/broadcast_shisi_event通用事件/_parallel_broadcast并行/broadcast_character_switched角色切换/broadcast_emotion_stage_changed阶段变化/broadcast_affinity_changed好感度变化/broadcast_sticker_send表情包；client_count在线数 |

---

## 🧠 路由器端点全景（22文件,~167路由端点）

| 路由文件 | 端点数 | 核心端点 |
|----------|--------|----------|
| `routers/character_routes.py` (36KB,**最大**) | 19 | CRUD /characters + activate激活 + persona读写 + import/export + presets预设列表 + memory/facts记忆事实CRUD + **preview-from-description/generate-from-description(描述生成角色)** + chat/export聊天导出 |
| `routers/chat_routes.py` | 11 | POST /api/chat + /api/chat/stream(SSE流式) + session管理 + chat/history + **微信通道6端点**(connect/disconnect/connection-status/status/status-stream/reconnect) |
| `routers/misc_routes.py` (16.4KB) | 11 | stats/dashboard仪表盘 + memory/facts + logs/logs.stream(SSE日志流) + config读写(**敏感字段掩码**) + user/llm-config用户级LLM配置读写 + channels/routes清单 |
| `routers/knowledge_routes.py` (14.9KB) | 8 | knowledge/stats+search+documents CRUD + **vault(知识库入口SP-4相关!)** + vault/features + crawl爬虫 + enrich增强 |
| `routers/auth_routes.py` (14.4KB) | 7 | register/login(JWT)/refresh/logout/me/change-password/admin/reset-password |
| `routers/llm_providers_routes.py` (12.7KB) | 4 | /all列出全部供应商 + PUT {key}更新 + toggle启停 + DELETE —— 含申请教程 |
| `routers/clone_routes.py` (12.2KB) | 9 | clone/preview预览 + upload上传 + contacts联系人 + datasets数据集CRUD + batch-delete批量删 + stats统计 |
| `websocket_server.py` | - | 见上节 |
| `routers/invite_routes.py` (11.5KB) | 4 | register-invite邀请注册 + admin/invites创建/列出/撤销 |
| `routers/mimo_voice_routes.py` (9.9KB) | 6 | clone音色克隆 + design音色设计 + switch-voice切换 + status + set-engine设置引擎 + synthesize合成 |
| `routers/safety_routes.py` (9.7KB) | 12 | safety/stats+log+config + RAG全套(stats/search/documents) + voice/status+synthesize + files/upload+{filename} + cache/stats+invalidate |
| `routers/wechat_routes.py` (9.4KB) | 8 | connections持久化连接CRUD + bind/bindings微信绑定CRUD |
| `routers/training_routes.py` (8.8KB) | 9 | training/status+progress+extract+clean+test+apply训练流水线 + proactive/state+history+config |
| `routers/storyline_routes.py` (8.3KB) | 6 | storyline CRUD + progress进度 + detect检测 + reset重置 |
| `routers/voice_routes.py` (8.2KB) | 6 | characters/{id}/voice音色绑定CRUD + voice/speakers音色列表 + test试听 |
| `routers/demo_routes.py` (8.1KB) | 4 | **demo/chat/stream访客演示SSE(SP-3系统门面!)** + demo/memory/recall+visualization记忆可视化 + demo/exit |
| `routers/admin_routes.py` (7.5KB) | 5 | users管理CRUD(admin角色require_role保护) |
| `routers/personality_routes.py` (6KB) | 9 | emotion/state+trend + persona/profile+evolution-log + **psych五端点(profile/snapshots/DELETE profile/mental-health/LIWC——对接persona_extractor心理模块)** |
| `routers/users_routes.py` (5.1KB) | 7 | 用户列表/详情/chat历史/emotion状态/role修改/reset重置/DELETE |
| `routers/tools_routes.py` (5KB) | 6 | tools列表+health+toggle + history + plugins列表+toggle |
| `routers/emotion_routes.py` (2.9KB) | 2 | 情绪参数GET/PUT编辑 |
| `routers/memory_routes.py` (3KB) | 4 | favorites收藏CRUD + forward转发(桥接shisi FavoriteManager/ForwardManager) |
| `routers/persona_card_routes.py` (3.3KB) | 3 | persona-card读写+preview(桥接shisi CharacterManager) |

---

## 🔧 主要设计模式

### 1. 应用工厂 + 依赖中心
- app_factory只管装配，业务在routers，共享状态在deps单例
- route_mounts能力矩阵：关键路由挂载失败→readiness报not_ready而非静默404

### 2. 双层认证体系
- **机器层**：X-API-Key（header/query双通道,hmac.compare_digest）
- **人类层**：JWT access/refresh双token + bcrypt密码 + RBAC三级角色

### 3. 多用户隔离贯穿
- User.llm_config JSON列 → 用户级LLM gateway（llm_provider._user_gateways呼应）
- WechatBinding.character_card_id → 微信账号绑角色卡
- SafetyLogManager/RingBufferHandler按user_id过滤

### 4. SSE流式全家桶
- /api/chat/stream、/api/demo/chat/stream、/api/logs/stream、/api/channels/wechat/status-stream 四处SSE

---

## ⚠️ 关键技术要点

### 安全设计亮点
1. JWT_SECRET生产强制≥32字符否则拒绝启动（fail-fast）
2. bcrypt直调用绕过passlib兼容性地雷
3. refresh token哈希入库（泄库不泄token）
4. 配置读取递归掩码敏感字段
5. 上传大小双层限制（env可配但硬顶100MB）
6. path_security防路径遍历（sanitize+resolve前缀双保险）

### 与CODE_GRAPH.md对照
- docstring自述"17个include_router共204端点,实扫2026-07-30"
- 本次正则实扫@router装饰器约167端点（差异来自多方法装饰器/qrcode_store/health等非标准计数口径），架构真值一致

### 与其他模块的集成点
| 集成对象 | 方式 |
|---------|------|
| orchestrator | run_api全局实例化,deps.orch注入 |
| ProactiveScheduler | run_api注册websocket+wechat双通道sender |
| shisi/* | deps.shisi_reg挂载13子管理器,/api/shisi/status探测 |
| persona_extractor | personality_routes psych五端点暴露心理画像 |
| voice/mimo_tts | mimo_voice_routes六端点 |
| wechat_direct | chat_channels+wechat_routes+qrcode_store三层 |

---

## 📋 已读文件列表（完整41个）

根目录(15): __init__.py, app_factory.py, run_api.py, deps.py, database.py, auth.py, auth_jwt.py, path_security.py, session_manager.py, qrcode_store.py, runtime_config.py, health_routes.py, main_routes.py, websocket_server.py  
routers/(23): __init__.py + admin/auth/character/chat/clone/demo/emotion/invite/knowledge/llm_providers/memory/mimo_voice/misc/persona_card/personality/safety/storyline/tools/training/users/voice/wechat_routes  
state/(4): __init__.py, training_state.py, safety_log.py, tool_history.py
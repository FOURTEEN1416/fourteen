# Project File Inventory

> Generated: 2026-05-31 | Purpose: 全项目文件按域分类，方便维护和快速定位

---

## 1. Frontend (React + TypeScript + Vite)

### 1.1 Entry Points
| File | Purpose |
|------|---------|
| `frontend/src/main.tsx` | React DOM 入口 |
| `frontend/src/App.tsx` | 全局路由 + Layout 编排 |
| `frontend/src/index.css` | 全局样式 + Tailwind 指令 |
| `frontend/src/vite-env.d.ts` | Vite 类型声明 |

### 1.2 Pages (14 pages)
| Route | File | Status |
|-------|------|--------|
| `/wechat` | `pages/WeChatPage.tsx` | ✅ Production |
| `/users` | `pages/UsersPage.tsx` | ⚠️ Partial mock |
| `/users/:userId` | `pages/UserWorkspace.tsx` | ✅ Production |
| `/users/:userId/roles/create` | `pages/CreateRole.tsx` | ⚠️ Partial mock |
| `/users/:userId/roles/:roleId/settings` | `pages/RoleSettings.tsx` | ✅ Production |
| `/users/:userId/roles/:roleId/status` | `pages/StatusCenter.tsx` | ✅ Production |
| `/users/:userId/roles/:roleId/storyline` | inline in App.tsx → StorylineEditor | ✅ Production |
| `/settings/llm` | `pages/SettingsLLM.tsx` | ✅ Production |
| `/settings/voice` | `pages/SettingsVoice.tsx` | ⚠️ Partial mock |
| `/settings/tools` | `pages/ToolsDashboard.tsx` | ✅ Production |
| `/settings/security` | `pages/SettingsSecurity.tsx` | 🔴 Full mock + tab bug |
| `/settings/logs` | `pages/SettingsLogs.tsx` | ⚠️ Partial mock |
| `/settings/general` | `pages/SettingsGeneral.tsx` | ✅ Production |
| `*` | `pages/NotFoundPage.tsx` | ✅ Production |
| — | `pages/SystemSettingsLayout.tsx` | Layout wrapper |

**Note:** `pages/CreateRole.tsx.bak` — 备份文件，可清理。

### 1.3 Components

#### Layout (3)
| Component | Purpose |
|-----------|---------|
| `components/layout/Sidebar.tsx` | 三级动态导航 |
| `components/layout/Breadcrumb.tsx` | 面包屑导航 |
| `components/layout/MobileNav.tsx` | 移动端底部导航 |

#### Shared UI Kit (21)
| Component | Purpose |
|-----------|---------|
| `AnimatedPage.tsx` | 页面过渡动画（opacity fade） |
| `AnimatedNumber.tsx` | 数字动画 |
| `Badge.tsx` | 徽标 |
| `ConfirmDialog.tsx` | 确认对话框 |
| `DangerButton.tsx` | 危险操作按钮 |
| `EmptyState.tsx` | 空状态占位 |
| `FileUpload.tsx` | 文件上传 |
| `Modal.tsx` | 模态框 |
| `ParallaxTilt.tsx` | 倾斜视差效果 |
| `ProgressBar.tsx` | 进度条 |
| `ScrollProgress.tsx` | 页面滚动进度 |
| `Select.tsx` | 下拉选择 |
| `Skeleton.tsx` | 骨架屏 |
| `Slider.tsx` | 滑块 |
| `StaggerContainer.tsx` | 子元素逐行动画（当前未使用） |
| `SubTabBar.tsx` | 子标签栏 |
| `Tabs.tsx` | 标签切换 |
| `TagInput.tsx` | 标签输入 |
| `Toast.tsx` | 通知提示 |
| `Toggle.tsx` | 开关 |
| `Tooltip.tsx` | 工具提示 |
| `index.ts` | 统一导出 |

#### Common (9)
| Component | Purpose |
|-----------|---------|
| `Badge.tsx` | 通用徽标 |
| `Button.tsx` | 通用按钮 |
| `Card.tsx` | 卡片容器 |
| `EmptyState.tsx` | 空状态 |
| `ErrorBoundary.tsx` | 错误边界 |
| `ProactiveEnginePanel.tsx` | 主动引擎面板 |
| `SensitiveInput.tsx` | 敏感信息输入 |
| `Skeleton.tsx` | 加载骨架 |
| `Toast.tsx` | Toast 通知 |
| `UrgencyBadge.tsx` | 紧急度徽标 |
| `index.ts` | 统一导出 |

#### Chat (5)
| Component | Purpose |
|-----------|---------|
| `ChatInput.tsx` | 聊天输入框 |
| `MessageBubble.tsx` | 消息气泡 |
| `MessageList.tsx` | 消息列表 |
| `ProactiveToast.tsx` | 主动消息提示 |
| `TypingIndicator.tsx` | 键入指示器 |

#### Storyline (3)
| Component | Purpose |
|-----------|---------|
| `StorylineEditor.tsx` | 剧情编辑器核心 |
| `StorylineIndicator.tsx` | 剧情进度指示 |
| `KnowledgePreview.tsx` | 知识预览 |

#### Emotion (1)
| Component | Purpose |
|-----------|---------|
| `EmotionPanel.tsx` | 情绪面板 |

#### UI (1)
| Component | Purpose |
|-----------|---------|
| `Modal.tsx` | UI 模态框 |

### 1.4 API Client (10 files)
| File | Purpose |
|------|---------|
| `api/client.ts` | Axios 实例 + 拦截器 |
| `api/queryClient.ts` | React Query 客户端配置 |
| `api/characters.ts` | 角色 CRUD API |
| `api/chat.ts` | 聊天 API |
| `api/clone.ts` | 语音克隆 API |
| `api/mimo.ts` | MiMo TTS API |
| `api/system.ts` | 系统设置 API |
| `api/training.ts` | 训练 API |
| `api/users.ts` | 用户 API |
| `api/wechat.ts` | 微信 API |

### 1.5 Hooks (8 files)
| Hook | Purpose |
|------|---------|
| `hooks/useQueries.ts` | React Query 封装（角色/用户/聊天） |
| `hooks/useDashboardData.ts` | 仪表板数据 |
| `hooks/useInView.ts` | 视口检测 |
| `hooks/useMousePosition.ts` | 鼠标位置追踪 |
| `hooks/useSmartPoll.ts` | 智能轮询 |
| `hooks/useSSE.ts` | SSE 连接 |
| `hooks/useWebSocket.ts` | WebSocket 连接 |
| `hooks/index.ts` | 统一导出 |

### 1.6 State Store (Zustand, 5 files)
| Store | Purpose |
|-------|---------|
| `store/chatStore.ts` | 聊天 UI 状态 |
| `store/characterBuilderStore.ts` | 角色创建表单状态 |
| `store/settingsStore.ts` | 设置面板状态 |
| `store/errorStore.ts` | 错误收集 |
| `store/logStore.ts` | 日志收集 |

### 1.7 Types (3 files)
| File | Purpose |
|------|---------|
| `types/api.ts` | API 请求/响应类型 |
| `types/framework.ts` | 框架基础类型 |
| `types/sticker.ts` | 表情包类型 |

---

## 2. Backend (Python + FastAPI)

### 2.1 API Layer
#### Core (7 files)
| File | Purpose |
|------|---------|
| `api/app_factory.py` | FastAPI 应用工厂 |
| `api/main_routes.py` | 主路由 / WebSocket |
| `api/deps.py` | 依赖注入（DB/Config/Session） |
| `api/auth.py` | X-API-Key 鉴权 |
| `api/session_manager.py` | 用户会话管理 |
| `api/websocket_server.py` | WebSocket 服务 |
| `api/qrcode_store.py` | 微信二维码存储 |

#### Routers (9 files, under `api/routers/`)
| Router | Purpose |
|--------|---------|
| `character_routes.py` | 角色 CRUD |
| `emotion_routes.py` | 情绪查询/更新 |
| `knowledge_routes.py` | 知识库管理 |
| `memory_routes.py` | 记忆查询 |
| `mimo_voice_routes.py` | MiMo TTS 转换 |
| `persona_card_routes.py` | 人设卡管理 |
| `storyline_routes.py` | 剧情线 CRUD + 配置 + 进度 |
| `voice_routes.py` | 语音管理 |
| `wechat_routes.py` | 微信连接/消息 |

#### State (3 files)
| File | Purpose |
|------|---------|
| `api/state/safety_log.py` | 安全日志 |
| `api/state/tool_history.py` | 工具调用历史 |
| `api/state/training_state.py` | 训练状态追踪 |

### 2.2 LLM Provider
| File | Purpose |
|------|---------|
| `llm_provider/llm_gateway.py` | LLM 路由网关 |
| `llm_provider/multi_provider_gateway.py` | 多 Provider Fallback |
| `llm_provider/openai_compatible_provider.py` | OpenAI 兼容接口 |
| `llm_provider/prompt_template_manager.py` | Prompt 模板管理 |

### 2.3 Emotion Engine (my_character/)
| File | Purpose |
|------|---------|
| `my_character/emotion_engine.py` | 情感引擎核心 |
| `my_character/emotion_memory.py` | 情感记忆 |
| `my_character/emotion_style_coupler.py` | 情感-风格耦合 |
| `my_character/evolution_engine.py` | 角色进化引擎 |
| `my_character/persona_engine.py` | 人设引擎 |
| `my_character/persona_card.py` | 人设卡 |
| `my_character/persona_schema.py` | 人设 Schema |
| `my_character/persona_evaluator.py` | 人设评估 |
| `my_character/persona_utils.py` | 人设工具函数 |
| `my_character/style_enhancer.py` | 风格增强 v1 |
| `my_character/style_enhancer_v2.py` | 风格增强 v2 |
| `my_character/tone_mimic.py` | 语气模仿 |
| `my_character/contextual_behavior.py` | 上下文行为 |
| `my_character/character_config.py` | 角色配置 |
| `my_character/constraint_validator.py` | 约束验证 |
| `my_character/consistency_checker.py` | 一致性检查 |
| `my_character/dynamic_anchor.py` | 动态锚点 |
| `my_character/anchor_protection.py` | 锚点保护 |
| `my_character/enhanced_prompt_engine.py` | 增强提示引擎 |

### 2.4 Memory System
| File | Purpose |
|------|---------|
| `memory/memory_pipeline.py` | 记忆流水线 |
| `memory/working_memory.py` | 工作记忆 |
| `memory/episodic_memory.py` | 情景记忆 |
| `memory/semantic_memory.py` | 语义记忆 |
| `memory/structured_memory.py` | 结构化记忆 |
| `memory/vector_memory.py` | 向量记忆 |
| `memory/conversation_summarizer.py` | 对话摘要 |
| `memory/diary_summarizer.py` | 日记摘要 |
| `memory/fact_extractor.py` | 事实提取 |
| `memory/importance_scorer.py` | 重要性评分 |
| `memory_ext/mem0_backend.py` | Mem0 后端适配器 |

### 2.5 Knowledge / RAG
| File | Purpose |
|------|---------|
| `knowledge_vault/knowledge_store.py` | 知识库存储 |
| `knowledge_vault/knowledge_injector.py` | 知识注入 |
| `knowledge_vault/content_extractor.py` | 内容提取 |
| `knowledge_vault/feed_reader.py` | 订阅源读取 |
| `knowledge_vault/search_collector.py` | 搜索收集 |
| `knowledge_vault/scheduler.py` | 定时任务 |
| `knowledge_vault/persona_rewriter.py` | 人设重写器 |
| `rag_engine/rag_engine.py` | RAG 引擎核心 |
| `plugins/weather.py` | 天气插件 |

### 2.6 Voice / TTS
| File | Purpose |
|------|---------|
| `voice/tts_manager.py` | TTS 管理器 |
| `voice/tts_provider_base.py` | TTS Provider 基类 |
| `voice/mimo_tts_provider.py` | MiMo Cloud TTS |
| `voice/edge_tts_provider.py` | Edge-TTS |
| `voice/bert_vits2_provider.py` | Bert-VITS2 |
| `voice/cosyvoice_provider.py` | CosyVoice |
| `voice/sovits_provider.py` | SoVITS |
| `voice/audio_converter.py` | 音频格式转换 |
| `voice/voice_training.py` | 语音训练 |
| `voice/clone_data_manager.py` | 克隆数据管理 |

### 2.7 WeChat Integration
| File | Purpose |
|------|---------|
| `wechat_direct/wechat_connector.py` | 微信直连连机器 |
| `wechatmsg_src/adapter.py` | 微信消息源适配器 |
| `weclone_adapter/adapter.py` | WeClone 适配器 |
| `weclone_adapter/style_profiler.py` | 风格分析器 |

### 2.8 Security
| File | Purpose |
|------|---------|
| `security/content_safety.py` | 内容安全过滤器 |
| `security/encryption.py` | 加密管理器 |
| `security/pii_anonymizer.py` | PII 匿名化 |
| `security/prompt_injection.py` | 提示注入检测 |

### 2.9 Observability
| File | Purpose |
|------|---------|
| `observability/logging_setup.py` | 日志配置 |
| `observability/metrics.py` | 指标收集 |
| `observability/tracing.py` | 追踪 |
| `observability/health.py` | 健康检查 |
| `observability/config_manager.py` | 配置管理 |
| `observability/config_models.py` | 配置模型 |
| `observability/graceful_shutdown.py` | 优雅关闭 |

### 2.10 Cache & Tools
| File | Purpose |
|------|---------|
| `cache/llm_cache.py` | LLM 响应缓存 |
| `cache/redis_client.py` | Redis 客户端 |
| `tools/base_tool.py` | 工具基类 |
| `wechatmsg_src/adapter.py` | 消息源适配 |

### 2.11 Legacy (shisi/) — 逐步废弃
| File | Purpose |
|------|---------|
| `shisi/` | 旧架构模块（ASE/Storyline/Migration） |
| `shisi/config.py` | 旧配置 |
| `shisi/migrations.py` | 旧迁移 |

### 2.12 Tests (37 files)
| File | Purpose |
|------|---------|
| `tests/conftest.py` | Pytest fixtures |
| `tests/test_character.py` | 角色测试 |
| `tests/test_storyline.py` | 剧情线测试 |
| `tests/test_memory.py` | 记忆测试 |
| `tests/test_integration.py` | 集成测试 |
| `tests/test_audio_converter.py` | 音频转换测试 |
| `tests/test_cache.py` | 缓存测试 |
| `tests/test_content_safety.py` | 安全测试 |
| `tests/test_pii_anonymizer.py` | PII 测试 |
| `tests/test_prompt_injection.py` | 注入检测测试 |
| `tests/test_rag_engine.py` | RAG 测试 |
| `tests/test_wechat.py` | 微信测试 |
| ... +25 more | 覆盖各模块 |

---

## 3. Config & Deployment

### Root Config
| File | Purpose |
|------|---------|
| `config/system.yaml` | 主配置 |
| `config/system_prod.yaml` | 生产配置 |
| `config/system_test.yaml` | 测试配置 |
| `config/llm_providers.json` | LLM Provider 配置 |
| `config/persona.yaml` | 人设配置 |
| `config/emotion.yaml` | 情感配置 |
| `config/emotion_style_matrix.yaml` | 情感风格矩阵 |
| `config/shisi.yaml` | 旧架构配置 |
| `.env` | 环境变量（忽略） |
| `.env.example` | 环境变量模板 |

### Build & Deploy
| File | Purpose |
|------|---------|
| `pyproject.toml` | Python 项目配置 |
| `requirements.txt` | Python 依赖 |
| `main.py` | 主入口 |
| `user_scheduler.py` | 多用户调度器 |
| `orchestrator/` | Orchestrator 编排包（实际逻辑在 orchestrator/optimized_orchestrator.py） |
| `frontend/package.json` | 前端依赖 |
| `frontend/vite.config.ts` | Vite 构建配置 |
| `frontend/tsconfig.json` | TypeScript 配置 |
| `frontend/tailwind.config.js` | Tailwind 配置 |
| `frontend/postcss.config.js` | PostCSS 配置 |

### Data (ignored)
| File | Purpose |
|------|---------|
| `data/sqlite.db` | SQLite 数据库 |
| `data/app.log` | 应用日志 |
| `data/api_*.log` | API 日志 |
| `data/wechat_*.json` | 微信状态持久化 |
| `data/proactive_state.json` | 主动引擎状态 |

---

## 4. Documentation & Architecture

| File | Purpose |
|------|---------|
| `docs/adr/ADR-000*.md` | 架构决策记录（6个） |
| `docs/architecture/8-layer-code-map.md` | 8 层代码地图 |
| `docs/architecture/design-principles.md` | 设计原则 |
| `docs/architecture/fitness-functions.md` | Fitness Functions |
| `docs/architecture/bus-factor.md` | 核心贡献者分析 |
| `docs/architecture/gap-closure-20260527.md` | 差距闭合计划 |
| `docs/superpowers/plans/` | 设计计划 |
| `docs/superpowers/specs/` | 设计规范 |
| `README.md` | 项目说明 |

---

## 5. Temp / Cleanup Candidates

| File | Reason |
|------|--------|
| `frontend/src/pages/CreateRole.tsx.bak` | 备份残留，可删 |
| `page-2026-05-28T08-57-40-251Z.png` | 截图 |
| `page-2026-05-29T09-58-20-667Z.png` | 截图 |
| `page-2026-05-29T14-01-04-524Z.png` | 截图 |
| `page-2026-05-30T13-01-26-801Z.png` | 截图 |
| `scripts/start.bat` | 启动脚本，与 `start.ps1` 重复 |
| `run_backend.cmd` | 启动脚本，与 `start.ps1` 重复 |
| `start.ps1` | 启动脚本，评估是否保留 |

---

## 6. Module Size Summary

| Module | Source Files | Lines (est) | Category |
|--------|-------------|-------------|----------|
| `frontend/` | ~45 | ~15,000 | UI |
| `my_character/` | 20 | ~4,000 | 情感引擎 |
| `api/` | 22 | ~3,500 | API 层 |
| `memory/` | 11 | ~2,000 | 记忆系统 |
| `voice/` | 11 | ~2,500 | 语音 |
| `shisi/` | 93 | ~15,000 | 旧架构 |
| `tests/` | 37 | ~8,000 | 测试 |
| `knowledge_vault/` | 8 | ~1,500 | 知识库 |
| `observability/` | 8 | ~1,000 | 可观测性 |
| `security/` | 5 | ~800 | 安全 |
| `config/` | 8 | ~200 | 配置 |
| `docs/` | 15+ | ~2,000 | 文档 |

---

## 7. Dependency Map

```
Frontend (React) ←── API Client ←── Backend (FastAPI) ←── Services
     ↕                              ↕                        ↕
  Zustand Store                 SQLAlchemy ORM         LLM / Memory / Voice
     ↕                                                    ↕
  React Query                                           External APIs
```

*Detailed dependency graph in `docs/architecture/knowledge-graph.md`*

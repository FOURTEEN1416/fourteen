# 唯一的你——十四

<p align="center">
  <img src="https://img.shields.io/badge/version-3.1.0-blue">
  <img src="https://img.shields.io/badge/Python-3.10+-blue">
  <img src="https://img.shields.io/badge/React-19-61dafb">
  <img src="https://img.shields.io/badge/Vite-8-646cff">
  <img src="https://img.shields.io/badge/TypeScript-6-3178c6">
  <img src="https://img.shields.io/badge/Tailwind-4-38bdf8">
  <img src="https://img.shields.io/badge/Zustand-5-orange">
  <img src="https://img.shields.io/badge/Tests-1751-brightgreen">
  <img src="https://img.shields.io/badge/license-MIT-yellow">
</p>

微信扫码就能聊，控制台调角色和语音。基于 LLM 的智能情感陪伴系统。

> **测试口径**（2026-09-21 v1.35 收仓口径统一实测）：后端 **1653 passed / 4 skipped**（收集 **1657**，0 失败；现役角色卡 **41 张**）；⚠️ 单进程整跑会在随机位置停住，分块跑法见 `AGENTS.md` §4.3；
> 前端 `98 passed`（vitest 16 文件）+ `tsc --noEmit` 0 错误。
> ⚠️ **基线随 `config/characters/` 卡数浮动**（该目录被 `.gitignore` 忽略、内容不随 git 复现；用例数 = 2 × 卡数 + 7）。**引用基线必须同时声明卡数**。

---

## 安全须知（公开仓库必读）

本仓库远程为 **Public**。启动与部署前请阅读：

1. **JWT**：非显式 dev（`AI_GF_ENV`/`APP_ENV`/`ENV` 不是 `dev`/`development`）时 **必须** 设置 `JWT_SECRET`（≥32 字符，`openssl rand -base64 48`），否则 `api/auth_jwt.py` 拒绝启动。公开 DEV 兜底密钥不可用于任何真实环境。
2. **API Key**：`.env.example` 中的占位符在认证启用且非显式 dev 时会导致启动失败（fail-closed）。生产请替换为 `openssl rand -base64 32` 的随机值。
3. **部署目标**：`deploy/` 脚本中的主机已改为占位符/环境变量（`DEPLOY_HOST` / `DEPLOY_DOMAIN`）。**若历史版本曾暴露** `139.199.199.174` 或相关 SSH 凭据，请 **轮换凭据** 并复查该主机暴露面；真实 IP/端口/路径只放私密运维文档。
4. **LLM 供应商密钥**：管理控制台保存的 key 只写入 `config/llm_providers.local.json`（已 gitignore）或环境变量；**不要**把真实 key 提交进 `config/llm_providers.json`。
5. **历史**：旧 commit 曾提交过 `.env` 占位符（`4d67ca2`）；若当时写过真实凭据，必须轮换。

```bash
# 本地开发（允许 DEV 兜底，仍建议配置密钥）
cp .env.example .env   # 确认 AI_GF_ENV=dev，并填写 LLM key
# JWT_SECRET=$(openssl rand -base64 48)   # 写入 .env 更稳妥

# 生产
export AI_GF_ENV=prod
export JWT_SECRET="$(openssl rand -base64 48)"
export API_KEY="$(openssl rand -base64 32)"
export API_KEY_ENABLED=true
```

---

## 快速开始

```bash
git clone https://github.com/FOURTEEN1416/fourteen.git
cd fourteen

python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env   # 填 LLM 的 key
python main.py
```

终端会打印二维码，微信扫一下就开始聊。

**前提：** Python 3.10+。FFmpeg 和 Redis 是可选的（语音转码 / 缓存用，没有也能跑）。

---

## 它能做什么

| 能力 | 说明 |
|------|------|
| **微信聊天** | 扫码登录，文字/语音消息都支持。**每人独立微信通道**（一人最多 2 条，互不干扰；好友可在微信里回复「角色」自选扮演角色），多用户同时聊，各自独立 |
| **角色系统** | 每个微信用户绑一个角色卡，性格、说话风格、口头禅都能调 |
| **情感引擎** | 聊得越久越了解你，有亲密度和情感阶段变化 |
| **主动搭话** | 不全是等你发消息，系统也会主动找话题 |
| **语音合成** | 文字回复能自动转语音发到微信。MiMo Cloud TTS（基础合成 / 语音克隆 / 音色设计） |
| **记忆系统** | 会记住你说过的事（三层记忆：短期+情景+长期） |
| **工具** | 天气、搜索、日历、计算器、提醒、时间感知等 12 个内置工具 |
| **剧情线** | 和角色的关系可以按"剧情"推进，有支线和进度追踪 |
| **邀请码注册** | 内测期间通过邀请码注册，管理员在控制台生成 |
| **管理控制台** | React 前端，17 个页面，角色管理/语音设置/系统配置一站式 |

> **语音引擎现状**：2026-08-28 起收敛为 **MiMo Cloud 单一引擎**，
> Edge-TTS / GPT-SoVITS / CosyVoice / Bert-VITS2 已从代码库删除。

---

## 配置

一个文件搞定：`config/system.yaml`。主要改这几块：

```yaml
llm:
  provider: auto          # auto = 自动 fallback 链
  # fallback_chain: agnes → zhipu → xunfei → baidu
  temperature: 0.85

voice:
  engine: "mimo-tts"      # 唯一引擎（2026-08-28 MiMo-only 收敛）
  mimo-tts:
    enabled: true
    api_key: "${MIMO_API_KEY}"
    model: "mimo-v2.5-tts"

wechat: {}                # 微信直连，扫码自动配
```

`.env` 里放 LLM 的 key。不填也能跑——fallback 链最底层有免费模型兜底。

> **配置注意（审查修正）**
> - tracked 的 `config/system.yaml` 默认 `env: dev` / `debug: true`，**仅供本地开发**。
>   生产必须用环境变量覆盖或部署 prod 配置（`debug: false`，`AI_GF_ENV=prod`）。
> - `config/characters/` 在 `.gitignore` 中，**不会**随公开仓克隆分发。
>   文档中的「角色卡库」指运行/部署侧需单独投递的目录（私有包或服务器本地），不是 git 真源。
> - `config/llm_providers.json` 只存供应商元数据（`api_key` 恒为空）；
>   真实 key 走 `config/llm_providers.local.json`（gitignored）或 `*_API_KEY` 环境变量。

---

## 怎么跑

```bash
# 控制台模式（后端 + 微信通道，默认入口）
python main.py

# 仅启动 API（:8000）
python -m uvicorn api.run_api:app --host 0.0.0.0 --port 8000

# 单独启动前端（:5173，Vite 代理 /api → :8000、/ws → :8765）
cd frontend && npm run dev
```

> `start_all.cmd` 等一键启动脚本已于 2026-08-28 删除，请使用上表命令。

启动后：
- 管理控制台：`http://localhost:5173`
- API 文档：`http://localhost:8000/docs`

---

## 怎么测

```bash
# 后端（注意：PYTHONPATH= 前缀用于清空宿主注入的 safe-delete 护栏）
PYTHONPATH= python -m pytest -q

# 前端
cd frontend && npm test          # vitest
cd frontend && npm run typecheck # tsc --noEmit

# 其他
PYTHONPATH= python -m pytest -m "not slow"   # 跳过慢用例
PYTHONPATH= python -m pytest --cov=. --cov-report=html
```

---

## 项目结构

```
├── api/                  FastAPI 后端（220 端点 / 186 条路径，2026-09-21 内省实测）
│   ├── app_factory.py    create_api_app() —— 唯一 app 工厂
│   ├── routers/          22 个域路由模块（character/chat/misc/personality/users/
│   │                     training/tools/safety/clone/auth/admin/invite/voice/
│   │                     mimo_voice/storyline/wechat/wechat_channel/emotion/memory/
│   │                     knowledge/persona_card/llm_providers）
│   └── achievement_engine.py / database.py / auth_jwt.py / deps.py ...
├── orchestrator/         编排器（9 文件）：主类 + _InitPhasesMixin + _StreamPipelineMixin
│                         + session_locks + voice_detector + console_chat
│                         + tool_gate（工具意图分级）+ context_budget（上下文预算/信封）
├── shisi/                DDD 领域层（116 文件）：application / core / infrastructure /
│                         character / knowledge / memory / affinity / voice / vault ...
├── utils/                公共工具（11 文件）：local_time（墙钟真源）/ fallback_lines /
│                         affinity_state / reply_mode / async_utils / important_dates ...
├── voice/                MiMo Cloud TTS + 音频转码（silk）
├── wechat_direct/        微信直连（每人独立通道：connector_registry + channel_paths
│                         + peer_character + wechat_connector）
├── user_scheduler.py     多用户调度（每个微信用户独立情感状态）
├── my_character/         情感引擎 + 角色卡
├── llm_provider/         LLM 接入层（多供应商 fallback）
├── proactive/            主动搭话调度（APScheduler）
├── persona_extractor/    人格提取 / PAD 检测 / 网络画像增强
├── security/             4 安全模块（content_safety / encryption /
│                         pii_anonymizer / prompt_injection）
├── tools/                内置工具（12 个）
├── frontend/             React 管理控制台
│   └── src/
│       ├── api/          13 个 API 模块（按域拆分）
│       ├── pages/        17 个页面
│       ├── store/        Zustand 3 个（authStore / characterBuilderStore / errorStore）
│       ├── hooks/        React Query hooks
│       └── components/   layout + auth + shared + common + admin + llm + storyline
├── tests/                1653 后端测试通过 + 4 跳过（2026-09-21 v1.35 收仓口径统一分块实测，收集 1657）+ 98 前端测试
├── config/               YAML 配置（角色卡 config/characters/ 为 gitignore 本地/部署投递，非公开仓内容；现役 41 张）
└── main.py               入口
```

> **注：** 早期文档中的 `rag_engine/` 目录已不存在，检索能力现位于
> `shisi/knowledge/`（RAGEngineV2 / Retriever / CharacterKnowledgeService）。
> `start_all.cmd` / `deploy_ai_girlfriend.bat` 等一键脚本已于 2026-08-28 删除，部署统一走 `deploy/`。

---

## 架构

- **架构地图**：`docs/CODEMAPS/ARCHITECTURE.md`
- **设计原则**：`docs/architecture/design-principles.md`
- **架构决策记录**：`docs/adr/`（**12 份**，ADR-0001~0007 + ADR-0011~**0015**；0015 = 系统提示词分层与按需注入）
- **代码图谱**：`CODE_GRAPH.md`
- **知识图谱**：`docs/architecture/knowledge-graph.md`

---

## 开发约定

```bash
ruff check .            # lint（提交前须零错误）
ruff format .           # 格式化
mypy .                  # 类型检查
cd frontend && npm run build      # 前端构建（tsc -b && vite build）
cd frontend && npm test           # Vitest 前端单元测试
cd frontend && npm run test:e2e   # Playwright E2E
```

CI 会自动跑 lint / type / test / build。具体规则见 `docs/architecture/fitness-functions.md`。

---

## 许可证

MIT

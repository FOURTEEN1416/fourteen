# 唯一的你——十四

<p align="center">
  <img src="https://img.shields.io/badge/version-3.1.0-blue">
  <img src="https://img.shields.io/badge/Python-3.10+-blue">
  <img src="https://img.shields.io/badge/React-19-61dafb">
  <img src="https://img.shields.io/badge/Vite-8-646cff">
  <img src="https://img.shields.io/badge/TypeScript-6-3178c6">
  <img src="https://img.shields.io/badge/Tailwind-4-38bdf8">
  <img src="https://img.shields.io/badge/Zustand-5-orange">
  <img src="https://img.shields.io/badge/Tests-1147-brightgreen">
  <img src="https://img.shields.io/badge/license-MIT-yellow">
</p>

微信扫码就能聊，控制台调角色和语音。基于 LLM 的智能情感陪伴系统。

> **测试口径**（2026-09-18 实测）：后端 `1060 passed / 4 skipped`（系统 Python 3.12）；
> 前端 `87 passed`（vitest 15 文件）+ `tsc --noEmit` 0 错误。

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
| **微信聊天** | 扫码登录，文字/语音消息都支持。多用户可以同时聊，各自独立 |
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
├── api/                  FastAPI 后端（204 端点 / 171 条路径，2026-09-17 内省实测）
│   ├── app_factory.py    create_api_app() —— 唯一 app 工厂
│   ├── routers/          21 个域路由模块（character/chat/misc/personality/users/
│   │                     training/tools/safety/clone/auth/admin/invite/voice/
│   │                     mimo_voice/storyline/wechat/emotion/memory/knowledge/
│   │                     persona_card/llm_providers）
│   └── achievement_engine.py / database.py / auth_jwt.py / deps.py ...
├── orchestrator/         编排器（7 文件）：主类 + _InitPhasesMixin + _StreamPipelineMixin
│                         + session_locks + voice_detector + console_chat
├── shisi/                DDD 领域层（121 文件）：application / core / infrastructure /
│                         character / knowledge / memory / affinity / voice / vault ...
├── voice/                MiMo Cloud TTS + 音频转码（silk）
├── wechat_direct/        微信直连（扫码登录 + 收发消息）
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
│       ├── api/          11 个 API 模块（按域拆分）
│       ├── pages/        17 个页面
│       ├── store/        Zustand 3 个（authStore / characterBuilderStore / errorStore）
│       ├── hooks/        React Query hooks
│       └── components/   layout + auth + shared + common + admin + llm + storyline
├── tests/                1060 后端测试通过 + 4 跳过（2026-09-18 实测）+ 87 前端测试
├── config/               YAML 配置 + config/characters/ 角色卡库
└── main.py               入口
```

> **注：** 早期文档中的 `rag_engine/` 目录已不存在，检索能力现位于
> `shisi/knowledge/`（RAGEngineV2 / Retriever / CharacterKnowledgeService）。

---

## 架构

- **架构地图**：`docs/CODEMAPS/ARCHITECTURE.md`
- **设计原则**：`docs/architecture/design-principles.md`
- **架构决策记录**：`docs/adr/`（11 份，ADR-0001~0007 + ADR-0011~0014）
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

# 唯一的你

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue">
  <img src="https://img.shields.io/badge/TypeScript-React%2018-3178c6">
  <img src="https://img.shields.io/badge/Tests-626+-brightgreen">
  <img src="https://img.shields.io/badge/license-MIT-yellow">
</p>

微信扫码就能聊，控制台调角色和语音。

---

## 快速开始

```bash
git clone https://github.com/fourteen-ai/unique-you.git
cd unique-you

python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env   # 填 LLM 的 key
python main.py
```

终端会打印二维码，微信扫一下就开始聊。

**前提：** Python 3.12+。FFmpeg 和 Redis 是可选的（语音转换 / 缓存用，没有也能跑）。

---

## 它能做什么

| 能力 | 说明 |
|------|------|
| **微信聊天** | 扫码登录，文字/语音消息都支持。多用户可以同时聊，各自独立 |
| **角色系统** | 每个微信用户绑一个角色卡，性格、说话风格、口头禅都能调 |
| **情感引擎** | 聊得越久越了解你，有亲密度和情感阶段变化 |
| **主动搭话** | 不全是等你发消息，系统也会主动找话题 |
| **语音合成** | 文字回复能自动转语音发到微信。支持 MiMo 云 / Edge-TTS / 本地模型 |
| **记忆系统** | 会记住你说过的事（三层记忆：短期+情景+长期） |
| **工具** | 天气、日历、提醒、搜索……需要什么可以加 |
| **剧情线** | 和角色的关系可以按"剧情"推进，有支线和进度追踪 |
| **邀请码注册** | 内测期间通过邀请码注册，管理员在控制台生成 |
| **管理控制台** | React 前端，15 个页面，角色管理/语音设置/系统配置一站式 |

---

## 配置

一个文件搞定：`config/system.yaml`。主要改这几块：

```yaml
llm:
  provider: auto          # auto = 自动 fallback 链（智谱→讯飞→百度→免费模型）
  temperature: 0.85

voice:
  engine: "mimo-tts"      # 默认语音引擎
  mimo-tts:
    enabled: true
    api_key: "sk-xxx"     # MiMo API key
    model: "mimo-v2.5-tts"

wechat: {}                # 微信直连，扫码自动配
```

`.env` 里放 LLM 的 key。不填也能跑——fallback 链最底层有个免费模型兜底。

---

## 怎么跑

```bash
# 一键启动（前后端）
.\start_all.cmd

# 单独启动后端（:8000）
python -m uvicorn api.run_api:app --reload --host 0.0.0.0 --port 8000

# 单独启动前端（:5173）
cd frontend && npx vite --port 5173
```

启动后：
- 管理控制台：`http://localhost:5173`
- API 文档：`http://localhost:8000/docs`

---

## 怎么测

```bash
pytest                          # 全量（626+ 用例）
pytest -m "not slow"           # 跳过慢的
pytest -x tests/test_invite_codes.py  # 邀请码专项测试（15 个）
pytest --cov=. --cov-report=html  # 覆盖率报告
```

---

## 项目结构

```
├── api/                  FastAPI 后端（168+ 路由）
│   ├── _*_routes.py      8 子路由（misc/chat/personality/users/training/tools/safety/clone = 71 端点）
│   ├── main_routes.py    仅 Pydantic 模型 + 常量 + 空 router 占位（95 行，0 端点）
│   └── routers/          13 个域路由（character/auth/admin/invite/voice/mimo/storyline/wechat/emotion/memory/knowledge/persona_card）
├── voice/                语音引擎：MiMo Cloud / Edge-TTS / SoVITS / Bert-VITS2
├── wechat_direct/        微信直连（扫码登录 + 收发消息）
├── girlfriend_manager.py 多用户调度（每个微信用户独立情感状态）
├── my_character/         情感引擎 + 角色卡
├── security/             4 安全模块（内容过滤/加密/脱敏/注入检测）
├── rag_engine/           RAG 检索引擎
├── llm_provider/         LLM 接入层（自动 fallback）
├── frontend/             React 管理控制台
│   └── src/
│       ├── api/          12 个 API 模块（按域拆分，含 auth/invites）
│       ├── pages/        15 个页面（全部注册路由）
│       ├── store/        Zustand（chatStore/errorStore/characterBuilderStore/authStore）
│       ├── hooks/        React Query hooks
│       ├── components/   layout + auth + shared + common + storyline + ui
│       └── types/        TypeScript 类型定义
├── docs/
│   ├── adr/              架构决策记录（10 个：ADR-0001~0006 + ADR-0011~0014）
│   ├── architecture/     8 层地图 / 设计原则 / Fitness Functions / Bus Factor
│   └── audits/           审计报告
├── tests/                626+ 后端单元测试
├── config/               YAML 配置
└── main.py               入口
```

---

## 架构

项目采用三体导航（Triad Navigation）方法论管理：

- **地图**（`.triad-navigation/MAP.md`）：8 层代码地图，描述现状
- **指南针**（`.triad-navigation/COMPASS.md`）：7 条设计原则 + 9 个 ADR
- **闭环控制**（`.triad-navigation/CONTROL.md`）：Fitness Functions + 审计节奏

深入看 `docs/architecture/8-layer-code-map.md` 和 `.triad-navigation/` 目录。

---

## 开发约定

```bash
ruff format .           # 格式化
ruff check .            # lint（CI 强制零错误）
mypy .                  # 类型检查（CI 强制零错误）
bun run build           # 前端构建（cd frontend/）
bun run test            # Vitest 前端单元测试
bunx playwright test    # Playwright E2E 端到端测试
```

CI 会自动跑全部检查（lint/type/test/build），不合规不合并。具体规则见 `docs/architecture/fitness-functions.md`。

---

## 许可证

MIT

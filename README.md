# 十四

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue">
  <img src="https://img.shields.io/badge/TypeScript-React%2018-3178c6">
  <img src="https://img.shields.io/badge/CI-passing-brightgreen">
  <img src="https://img.shields.io/badge/license-MIT-yellow">
</p>

AI 虚拟伴侣。微信扫码就能聊，控制台调角色和语音。

---

## 快速开始

```bash
git clone https://github.com/fourteen-ai/ai-girlfriend.git
cd ai-girlfriend

python -m venv .venv && .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env   # 填 LLM 的 key
python main.py
```

终端会打印二维码，微信扫一下就开始聊。

**前提：** Python 3.10+。FFmpeg 和 Redis 是可选的（语音转换 / 缓存用，没有也能跑）。

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
| **控制台** | 浏览器打开 `localhost:8000`，调角色设语音查记忆 |

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
python main.py                  # 开发模式（终端+微信）
ENV=prod python main.py        # 生产模式
python main.py --no-wechat      # 只要控制台，不连微信
```

启动后：
- 控制台：`http://localhost:8000`
- API 文档：`http://localhost:8000/docs`
- WebSocket：`ws://localhost:8765/chat`

---

## 怎么测

```bash
pytest                          # 全量（525+ 用例）
pytest -m "not slow"           # 跳过慢的
pytest --cov=. --cov-report=html  # 覆盖率报告
```

---

## 项目结构

```
├── api/             FastAPI（统一路由，前端只调这一层）
│   └── routers/     角色 / 语音 / 剧情线 / 记忆 / 知识库
├── voice/           语音引擎：MiMo Cloud / Edge-TTS / SoVITS / Bert-VITS2
├── wechat_direct/   微信直连（扫码登录 + 收发消息）
├── girlfriend_manager.py  多用户调度（每个微信用户独立情感状态）
├── my_character/    情感引擎 + 角色卡
├── shisi/           旧架构（逐步废弃，前端已不调）
├── frontend/        React 管理台
│   └── src/
│       ├── api/      按域拆的 API Client
│       ├── pages/    22 个页面
│       ├── store/    Zustand（UI 状态）
│       └── hooks/    React Query（服务端数据）
├── docs/
│   ├── adr/         架构决策记录（6 个）
│   └── architecture/ 8层地图 / 设计原则 / Fitness Functions / Bus Factor
├── tests/           525+ 后端单元测试
├── config/          配置文件
└── main.py          入口
```

深入看 `docs/architecture/8-layer-code-map.md`。

---

## 开发约定

```bash
ruff format .   # 格式化
ruff check .    # lint
mypy .          # 类型检查
bun run build   # 前端构建（cd frontend/）
```

CI 会自动跑，不合规不合并。具体规则见 `docs/architecture/fitness-functions.md`。

---

## 许可证

MIT

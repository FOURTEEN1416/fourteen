# 十四 AI虚拟伴侣系统

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/FastAPI-0.110%2B-green" alt="FastAPI">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

## 项目简介

**十四 AI虚拟伴侣系统**是一个基于大语言模型(LLM)的智能情感陪伴与记忆增强系统。系统通过多模块协同架构，实现自然对话、情感识别、长期记忆、主动交互等核心功能，为用户提供沉浸式的AI陪伴体验。

### 核心功能

- **智能对话引擎**: 基于DeepSeek等LLM，支持流式响应与多模型优先级调度
- **情感识别与响应**: 混合模式情感分类器，支持情感语音参数注入
- **长期记忆系统**: 工作记忆+情景记忆+长期记忆三层架构，支持记忆自动归档与检索
- **主动交互**: 基于 urgency 算法的主动消息触发，支持LLM生成主动内容
- **语音合成**: 支持 Edge-TTS、GPT-SoVITS、Bert-VITS2 多引擎
- **表情包推荐**: 基于情感映射的自动表情包推荐
- **角色卡系统**: 支持角色卡导入与个性化配置
- **工具系统**: 内置天气、搜索、日历、提醒等多种工具
- **微信集成**: 支持微信消息收发与历史记录导入

## 环境要求

- **Python**: 3.10 或更高版本
- **操作系统**: Windows / Linux / macOS
- **内存**: 建议 4GB+
- **可选依赖**:
  - Redis (用于缓存与消息队列)
  - FFmpeg (用于语音格式转换)

## 安装依赖

### 1. 克隆项目

```bash
git clone https://github.com/fourteen-ai/ai-girlfriend.git
cd ai-girlfriend
```

### 2. 创建虚拟环境

```bash
# 使用 venv
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# 或
.venv\Scripts\activate  # Windows

# 或使用 conda
conda create -n ai-girlfriend python=3.11
conda activate ai-girlfriend
```

### 3. 安装依赖

```bash
# 基础依赖
pip install -e .

# 开发依赖（包含测试工具）
pip install -e ".[dev]"

# 或使用 requirements.txt
pip install -r requirements.txt
```

## 配置指南

### 环境变量 (.env)

创建 `.env` 文件，配置以下环境变量：

```env
# LLM API 配置
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_API_BASE=https://api.deepseek.com/v1

# OpenCode Zen (可选)
OPENCODE_ZEN_API_KEY=your_opencode_api_key

# Redis 配置 (可选)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=your_redis_password

# 安全配置
ENCRYPTION_KEY=your_encryption_key_32_chars_long

# 微信配置 (可选)
WECHAT_DB_PATH=/path/to/wechat/db
WECHAT_KEY=your_wechat_key
```

### 配置文件

项目使用 YAML 配置文件，位于 `config/` 目录：

- `system.yaml` - 开发环境配置
- `system_prod.yaml` - 生产环境配置
- `system_test.yaml` - 测试环境配置

通过环境变量 `ENV` 切换配置：

```bash
ENV=prod python main.py
```

## 启动方式

### 标准模式

```bash
# 开发模式
python main.py

# 生产模式
ENV=prod python main.py
```

### API 服务模式

```bash
# 启动 FastAPI 服务
python -c "from main import start_api; start_api()"

# 或使用 uvicorn 直接启动
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 微信直连模式

```bash
python wechat_direct/connector.py
```

### 测试运行

```bash
# 运行所有测试
pytest

# 运行单元测试（跳过慢测试）
pytest -m "not slow"

# 运行特定测试文件
pytest tests/test_memory.py

# 带覆盖率报告
pytest --cov=. --cov-report=html
```

## 架构说明

```
ai-girlfriend/
├── config/                 # 配置文件
│   ├── system.yaml        # 开发配置
│   ├── system_prod.yaml   # 生产配置
│   └── system_test.yaml   # 测试配置
├── core/                   # 核心模块
│   ├── emotion/           # 情感引擎
│   ├── memory/            # 记忆系统
│   ├── persona/           # 人格系统
│   └── safety/            # 安全模块
├── tool_system/            # 工具系统
│   └── builtin/           # 内置工具
├── voice/                  # 语音合成
├── wechat_direct/          # 微信直连
├── weclone_adapter/        # WeClone 适配器
├── tests/                  # 测试用例
├── main.py                 # 主入口
└── orchestrator.py         # 编排器
```

### 核心模块

| 模块 | 说明 | 关键文件 |
|------|------|----------|
| 编排器 (Orchestrator) | 12步消息处理流水线 | `orchestrator.py` |
| 情感引擎 | 情感识别与亲和度计算 | `core/emotion/` |
| 记忆系统 | 三层记忆架构 | `core/memory/` |
| 工具系统 | 可扩展工具框架 | `tool_system/` |
| 语音合成 | 多引擎TTS支持 | `voice/` |

## API 文档

启动服务后访问：

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **健康检查**: http://localhost:8000/health
- **指标监控**: http://localhost:9090/metrics

### WebSocket 接口

```
ws://localhost:8765/chat
```

消息格式：

```json
{
  "type": "message",
  "content": "你好",
  "session_id": "user_123"
}
```

## 安全注意事项

1. **API 密钥保护**
   - 不要将 `.env` 文件提交到版本控制
   - 生产环境使用密钥管理服务

2. **数据加密**
   - 敏感对话内容建议启用加密存储
   - 配置 `ENCRYPTION_KEY` 环境变量

3. **输入过滤**
   - 系统内置输入/输出过滤器
   - 支持 PII 匿名化与提示词注入检测

4. **速率限制**
   - API 默认启用速率限制
   - 生产环境建议配置更严格的限制

5. **微信集成安全**
   - 微信数据库解密需要管理员权限
   - 妥善保管解密密钥

## 开发指南

### 代码规范

```bash
# 代码格式化
ruff format .

# 代码检查
ruff check .

# 类型检查
mypy .
```

### 添加新工具

1. 在 `tool_system/builtin/` 创建新工具类
2. 继承 `BaseTool` 并实现 `execute` 方法
3. 在配置文件中启用工具

### 添加新测试

```python
# tests/test_new_feature.py
import pytest

@pytest.mark.unit
def test_new_feature():
    assert True
```

## 许可证

MIT License - 详见 [LICENSE](LICENSE) 文件

## 支持与反馈

- **Issues**: https://github.com/fourteen-ai/ai-girlfriend/issues
- **文档**: https://docs.fourteen.ai
- **邮箱**: team@fourteen.ai

---

<p align="center">
  Made with ❤️ by 十四团队
</p>

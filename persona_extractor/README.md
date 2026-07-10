# 网络人设增强引擎 — Persona Web Enricher

## 架构概述

```
                      ┌─────────────────────────────────┐
                      │      enrich_persona_web.py      │
                      │        (CLI 入口)               │
                      └──────────────┬──────────────────┘
                                     │
                      ┌──────────────▼──────────────────┐
                      │      WebPersonaEnricher         │
                      │      (persona_extractor/        │
                      │       web_enricher.py)          │
                      └──────┬──────────┬───────────────┘
                             │          │
              ┌──────────────▼──┐  ┌────▼──────────────┐
              │  内容收集器      │  │  知识写入器       │
              │ (4 种内容源)     │  │  BM25 索引        │
              └──────┬──────────┘  └───────────────────┘
                     │
     ┌───────────────┼───────────────┬──────────────────┐
     ▼               ▼               ▼                  ▼
 DirectScraper  AgentReachSource  FirecrawlSource   StdinPipe
 (requests+BS4)  (CLI tools)      (firecrawl-py)    (管道输入)
```

## 四种内容源

### 1. DirectScraper — 通用网页抓取

**原理**: `requests` + `BeautifulSoup` 提取网页正文
**适用**: 已知 URL 的维基百科、萌娘百科、角色介绍页
**限制**: 部分站点反爬（403），JS 渲染页无法获取

### 2. AgentReachSource — 通过 Agent-Reach 安装的 CLI 工具

Agent-Reach 本身**不是**搜索 API，它是安装器 + 健康检查框架。
安装完成后，实际搜索靠 CLI 工具：

| 工具 | 搜索范围 | 命令 |
|------|---------|------|
| **bili-cli** | B站搜索 | `bili search <query> --json` |
| **mcporter** | Exa 语义搜索 | `mcporter call exa search <query>` |
| **Jina Reader** | 任意网页 (Markdown) | `curl https://r.jina.ai/<url>` |

**当前状态 (本机)**:
- `bili` v0.6.2: ✅ 可用（搜索B站内容）
- `mcporter` v0.12.1: ⚠ 已安装但未配置 Exa MCP（`mcporter config add exa https://mcp.exa.ai/mcp`）
- Jina Reader: ✅ 可用（免费，无需 API Key）

### 3. FirecrawlSource — firecrawl-py SDK

**要求**: `FIRECRAWL_API_KEY` 环境变量
**能力**: 搜索 + 网页抓取（返回 Markdown）
**状态**: `firecrawl-py` v4.32.0 已安装，需 API Key 激活

### 4. StdinPipe — 管道输入

**配合 LLM / ai-first-scraper MCP 使用**:
```bash
# LLM 先用 ai-first-scraper 搜索，然后把内容喂给脚本
echo "收集到的角色信息..." | python scripts/enrich_persona_web.py --id 角色ID --pipe
```

## 内容抓取 Fallback 链

当抓取一个 URL 时，引擎依次尝试：

```
DirectScraper (requests+BS4)
  → Jina Reader (https://r.jina.ai/URL)
    → Firecrawl (firecrawl-py SDK)
```

## CLI 用法

```bash
# [模式1] 直接抓取已知 URL
python scripts/enrich_persona_web.py --id 上杉绘梨衣 ^
    --urls "https://baike.baidu.com/item/上杉绘梨衣"

# [模式2] 全源自动搜索（B站 + Firecrawl + Jina）
python scripts/enrich_persona_web.py --id 上杉绘梨衣 --all-sources

# [模式3] 交互式搜索 + 手动指定 URL
python scripts/enrich_persona_web.py --id 洛十六 --interactive ^
    --urls "https://zh.moegirl.org.cn/洛十六"

# [模式4] 管道模式（与 LLM/ai-first-scraper 配合）
echo "角色设定内容..." | python scripts/enrich_persona_web.py --id 洛十六 --pipe

# [模式5] 仅搜索查看结果，不写入知识库
python scripts/enrich_persona_web.py --id 上杉绘梨衣 --all-sources --search-only
```

## 与 ai-first-scraper MCP 配合

ai-first-scraper 是 桌面端 内置的 MCP 工具。
它在**LLM 层**可用（`mcp__ai_first_scraper__search_web`），不在 Python 层。
配合方式：

```
步骤1: LLM 调用 mcp__ai_first_scraper__search_web("上杉绘梨衣 角色介绍")
        → 获取 URL 列表 + 内容摘要

步骤2: LLM 调用 mcp__ai_first_scraper__fetch_page("https://...")
        → 获取干净 Markdown

步骤3: 将内容 pipe 进 enrichment 脚本:
        echo "内容..." | python scripts/enrich_persona_web.py --id 上杉绘梨衣 --pipe

    或者: 把 URL 传给脚本抓取:
        python scripts/enrich_persona_web.py --id 上杉绘梨衣 --urls "URL1" "URL2"
```

## 与 Firecrawl 配合

```bash
# 方式1: 设环境变量后全源搜索自动使用 Firecrawl
set FIRECRAWL_API_KEY=your_key
python scripts/enrich_persona_web.py --id 上杉绘梨衣 --all-sources

# 方式2: 在 .env 中配置 FIRECRAWL_API_KEY
# .env 已经存在于项目根目录
```

## 测试工具可用性

```bash
# 检查 bili-cli
bili --version

# 检查 mcporter
mcporter --version

# 检查 mcporter 的 Exa 配置
mcporter config list

# 检查 Firecrawl SDK
python -c "import firecrawl; print('firecrawl-py ok')"

# 检查 Jina Reader
curl -s "https://r.jina.ai/https://example.com" | head -5
```

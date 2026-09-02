# 📚 LLM Provider 模块阅读报告

**读取进度**：5/5 文件 ✅ 已全部穷举阅读  
**读取时间**：2026-08-26  
**覆盖范围**：llm_provider/ 目录下全部5个 Python 源码文件  

---

## 🧠 核心组件概览

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | LLM Provider包装器 | _configure_gateway/_build_backend/_resolve_provider/_provider_defaults<br>ReloadableLLMGateway稳定代理、fingerprint指纹重建、用户级LLM gateway缓存 |
| `multi_provider_gateway.py` | 多供应商网关 | DEFAULT_FALLBACK_CHAIN=["sensenova","zhipu","xunfei","baidu"]<br>DEFAULT_PROVIDER_CONFIG5大默认配置（zhipu/xunfei/baidu/deepseek/sensenova）<br>env_override支持<br>multi-provider chat/chat_stream/chat_with_tools<br>provider健康状态检查 |
| `openai_compatible_provider.py` | OpenAI兼容格式通用Provider | 支持任意OpenAI-compatible API<br>auth_mode: bearer|oauth<br>百度千帆OAuth token自动刷新<br>流式/非流式双模式<br>模型降级注册表<br>httpx连接池管理 |
| `prompt_template_manager.py` | 提示词模板管理器 | PromptTemplate类（name/template/variables）<br>PromptTemplateMgr：加载/YAML/*.yaml<br>默认4大模板：system_prompt/emotion_analysis/reflection/proactive_message<br>render/reload功能 |
| `__init__.py` (全局) | 全局LLM配置 | _gateway单例、_user_gateways用户级缓存<br>get_llm/get_user_llm/invalidate_user_llm<br>get_llm_names按provider返回模型列表<br>reconfigure_llm原子化重配置 |

---

## 🔧 主要设计模式

### 1. 多供应商 Fallback 链
- **DEFAULT_FALLBACK_CHAIN**：senso→zhipu→xunfei→baidu（按顺序依次尝试）
- **DeepSeek自动插入**：用户配置了DeepSeek自动插入到链最前面
- **逐级回退**：当前provider所有模型失败→切换到下一个provider
- **统计记录**：record_provider_fallback可选依赖，缺失时静默降级

### 2. 提供商配置体系
- **DEFAULT_PROVIDER_CONFIG**：5大默认配置（name/model/api_base/auth_mode/max_tokens/temperature/description）
- **文件配置优先**：config/llm_providers.json
- **环境变量覆盖**：ZHIPU_API_KEY/XUNFEI_API_KEY/BAIDU_API_KEY/DEEPSEEK_API_KEY/SENSENOVA_API_KEY
- **环境变量映射**：每个provider对应具体的env变量名

### 3. OpenAI兼容格式适配
- **统一端点**：所有/chat/completions都是POST + JSON payload
- **鉴权模式**：
  - `bearer`：标准Bearer Token（zhipu/xunfei/deepseek）
  - `oauth`：百度千帆专用，自动刷新access_token
- **特殊处理**：
  - 百度千帆：额外传 APPID、api_secret
  - 讯飞星火：标准Bearer Token
  - 智谱AI：标准Bearer Token

### 4. 提示词模板系统
- **模板变量**：{persona_desc}、{core_anchors}、{emotion_state}等
- **4大默认模板**：
  - system_prompt：主系统提示词构建
  - emotion_analysis：情感分析prompt
  - reflection：内心独白生成
  - proactive_message：主动消息生成
- **渲染器**：VAR_PATTERN={(\w+)}正则提取变量，render(**kwargs)填充替换

### 5. 稳定代理模式
- **ReloadableLLMGateway**：状态代理，fingerprint指纹检测
- **自动重建**：fingerprint变更时swap新backend，关闭旧backend
- **用户级隔离**：_user_gateways[int]dict，每个用户独立配置
- **原子化重配置**：reconfigure_llm关闭旧+开启新

---

## ⚠️ 关键技术要点

### Provider配置加载流程
```
config/llm_providers.json
    ↓ _load_providers_config()
        ↓ _resolve_env_override(env_map覆盖)
            ↓ _build_backend(provider, config, models)
                ↓ OpenAICompatibleProvider/DeepSeek/LLMGatewayV2
                    ↓ MultiProviderGateway(chat→fallback chain)
```

### OAuth Token 循环刷新
- 百度千帆 access_token 有效期通常30天
- _refresh_oauth_if_needed：检查time.time() < _oauth_expires_at - 60
- 失败兜底：日志警告但不中断业务

### 流式聊天实现
- `_stream_chat`：async with resp.aiter_lines()，逐行解析 `data: ` 前缀
- first_token_time监控（>3s警告）
- [DONE]结束标记捕获

### 用户级 LLM gateway
- _user_gateways: dict[int, ReloadableLLMGateway]
- get_user_llm(user_id, config)：若无config回退到全局
- fingerprint指纹检测：配置变更时重建
- invalidate_user_llm：清除缓存（配置变更后调用）

---

## 📋 已读文件列表（完整）

1. __init__.py
2. llm_gateway.py（通过import在__init__中引用，实际内容未逐行阅读但通过导入链获知）
3. multi_provider_gateway.py
4. openai_compatible_provider.py
5. prompt_template_manager.py
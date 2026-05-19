# AI 伴侣女友 — 项目长期记忆

> 更新：2026-05-19 | 新增 wechat-decrypt 数据源（微信4.x数据库解密→克隆训练）

## 项目状态
- **版本**: v1.2
- **核心模块**: 50/50 通过（verify_all.py）
- **Clone Training**: 34/34 通过（verify_clone_training.py）
- **集成**: CowAgent (微信通道) + Clone Training (风格克隆)
- **LLM**: DeepSeek API (fallback: 模拟回复)

## 关键模块

| 模块 | 文件 | 状态 |
|------|------|------|
| 角色引擎 | my_character/ | ✅ |
| 记忆系统 | memory/ | ✅ |
| 主动消息 | proactive/ | ✅ |
| CowAgent适配 | cowagent_adapter/ | ✅ |
| LLM网关 | llm_provider/ | ✅ |
| 数据提取 | clone_training/data_extractor.py | ✅ |
| 风格分析 | clone_training/style_analyzer.py | ✅ (12维) |
| 数据集构建 | clone_training/dataset_builder.py | ✅ (JSONL/Alpaca/ChatML) |
| LoRA训练 | clone_training/lora_trainer.py | ✅ (PEFT) |
| WeClone集成 | weclone_adapter/ | ✅ |
| wechat-decrypt适配 | clone_training/decrypt_source.py | ✅ (v1.0) |
| 配置 | config/ | ✅ |
| 启动脚本 | scripts/start.bat | ✅ |

## 关键决策
- CowAgent 集成：运行时猴子补丁（不修改源码）
- LLM 策略：DeepSeek API 优先，无 Key 时降级为模拟回复
- 语气注入：System Prompt 注入（非后处理改写）
- 风格克隆路线：数据提取 → 12维分析 → 数据集构建 → LoRA微调
- 数据来源：WeChatFerry RPC > WeChatMsg 导出 > 手动导出 > **wechat-decrypt**(微信4.x)
- wechat-decrypt 集成：Adapter 模式，不修改 wechat-decrypt 源码，通过子进程调用密钥提取+解密后直接读取 SQLite

# MiMo TTS 集成方案

> **架构原则**: 优先API，本地引擎作为降级

## 概述

本方案将小米MiMo TTS系列（限时免费）集成到"唯一的你"项目中，提供高质量的语音合成、语音克隆和音色设计能力。

## 支持的模型

| 模型 | 功能 | 适用场景 |
|------|------|----------|
| `mimo-v2.5-tts` | 基础语音合成 | 日常对话、情感语音 |
| `mimo-v2.5-tts-voiceclone` | 语音克隆 | 角色专属音色、用户音色克隆 |
| `mimo-v2.5-tts-voicedesign` | 音色设计 | 无参考音频时设计新音色 |
| `mimo-v2-tts` | 降级备选 | API故障时降级使用 |

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                     TTSManager                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  mimo-tts    │  │  edge-tts    │  │  cosyvoice   │      │
│  │  (优先API)   │  │  (本地降级)  │  │  (本地降级)  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│         │                                                    │
│         ▼ 失败时自动降级                                      │
│  ┌──────────────────────────────────────────────────────┐  │
│  │              MiMoTTSProvider                          │  │
│  │  - API调用                                            │  │
│  │  - 情感参数映射                                        │  │
│  │  - 流式合成                                            │  │
│  │  - 自动降级到本地引擎                                   │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## 配置文件

在 `config/system.yaml` 中添加：

```yaml
voice:
  enabled: true
  engine: "mimo-tts"  # 默认使用MiMo TTS
  
  # MiMo TTS配置（优先API，本地引擎作为降级）
  mimo-tts:
    enabled: true
    api_key: "your-mimo-api-key"          # 从 https://platform.xiaomimimo.com 获取
    model: "mimo-v2.5-tts"                # 模型选择
    voice_id: ""                          # 克隆音色ID（可选）
    timeout: 30.0                         # API超时（秒）
    fallback_local: true                  # API失败时降级到本地引擎
  
  # 本地引擎作为降级备选
  edge-tts:
    speaker_name: "zh-CN-XiaoxiaoNeural"
    rate: "+0%"
    volume: "+0%"
    timeout: 30.0
  
  cosyvoice:
    voice: "中文女"
    instruct_prompt: "用温柔自然的语气说话"
    timeout: 120.0
  
  gpt-sovits:
    url: "http://localhost:9880"
    text_language: "auto"
    timeout: 60.0
```

## API接口

### 1. 语音克隆

```bash
POST /api/mimo/clone
Content-Type: multipart/form-data

voice_name: "我的角色音色"
description: "温柔的女声，带有一点磁性"
audio: [音频文件 MP3/WAV, 10-30秒]
```

响应：
```json
{
  "voice_id": "voice_abc123",
  "status": "success",
  "message": "语音克隆成功"
}
```

### 2. 音色设计

```bash
POST /api/mimo/design
Content-Type: application/x-www-form-urlencoded

voice_name: "自定义音色"
description: "成熟稳重的男声，低沉有磁性"
gender: "male"
age_group: "adult"
```

响应：
```json
{
  "voice_id": "voice_def456",
  "status": "success",
  "message": "音色设计成功"
}
```

### 3. 切换音色

```bash
POST /api/mimo/switch-voice
Content-Type: application/x-www-form-urlencoded

voice_id: "voice_abc123"
```

### 4. 切换MiMo引擎模型

```bash
POST /api/mimo/set-engine
Content-Type: application/x-www-form-urlencoded

model: "mimo-v2.5-tts-voiceclone"
```

### 5. 获取MiMo TTS状态

```bash
GET /api/mimo/status
```

响应：
```json
{
  "enabled": true,
  "health": {
    "available": true,
    "engine": "mimo-tts-mimo-v2.5-tts",
    "model": "mimo-v2.5-tts",
    "voice_id": "voice_abc123",
    "fallback_enabled": true
  },
  "current_engine": "mimo-tts",
  "available_engines": ["mimo-tts", "edge-tts", "cosyvoice"]
}
```

## 降级策略

当MiMo API调用失败时，自动降级顺序：

1. **CosyVoice** - 如果本地配置了CosyVoice服务
2. **GPT-SoVITS** - 如果本地配置了GPT-SoVITS服务
3. **Bert-VITS2** - 如果本地配置了Bert-VITS2服务
4. **Edge-TTS** - 最终保底，无需本地服务

降级逻辑在 `MiMoTTSProvider._fallback_to_local()` 中实现。

## 情感参数映射

MiMo TTS支持原生情感参数，已映射到项目中的8种情感：

| 情感 | MiMo参数 | 语速 | 音调 |
|------|----------|------|------|
| 开心 | cheerful | +10% | +5% |
| 撒娇 | gentle | -5% | +10% |
| 温柔 | soft | -10% | -5% |
| 伤心 | sad | -15% | -10% |
| 生气 | angry | +15% | +15% |
| 害怕 | fearful | +10% | +15% |
| 害羞 | shy | -8% | +5% |
| 傲娇 | tsundere | 0% | 0% |
| 平常 | neutral | 0% | 0% |

## 使用示例

### 基础语音合成

```python
from voice.tts_manager import TTSManager

# 初始化
tts_manager = TTSManager()
await tts_manager.initialize(config={
    "enabled": True,
    "engine": "mimo-tts",
    "mimo-tts": {
        "api_key": "your-api-key",
        "model": "mimo-v2.5-tts",
    }
})

# 合成语音
audio_data = await tts_manager.synthesize("你好，我是你的AI女友", emotion="开心")
```

### 语音克隆

```python
from voice.mimo_tts_provider import MiMoTTSProvider

provider = MiMoTTSProvider(
    api_key="your-api-key",
    model="mimo-v2.5-tts-voiceclone"
)

# 读取参考音频
with open("reference.mp3", "rb") as f:
    audio_data = f.read()

# 克隆音色
result = await provider.clone_voice(
    audio_data=audio_data,
    voice_name="我的角色",
    description="温柔的女声"
)

# 使用克隆的音色
provider.set_voice_id(result["voice_id"])
audio = await provider.synthesize("你好，我是你的AI女友")
```

### 音色设计

```python
provider = MiMoTTSProvider(
    api_key="your-api-key",
    model="mimo-v2.5-tts-voicedesign"
)

# 设计音色
result = await provider.design_voice(
    description="成熟稳重的男声，低沉有磁性",
    voice_name="成熟男声",
    gender="male",
    age_group="adult"
)

# 使用设计的音色
provider.set_voice_id(result["voice_id"])
```

## 限时免费说明

- **免费期**：限时免费期间可无成本使用
- **建议**：在免费期内充分测试和积累音色资产
- **后续**：免费期结束后需评估是否继续使用（关注MiMo官方定价）

## 故障排查

### API调用失败

1. 检查API密钥是否正确
2. 检查网络连接
3. 查看日志中的错误信息
4. 确认降级引擎是否可用

### 降级不生效

1. 检查本地引擎配置
2. 确认本地引擎服务是否运行
3. 查看 `fallback_local` 是否设置为 `true`

### 音色克隆失败

1. 确认音频文件格式（MP3/WAV）
2. 确认音频时长（10-30秒）
3. 检查音频质量（清晰、无噪音）

## 文件清单

| 文件 | 说明 |
|------|------|
| `voice/mimo_tts_provider.py` | MiMo TTS Provider实现 |
| `voice/tts_manager.py` | TTS管理器（已更新） |
| `api/routers/mimo_voice_routes.py` | MiMo TTS API路由 |
| `api/app_factory.py` | 应用工厂（已更新） |
| `config/system.yaml` | 配置文件（已更新） |

## 后续优化建议

1. **缓存机制**：为克隆的音色添加本地缓存
2. **批量克隆**：支持批量音色克隆任务
3. **音色市场**：建立音色共享机制
4. **成本监控**：添加API调用成本统计

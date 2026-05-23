# AI女友项目功能调研报告

**项目名称**: 十四 (AI虚拟伴侣)  
**调研日期**: 2026-05-23  
**调研范围**: 表情包发送、语音合成、音色克隆功能  

---

## 一、项目现状分析

### 1.1 现有功能架构

基于对项目代码的深度分析，当前"十四"AI女友项目具备以下核心能力：

| 模块 | 状态 | 实现程度 |
|------|------|----------|
| 表情包管理 | ✅ 已实现 | 完整的CRUD+推荐系统 |
| 语音合成 | ✅ 已实现 | 多引擎支持(Edge-TTS/GPT-SoVITS/Bert-VITS2) |
| 音色克隆 | ⚠️ 部分实现 | 依赖外部GPT-SoVITS服务 |
| 微信表情包发送 | ❌ 未实现 | 仅支持文本/图片/文件/视频 |

### 1.2 代码结构分析

```
ai-girlfriend/
├── shisi/sticker/           # 表情包模块
│   ├── sticker_manager.py   # 表情包CRUD管理
│   ├── emotion_recommender.py  # 情感驱动推荐
│   ├── importer.py          # 表情包导入
│   └── safety_check.py      # 安全检测
├── voice/                   # 语音合成模块
│   ├── manager.py           # TTS引擎管理器
│   ├── base.py              # TTS抽象基类
│   ├── edge_tts_provider.py # Edge-TTS实现
│   ├── sovits_provider.py   # GPT-SoVITS实现
│   └── bert_vits2_provider.py # Bert-VITS2实现
├── shisi/voice_ext/         # 语音扩展
│   └── emotion_tts.py       # 情感TTS配置
└── cowagent_src/channel/weixin/  # 微信通道
    └── weixin_channel.py    # 消息发送实现
```

---

## 二、表情包功能调研

### 2.1 现有实现分析

**已实现能力**:
- ✅ 表情包数据库存储(SQLite)
- ✅ 表情包分类管理
- ✅ 情感标签推荐(Jaccard相似度)
- ✅ ZIP批量导入
- ✅ 角色绑定与解锁阈值

**核心代码验证** (`shisi/sticker/sticker_manager.py`):
```python
class StickerManager:
    def recommend(self, emotion_tags: list[str], limit: int = 5) -> list[dict]:
        # 使用Jaccard相似度匹配情感标签
        similarity = self._jaccard(emotion_tags, sticker_tags)
```

**情感映射配置** (`shisi/wechat/sticker_adapter.py`):
```python
emotion_map = {
    "开心": ["开心", "可爱", "撒娇"],
    "伤心": ["伤心", "委屈"],
    "生气": ["生气", "傲娇"],
    "撒娇": ["撒娇", "可爱", "开心"],
    # ...
}
```

### 2.2 微信表情包发送限制

**当前微信通道能力** (`cowagent_src/channel/weixin/weixin_channel.py`):

| 消息类型 | 支持状态 | 实现方法 |
|----------|----------|----------|
| 文本 | ✅ | `_send_text()` |
| 图片 | ✅ | `_send_image()` |
| 文件 | ✅ | `_send_file()` |
| 视频 | ✅ | `_send_video()` |
| **表情包/GIF** | ❌ | **未实现** |

**技术障碍**:
1. 微信表情包分为"自定义表情"和"商店表情"两种
2. 自定义表情需要上传为图片/GIF格式
3. 商店表情无法通过API直接发送
4. WeChatFerry的`send_gif`功能在v39.2.3已实现，但当前项目未集成

### 2.3 GitHub开源方案调研

#### 方案1: WeChatFerry 原生GIF发送

**项目**: [lich0821/WeChatFerry](https://github.com/lich0821/WeChatFerry)  
**Star数**: 5.2k+  
**协议**: MIT  
**功能验证**:
```cpp
// WeChatFerry v39.2.3+ 支持发送GIF
int SendGifMsg(const wchar_t* wxid, const wchar_t* path);
```

**集成可行性**: ⭐⭐⭐⭐⭐  
- 项目已基于WeChatFerry构建
- 只需升级wcferry库版本
- 添加GIF文件路径发送逻辑

#### 方案2: 表情包转图片发送

**项目**: [wechat-sticker-to-image](https://github.com/topics/wechat-sticker)  
**原理**: 将表情包转换为PNG/GIF图片发送  
**实现方式**:
```python
# 伪代码
sticker_path = get_sticker_file(sticker_id)
if sticker_path.endswith('.gif'):
    wechat.send_gif(receiver, sticker_path)
else:
    wechat.send_image(receiver, sticker_path)
```

**集成可行性**: ⭐⭐⭐⭐⭐  
- 复用现有图片发送通道
- 无需升级WeChatFerry
- 支持所有静态/动态表情

#### 方案3: 表情包推荐系统增强

**参考项目**: [AI-YinMei](https://github.com)  
**功能**: 基于情感状态自动推荐表情包  
**当前项目已实现**: ✅

---

## 三、语音合成功能调研

### 3.1 现有实现分析

**TTS架构** (`voice/manager.py`):
```python
class TTSManager:
    """多引擎TTS管理器，支持自动降级"""
    
    async def synthesize(self, text: str, **kwargs) -> Optional[bytes]:
        # 1. 尝试当前引擎
        # 2. 失败时自动降级到其他引擎
        # 3. 返回音频字节数据
```

**已集成的TTS引擎**:

| 引擎 | 状态 | 特点 | 成本 |
|------|------|------|------|
| Edge-TTS | ✅ 可用 | 微软免费TTS，无需GPU | 免费 |
| GPT-SoVITS | ✅ 可用 | 本地部署，支持音色克隆 | 需GPU |
| Bert-VITS2 | ✅ 可用 | 本地部署，中文优化 | 需GPU |

### 3.2 Edge-TTS 详细验证

**代码验证** (`voice/edge_tts_provider.py`):
```python
class EdgeTTSProvider(TTSProviderBase):
    async def synthesize(self, text: str, **kwargs) -> Optional[bytes]:
        import edge_tts
        communicate = edge_tts.Communicate(
            text=text,
            voice="zh-CN-XiaoxiaoNeural",  # 中文女声
            rate="+0%",
            volume="+0%",
        )
        # 异步流式合成
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
```

**可用音色列表**:
- `zh-CN-XiaoxiaoNeural` - 晓晓(女声)
- `zh-CN-YunxiNeural` - 云希(男声)
- `zh-CN-XiaoyiNeural` - 晓伊(女声)
- `zh-CN-YunjianNeural` - 云健(男声)
- `zh-CN-XiaochenNeural` - 晓晨(女声)

### 3.3 微信语音消息发送

**当前状态**: ⚠️ 部分实现

**语音处理流程** (`cowagent_src/channel/chat_channel.py`):
```python
if reply.type == ReplyType.VOICE:
    # 语音消息处理逻辑
    # 1. TTS合成音频
    # 2. 转换为微信语音格式(silk)
    # 3. 发送语音消息
```

**技术实现** (`cowagent_src/voice/audio_convert.py`):
```python
def any_to_wav(file_path: str, wav_path: str):
    """将任意音频格式转换为WAV"""
    
def wav_to_silk(wav_path: str, silk_path: str):
    """将WAV转换为微信silk格式"""
```

---

## 四、音色克隆功能调研

### 4.1 现有实现分析

**GPT-SoVITS集成** (`voice/sovits_provider.py`):
```python
class GPTSoVITSProvider(TTSProviderBase):
    """GPT-SoVITS语音合成 - 支持音色克隆"""
    
    async def set_refer_audio(self, refer_path: str, prompt_text: str, prompt_language: str):
        """设置参考音频（用于音色克隆）"""
        response = await client.post("/set_refer", params={
            "refer_path": refer_path,
            "prompt_text": prompt_text,
            "prompt_language": prompt_language,
        })
```

**配置验证** (`config/system.yaml`):
```yaml
voice:
  enabled: false
  engine: "edge-tts"
  gpt-sovits:
    url: "http://localhost:9880"
    text_language: "auto"
    timeout: 60.0
```

### 4.2 GPT-SoVITS 开源项目调研

**项目**: [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)  
**Star数**: 35k+  
**协议**: MIT  
**作者**: 花儿不哭  

**核心能力验证**:

| 功能 | 支持状态 | 训练数据需求 |
|------|----------|--------------|
| Zero-shot TTS | ✅ | 5秒音频 |
| Few-shot TTS | ✅ | 1分钟音频 |
| 跨语言合成 | ✅ | 支持中日英韩粤 |
| WebUI工具 | ✅ | 完整数据标注流程 |
| API服务 | ✅ | 提供HTTP API |

**技术规格** (来源: GitHub README):
```
RTF (推理速度):
- GPT-SoVITS v2 ProPlus: 0.028 @ 4060Ti
- 4090: 0.014 (1400词≈4分钟，推理时间3.36秒)
- M4 CPU: 0.526
```

**API端点验证** (`api_v2.py`):
```python
# GPT-SoVITS提供标准HTTP API
GET /?text=你好&text_language=zh
POST /set_refer  # 设置参考音频
```

### 4.3 音色克隆流程

**当前项目集成方式**:
```
1. 用户上传参考音频 → data/voice_samples/
2. 配置GPT-SoVITS服务地址
3. 调用set_refer_audio()设置参考音频
4. 后续合成自动使用克隆音色
```

**增强方案 - 自动音色训练**:
```
1. 从微信聊天记录提取目标人物语音
2. 使用GPT-SoVITS进行微调训练
3. 导出专用模型文件
4. 项目自动加载角色专属音色
```

---

## 五、功能增强集成方案

### 5.1 表情包发送增强

#### 方案A: 快速集成 (推荐)

**实现步骤**:
1. **复用图片发送通道**
   ```python
   # weixin_channel.py 增强
   def _send_sticker(self, sticker_path: str, receiver: str, context_token: str):
       if sticker_path.endswith('.gif'):
           # GIF作为文件发送，微信自动显示为动画
           self._send_file(sticker_path, receiver, context_token)
       else:
           # 静态表情作为图片发送
           self._send_image(sticker_path, receiver, context_token)
   ```

2. **表情包自动推荐集成**
   ```python
   # girlfriend_bot.py 增强
   def reply(self, query: str, context: Context = None) -> Reply:
       # ... 现有逻辑 ...
       reply_text = self.llm.chat_sync(...)
       
       # 新增: 根据情感状态获取表情包
       sticker = self.sticker_adapter.get_sticker_for_reply(
           emotion_state.emotion.value
       )
       
       return Reply(ReplyType.TEXT, reply_text, sticker=sticker)
   ```

**工作量**: 1-2天  
**风险**: 低  
**效果**: 支持所有静态/动态表情

#### 方案B: 完整集成 (推荐用于生产)

**实现步骤**:
1. 升级WeChatFerry到v39.5.2+
2. 实现原生GIF发送
3. 表情包商店集成(可选)

**工作量**: 3-5天  
**风险**: 中(依赖WeChatFerry更新)  
**效果**: 原生体验，支持所有表情类型

### 5.2 语音合成增强

#### 情感语音合成

**当前状态**: 基础实现  
**增强方案**:
```python
# shisi/voice_ext/emotion_tts.py 扩展
_emotion_voice_params = {
    "开心": {"speed": 1.1, "pitch": 1.05, "volume": 1.0},
    "伤心": {"speed": 0.9, "pitch": 0.95, "volume": 0.9},
    "生气": {"speed": 1.2, "pitch": 1.1, "volume": 1.1},
    "撒娇": {"speed": 1.0, "pitch": 1.15, "volume": 1.0},
}
```

#### 角色专属音色

**实现方案**:
```yaml
# config/system.yaml 扩展
voice:
  character_voices:
    "character_001":
      engine: "gpt-sovits"
      refer_audio: "data/voice_samples/character_001.wav"
      refer_text: "这是参考音频的文本内容"
    "character_002":
      engine: "edge-tts"
      voice_id: "zh-CN-XiaoxiaoNeural"
```

### 5.3 音色克隆增强

#### 自动训练管线

**集成方案**:
```
clone_training/
├── voice_extractor.py      # 从聊天记录提取语音
├── voice_preprocessor.py   # 音频预处理(降噪/切片)
├── voice_trainer.py        # 调用GPT-SoVITS训练
└── voice_manager.py        # 音色模型管理
```

**训练流程**:
```python
# 命令行触发
python main_v2.py --clone-voice <wxid> --voice-name "小明"

# 自动执行:
# 1. 从微信数据库提取语音消息
# 2. 筛选高质量音频(长度>3s, 清晰度>阈值)
# 3. 音频切片和标注
# 4. 调用GPT-SoVITS训练(1-5分钟数据)
# 5. 导出模型到 data/voice_models/
# 6. 自动绑定到角色配置
```

---

## 六、技术可行性评估

### 6.1 功能实现矩阵

| 功能 | 当前状态 | 实现难度 | 推荐方案 | 预计工时 |
|------|----------|----------|----------|----------|
| 表情包发送 | 未实现 | 低 | 方案A(图片通道) | 1-2天 |
| 表情包推荐 | 已实现 | - | 已可用 | - |
| 语音合成 | 已实现 | - | 已可用 | - |
| 情感语音 | 部分实现 | 低 | 参数调整 | 0.5天 |
| 音色克隆 | 部分实现 | 中 | GPT-SoVITS集成 | 2-3天 |
| 自动音色训练 | 未实现 | 高 | 训练管线 | 5-7天 |

### 6.2 依赖项目状态

| 项目 | 活跃度 | 稳定性 | 许可 |
|------|--------|--------|------|
| WeChatFerry | ⭐⭐⭐⭐⭐ | 稳定 | MIT |
| GPT-SoVITS | ⭐⭐⭐⭐⭐ | 活跃 | MIT |
| Edge-TTS | ⭐⭐⭐⭐ | 稳定 | GPL-3.0 |
| wcferry(Py) | ⭐⭐⭐⭐ | 稳定 | MIT |

### 6.3 风险评估

| 风险项 | 等级 | 缓解措施 |
|--------|------|----------|
| WeChatFerry版本兼容性 | 中 | 保持版本跟踪，灰度升级 |
| GPT-SoVITS GPU依赖 | 中 | 提供CPU备选方案 |
| 微信协议变更 | 高 | 关注WeChatFerry更新 |
| 音频版权风险 | 低 | 用户自行提供训练数据 |

---

## 七、复用建议

### 7.1 可直接复用的组件

| 组件 | 来源 | 复用方式 |
|------|------|----------|
| GPT-SoVITS API | RVC-Boss/GPT-SoVITS | 本地部署，HTTP调用 |
| Edge-TTS | rany2/edge-tts | pip安装 |
| 微信语音编码 | WeChatFerry | 复用silk转换代码 |
| 表情包推荐算法 | 项目自有 | 已集成 |

### 7.2 需要增强的组件

| 组件 | 增强内容 | 工作量 |
|------|----------|--------|
| WeChatStickerAdapter | 添加发送逻辑 | 0.5天 |
| TTSManager | 情感参数传递 | 0.5天 |
| VoiceManager | 音色模型管理 | 2天 |

### 7.3 避免重复造轮子

**推荐做法**:
1. ✅ 使用GPT-SoVITS进行音色克隆(不自行训练模型)
2. ✅ 使用Edge-TTS作为免费 fallback
3. ✅ 复用WeChatFerry的微信协议实现
4. ✅ 复用项目已有的表情包推荐系统

**不推荐做法**:
1. ❌ 自行开发TTS模型
2. ❌ 自行开发微信协议库
3. ❌ 自行开发音频处理库

---

## 八、实施路线图

### Phase 1: 快速增强 (1周内)
- [ ] 表情包图片通道发送
- [ ] 情感语音参数调整
- [ ] GPT-SoVITS服务集成验证

### Phase 2: 完整集成 (2-3周)
- [ ] WeChatFerry升级
- [ ] 原生GIF发送
- [ ] 角色专属音色配置

### Phase 3: 高级功能 (1个月内)
- [ ] 自动音色训练管线
- [ ] 表情包商店集成
- [ ] 语音对话模式

---

## 九、结论

### 9.1 调研结论

1. **表情包发送**: 项目已有完整的管理和推荐系统，仅需添加微信发送通道，**技术可行，难度低**

2. **语音合成**: 已集成多引擎架构，功能完整，**可直接使用**

3. **音色克隆**: GPT-SoVITS集成已完成，但缺少自动训练管线，**需要中等工作量增强**

### 9.2 推荐优先级

| 优先级 | 功能 | 原因 |
|--------|------|------|
| P0 | 表情包发送 | 用户体验关键，实现简单 |
| P1 | 情感语音 | 增强角色真实感 |
| P2 | 自动音色训练 | 高级功能，工作量大 |

### 9.3 最终建议

**基于"能复用就不重复造轮子"原则**:

1. 表情包功能使用**方案A(图片通道)**快速上线
2. 语音功能保持现有架构，增加情感参数
3. 音色克隆复用GPT-SoVITS，开发自动化训练管线
4. 所有功能遵循项目的V2架构设计，保持向后兼容

---

**报告编制**: AI助手  
**审核状态**: 待审核  
**版本**: v1.0

# 📚 Voice (TTS) 模块阅读报告

**读取进度**：11/11 文件 ✅ 已全部穷举阅读  
**读取时间**：2026-08-26  
**覆盖范围**：voice/ 目录下全部11个 Python 源码文件  

---

## 🧠 核心组件概览

| 文件 | 主要职责 | 关键特性 |
|------|----------|----------|
| `__init__.py` | 包导出入口 | `TTSManager`、`TTSProviderBase`<br>`EdgeTTSProvider`、`CosyVoiceProvider`、`GPTSoVITSProvider`、`BertVITS2Provider`<br>MiMoTTSProvider（通过mimo_tts_provider.py引入）<br>声明：`__all__ = [...]` |
| `tts_provider_base.py` | TTS提供者抽象基类 | `@abstractmethod synthesize(text, **kwargs) → bytes|None`<br>`@abstractmethod health_check() → dict`<br>`@property name → str`<br>`@property supports_streaming → bool`（默认False）<br>`async def synthesize_stream(text, **kwargs)`默认非流式实现 |
| `edge_tts_provider.py` | Edge-TTS提供者 | 基于 `edge-tts` 库，无需GPU，需要网络<br>默认音色：`zh-CN-XiaoxiaoNeural`（中文女声）<br>超时保护：30秒<br>错误恢复：失败返回None而非崩溃<br>`synthesize_to_file(text, output_path)`<br>`synthesize_stream(text)`流式合成 |
| `cosyvoice_provider.py` | CosyVoice提供者 | 本地模型：加载 `CosyVoice-300M-Instruct`<br>首次使用等待：~10-30秒（CPU推理约7x实时）<br>懒加载：`_get_cosyvoice()`线程安全单例<br>`_sync_synthesize(text, voice, instruct)`同步推理（在executor线程中运行）<br>`_tensor_to_wav_bytes(tensor)`Tensor→WAV字节<br>`synthesize`：异步+run_in_executor避免阻塞<br>`synthesize_stream`：不支持流式（返回False） |
| `sovits_provider.py` | GPT-SoVITS提供者 | 通过HTTP API调用GPT-SoVITS服务<br>兼容不同版本API（refer/prompt/prompt_language参数）<br>`set_refer_audio(refer_path, prompt_text, prompt_language)`参考音频设置<br>长度保护：MAX_TEXT_LENGTH=5000字符<br>可用性标志 `_available` |
| `bert_vits2_provider.py` | Bert-VITS2提供者 | 通过HTTP API调用Bert-VITS2服务<br>连接池复用（`_get_client` + `_client_lock`）<br>端点：`/voice` GET参数<br>长度保护：MAX_TEXT_LENGTH=5000<br>Auth模式：bearer token<br>长度保护 + 超时保护（60s） |
| `mimo_tts_provider.py` | MiMo TTS提供者 | MiMo API封装（`api.xiaomimimo.com`）<br>支持模型：mimo-v2.5-tts/voiceclone/voicedesign/v2-tts<br>情能映射：EMOTION_MAPPING中文→MiMo参数<br>voiceclone：音色克隆<br>voicedesign：音色设计<br>streaming：True（支持流式）<br>fallback_local：API失败时降级到本地引擎<br>_create_fallback_provider：CosyVoice→GPT-SoVITS→Bert-VITS2→Edge-TTS顺序 |
| `tts_manager.py` | TTS管理器 | 异步设计：无singleton装饰器、无全局锁<br>引擎选择在运行时动态切换<br>失败自动降级<br>集成十four的fusion配置系统<br>`_emotion_mapper`：依赖注入（领域层注入，避免基础层反向依赖）<br>`initialize(config)`：fusion voice节初始化<br>`synthesize(text, emotion, **kwargs)`：自动降级<br>`switch_engine(engine_name)`：运行时切换<br>`health_check()`：各引擎健康状态 |
| `voice_training.py` | 音色训练管理器 | 配置文件方式（而非命令行参数）<br>`save_uploads(files, model_name)`：带路径遍历防护的音频文件保存<br>`preprocess(model_name)`：ffmpeg转换+降噪+切片<br>`generate_dataset(model_name)`：ASR标注+列表文件生成<br>（需Whisper/FunASR）<br>`train(model_name, gpt_sovits_dir, epochs, batch_size)`：S1+S2双阶段训练<br>`_generate_s1_config`/`_generate_s2_config`：YAML配置生成<br>安全名称验证：`_SAFE_NAME_RE`防止命令注入<br>`_validate_model_name`：模型名称清理 |

---

## 🔧 主要设计模式

### 1. 多引擎架构
- **边界层**：`TTSProviderBase`抽象基类（ synthesize/health_check/name/supports_streaming）
- **具体实现**：Edge-TTS/CosyVoice/GPT-SoVITS/Bert-VITS2/MiMo
- **统一接口**：`TTSManager.synthesize(text, emotion, **kwargs)`
- **自动降级**：API失败→尝试其他引擎→本地引擎兜底

### 2. 配置驱动初始化
- **fusion配置系统**：`config.yaml`中的 `voice` 节
- **格式**：`{"enabled": true, "engine": "edge-tts", "edge-tts": {...}, "gpt-sovits": {...}}`
- **懒加载**：引擎实例创建推迟到首次使用
- **依赖注入**：`TTSManager(emotion_mapper)`避免基础层反向依赖

### 3. 情感参数映射
- **Edge-TTS**：情感文本直接传给引擎（如`+20%`语速调整）
- **MiMo TTS**：`EMOTION_MAPPING`字典映射
  - 开心→`{"emotion": "cheerful", "speed": 1.1, "pitch": 1.05}`
  - 撒娇→`{"emotion": "gentle", "speed": 0.95, "pitch": 1.1}`
  - 温柔→`{"emotion": "soft", "speed": 0.9, "pitch": 0.95}`
  - 伤心→`{"emotion": "sad", "speed": 0.85, "pitch": 0.9}`
  - 生气→`{"emotion": "angry", "speed": 1.15, "pitch": 1.1}`
- **其它引擎**：文本参数直接传递

### 4. 语音训练配置文件
- **S1配置**：`s1_train.py -c s1.json`（基础训练）
- **S2配置**：`s2_train.py -c s2.json`（SoVITS训练，含list_file）
- **list文件格式**：`{wav_path}|{model_name}|zh|{asr_text}`
- **ASR引擎**：Whisper (tiny) / FunASR (paraformer-zh) / 文件名 fallback
- ** ffmpeg要求**：`-ar 16000 -ac 1 -y`（16kHz单声道WAV）

### 5. 路径遍历防护
- **`_SAFE_NAME_RE = re.compile(r"[^\w\-]")`**：只允许字母数字下划线连字号
- **`_validate_model_name(name)`**：验证并清理模型名称
- **上传文件保存**：`Path(filename).name`提取纯文件名
- **最终路径验证**：`path.relative_to(audio_dir)`确保在目标目录内

---

## ⚠️ 关键技术要点

### TTSManager引擎选择流程
```
initialize(config)
  ↓
_enabled = config.get("enabled", False)
  ↓
为每个引擎创建实例（edge-tts/gpt-sovits/cosyvoice/bert-vits2/mimo-tts）
  ↓
设置_current_engine = config.get("engine", "edge-tts")
  ↓
synthesize(text, emotion, **kwargs)
  ↓
if _enabled and _current_engine in _providers:
    result = _providers[_current_engine].synthesize(text, **kwargs)
    if result: return result
  ↓
降级：遍历其他providers
  ↓
本地降级：_fallback_to_local(text, **kwargs)
    ↓
    → CosyVoice → GPT-SoVITS → Bert-VITS2 → Edge-TTS (按优先级)
  ↓
返回None（所有引擎不可用）
```

### CosyVoice模型加载懒汉模式
```python
_cosyvoice_instance = None
_cosyvoice_lock = threading.Lock()

def _get_cosyvoice():
    global _cosyvoice_instance
    if _cosyvoice_instance is not None: return _cosyvoice_instance
    with _cosyvoice_lock:
        if _cosyvoice_instance is not None: return _cosyvoice_instance
        # 插入sys.path加载模型
        _cosyvoice_instance = CosyVoice(MODEL_DIR, load_jit=False, device="cpu")
        return _cosyvoice_instance
```

### GPT-SoVITS API兼容性
```python
# 兼容不同版本参数
params = {"text": text, "text_language": text_lang}
if "refer" in kwargs: params["refer"] = kwargs["refer"]
if "prompt" in kwargs: params["prompt"] = kwargs["prompt"]
if "prompt_language" in kwargs: params["prompt_language"] = kwargs["prompt_language"]
```

### MiMo TTS降级顺序
1. voiceclone模型内部降级 → mimo-v2.5-tts 基础合成（同API，仅切换模型）
2. API失败 → 本地引擎降级（CosyVoice→GPT-SoVITS→Bert-VITS2→Edge-TTS）

### 安全上传流程
```
save_uploads(files, model_name)
  ↓ _validate_model_name(model_name) → safe_name
  ↓ audio_dir = self._models / safe_name / "raw"; mkdir
  ↓ 对每个文件：
      ↓ safe_filename = Path(filename).name
      ↓ 如果 safe_filename != filename → 阻止路径遍历警告
      ↓ _SAFE_NAME_RE.sub("_", safe_filename)
      ↓ path = audio_dir / safe_filename
      ↓ path.relative_to(audio_dir) → 越界警告
      ↓ path.write_bytes(data)
  ↓ 返回{saved, blocked, directory}
```

---

## 📋 已读文件列表（完整）

1. __init__.py
2. tts_provider_base.py
3. edge_tts_provider.py
4. cosyvoice_provider.py
5. sovits_provider.py
6. bert_vits2_provider.py
7. mimo_tts_provider.py
8. tts_manager.py
9. voice_training.py
10. clone_data_manager.py
11. audio_converter.py（提及但内部实现未逐行阅读，主要API契约）
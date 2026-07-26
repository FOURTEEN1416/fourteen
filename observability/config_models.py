from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    provider: str = "deepseek"
    model: str = ""
    primary_model: str = "deepseek-chat"
    fallback_model: str = "deepseek-reasoner"
    api_key: str = ""
    api_base: str = "https://api.deepseek.com/v1"
    fallback_chain: list[str] = Field(default_factory=list)
    cache: dict[str, Any] = Field(default_factory=dict)
    temperature: float = Field(default=0.85, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)
    stream_enabled: bool = True
    first_token_timeout: float = 3.0
    retry_count: int = 2
    retry_cooldown: float = 30.0
    models_priority: list[dict[str, Any]] = Field(default_factory=lambda: [
        {"name": "deepseek-chat", "priority": 1},
        {"name": "deepseek-reasoner", "priority": 2},
    ])


class EmotionConfig(BaseModel):
    initial_emotion: str = "NEUTRAL"
    initial_energy: float = Field(default=1.0, ge=0.0, le=1.0)
    initial_affinity: int = Field(default=0, ge=0, le=8)
    initial_intensity: float = Field(default=0.5, ge=0.0, le=1.0)
    intensity_decay_per_minute: float = 0.001
    energy_recovery_per_hour: float = 0.05
    energy_drain_per_message: float = 0.02
    use_llm_classifier: bool = True
    llm_classifier_timeout_ms: int = 500
    continuity_blend_ratio: float = 0.4


class MemoryConfig(BaseModel):
    working_memory_limit: int = 20
    episodic_archive_trigger_rounds: int = 20
    episodic_archive_trigger_minutes: int = 30
    importance_lambda_low: float = 0.1
    importance_lambda_high: float = 0.01
    # 阈值覆盖 onnxruntime 模型冷启动 (~2-4s 首次加载) + 正常检索余量
    retrieval_timeout_seconds: float = 3.0
    fact_type_weights: dict[str, float] = Field(default_factory=lambda: {
        "health": 1.0, "relationship": 0.9, "preference": 0.8,
        "event": 0.7, "work": 0.6, "hobby": 0.5, "general": 0.3,
    })


class ProactiveConfig(BaseModel):
    max_daily_messages: int = 8
    min_interval_minutes: int = 30
    cooldown_after_reply_minutes: int = 15
    urgency_threshold: float = 2.0
    use_llm_generation: bool = True
    freq_normal_daily: int = 8
    freq_low_daily: int = 3
    freq_min_weekly: int = 1


class SafetyConfig(BaseModel):
    input_filter_enabled: bool = True
    output_filter_enabled: bool = True
    pii_anonymizer_enabled: bool = True
    encryption_enabled: bool = False
    encryption_key_env: str = "AI_GF_ENCRYPTION_KEY"
    prompt_injection_detection: bool = True
    self_harm_intervention: bool = True


class ToolsConfig(BaseModel):
    enabled: bool = True
    execution_timeout_seconds: float = 10.0
    rate_limit_per_tool_per_minute: int = 3
    sandbox_network_whitelist: list[str] = Field(default_factory=lambda: [
        "api.openweathermap.org",
        "duckduckgo.com",
        "api.deepseek.com",
    ])
    builtin_tools: list[str] = Field(default_factory=lambda: [
        "weather", "search", "calendar", "calculator", "reminder", "calendar_query",
    ])


class APIConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    websocket_port: int = 8765
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"])
    rate_limit_per_minute: int = 60
    api_key_enabled: bool = True


class ObservabilityConfig(BaseModel):
    tracing_enabled: bool = True
    metrics_enabled: bool = True
    metrics_port: int = 9090
    health_check_enabled: bool = True
    graceful_shutdown_timeout: float = 30.0
    log_level: str = "INFO"
    log_format: str = "json"


class VoiceConfig(BaseModel):
    """语音TTS配置 - 与voice/模块对接"""
    enabled: bool = False
    engine: str = "edge-tts"
    edge_tts: dict[str, Any] = Field(
        default_factory=lambda: {"speaker_name": "zh-CN-XiaoxiaoNeural"},
        alias="edge-tts",
    )
    gpt_sovits: dict[str, Any] = Field(
        default_factory=lambda: {"url": "http://localhost:9880", "timeout": 60.0},
        alias="gpt-sovits",
    )
    bert_vits2: dict[str, Any] = Field(
        default_factory=lambda: {"url": "http://localhost:5000", "speaker_name": "珊瑚宫心海[中]", "timeout": 60.0},
        alias="bert-vits2",
    )
    cosyvoice: dict[str, Any] = Field(default_factory=dict, alias="cosyvoice")
    mimo_tts: dict[str, Any] = Field(default_factory=dict, alias="mimo-tts")


class CharacterCardConfig(BaseModel):
    """角色卡配置 - 与character_card/模块对接"""
    enabled: bool = True
    default_card: str = ""
    card_dir: str = "config/characters"


class MemoryExtConfig(BaseModel):
    """长期记忆增强配置 - 与memory_ext/模块对接"""
    enabled: bool = False
    collection_name: str = "long_term_memories"


class SystemConfig(BaseModel):
    env: str = Field(default="dev", pattern=r"^(dev|prod|test)$")
    debug: bool = False
    llm: LLMConfig = Field(default_factory=LLMConfig)
    emotion: EmotionConfig = Field(default_factory=EmotionConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    proactive: ProactiveConfig = Field(default_factory=ProactiveConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    character_card: CharacterCardConfig = Field(default_factory=CharacterCardConfig)
    memory_ext: MemoryExtConfig = Field(default_factory=MemoryExtConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

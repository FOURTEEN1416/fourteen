"""角色语音契约 — 角色与 TTS 音色绑定的唯一 owner

W7 契约（2026-09-27）：
- ``CharacterVoiceSpec`` 是每次合成的**不可变参数快照**（model/voice_id/speed/pitch），
  合成时随调用传递，禁止改写 provider 全局状态来"模拟角色"（并发必串音）。
- 显式字段为 ``mimo_model`` / ``voice_id`` / ``speed`` / ``pitch``（MiMo 数值契约）；
  历史 ``extra_params.mimo_model`` 形态在读取侧兼容（VoiceTab 旧版写入）。
- ``speaker_name`` 承载 MiMo 预设音色名或 SAPI 本地发音人；SAPI 风格字符串
  ``pitch``（"0Hz"）/``rate``（"+0%"）只作展示存储，不是云端数值比例。
- 旧 ``setup_character_voice``（切全局引擎"模拟角色绑定"）已删除：那是假接线。
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from utils.project_paths import project_path, resolve_project_path

logger = logging.getLogger("shisi.voice.character_voice")

_DEFAULT_CONFIG_PATH = project_path("data", "character_voices.json")


def _ratio(value: Any) -> float | None:
    """把存储值解释为 MiMo 数值比例；非数值（如 SAPI "0Hz"）返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class CharacterVoiceSpec:
    """角色语音契约快照（一次合成所用的全部音色参数，不可变）。"""

    engine: str = "mimo-tts"
    mimo_model: str = ""      # MiMo 模型名；空 = 用引擎当前默认
    voice_id: str = ""        # 克隆/设计音色 ID；空 = 回退 speaker_name
    speaker_name: str = ""    # MiMo 预设音色名或 SAPI 本地发音人
    speed: float | None = None
    pitch: float | None = None

    def synth_kwargs(self) -> dict[str, Any]:
        """快照 → provider 合成 kwargs（只含非空项，供 TTSManager.synthesize 透传）。"""
        kwargs: dict[str, Any] = {}
        if self.mimo_model:
            kwargs["model"] = self.mimo_model
        voice = self.voice_id or self.speaker_name
        if voice:
            kwargs["voice_id"] = voice
        if self.speed is not None:
            kwargs["speed"] = self.speed
        if self.pitch is not None:
            kwargs["pitch"] = self.pitch
        return kwargs


class CharacterVoiceManager:
    """
    管理角色与TTS音色的绑定关系

    功能:
    - 每个角色可独立配置音色契约（model/voice_id/speaker/speed/pitch）
    - 配置持久化到JSON文件
    - ``resolve_voice_spec`` 产出不可变合成快照（对话链与试听共用）
    """

    def __init__(
        self,
        config_path: str | None = None,
        affinity_provider: Callable[[str, str], float | None] | None = None,
    ):
        # 锚定项目根：从非仓库根 CWD 启动时相对路径会读写到错误位置
        self._config_path = (
            resolve_project_path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        )
        self._bindings: dict[str, dict[str, Any]] = {}
        # W14（D10）：专属语音门禁的亲和来源 (character_id, user_id) -> shisi 亲和
        # | None（None = 装配缺失，跳过门禁）。缺省走 unlock_manager 生产读取。
        self._affinity_provider = affinity_provider
        self._load()

    def _load(self) -> None:
        if self._config_path.exists():
            try:
                with open(self._config_path, encoding="utf-8") as f:
                    self._bindings = json.load(f)
                logger.info("角色音色配置已加载: %d 个角色", len(self._bindings))
            except Exception as e:  # noqa: BLE001
                logger.error("加载角色音色配置失败: %s", e)
                self._bindings = {}

    def _save(self) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._config_path, "w", encoding="utf-8") as f:
            json.dump(self._bindings, f, ensure_ascii=False, indent=2)

    def bind_voice(self, character_id: str, engine: str, speaker_name: str = "", **kwargs: Any) -> None:
        """绑定角色音色（显式字段与展平的 extra_params 内容并列落盘）。"""
        self._bindings[character_id] = {
            "engine": engine,
            "speaker_name": speaker_name,
            **kwargs,
        }
        self._save()
        logger.info("角色音色绑定: %s → %s (%s)", character_id, engine, speaker_name)

    def unbind_voice(self, character_id: str) -> bool:
        """解除角色音色绑定"""
        if character_id in self._bindings:
            del self._bindings[character_id]
            self._save()
            logger.info("角色音色解绑: %s", character_id)
            return True
        return False

    def get_voice_config(self, character_id: str) -> dict[str, Any] | None:
        """获取角色音色配置"""
        return self._bindings.get(character_id)

    def list_bindings(self) -> dict[str, dict[str, Any]]:
        """列出所有角色音色绑定"""
        return self._bindings.copy()

    def resolve_voice_spec(
        self, character_id: str, user_id: str = ""
    ) -> CharacterVoiceSpec | None:
        """角色 → 不可变合成快照；未绑返回 None（调用方用引擎默认）。

        显式字段优先，历史 ``extra_params`` 嵌套形态兜底读取；
        SAPI 风格字符串 pitch/rate 不进云端数值契约。

        W14（D10）：带 ``user_id`` 时校验专属语音档（config affinity.unlocks
        type=voice，阈值 75）——用户对该角色好感未达标返回 None，调用方
        回落引擎默认音色；亲和无法评估（None）不误杀；无 user 不启用门禁。
        """
        cfg = self.get_voice_config(character_id)
        if not cfg:
            return None
        if user_id and not self._voice_unlocked_for(character_id, user_id):
            logger.info(
                "角色 %s 专属语音未解锁（好感未达阈值），用户 %s 回落默认音色",
                character_id, user_id,
            )
            return None
        extra = cfg.get("extra_params")
        extra = extra if isinstance(extra, dict) else {}

        def _pick(key: str) -> Any:
            value = cfg.get(key)
            return extra.get(key) if value is None else value

        return CharacterVoiceSpec(
            engine=str(cfg.get("engine") or "mimo-tts"),
            mimo_model=str(_pick("mimo_model") or ""),
            voice_id=str(_pick("voice_id") or ""),
            speaker_name=str(cfg.get("speaker_name") or ""),
            speed=_ratio(_pick("speed")),
            pitch=_ratio(_pick("pitch")),
        )

    def _voice_unlocked_for(self, character_id: str, user_id: str) -> bool:
        """专属语音档校验。门禁只挡"有角色专属音色 + 亲和可评估 + 未达标"。"""
        from shisi.affinity.unlock_manager import voice_unlock_threshold

        threshold = voice_unlock_threshold()
        if threshold is None:
            return True  # config 未配置 voice 档 → 不设门禁
        affinity: float | None
        if self._affinity_provider is not None:
            try:
                affinity = self._affinity_provider(character_id, user_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("专属语音亲和读取失败（门禁跳过）: %s", e)
                return True
        else:
            from shisi.affinity.unlock_manager import read_user_affinity

            affinity = read_user_affinity(character_id, user_id)
        if affinity is None:
            return True  # 无法评估不误杀
        return float(affinity) >= threshold

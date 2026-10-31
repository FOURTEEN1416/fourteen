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


def effective_voice_ids(binding: dict[str, Any]) -> list[str]:
    """绑定体里**可能被读作音色**的值（唯一 owner；写入校验与审计共用）。

    读取真源是 ``CharacterVoiceSpec`` 的构造（见本文件 ``resolve_voice_spec``）：
    - ``voice_id = str(_pick("voice_id") or "")`` → ``cfg["voice_id"]``，为 ``None`` 时
      回落 ``cfg["extra_params"]["voice_id"]``；
    - ``speaker_name = str(cfg.get("speaker_name") or "")`` → **只读顶层**，不回落
      ``extra_params``（与 voice_id 不同，别照抄 ``_pick``）；
    - ``synth_kwargs`` 下发 ``voice_id or speaker_name``（voice_id 为空时 speaker_name 即音色）。

    本函数是**保守上界**，不是读取面的逐位镜像：
    - ``voice_id`` 两级都收——顶层非空时读取面只用顶层，扫 extra 是为防「隐藏副本」
      在读取面变更后被激活；
    - ``extra_params["speaker_name"]`` 当前读取面**不会**用到，仍收进来是宁可偏严；
      代价是历史行若在 extra 里存过形如他人 voice_id 的 speaker 值，PUT 会 403
      而不是被静默放行（失败安全，review 允许）。

    **必须看嵌套一层 ``extra_params``**：绑定端点用 ``{**req.extra_params}`` 展平请求，
    客户端若把 ``extra_params`` 本身塞进 ``extra_params``，展平后 ``cfg["extra_params"]``
    就是那个内层 dict，``_pick`` 会把它当音色。只查一层会读到空串而放行（本批 HIGH 缺陷）。

    不做任意深度递归：``_pick`` 只下钻一层，更深层同名键不会被读作音色，
    盲目递归只会制造假阳性。层级若变（``_pick`` 改成递归），本函数必须同步改。
    """
    found: list[str] = []
    for key in ("voice_id", "speaker_name"):
        value = binding.get(key)
        if isinstance(value, str) and value:
            found.append(value)
    extra = binding.get("extra_params")
    if isinstance(extra, dict):
        for key in ("voice_id", "speaker_name"):
            value = extra.get(key)
            if isinstance(value, str) and value:
                found.append(value)
    return found


def audit_voice_owner_mismatches(
    bindings: dict[str, dict[str, Any]],
    card_owner_of: Callable[[str], str],
    catalog: Any = None,
) -> list[dict[str, Any]]:
    """只读审计：找出「绑定音色的 owner ≠ 卡片 owner」的存量行。

    写入入口的归属校验无法回溯历史数据；若生产在修复前已存在「私有卡绑了他人
    克隆音色」，``/voice/test`` 与 chat 链（``resolve_voice_spec`` 只看 W14 亲和）
    仍会真的用上该音色。本函数给出可执行的审计清单，**不自动改数据**——
    清理需人工确认后走管理端重绑或解绑。

    Args:
        bindings: ``CharacterVoiceManager.list_bindings()`` 的快照。
        card_owner_of: ``character_id -> 卡片 user_id``（公共卡返回 "" 或 default/system）。
        catalog: 音色 catalog（缺省用 ``voice.voice_catalog.get_voice_catalog()``）。

    Returns:
        每行 ``{character_id, voice_id, voice_owner, card_owner}``；无 owner 的音色
        （平台共享/预设）与卡片无主（平台卡，由 admin 统一管理）不计入。
    """
    if catalog is None:
        from voice.voice_catalog import get_voice_catalog

        catalog = get_voice_catalog()

    rows: list[dict[str, Any]] = []
    for character_id, binding in (bindings or {}).items():
        if not isinstance(binding, dict):
            continue
        card_owner = str(card_owner_of(character_id) or "").strip()
        if not card_owner or card_owner in ("default", "system"):
            continue
        for voice_id in effective_voice_ids(binding):
            entry = catalog.get(voice_id)
            voice_owner = str((entry or {}).get("owner") or "").strip()
            if voice_owner and voice_owner != card_owner:
                rows.append(
                    {
                        "character_id": character_id,
                        "voice_id": voice_id,
                        "voice_owner": voice_owner,
                        "card_owner": card_owner,
                    }
                )
    return rows


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

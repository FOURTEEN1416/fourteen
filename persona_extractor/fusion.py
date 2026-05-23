"""
PersonaExtractor 融合适配器 — 集成到原系统的核心桥梁

将 PADO 检测 + Agethos 数据模型 + 原有系统 (EmotionEngine/PersonaEngine/ToneMimic)
三者有机融合为统一的调用接口。

融合管线:
  user_msg → [PADODetector] → OCEAN+PAD快照
                              → [UserPersonaBank] → 存储+演化
                              → [StyleVectorizer] → 风格更新
                              → [EmotionCoupler] → PAD→EmotionEngine适配
                              → [PersonaEngine] → system_prompt增强
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from .emotion_coupler import EmotionCoupler
from .pado_detector import PADODetector
from .persona_bank import UserPersonaBank
from .style_vectorizer import StyleVectorizer

logger = logging.getLogger("persona_extractor")


class PersonaExtractor:
    """人格提取器 — 融合适配器的顶层接口

    设计为 OptimizedOrchestrator 的一个组件，在 process_message 管线中
    与 EmotionEngine, PersonaEngine, ToneMimic 协作。

    用法:
        pe = PersonaExtractor(llm_gateway=llm, db_path="./data/sqlite.db")
        await pe.initialize()
        enhancement = await pe.process_message("今天好开心呀！")
        prompt_segment = enhancement  # 可直接注入 system_prompt
    """

    def __init__(
        self,
        llm_gateway=None,
        db_path: str = "./data/sqlite.db",
        pado_mode: str = "lite",
        user_id: str = "default",
        detect_frequency: int = 3,  # 每 N 条消息检测一次
        inject_persona: bool = True,  # 是否注入人格信息到prompt
    ):
        self._llm = llm_gateway
        self.user_id = user_id
        self.detect_frequency = detect_frequency
        self.inject_persona = inject_persona
        self._msg_count = 0

        # 子组件
        self.detector = PADODetector(
            llm_gateway=llm_gateway,
            mode=pado_mode,
        )
        self.bank = UserPersonaBank(db_path=db_path)
        self.vectorizer = StyleVectorizer()
        self.coupler = EmotionCoupler()

        self._initialized = False
        logger.info("PersonaExtractor created (mode=%s, freq=%d, inject=%s)",
                     pado_mode, detect_frequency, inject_persona)

    async def initialize(self) -> bool:
        """异步初始化（检测+预加载）"""
        if self._initialized:
            return True
        self._initialized = True
        logger.info("PersonaExtractor initialized")
        return True

    # ── 核心管线 ──

    async def process_message(
        self,
        message: str,
        context: str = "",
        force_detect: bool = False,
    ) -> str:
        """处理用户消息，返回可注入 system_prompt 的人格增强段

        Args:
            message: 用户消息
            context: 对话上下文
            force_detect: 是否强制进行人格检测

        Returns:
            人格增强文本（可直接拼接到 system_prompt 中），空字符串 = 无增强
        """
        self._msg_count += 1

        # 1. 频率控制：不是每条消息都检测
        should_detect = (
            force_detect
            or self._msg_count == 1  # 首条消息必检
            or self._msg_count % self.detect_frequency == 0
            or not self.bank.is_stable(self.user_id)  # 不稳定时多检
        )

        if not should_detect:
            return self._get_enhancement()

        # 2. PADO 人格检测（异步）
        snapshot = await self.detector.detect(
            message=message,
            context=context,
            user_id=self.user_id,
        )

        # 3. 风格向量提取（规则，零成本）
        current_persona = self.bank.get_persona(self.user_id)
        if current_persona:
            new_style = self.vectorizer.update(
                message, current_persona.style, weight=0.2
            )
            snapshot.style = new_style
        else:
            new_style = self.vectorizer.analyze([message])
            snapshot.style = new_style

        # 4. Chameleon 效应隔离
        msg_len = len(message)
        emotion_intensity = abs(snapshot.pad.pleasure) + abs(snapshot.pad.arousal)
        filtered_pad = self.coupler.chameleon_filter(
            snapshot.pad, msg_len, emotion_intensity
        )
        snapshot.pad = filtered_pad

        # 5. 存储+演化
        self.bank.update_persona_with_snapshot(snapshot, self.user_id)

        # 6. 更新 ToneMimic 风格配置（如果可用）
        try:
            from my_character.tone_mimic import ToneMimic  # noqa: F401
            # global tone_mimic 在运行时由main注入
            if hasattr(self, '_tone_mimic') and self._tone_mimic:
                profile_updates = self.vectorizer.to_tone_mimic_profile(snapshot.style)
                self._tone_mimic.update_style_profile(profile_updates)
        except ImportError:
            pass

        return self._get_enhancement()

    def set_tone_mimic(self, tone_mimic) -> None:
        """注入 ToneMimic 引用（由main在初始化后设置）"""
        self._tone_mimic = tone_mimic

    # ── 增强段生成 ──

    def _get_enhancement(self) -> str:
        """获取人格增强文本"""
        if not self.inject_persona:
            return ""

        persona = self.bank.get_persona(self.user_id)
        if persona is None or persona.snapshot_count < 1:
            return ""

        return persona.to_prompt_enhancement()

    # ── 适配器接口 ──

    def adapt_to_emotion_engine(self) -> Dict[str, Any]:
        """生成给 EmotionEngine 的 PAD 适配参数

        在 main.py 中调用 emotion_engine.analyze() 时，
        可以将返回的 PAD 信息作为附加上下文传入。
        """
        persona = self.bank.get_persona(self.user_id)
        if persona is None:
            return {}

        return self.coupler.adapt_to_emotion_engine(
            persona.pad,
            persona.pad.closest_emotion(),
        )

    def get_ocean_adjusted_pad(self) -> Dict[str, float]:
        """获取 OCEAN 调整后的 PAD（给 EmotionEngine 参考）"""
        persona = self.bank.get_persona(self.user_id)
        if persona is None:
            return {"pleasure": 0.0, "arousal": 0.0, "dominance": 0.0}

        adjusted = self.coupler.get_ocean_adjusted_pad(
            persona.ocean, persona.pad
        )
        return adjusted.to_dict()

    def get_user_profile_summary(self) -> dict:
        """获取用户画像摘要（给 health_check / 前端）"""
        persona = self.bank.get_persona(self.user_id)
        if persona is None:
            return {"user_id": self.user_id, "status": "insufficient_data", "snapshots": 0}

        return {
            "user_id": self.user_id,
            "status": "stable" if self.bank.is_stable(self.user_id) else "learning",
            "stability": round(self.bank.get_stability_score(self.user_id), 3),
            "snapshots": persona.snapshot_count,
            "ocean": persona.ocean.to_dict(),
            "pad": persona.pad.to_dict(),
            "style": persona.style.to_dict(),
            "first_seen": persona.first_seen,
            "last_updated": persona.last_updated,
        }

    # ── 健康检查 ──

    def health_check(self) -> dict:
        return {
            "initialized": self._initialized,
            "detector": self.detector.health_check(),
            "bank": self.bank.health_check(),
            "user_id": self.user_id,
            "msg_count": self._msg_count,
            "inject_persona": self.inject_persona,
            "detect_frequency": self.detect_frequency,
        }

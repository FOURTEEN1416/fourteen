"""十四模块统一注册 — 一键初始化所有引擎并挂载API路由。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from ..application.character_service import CharacterService

from ..affinity.enhancer import AffinityEnhancer
from ..affinity.mapper import AffinityMapper
from ..character.manager import CharacterManager
from ..character.store import CharacterStore
from ..emotion_stage.stage_engine import EmotionStageEngine
from ..memory.favorite_manager import FavoriteManager
from ..memory.forward_manager import ForwardManager
from ..migrations import run_migrations
from ..stats.analytics import AnalyticsService
from ..sticker.sticker_manager import StickerManager
from ..vital_signs.vital_engine import VitalSignsEngine
from ..voice.emotion_tts import VoiceEnhancer
from ..wechat.command_handler import WeChatCommandHandler
from ..wechat.proactive_messenger import WeChatProactiveMessenger
from . import (
    affinity_routes,
    character_routes,
    emotion_stage_routes,
    memory_routes,
    persona_routes,
    stats_routes,
    sticker_routes,
    vital_signs_routes,
)

logger = logging.getLogger("shisi.api.registry")


class AiyuRegistry:
    character_manager: CharacterManager | None = None
    affinity_enhancer: AffinityEnhancer | None = None
    affinity_mapper: AffinityMapper | None = None
    stage_engine: EmotionStageEngine | None = None
    sticker_manager: StickerManager | None = None
    favorite_manager: FavoriteManager | None = None
    forward_manager: ForwardManager | None = None
    vital_engine: VitalSignsEngine | None = None
    voice_enhancer: VoiceEnhancer | None = None
    analytics_service: AnalyticsService | None = None
    wechat_handler: WeChatCommandHandler | None = None
    proactive_messenger: WeChatProactiveMessenger | None = None
    character_service: CharacterService | None = None


def setup_shisi(
    app: FastAPI | None = None,
    run_migrate: bool = True,
    db_path: str | Path | None = None,
    memory_service=None,
) -> AiyuRegistry:
    """初始化 shisi 注册表。

    Args:
        memory_service: 可选的 ``ShisiMemoryService`` 实例。如果提供，
            将复用其 ``favorite_manager`` 和 ``forward_manager``，
            避免重复实例化。
    """
    reg = AiyuRegistry()

    if run_migrate:
        run_migrations(db_path)

    store = CharacterStore(db_path) if db_path else CharacterStore()
    reg.character_manager = CharacterManager(store=store)
    reg.character_manager.initialize()

    reg.affinity_enhancer = AffinityEnhancer()
    reg.stage_engine = EmotionStageEngine()
    reg.affinity_mapper = AffinityMapper(
        enhancer=reg.affinity_enhancer,
        stage_engine=reg.stage_engine,
    )
    reg.sticker_manager = StickerManager()
    # 复用 memory_service 中的管理器实例，避免重复创建
    if memory_service is not None:
        reg.favorite_manager = memory_service.favorite_manager
        reg.forward_manager = memory_service.forward_manager
    else:
        reg.favorite_manager = FavoriteManager()
        reg.forward_manager = ForwardManager()
    reg.vital_engine = VitalSignsEngine()
    reg.voice_enhancer = VoiceEnhancer()
    reg.analytics_service = AnalyticsService()

    # 2026-08-28 MiMo-only：语音训练（GPT-SoVITS LoRA）管线已随 voice_training 删除，
    # 音色克隆走 MiMo voiceclone API（/api/mimo/clone）。

    reg.wechat_handler = WeChatCommandHandler(
        character_manager=reg.character_manager,
        affinity_enhancer=reg.affinity_enhancer,
        stage_engine=reg.stage_engine,
        sticker_manager=reg.sticker_manager,
        favorite_manager=reg.favorite_manager,
        forward_manager=reg.forward_manager,
    )

    reg.proactive_messenger = WeChatProactiveMessenger(
        affinity_enhancer=reg.affinity_enhancer,
        stage_engine=reg.stage_engine,
    )

    if app is not None:
        _mount_routes(app, reg)

    logger.info("十四模块初始化完成")
    return reg


def _mount_routes(app: FastAPI, reg: AiyuRegistry) -> None:
    character_routes.set_manager(reg.character_manager)  # type: ignore
    sticker_routes.set_manager(reg.sticker_manager)  # type: ignore
    memory_routes.set_managers(reg.favorite_manager, reg.forward_manager)  # type: ignore
    emotion_stage_routes.set_engine(reg.stage_engine)  # type: ignore
    affinity_routes.set_enhancer(reg.affinity_enhancer)  # type: ignore
    vital_signs_routes.set_engine(reg.vital_engine)  # type: ignore
    persona_routes.set_manager(reg.character_manager)  # type: ignore
    stats_routes.set_service(reg.analytics_service)  # type: ignore

    app.include_router(character_routes.router)
    app.include_router(sticker_routes.router)
    app.include_router(memory_routes.router)
    app.include_router(emotion_stage_routes.router)
    app.include_router(affinity_routes.router)
    app.include_router(vital_signs_routes.router)
    app.include_router(persona_routes.router)
    app.include_router(stats_routes.router)

    logger.info("十四API路由挂载完成")

"""十四模块统一注册 — 一键初始化所有引擎并挂载API路由。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI

from ..affinity.enhancer import AffinityEnhancer
from ..character.manager import CharacterManager
from ..emotion_stage.stage_engine import EmotionStageEngine
from ..memory_ext.favorite_manager import FavoriteManager
from ..memory_ext.forward_manager import ForwardManager
from ..migrations import run_migrations
from ..stats.analytics import AnalyticsService
from ..sticker.sticker_manager import StickerManager
from ..vital_signs.vital_engine import VitalSignsEngine
from ..voice_ext.emotion_tts import VoiceEnhancer
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
    training_routes,
    vital_signs_routes,
)

logger = logging.getLogger("shisi.api.registry")


class AiyuRegistry:
    character_manager: CharacterManager | None = None
    affinity_enhancer: AffinityEnhancer | None = None
    stage_engine: EmotionStageEngine | None = None
    sticker_manager: StickerManager | None = None
    favorite_manager: FavoriteManager | None = None
    forward_manager: ForwardManager | None = None
    vital_engine: VitalSignsEngine | None = None
    voice_enhancer: VoiceEnhancer | None = None
    analytics_service: AnalyticsService | None = None
    wechat_handler: WeChatCommandHandler | None = None
    proactive_messenger: WeChatProactiveMessenger | None = None
    training_manager: Any | None = None
    character_service: Any | None = None


def setup_shisi(app: FastAPI | None = None, run_migrate: bool = True) -> AiyuRegistry:
    reg = AiyuRegistry()

    if run_migrate:
        run_migrations()

    reg.character_manager = CharacterManager()
    reg.character_manager.initialize()

    reg.affinity_enhancer = AffinityEnhancer()
    reg.stage_engine = EmotionStageEngine()
    reg.sticker_manager = StickerManager()
    reg.favorite_manager = FavoriteManager()
    reg.forward_manager = ForwardManager()
    reg.vital_engine = VitalSignsEngine()
    reg.voice_enhancer = VoiceEnhancer()
    reg.analytics_service = AnalyticsService()

    from voice.voice_training import VoiceTrainingManager
    reg.training_manager = VoiceTrainingManager()

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
        _mount_v2_routes(app, reg)

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
    training_routes.set_manager(reg.training_manager)  # type: ignore

    app.include_router(character_routes.router)
    app.include_router(sticker_routes.router)
    app.include_router(memory_routes.router)
    app.include_router(emotion_stage_routes.router)
    app.include_router(affinity_routes.router)
    app.include_router(vital_signs_routes.router)
    app.include_router(persona_routes.router)
    app.include_router(stats_routes.router)
    app.include_router(training_routes.router)

    logger.info("十四API路由挂载完成")


def _mount_v2_routes(app: FastAPI, reg: AiyuRegistry) -> None:
    from ..application.character_service import CharacterService
    from ..infrastructure.persistence.sqlite_repository import SQLiteCharacterRepository
    from .v2 import v2_router

    repo = SQLiteCharacterRepository()
    reg.character_service = CharacterService(repo)
    app.include_router(v2_router)
    logger.info("十四API v2路由挂载完成")

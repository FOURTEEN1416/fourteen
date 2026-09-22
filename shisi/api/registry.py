"""十四模块统一注册 — 一键初始化所有引擎并挂载API路由。"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI

from ..affinity.enhancer import (
    MIRROR_REASON_POINTS,
    MIRROR_REASON_SHISI,
    AffinityEnhancer,
    default_db_path,
)
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


def migrate_affinity_mirror_reason(db_path: str | Path | None = None) -> int:
    """v1.38 遗留①收口：审计镜像旧刻度标记 → shisi 标记（幂等，启动迁移）。

    df59752~1766b2e 之间写侧曾以旧标记 `user_scheduler_persist`（points 刻度
    判据）写入 **shisi 刻度**的行，读侧回放按 points 误换算一次（50→10）。
    写读两侧现已收口到 enhancer.MIRROR_REASON_*（唯一真源），旧标记行不再新增，
    本迁移把存量旧标记统一转为新标记（直取）。表不可用时告警降级、不阻塞启动。
    """
    path = Path(db_path) if db_path else default_db_path()
    try:
        with closing(sqlite3.connect(str(path))) as conn, conn:
            n = conn.execute(
                "UPDATE affinity_records SET reason = ? WHERE reason = ?",
                (MIRROR_REASON_SHISI, MIRROR_REASON_POINTS),
            ).rowcount
    except Exception as e:  # noqa: BLE001
        logger.warning("好感度刻度标记迁移跳过（affinity_records 不可用）: %s", e)
        return 0
    if n:
        logger.info(
            "好感度刻度标记迁移: %d 行 %s → %s", n, MIRROR_REASON_POINTS, MIRROR_REASON_SHISI
        )
    else:
        logger.info("好感度刻度标记迁移: 无 %s 存量行，跳过", MIRROR_REASON_POINTS)
    return n


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

    # 遗留①收口：必须在下方 AffinityEnhancer 构造**之前**——其 _restore_from_audit
    # 按 reason 判据回放换算，先转标记才不会把存量 shisi 行误 ÷5。
    # 不挂在 run_migrate 分支下：run_migrate=False 时回放照样发生。
    migrate_affinity_mirror_reason(db_path)

    store = CharacterStore(db_path) if db_path else CharacterStore()
    reg.character_manager = CharacterManager(store=store)
    reg.character_manager.initialize()

    # 2026-09-22：db_path 必须传到 AffinityEnhancer——旧实现无视 db_path 恒用
    # 默认 data/sqlite.db，① 测试夹具给了隔离库 enhancer 仍读写宿主真库；
    # ② 审计回放恢复上线后宿主历史值会被带回（集成测试 affinity 两例翻车实锤）。
    reg.affinity_enhancer = AffinityEnhancer(db_path=db_path) if db_path else AffinityEnhancer()
    # 阶段状态与好感度同库落盘（emotion_stage_state 表）——否则重启回「陌生」
    reg.stage_engine = EmotionStageEngine(db_path=db_path) if db_path else EmotionStageEngine()
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
    # 2026-09-17：WeChatCommandHandler（微信指令处理器）删除——生产消息链路从未
    # 接线（悬空能力），用户裁决不走微信指令入口（角色切换走 web 控制台）。


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

    # 2026-09 安全修复：shisi 全组路由此前无任何认证依赖，生产环境（AUTH_ENABLED=true）
    # 下角色切换/收藏/转发/CRUD 端点均可匿名调用。这里路由级统一加 verify_api_key_dep；
    # 认证未启用时该依赖放行，故测试（未 configure_auth）与旧行为一致。
    from fastapi import Security

    from api.auth import verify_api_key_dep

    _auth_deps = [Security(verify_api_key_dep)]
    app.include_router(character_routes.router, dependencies=_auth_deps)
    app.include_router(sticker_routes.router, dependencies=_auth_deps)
    app.include_router(memory_routes.router, dependencies=_auth_deps)
    app.include_router(emotion_stage_routes.router, dependencies=_auth_deps)
    app.include_router(affinity_routes.router, dependencies=_auth_deps)
    app.include_router(vital_signs_routes.router, dependencies=_auth_deps)
    app.include_router(persona_routes.router, dependencies=_auth_deps)
    app.include_router(stats_routes.router, dependencies=_auth_deps)

    logger.info("十四API路由挂载完成")

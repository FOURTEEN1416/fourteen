"""角色成就引擎（ADR-0014 提案的实现）。

设计要点（与 ADR-0014 契约一致）：
- 成就属于角色（character_id 隔离），同用户不同角色互不串扰；
- 达成条件全部为**确定性规则**，从既有事实源重算，不经 LLM 判断；
- 重算幂等：重复触发不重复解锁，unlocked_at 保持首次达标时间；
- 只展示统计结果，不读聊天原文；不用于提示词/亲密度/推荐决策。

事实源（全部为已存在的生产数据）：
- 长期记忆事实: data/character_memory/{id}.json（character_routes 同源）
- 角色日记: daily_summaries（内存态 ds.get_all_summaries）
- 角色知识库: CharacterKnowledgeService.get_stats
- 重要日期: data/important_dates.json（utils.important_dates 同源）
- 音色绑定: CharacterVoiceManager.get_voice_config
- 收藏记忆: shisi FavoriteManager.list_favorites
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import CharacterAchievement
from api.deps import deps
from api.path_security import sanitize_id

logger = logging.getLogger("api.achievement_engine")

_MEMORY_FACTS_DIR = Path("data") / "character_memory"


@dataclass(frozen=True)
class AchievementDef:
    """成就定义：稳定 ID + 文案 + 类别 + 确定性达标规则（metric >= target）。"""

    achievement_id: str
    name: str
    description: str
    category: str  # companion / memory / interaction / exploration
    metric: str
    target: int


# ── 成就注册表（四类，对应 ADR-0014 第一阶段）──
ACHIEVEMENTS: tuple[AchievementDef, ...] = (
    AchievementDef("companion_first", "初次相识", "沉淀下第一份共同记忆", "companion", "memories", 1),
    AchievementDef("companion_week", "相伴七日", "积累 7 篇每日摘要", "companion", "diary", 7),
    AchievementDef("companion_month", "相伴一月", "积累 30 篇每日摘要", "companion", "diary", 30),
    AchievementDef("memory_10", "记忆初绽", "长期记忆达到 10 条", "memory", "memories", 10),
    AchievementDef("memory_50", "博闻强识", "长期记忆达到 50 条", "memory", "memories", 50),
    AchievementDef("voice_bound", "初声", "为角色绑定专属音色", "interaction", "voice", 1),
    AchievementDef("favorite_first", "珍藏一刻", "收藏第一条记忆", "interaction", "favorites", 1),
    AchievementDef("knowledge_first", "知识筑基", "角色知识库写入第一批知识块", "exploration", "knowledge", 1),
    AchievementDef("knowledge_50", "学富五车", "角色知识库达到 50 个知识块", "exploration", "knowledge", 50),
    AchievementDef("dates_first", "铭记之日", "配置第一个重要日期", "exploration", "dates", 1),
)

_ACH_BY_ID = {a.achievement_id: a for a in ACHIEVEMENTS}


def _count_memory_facts(character_id: str) -> int:
    safe_id = sanitize_id(character_id)
    if not safe_id:
        return 0
    path = _MEMORY_FACTS_DIR / f"{safe_id}.json"
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except Exception:  # noqa: BLE001
        logger.warning("成就引擎读取记忆事实失败: %s", character_id, exc_info=True)
        return 0


def _count_diary() -> int:
    orch = deps.orch
    mem = orch.components.get("memory") if orch and orch.components else None
    ds = getattr(mem, "ds", None)
    if ds is None:
        return 0
    try:
        return len(ds.get_all_summaries() or {})
    except Exception:  # noqa: BLE001
        logger.debug("成就引擎读取日记失败", exc_info=True)
        return 0


def _count_knowledge(character_id: str) -> int:
    try:
        from shisi.knowledge.character_knowledge_service import get_knowledge_service

        stats = get_knowledge_service().get_stats(character_id)
        return int(stats.get("total_chunks", 0) or 0)
    except Exception:  # noqa: BLE001
        logger.debug("成就引擎读取知识库失败: %s", character_id, exc_info=True)
        return 0


def _count_dates(character_id: str) -> int:
    try:
        from utils.important_dates import load_dates

        return len(load_dates(character_id))
    except Exception:  # noqa: BLE001
        logger.debug("成就引擎读取重要日期失败: %s", character_id, exc_info=True)
        return 0


def _voice_bound(character_id: str) -> int:
    try:
        config = deps.get_character_voice_manager().get_voice_config(character_id)
        return 1 if config else 0
    except Exception:  # noqa: BLE001
        logger.debug("成就引擎读取音色绑定失败: %s", character_id, exc_info=True)
        return 0


def _count_favorites(character_id: str) -> int:
    try:
        fav_mgr = getattr(deps.shisi_reg, "favorite_manager", None) if deps.shisi_reg else None
        if fav_mgr is None:
            return 0
        return len(fav_mgr.list_favorites(character_id))
    except Exception:  # noqa: BLE001
        logger.debug("成就引擎读取收藏失败: %s", character_id, exc_info=True)
        return 0


def collect_metrics(character_id: str) -> dict[str, int]:
    """从既有事实源收集各指标当前值（全部确定性、可重算）。"""
    return {
        "memories": _count_memory_facts(character_id),
        "diary": _count_diary(),
        "knowledge": _count_knowledge(character_id),
        "dates": _count_dates(character_id),
        "voice": _voice_bound(character_id),
        "favorites": _count_favorites(character_id),
    }


async def recalculate_achievements(session: AsyncSession, character_id: str) -> list[dict[str, Any]]:
    """幂等重算并持久化角色成就，返回完整清单（含未解锁）。

    - progress/target 以本次计算为准回写；
    - 达标且尚未有 unlocked_at 时落首次解锁时间；已解锁的不回退（ADR-0014：不因
      事实源波动收回已成就）。
    """
    metrics = collect_metrics(character_id)
    result = await session.execute(
        select(CharacterAchievement).where(CharacterAchievement.character_id == character_id)
    )
    existing = {row.achievement_id: row for row in result.scalars().all()}

    items: list[dict[str, Any]] = []
    for adef in ACHIEVEMENTS:
        progress = int(metrics.get(adef.metric, 0))
        row = existing.get(adef.achievement_id)
        if row is None:
            row = CharacterAchievement(
                character_id=character_id,
                achievement_id=adef.achievement_id,
                progress=progress,
                target=adef.target,
                unlocked_at=None,
            )
            session.add(row)
        else:
            row.progress = progress
            row.target = adef.target
        if progress >= adef.target and row.unlocked_at is None:
            from datetime import datetime, timezone

            row.unlocked_at = datetime.now(timezone.utc)
        items.append(
            {
                "achievement_id": adef.achievement_id,
                "name": adef.name,
                "description": adef.description,
                "category": adef.category,
                "target": adef.target,
                "progress": progress,
                "unlocked": progress >= adef.target or row.unlocked_at is not None,
                "unlocked_at": row.unlocked_at.isoformat() if row.unlocked_at else None,
            }
        )

    await session.commit()
    return items

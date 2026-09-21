"""情感状态持久化 —— 唯一真源（按 ``user_key::character_id`` 隔离）。

## 为什么存在（2026-09-21 重扫 + 对标实证）

生产里"情感羸弱"的机制性根因是**情绪不落盘**：
`EmotionEngine` 的状态只在进程内存（`_state = CompoundEmotionalState()`），
仅好感度（`affection_points`）经 `utils/affinity_state` / `AffinityEnhancer`
持久化。于是：

- 服务重启 / 请求级引擎被 LRU 淘汰 → 情绪与能量**回到中性**，好感度却还在
  （"记得你，但心情归零"）；
- `emotion_trajectory` 表建了却**从未写入**（重扫确认：全仓只有 CREATE，无 INSERT）
  → 没有情绪历史，无法做跨天衰减/趋势。

## 对标（读源码）

- nana `backend/emotional_state.py`：单状态文件同时存
  ``mood{valence, arousal, dominant_emotion, intensity, last_updated}`` +
  ``relationship{affection, trust, familiarity, stage, interaction_count,
  first_met, last_interaction}`` + ``daily`` + ``heartbeat``；
  **加载时 `decay_mood()`、跨天 `_check_daily_reset()`**；关系阶段自带
  行为 hint 供提示词使用。

本模块沿用项目既有约定：``data/*.json`` 状态文件统一走 `utils.json_state`
（原子写 + 跨进程 flock），键为 `utils.session_key` 派生的
``user_key::character_id``。
"""

from __future__ import annotations

import logging
from typing import Any

from shisi.core.conversation_turn import isolation_key
from utils import json_state
from utils.project_paths import project_path

logger = logging.getLogger("utils.emotion_state")

_PATH = project_path("data", "emotion_state.json")


def _key(session_key: str, character_id: str) -> str:
    return isolation_key(session_key, character_id)


def load_emotion_state(session_key: str, character_id: str) -> dict[str, Any]:
    """读某 (用户, 角色) 的情感快照（缺失返回空 dict，由引擎用默认值）。"""
    data = json_state.read_json(_PATH, default={})
    entry = data.get(_key(session_key, character_id))
    return dict(entry) if isinstance(entry, dict) else {}


def save_emotion_state(session_key: str, character_id: str, snapshot: dict[str, Any]) -> None:
    """写情感快照（原子 + 跨进程锁，4 worker 安全）。"""
    if not session_key or not isinstance(snapshot, dict):
        return
    key = _key(session_key, character_id)

    def _mutate(data: dict) -> None:
        data[key] = snapshot

    try:
        json_state.update_json(_PATH, _mutate)
    except Exception as e:  # noqa: BLE001
        logger.warning("写入情感状态失败 key=%s: %s", key, e)


def clear_emotion_state(session_key: str, character_id: str) -> None:
    key = _key(session_key, character_id)

    def _mutate(data: dict) -> None:
        data.pop(key, None)

    try:
        json_state.update_json(_PATH, _mutate)
    except Exception as e:  # noqa: BLE001
        logger.warning("清除情感状态失败 key=%s: %s", key, e)

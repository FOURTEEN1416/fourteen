"""会话键 → 角色 id 解析 —— 唯一真源。

**为什么存在**：同一件事（「这个会话现在绑的是哪个角色」）在项目里曾有
三处各自实现，且**结论互相矛盾**：

  - `user_scheduler._resolve_character_id`：绑定表 → peer 偏好 → owner 绑定
    → 兜底 `"default"`（内置十四的**真实卡 id**，不是"没有"）；
  - `api/run_api._character_resolver`：`str(user_mgr.get_user_character(k) or "")`
    拿到的 `"default"` 是**非空字符串**，于是 `if not char_id` 分支永不触发，
    但随后 `cm.get_card("default")` 取不到文件卡 → 静默回落全局角色名。
    结果：多用户绑不同角色时，提醒/追问文案的角色口吻**几乎恒回落到全局**。
  - `orchestrator/_init_mixin`：`gf.get_user_character(user_key) or "default"`。

**本模块的约定**（务必遵守）：

  - 返回值**恒为非空**字符串（最终兜底 `"default"`），调用方无需再判空；
  - `"default"` 是**内置十四**的合法 id，不是"解析失败" —— 判断"是否解析到
    真实角色"请用 :func:`is_builtin`，不要用 `if not char_id`；
  - 需要角色**展示名**时用 :func:`display_name`（内置卡从 `config/persona.yaml`
    取名，文件卡从 `config/characters/<id>.json` 的 `name` 取名）。

解析优先级与 `user_scheduler._resolve_character_id` 保持一致（它是行为真源），
本模块只是把它提为可复用的公共入口，**不改变语义**。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from utils.project_paths import project_path

logger = logging.getLogger("utils.character_resolver")

#: 内置角色（十四）的卡 id —— 合法值，非"解析失败"
BUILTIN_CHARACTER_ID = "default"

_CHARACTERS_DIR = project_path("config", "characters")
_PERSONA_YAML = project_path("config", "persona.yaml")

# 展示名缓存（文件 mtime 变化即失效）
_name_cache: dict[str, tuple[float, str]] = {}


def is_builtin(character_id: str | None) -> bool:
    """是否内置角色（`default`）—— 即"没有外部角色卡绑定"。"""
    return str(character_id or "").strip() in ("", BUILTIN_CHARACTER_ID)


def resolve_character_id(session_key: str, user_manager: Any = None) -> str:
    """会话键 → 角色 id（恒非空）。

    ``user_manager`` 为 :class:`UserScheduler` 实例（或任何具备
    `get_user_character` 的对象）。传入时以其为准（它持有绑定表与用户实例，
    是运行时最权威的来源）；不可用时回落 `"default"`。

    与旧实现的关键差别：**不再把 `"default"` 当作"没解析到"**。调用方若需要
    区分，请用 :func:`is_builtin`。
    """
    key = str(session_key or "").strip()
    if not key:
        return BUILTIN_CHARACTER_ID
    if user_manager is not None:
        getter = getattr(user_manager, "get_user_character", None)
        if callable(getter):
            try:
                value = str(getter(key) or "").strip()
                if value:
                    return value
            except Exception as e:  # noqa: BLE001
                logger.debug("get_user_character 解析失败 session=%s: %s", key, e)
    return BUILTIN_CHARACTER_ID


def display_name(character_id: str | None) -> str:
    """角色 id → 展示名（解析不到时回落 id 本身，绝不抛异常）。

    - 内置 `default` → `config/persona.yaml` 的 `name`（十四）；
    - 文件卡 → `config/characters/<id>.json` 的 `name`。
    """
    cid = str(character_id or "").strip() or BUILTIN_CHARACTER_ID
    if cid == BUILTIN_CHARACTER_ID:
        return _persona_yaml_name() or cid

    card_path = _CHARACTERS_DIR / f"{cid}.json"
    try:
        if not card_path.exists():
            return cid
        mtime = card_path.stat().st_mtime
        cached = _name_cache.get(cid)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        data = json.loads(card_path.read_text(encoding="utf-8"))
        name = str((data or {}).get("name") or "").strip() or cid
        _name_cache[cid] = (mtime, name)
        return name
    except Exception as e:  # noqa: BLE001
        logger.debug("读取角色卡展示名失败 id=%s: %s", cid, e)
        return cid


def _persona_yaml_name() -> str:
    """从 `config/persona.yaml` 读内置角色名（轻量解析，避免依赖 yaml 库路径）。"""
    try:
        if not _PERSONA_YAML.exists():
            return ""
        for line in _PERSONA_YAML.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                value = stripped[len("name:"):].strip().strip("'\"")
                return value
    except Exception as e:  # noqa: BLE001
        logger.debug("读取 persona.yaml 名称失败: %s", e)
    return ""


def clear_cache() -> None:
    """清空展示名缓存（测试/热更新用）。"""
    _name_cache.clear()


def characters_dir() -> Path:
    """角色卡目录（测试可 monkeypatch 本模块的 `_CHARACTERS_DIR`）。"""
    return _CHARACTERS_DIR

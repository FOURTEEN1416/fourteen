"""ASEHub — 按完整会话键隔离的主动消息引擎注册表。

2026-09-21 P1 隔离：旧实现全局单例 ASEEngine，配额/紧迫度/口吻全部用户共享，
A 的「想你」会以同一模板/同一亲密度发给 B。本 hub 为每个 user_key
（微信侧即 `owner:peer@im.wechat`）懒创建独立引擎，状态分文件落盘。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("proactive.ase_hub")

_STATE_DIR = Path(__file__).resolve().parent.parent / "data" / "ase_states"
_INDEX_PATH = _STATE_DIR / "index.json"
_MAX_ENGINES = 64


def safe_state_name(user_key: str) -> str:
    digest = hashlib.md5(str(user_key or "").encode("utf-8")).hexdigest()[:16]
    return f"{digest}.json"


def _remember_index(user_key: str, state_path: str) -> None:
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        data: dict[str, str] = {}
        if _INDEX_PATH.exists():
            raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = {str(k): str(v) for k, v in raw.items()}
        data[str(user_key)] = str(state_path)
        _INDEX_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.debug("ASE index write failed: %s", e)


def load_user_key_index() -> list[str]:
    try:
        if not _INDEX_PATH.exists():
            return []
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return [str(k) for k in raw if str(k).strip()]
    except Exception as e:  # noqa: BLE001
        logger.debug("ASE index read failed: %s", e)
    return []


class ASEHub:
    """user_key → ASEEngine 注册表（LRU 上限，状态按用户分文件）。"""

    def __init__(self, engine_factory: Callable[..., Any]):
        self._factory = engine_factory
        self._engines: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.RLock()
        self._state_dir = _STATE_DIR

    @property
    def engine_factory(self) -> Callable[..., Any]:
        return self._factory

    def known_user_keys(self) -> list[str]:
        with self._lock:
            keys = list(self._engines.keys())
        for k in load_user_key_index():
            if k not in keys:
                keys.append(k)
        return keys

    def get(self, user_key: str) -> Any:
        key = str(user_key or "").strip()
        if not key:
            raise ValueError("ASEHub.get 需要非空 user_key")
        with self._lock:
            eng = self._engines.get(key)
            if eng is not None:
                self._engines.move_to_end(key)
                return eng
            self._state_dir.mkdir(parents=True, exist_ok=True)
            state_path = str(self._state_dir / safe_state_name(key))
            eng = self._factory(user_key=key, state_path=state_path)
            self._engines[key] = eng
            while len(self._engines) > _MAX_ENGINES:
                old_key, _old = self._engines.popitem(last=False)
                closer = getattr(_old, "close", None)
                if callable(closer):
                    with contextlib.suppress(Exception):
                        closer()
                logger.debug("ASE 引擎 LRU 淘汰 user=%s", old_key)
            logger.info("ASE 引擎创建 user=%s state=%s", key, state_path)
        _remember_index(key, state_path)
        return eng

    def has(self, user_key: str) -> bool:
        with self._lock:
            return str(user_key or "").strip() in self._engines

    def on_chat(
        self,
        user_key: str,
        user_message: str,
        reply: str,
        emotion_state: dict | None = None,
        affinity_level: int | None = None,
        **kwargs: Any,
    ) -> None:
        key = str(user_key or "").strip()
        if not key:
            return
        eng = self.get(key)
        try:
            eng.on_chat(
                user_message,
                reply,
                emotion_state=emotion_state,
                affinity_level=affinity_level,
                **kwargs,
            )
        except TypeError:
            # 旧签名无 emotion_state/affinity_level
            eng.on_chat(user_message, reply)

    def tick(self, user_key: str, hours: float, dry_run: bool = False) -> Any:
        return self.get(user_key).tick(hours, dry_run=dry_run)

    def commit_sent(self, user_key: str, result: dict) -> None:
        eng = self.get(user_key)
        if hasattr(eng, "commit_sent"):
            eng.commit_sent(result)

    # ── 兼容全局单例用法（调度器探测属性）────────────────
    def __getattr__(self, name: str) -> Any:
        # 仅当至少一个用户引擎存在时代理到「最近」引擎，避免误把全局状态当用户状态
        with self._lock:
            if not self._engines:
                raise AttributeError(name)
            last = next(reversed(self._engines.values()))
        return getattr(last, name)

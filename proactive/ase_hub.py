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
_INDEX_LOCK = threading.Lock()
_MAX_ENGINES = 64


def safe_state_name(user_key: str) -> str:
    digest = hashlib.md5(str(user_key or "").encode("utf-8")).hexdigest()[:16]
    return f"{digest}.json"


def _remember_index(user_key: str, state_path: str) -> None:
    _write_index({**_read_index_raw(), str(user_key): str(state_path)})


def _forget_index(user_key: str) -> None:
    """状态文件已删除时同步移除索引项（旧实现只记不删，索引只增不减）。"""
    data = _read_index_raw()
    if str(user_key) in data:
        data.pop(str(user_key))
        _write_index(data)


def _read_index_raw() -> dict[str, str]:
    try:
        if not _INDEX_PATH.exists():
            return {}
        raw = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
    except Exception as e:  # noqa: BLE001
        logger.debug("ASE index read failed: %s", e)
    return {}


def _write_index(data: dict[str, str]) -> None:
    with _INDEX_LOCK:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            # P1-18：原子写（tmp + replace），半写 JSON 会让整个索引不可读
            tmp = _INDEX_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(_INDEX_PATH)
        except Exception as e:  # noqa: BLE001
            logger.debug("ASE index write failed: %s", e)


def load_user_key_index() -> list[str]:
    return [k for k in _read_index_raw() if k.strip()]


class ASEHub:
    """user_key → ASEEngine 注册表（LRU 上限，状态按用户分文件）。"""

    def __init__(self, engine_factory: Callable[..., Any]):
        self._factory = engine_factory
        self._engines: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.RLock()
        self._state_dir = _STATE_DIR
        # P1-18：hub 级"最近一次全局配置"回放簿——新建引擎（LRU 淘汰后
        # 重新 get、重启后新会话）也必须带上控制台已改的静默窗/暂停/阈值
        self._replay: dict[str, Any] = {}

    def _apply_replay(self, eng: Any) -> None:
        if not self._replay:
            return
        qh = self._replay.get("quiet_hours")
        if qh is not None and hasattr(eng, "set_quiet_hours"):
            with contextlib.suppress(Exception):
                eng.set_quiet_hours(*qh)
        rc = self._replay.get("runtime_config")
        if rc and hasattr(eng, "apply_runtime_config"):
            with contextlib.suppress(Exception):
                eng.apply_runtime_config(**rc)

    def _forget(self, user_key: str) -> None:
        """丢弃一个引擎（先落盘其状态）。状态文件损坏/重建时用。"""
        with self._lock:
            eng = self._engines.pop(user_key, None)
        if eng is None:
            return
        self.save_engine_state(eng)
        closer = getattr(eng, "close", None)
        if callable(closer):
            with contextlib.suppress(Exception):
                closer()

    @property
    def engine_factory(self) -> Callable[..., Any]:
        return self._factory

    def known_user_keys(self) -> list[str]:
        with self._lock:
            keys = list(self._engines.keys())
        for k, path in _read_index_raw().items():
            if k in keys:
                continue
            if path and not Path(path).exists():
                _forget_index(k)  # P1-18：状态文件已不在 → 索引项回收（旧只记不删）
                continue
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
            self._apply_replay(eng)
            self._engines[key] = eng
            while len(self._engines) > _MAX_ENGINES:
                old_key, _old = self._engines.popitem(last=False)
                # P1-18：淘汰前先落盘（旧实现直接丢——未落盘的冷却/配额随引擎蒸发）
                self.save_engine_state(_old)
                closer = getattr(_old, "close", None)
                if callable(closer):
                    with contextlib.suppress(Exception):
                        closer()
                logger.debug("ASE 引擎 LRU 淘汰 user=%s", old_key)
            logger.info("ASE 引擎创建 user=%s state=%s", key, state_path)
        _remember_index(key, state_path)
        return eng

    @staticmethod
    def save_engine_state(eng: Any) -> None:
        saver = getattr(eng, "save_state", None)
        if callable(saver):
            try:
                saver()
            except Exception as e:  # noqa: BLE001
                logger.debug("ASE 引擎状态落盘失败: %s", e)

    def iter_engines(self):
        with self._lock:
            return list(self._engines.items())

    # ── P1-18 全局操作 = 对**所有已加载引擎**扇出（旧实现经 __getattr__
    #    只命中最近一个引擎：控制台暂停/静默窗/日重置对其他用户全部失效）──

    def save_state(self) -> None:
        for _k, eng in self.iter_engines():
            self.save_engine_state(eng)

    def reset_daily_count(self) -> None:
        for _k, eng in self.iter_engines():
            with contextlib.suppress(Exception):
                eng.reset_daily_count()

    def set_quiet_hours(self, start: int, end: int) -> None:
        self._replay["quiet_hours"] = (start, end)
        for _k, eng in self.iter_engines():
            with contextlib.suppress(Exception):
                eng.set_quiet_hours(start, end)

    def apply_runtime_config(self, **kwargs: Any) -> None:
        merged = self._replay.get("runtime_config", {})
        merged.update({k: v for k, v in kwargs.items() if v is not None})
        self._replay["runtime_config"] = merged
        for _k, eng in self.iter_engines():
            with contextlib.suppress(Exception):
                eng.apply_runtime_config(**kwargs)

    def get_runtime_config(self) -> dict[str, Any]:
        """展示口径：优先取已加载引擎的真值；一个都没有时回放 hub 簿记。"""
        for _k, eng in self.iter_engines():
            if hasattr(eng, "get_runtime_config"):
                cfg = dict(eng.get_runtime_config())
                rc = self._replay.get("runtime_config") or {}
                cfg.update({k: v for k, v in rc.items() if v is not None})
                return cfg
        return {
            "threshold": None, "max_daily_messages": None,
            "min_interval_minutes": None, "cooldown_after_reply_minutes": None,
            "paused": None, **(self._replay.get("runtime_config") or {}),
        }

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

    def resolve_engine_for_manual_send(self) -> tuple[str, Any] | None:
        """P1-19：手动发送端点显式取一个具体引擎（最近活跃，退而取索引首键）。

        禁止在 hub 实例上经 ``__getattr__`` 代理读配额再**赋值**——赋值会在 hub 上
        创建真实属性、永久遮蔽代理，引擎侧配额 +1 从未归还（09-18 修过的
        「自测吃光配额」在 hub 化后原样回归）。
        """
        with self._lock:
            if self._engines:
                key = next(reversed(self._engines))
                return key, self._engines[key]
        for k in load_user_key_index():
            try:
                return k, self.get(k)
            except Exception as e:  # noqa: BLE001
                logger.warning("ASEHub 手动发送建引擎失败 user=%s: %s", k, e)
        return None

    # ── 兼容全局单例用法（调度器探测属性）────────────────
    def __getattr__(self, name: str) -> Any:
        # 仅当至少一个用户引擎存在时代理到「最近」引擎，避免误把全局状态当用户状态
        with self._lock:
            if not self._engines:
                raise AttributeError(name)
            last = next(reversed(self._engines.values()))
        return getattr(last, name)

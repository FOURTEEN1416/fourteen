"""VitalSignsEngine — 情感驱动生理指标模拟引擎。

## 为什么重写（2026-09-28 · 裁决 D12-L「生理指标从假读数变真演算」）

此前这张引擎是**孤岛**：

- `update_on_emotion` / `tick` 全仓**零生产调用者**（只有测试在调）；
- `vital_signs_state` 表由 `run_migrations` 建好，却**零写入者、零读取者**；
- 于是 `get_current` 恒走 `_default_state` —— 心率永远 72、体温永远 36.5，
  接口把一组**没有任何来源的常数**当读数返回，且不标注（违反 LLM 透明边界）。

本批把它接成事实：

1. **落库**：状态键 = `vital_state_key(会话键, 角色 id)`，与 ASEHub / 好感度 /
   画像同构（唯一形状 owner = `shisi.core.conversation_turn.isolation_key`）。
   `emotion_stage_state` 先例：表里的 `character_id` 列存的就是这个隔离键。
   写 = UPSERT，读 = 内存命中 → 库命中（回填内存）→ 默认值。
2. **事件驱动**：`proactive/ase_engine.py` 收到情绪快照时调用 `update_on_emotion`；
3. **同拍演算**：`proactive/scheduler.py` 既有 per-user 主动消息拍上追挂 `tick`
   （噪声 + 向基准回稳），不新建第二条调度；
4. **无状态诚实化**：没有任何演算结果时仍返回基准值，但**显式标注**
   「示意值，非真实生理信号」——API 与微信文案同一常量，两处不得各写一套。

降级口径：库不可用（表缺失/被锁）一律退化为纯内存、只记 debug，**绝不抛异常**——
生理读数断了不能拖垮主动消息与聊天主链。
"""

from __future__ import annotations

import logging
import random
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..affinity.enhancer import default_db_path
from ..config import get_config
from ..core.conversation_turn import isolation_key
from .emotion_mapping import EMOTION_VITAL_MAP

logger = logging.getLogger("shisi.vital_signs.vital_engine")

#: 无演算结果时的自证文案（API `note` 与微信文案**共用同一真源**）
DEFAULT_READING_NOTE = "示意值，非真实生理信号"

_EMOTION_CALM = "平静"


@dataclass
class VitalSignsState:
    heart_rate: float
    temperature: float
    breath_rate: float
    last_emotion: str
    #: False = 这组数字来自真实的演算（事件或 tick）；True = 基准占位
    is_default: bool = False


def vital_state_key(session_key: str = "", character_id: str = "") -> str:
    """生理指标状态键 = ``isolation_key(会话键, 角色 id)``。

    - 角色 id 缺省时按会话键解析（唯一 owner = `utils.character_resolver`，
      与 scheduler `_resolve_character_id` 同一口径：内置卡回落 `|` 角色后缀）；
    - **无会话维度**（控制台/旧调用按裸角色 id 查）时返回裸角色键；
    - 两者都空返回空串，调用方据此跳过（不写匿名共享行）。
    """
    sid = str(session_key or "").strip()
    cid = str(character_id or "").strip()
    if not cid:
        cid = _character_id_for_session(sid) if sid else ""
    if not sid:
        return cid
    if not cid:
        return ""
    return isolation_key(sid, cid)


def _character_id_for_session(session_key: str) -> str:
    try:
        from api.deps import deps

        gf = getattr(deps, "gf", None)
    except Exception as e:  # noqa: BLE001
        logger.debug("生理状态键解析取用户管理器失败（回落内置）: %s", e)
        gf = None
    from utils.character_resolver import BUILTIN_CHARACTER_ID, is_builtin, resolve_character_id

    cid = resolve_character_id(session_key, gf)
    if is_builtin(cid):
        from utils.session_key import character_suffix_of

        cid = character_suffix_of(session_key) or BUILTIN_CHARACTER_ID
    return cid


class VitalSignsEngine:
    def __init__(self, db_path: Path | str | None = None):
        vs = get_config("vital_signs") or {}
        hr = vs.get("heart_rate", {})
        self._hr_default = hr.get("default", 72.0)
        self._hr_min = hr.get("min", 60.0)
        self._hr_max = hr.get("max", 120.0)

        temp = vs.get("temperature", {})
        self._temp_default = temp.get("default", 36.5)
        self._temp_min = temp.get("min", 36.0)
        self._temp_max = temp.get("max", 37.5)

        br = vs.get("breath_rate", {})
        self._br_default = br.get("default", 16.0)
        self._br_min = br.get("min", 12.0)
        self._br_max = br.get("max", 25.0)

        self._smoothing = vs.get("smoothing_factor", 0.3)
        self._noise_hr = hr.get("noise_amplitude", 2)
        self._noise_temp = temp.get("noise_amplitude", 0.1)
        self._noise_br = br.get("noise_amplitude", 1)

        self._states: dict[str, VitalSignsState] = {}
        # 默认库复用 shisi 状态库真源（与好感度/阶段同库；测试可显式传临时库，
        # 宿主 `data/sqlite.db` 只由 `tests/conftest.py` 的沙箱夹具重定向该真源）。
        self._db_path = Path(db_path) if db_path else Path(default_db_path())

    # ── 演算 ────────────────────────────────────────────

    def update_on_emotion(self, character_id: str, emotion: str) -> VitalSignsState:
        mapping = EMOTION_VITAL_MAP.get(emotion, EMOTION_VITAL_MAP.get(_EMOTION_CALM, {}))
        target_hr = self._hr_default + mapping.get("heart_rate_delta", 0)
        target_temp = self._temp_default + mapping.get("temperature_delta", 0)
        target_br = self._br_default + mapping.get("breath_rate_delta", 0)

        current = self._lookup(character_id)
        if current and not current.is_default:
            s = self._smoothing
            new_hr = current.heart_rate * (1 - s) + target_hr * s
            new_temp = current.temperature * (1 - s) + target_temp * s
            new_br = current.breath_rate * (1 - s) + target_br * s
        else:
            new_hr = target_hr
            new_temp = target_temp
            new_br = target_br

        new_hr = self._clamp(new_hr, self._hr_min, self._hr_max)
        new_temp = self._clamp(new_temp, self._temp_min, self._temp_max)
        new_br = self._clamp(new_br, self._br_min, self._br_max)

        state = VitalSignsState(
            heart_rate=round(new_hr, 1),
            temperature=round(new_temp, 1),
            breath_rate=round(new_br, 1),
            last_emotion=emotion,
        )
        self._states[character_id] = state
        self._persist(character_id, state)
        return state

    def tick(self, character_id: str) -> VitalSignsState:
        current = self._lookup(character_id)
        if not current or current.is_default:
            # 没演算过就不落库：写进去会让读侧把基准当"真实读数"（标注消失）
            return self._default_state(character_id)

        noise_hr = random.gauss(0, self._noise_hr)
        noise_temp = random.gauss(0, self._noise_temp)
        noise_br = random.gauss(0, self._noise_br)

        decay = 0.05
        new_hr = current.heart_rate * (1 - decay) + self._hr_default * decay + noise_hr
        new_temp = current.temperature * (1 - decay) + self._temp_default * decay + noise_temp
        new_br = current.breath_rate * (1 - decay) + self._br_default * decay + noise_br

        state = VitalSignsState(
            heart_rate=round(self._clamp(new_hr, self._hr_min, self._hr_max), 1),
            temperature=round(self._clamp(new_temp, self._temp_min, self._temp_max), 1),
            breath_rate=round(self._clamp(new_br, self._br_min, self._br_max), 1),
            last_emotion=current.last_emotion,
        )
        self._states[character_id] = state
        self._persist(character_id, state)
        return state

    # ── 读取 ────────────────────────────────────────────

    def _lookup(self, character_id: str) -> VitalSignsState | None:
        """内存命中 → 库命中（回填内存）→ None。"""
        key = str(character_id or "")
        if not key:
            return None
        state = self._states.get(key)
        if state is not None:
            return state
        state = self._read_from_db(key)
        if state is not None:
            self._states[key] = state
        return state

    def current_or_none(self, character_id: str) -> VitalSignsState | None:
        """有演算结果才返回值（消费侧据此决定「注入 / 不注入」）。"""
        state = self._lookup(character_id)
        return None if state is None or state.is_default else state

    def get_current(self, character_id: str) -> VitalSignsState:
        state = self._lookup(character_id)
        if state is not None:
            return state
        return self._default_state(character_id)

    def format_wechat_message(self, character_id: str) -> str:
        state = self.get_current(character_id)
        text = (
            f"❤️ 心率：{state.heart_rate}bpm | 🌡️ 体温：{state.temperature}℃ | "
            f"💨 呼吸：{state.breath_rate}次/分"
        )
        if state.is_default:
            text = f"{text}（{DEFAULT_READING_NOTE}）"
        return text

    def _default_state(self, character_id: str) -> VitalSignsState:
        return VitalSignsState(
            self._hr_default, self._temp_default, self._br_default, _EMOTION_CALM, is_default=True
        )

    # ── 落盘（写失败降级为纯内存，绝不抛）─────────────────

    def _persist(self, character_id: str, state: VitalSignsState) -> None:
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                conn.execute(
                    "INSERT INTO vital_signs_state "
                    "(character_id, heart_rate, temperature, breath_rate, last_emotion, updated_at) "
                    "VALUES (?,?,?,?,?,datetime('now')) "
                    "ON CONFLICT(character_id) DO UPDATE SET "
                    "heart_rate=excluded.heart_rate, temperature=excluded.temperature, "
                    "breath_rate=excluded.breath_rate, last_emotion=excluded.last_emotion, "
                    "updated_at=excluded.updated_at",
                    (
                        str(character_id),
                        float(state.heart_rate),
                        float(state.temperature),
                        float(state.breath_rate),
                        str(state.last_emotion),
                    ),
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("生理读数落库跳过（库不可用）key=%s: %s", character_id, e)

    def _read_from_db(self, character_id: str) -> VitalSignsState | None:
        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn, conn:
                row = conn.execute(
                    "SELECT heart_rate, temperature, breath_rate, last_emotion "
                    "FROM vital_signs_state WHERE character_id=?",
                    (str(character_id),),
                ).fetchone()
        except Exception as e:  # noqa: BLE001
            logger.debug("生理读数读库跳过 key=%s: %s", character_id, e)
            return None
        if not row:
            return None
        return VitalSignsState(
            heart_rate=float(row[0]),
            temperature=float(row[1]),
            breath_rate=float(row[2]),
            last_emotion=str(row[3] or _EMOTION_CALM),
        )

    @staticmethod
    def _clamp(v: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, v))


# ── 进程内共享实例（路由读的要正是 ASE/scheduler 写的那个）──────

#: 按**已解析库路径**缓存：同库多实例会让写侧与读侧各持一份内存状态（假接线）。
#: 不同库各一份，测试传临时库即自洽。
_instances: dict[str, VitalSignsEngine] = {}


def get_vital_engine(db_path: Path | str | None = None) -> VitalSignsEngine:
    resolved = str(Path(db_path) if db_path else Path(default_db_path()))
    eng = _instances.get(resolved)
    if eng is None:
        eng = _instances[resolved] = VitalSignsEngine(db_path=resolved)
    return eng

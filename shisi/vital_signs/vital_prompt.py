"""生理读数 → 小说模式提示词注入（D12-L 的消费端）。

## 为什么单独成模块

`VitalSignsEngine` 只管**演算与存储**；本模块只管**把读数交给生成链**，
并且只在**小说式**（`utils/reply_mode.py`）下交。理由：

- 沉浸式模式明令「严禁描写自己的表情、声音、心跳」—— 注入生理读数和该指令
  直接打架，模型会突然在微信里写「（她心跳到了 102）」；
- 无演算结果时**不注入**：读侧此刻拿到的是基准占位（已在 API/微信文案里
  标注为「示意值」），把占位当事实喂给生成 = 又一处假接地。

标注不可省：注入段自带「非真实医疗信号」，避免模型把它当体检结论复述给用户。
"""

from __future__ import annotations

import logging

from .vital_engine import get_vital_engine, vital_state_key

logger = logging.getLogger("shisi.vital_signs.vital_prompt")

#: 注入段的自证文案（与 `DEFAULT_READING_NOTE` 分工不同：那条说明"这是占位基准"，
#: 本条说明"即便有演算，它也不是医疗数据"）
NON_MEDICAL_NOTE = (
    "以上为本角色模拟的生理读数，非真实医疗信号，"
    "仅供神态与氛围描写参考，不得据此编造病情、体检结论或就医建议。"
)


def novel_vital_prompt_section(
    session_key: str = "",
    character_id: str = "",
    *,
    mode: str | None = None,
    engine=None,
) -> str:
    """小说式 → 返回可拼进 system 的生理读数段；其余模式 / 无读数 → 空串。

    Args:
        session_key: 会话键（与 ASEHub / 好感度同一形态）。
        character_id: 当轮角色 id；缺省时按会话键解析。
        mode: 显式指定回复模式；缺省时读运行时真源 `utils/reply_mode`
            （web 控制端切换即时生效）。
        engine: 显式引擎实例（测试注入临时库）；缺省用进程共享实例。
    """
    from utils.reply_mode import REPLY_MODE_NOVEL, read_reply_mode

    effective = mode if mode in ("novel", "immersive") else read_reply_mode()
    if effective != REPLY_MODE_NOVEL:
        return ""

    key = vital_state_key(session_key, character_id)
    if not key:
        return ""

    eng = engine if engine is not None else get_vital_engine()
    state = eng.current_or_none(key)
    if state is None:
        return ""

    return (
        "# 此刻的生理读数（角色模拟，供描写参考）\n"
        f"心率 {state.heart_rate}bpm｜体温 {state.temperature}℃｜"
        f"呼吸 {state.breath_rate}次/分｜最近情绪：{state.last_emotion}\n"
        f"⚠️ {NON_MEDICAL_NOTE}"
    )

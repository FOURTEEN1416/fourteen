"""回复模式：沉浸式真人聊天 / 小说式。

⚠️ 2026-09-19 用户要求：「在 web 端手动控制，分成两个模式 ——
一个是沉浸式聊天（像真人真正在聊天），一个是像小说一样（带上动作、神态这些）」。

为什么需要：生产实证里同一个角色会在两种风格之间**随机跳**：
  14:17「我……（她捏着白色小风扇的手指轻轻收紧了一下，耳尖泛起一点不易察觉的红）」
  14:52「嗯，下了一下午了／雨声还挺舒服的／你那边怎么样」（纯对话）
  14:57「（停下脚步，回头看你，耳尖微微泛红）就这点出息。」
根因是 system prompt 里同时存在"写动作神态"的要求与"像真人发微信"的要求，
没有明确二选一 —— 模型就随机挑一个。

⚠️ 2026-09-19 第二轮（用户复报「还是展现出小说的感觉，一个人怎么会面对面发消息」）：
括号旁白确实已经消失，但角色仍在**演一个面对面场景** —— 生产原句：
「我尝一口，看是不是糖放多了」「那我走」「嗯。那就坐会儿吧」「那喝口茶消消食」。
根因**不在格式要求**，而在**角色卡把关系设定成物理共处**：当时绑定的角色 62105bca
其 `scenario` 写「你刚从公交车上下来…她站在巷口等你…转身走在前面带路」，
`description` 写「从小一起长大的青梅竹马…她家就在巷子尽头那栋居民楼的四层」。
模型把「关系设定」当成了「此时此地的舞台」。故 `_INSTRUCTION_IMMERSIVE` 补：
① 非共处约束（只能说话、不能做动作）② 把实际踩到的句子直接写成反例
③ 明确「场景设定 / 开场情境只是背景，不代表此刻在一起」。

真源：`data/scheduler_config.json` 的 `reply_mode` 键。与 `quiet_hours` /
`follow_up` 同一份**跨 worker 运行时配置文件**（uvicorn 4 worker 共读，
web 控制端写入即对所有 worker 生效，无需重启）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("reply_mode")

REPLY_MODE_IMMERSIVE = "immersive"   # 沉浸式：像真人发微信，不写动作/神态/旁白
REPLY_MODE_NOVEL = "novel"           # 小说式：允许动作、神态、环境、心理描写
REPLY_MODES = (REPLY_MODE_IMMERSIVE, REPLY_MODE_NOVEL)
DEFAULT_REPLY_MODE = REPLY_MODE_IMMERSIVE

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "scheduler_config.json"

_MODE_LABELS = {
    REPLY_MODE_IMMERSIVE: "沉浸式聊天（像真人发微信）",
    REPLY_MODE_NOVEL: "小说式（带动作/神态描写）",
}

_INSTRUCTION_IMMERSIVE = """【回复模式：沉浸式真人聊天】
你在用微信和人聊天，**只发你真正会打进对话框的那句话**。
- **你和对方不在同一个地方**，只能靠手机文字交流。你拿不到、看不到、尝不到、碰不到
  对方的任何东西，也去不了对方身边。凡是需要身体在场的说法，一律不许说。
- 严禁括号动作/神态/环境/心理描写（如「（她停下脚步，回头看你）」），严禁旁白与星号叙述。
- 严禁描写自己的表情、声音、心跳、动作；情绪只能通过**说话方式**体现。
- 口语、短句，通常 3~25 字；一次只回应对方这一句，不要自问自答、不要连珠炮式追问。
- 严禁编造对方没有说过的处境（地点、天气、行程、身体状态、正在做什么）。
- 角色卡里的「场景设定 / 开场情境」只是**背景设定**，不代表你们此刻在一起，
  更不是正在发生的事；不要据此演画面、不要把它当此时此地的舞台。
- 对方说过的事才是事实；对方否认过的情境必须立刻放弃，不得再提。

【常见错误 → 正确写法】
✗ 我尝一口，看是不是糖放多了  → ✓ 是糖放多了吗？
✗ 那我走 / 嗯。那就坐会儿吧    → ✓ 那你先忙 / 好，我在的
✗ 那喝口茶消消食             → ✓ 喝口茶吧
记住：**你只能说话，不能做动作。**"""

_INSTRUCTION_NOVEL = """【回复模式：小说式】
允许用括号写动作、神态、环境与心理细节，营造画面感与沉浸感。
- 仍必须**接着对方实际说的内容**推进，不得自说自话或凭空设定对方处境。
- 描写服务于情绪与信息，不要每轮都堆砌；对话本身仍要自然。
- 对方否认过的情境必须立刻放弃，不得再提。"""


def read_reply_mode() -> str:
    """读取当前回复模式；缺失/非法一律回落默认（沉浸式）。"""
    try:
        if _CONFIG_PATH.exists():
            raw: Any = json.loads(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            mode = raw.get("reply_mode")
            if mode in REPLY_MODES:
                return str(mode)
    except Exception:  # noqa: BLE001
        logger.debug("读取 reply_mode 失败，回落默认值", exc_info=True)
    return DEFAULT_REPLY_MODE


def write_reply_mode(mode: str) -> str:
    """写入回复模式（保留文件内其他键）。返回实际生效值。"""
    if mode not in REPLY_MODES:
        raise ValueError(f"unknown reply_mode: {mode!r}; expected one of {REPLY_MODES}")
    data: dict[str, Any] = {}
    try:
        if _CONFIG_PATH.exists():
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        data = {}
    data["reply_mode"] = mode
    try:
        _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_CONFIG_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning("写入 reply_mode 失败: %s", e)
    return mode


def reply_mode_instruction(mode: str | None = None) -> str:
    """返回应追加到 system prompt 的模式指令。"""
    m = mode if mode in REPLY_MODES else read_reply_mode()
    return _INSTRUCTION_NOVEL if m == REPLY_MODE_NOVEL else _INSTRUCTION_IMMERSIVE


def reply_mode_label(mode: str) -> str:
    return _MODE_LABELS.get(mode, mode)

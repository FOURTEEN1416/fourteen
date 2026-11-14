"""发声相关开关的唯一 owner（B-2 2026-11-11 / B-3+B-4+D1 2026-11-12）。

**唯一 owner**：本模块集中持有五条开关的读取与语义，调用点禁止各写一份：

- ``AGENT_CORE_EVENTS``（影子账本）与 ``SPEAKER_PROACTIVE``（主动线上收）：
  启动期一致性判定 + 消费侧入口 :func:`speaker_proactive_enabled`；
- ``SPEAKER_REPLY``（B-3 主链拆条新语义，:func:`speaker_reply_enabled`）；
- ``SPEAKER_REMINDER``（B-4 提醒线人设前缀，:func:`speaker_reminder_enabled`）；
- ``PROACTIVE_VOICE_VIA_PLANE``（D1 主动语音走控制面，:func:`proactive_voice_via_plane_enabled`）
  ——纯运行期消费侧开关，不参与启动期一致性判定（与账本无耦合）。

两个启动期挂载点（``orchestrator/_init_mixin.initialize`` 与
``api/app_factory``）都调 ``validate_speaker_switches``，**禁止**在调用点
各写一份判定（分叉即漂移）。

**语义**（契约冻结）：

- 五条开关**缺省都视为 ``'1'``**（未设置 = 开；空串/纯空白同样按未设置）；
  ⚠️ 这是**启动期一致性视图**：``agent_core.events.enabled()`` 的运行期缺省是
  **关**（零 IO 纪律），两者不矛盾——视图只负责「显式关」的自动降级判定，
  部署必须按 ``.env.example`` 显式写 ``AGENT_CORE_EVENTS=1``；
- off 判定 = ``strip().lower()`` 属于 ``('0', 'false', 'off', 'no')``；
- **speaker on 且 ledger 显式 off** → 返回双 ``'0'``（自动降级：账本关着时
  主动线上收拿不到承诺真源，只会上收一半 → 宁可整条回旧路径）并记
  ``ERROR: speaker_auto_disabled:ledger_off``；
- ledger on 且 speaker off 合法（只要旧行为，不消费账本），原样返回；
- 其余组合原样返回（归一为 ``'0'`` / ``'1'``）。

**纪律**：纯读 ``os.environ``、幂等、无 IO、**不修改** ``os.environ``
（降级只体现在返回值里，由调用方决定如何消费；本模块不越权改环境）。

**消费侧入口**：:func:`speaker_proactive_enabled`。🔴 2026-11-11 复核修复——
在此之前 ``SPEAKER_PROACTIVE`` 只有两个把返回值 ``logger.info`` 打印的调用点，
返回值无人消费 ⇒ 开关是死的：``ledger=1`` + ``SPEAKER_PROACTIVE=0`` 时主动线
承诺闸照旧全开，运维按文档置 0 会得到「以为回滚了、实际没回滚」。现在
``proactive/scheduler.py`` 的承诺闸（登记 / 注入 / hooks 剔除 / 入站销账）
每次 tick 现读本入口，开关才真正可回滚。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

LEDGER_ENV = "AGENT_CORE_EVENTS"
SPEAKER_ENV = "SPEAKER_PROACTIVE"
REPLY_ENV = "SPEAKER_REPLY"
REMINDER_ENV = "SPEAKER_REMINDER"
PROACTIVE_VOICE_ENV = "PROACTIVE_VOICE_VIA_PLANE"

#: 缺省值：五条开关出厂都开（与 ``agent_core.events`` 的 ``_TRUTHY`` 不同——
#: 那边缺省=关是「写接口首行短路」的历史口径，这里是**启动期一致性视图**，
#: 缺省按产品意图（开）呈现，避免把「没配」误报成「关」）。
_DEFAULT_ON = "1"
_OFF_VALUES = frozenset({"0", "false", "off", "no"})
_AUTO_DISABLED_EVENT = "speaker_auto_disabled:ledger_off"


def _effective(raw: str | None) -> str:
    """``os.environ`` 取值 → ``'0'`` / ``'1'``（缺省/空串/空白 = on）。"""
    text = str(raw or "").strip()
    if not text:
        return _DEFAULT_ON
    return "0" if text.lower() in _OFF_VALUES else "1"


def validate_speaker_switches() -> dict[str, str]:
    """返回两条开关的**生效值**（``{'agent_core_events': '0'|'1', ...}``）。"""
    ledger = _effective(os.environ.get(LEDGER_ENV))
    speaker = _effective(os.environ.get(SPEAKER_ENV))
    if speaker == "1" and ledger == "0":
        logger.error("%s", _AUTO_DISABLED_EVENT)
        return {"agent_core_events": "0", "speaker_proactive": "0"}
    return {"agent_core_events": ledger, "speaker_proactive": speaker}


def speaker_proactive_enabled() -> bool:
    """主动线承诺闸是否生效（**消费侧唯一入口**，每次现读环境）。

    与 :func:`validate_speaker_switches` 同一判定（自动降级也算进来）：speaker
    on 而 ledger 显式 off 时返回 ``False``。调用方只用它决定「是否登记/消费
    承诺」，不必再自己判一次 ``AGENT_CORE_EVENTS``（双判即双真源）。
    """
    return validate_speaker_switches()["speaker_proactive"] == "1"


def speaker_reply_enabled() -> bool:
    """B-3 主链拆条新语义是否生效（**消费侧唯一入口**，每次现读环境）。

    ``SPEAKER_REPLY``：缺省/空串 = 开（``'1'``）；``('0','false','off','no')``
    视为关。关闭时 ``split_reply_for_wechat`` 走 legacy 路径（不传 hard_limit），
    逐字回到 2026-11-11 之前的拆条行为，供一行回滚。
    """
    return _effective(os.environ.get(REPLY_ENV)) == "1"


def speaker_reminder_enabled() -> bool:
    """B-4 提醒线人设注入是否生效（**消费侧唯一入口**，每次现读环境）。

    ``SPEAKER_REMINDER``：缺省/空串 = 开（``'1'``）；``('0','false','off','no')``
    视为关。关闭时 ``ReminderDeliveryTask._compose_text`` 的 ``system_prompt``
    逐字回到 2026-11-12 之前的 generic 任务指令（不读人设），供一行回滚。
    """
    return _effective(os.environ.get(REMINDER_ENV)) == "1"


def proactive_voice_via_plane_enabled() -> bool:
    """D1 主动语音是否经控制面 + 连接器宿主投递（**消费侧唯一入口**）。

    ``PROACTIVE_VOICE_VIA_PLANE``：缺省/空串 = 开（``'1'``）；
    ``('0','false','off','no')`` 视为关 → ``send_proactive_voice`` 逐字回到
    2026-11-12 之前的直发路径（本进程找凭据 + 合成 + ``send_voice_clip_to``），
    供一行回滚。控制面不可用（无活跃连接器宿主）时同样回旧路径，与本开关无关。
    """
    return _effective(os.environ.get(PROACTIVE_VOICE_ENV)) == "1"

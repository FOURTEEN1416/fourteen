"""主动消息 post-draft judge（A-4，2026-10-27 Q3 用户裁决：加，fail-closed）。

只判「发 / 不发」，**不允许改写正文**——judge 的职责是守门，不是当第二作者。
judge 不可用 / 超时 / 输出不可解析 = **drop**（fail-closed）：主动消息的错误
成本远高于被动回复（用户没开口时被打扰，错误会被放大）。

设计依据：``docs/reports/2026-10-27-仿真人化引入方案.md`` §3.4（A-4）。
机制借鉴自生态调研里的 fail-closed persona judge，代码独立重写——该项目
无 LICENSE（默认版权保留），只能借机制、不可抄代码。

配置真源：``data/scheduler_config.json`` → ``llm_proactive.judge_enabled``
（默认 true，回滚开关）/ ``llm_proactive.judge_timeout_seconds``（默认 8s）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils import json_state
from utils.project_paths import project_path

logger = logging.getLogger("proactive.post_draft_judge")

#: 测试可 monkeypatch 到临时文件（与 scheduler/humanize 同款隔离口径）。
_CONFIG_PATH = Path(project_path("data", "scheduler_config.json"))

JUDGE_SYSTEM = """你是主动消息发出前的**审稿人**。只判断这条消息现在该不该发给对方，不评价文风，不修改正文。

命中以下任一情况 → drop（allow=false）：
1. 空泛问候 / 零信息：「在吗」「吃了吗」「今天怎么样」且没有任何具体由头；
2. 打卡式嘱咐：按点提醒吃饭/睡觉/喝水，与刚刚的对话无关；
3. 与最近对话无关、突兀另起一个不相关的话题；
   例外：候选明确承接【更早聊过的话题】且衔接自然 → 不算无关；
   只是生硬翻旧账（「上次那个事」式空指）→ 仍 drop；
4. 编造共同经历、承诺或对方没说过的事；
5. 客服腔 / 模板腔 / 身份声明（「作为 AI」「我能为你做什么」）。

allow=true 仅当：内容具体、自然、承接最近对话，或像真人忽然想起对方才发的。

只输出一行 JSON，不要输出任何其他文字：
{"allow": true, "reason": "一句话原因"}"""


@dataclass(frozen=True)
class JudgeVerdict:
    """judge 的终审结果。``allow=False`` 时调用方不得投递。"""

    allow: bool
    reason: str


def read_judge_config() -> tuple[bool, float]:
    """读 judge 开关与超时（热读；坏值回落默认 enabled=True / 8s）。"""
    raw: Any = json_state.read_json(_CONFIG_PATH, default={})
    block = raw.get("llm_proactive") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        block = {}
    enabled = block.get("judge_enabled", True)
    if not isinstance(enabled, bool):
        enabled = True
    try:
        timeout = float(block.get("judge_timeout_seconds", 8) or 8)
    except (TypeError, ValueError):
        timeout = 8.0
    return enabled, max(2.0, min(30.0, timeout))


def build_judge_prompt(
    message: str,
    *,
    recent: list[str] | None = None,
    quiet_hours: Any | None = None,
    topic_context: str = "",
) -> str:
    """构造 judge 输入：候选正文 + 最近对话摘要 + 当前时间/静默状态。

    不输入完整人设——避免 judge 变成第二作者（设计 §3.4 明确要求）。

    ``topic_context``：parked 复提时「更早聊过的话题」摘要（2026-10-27 话题
    状态机 Q3）。它不在最近对话里，规则 3 必须显式给它例外；为空时行为与
    旧版逐字一致。
    """
    from utils.local_time import now_local

    lines = [f"当前时间：{now_local().strftime('%Y-%m-%d %H:%M %A')}"]
    if quiet_hours:
        lines.append(f"静默时段配置：{quiet_hours}")
    if recent:
        lines.append("最近对话（旧→新）：")
        for row in list(recent)[-5:]:
            text = str(row or "").strip()
            if text:
                lines.append(f"- {text}")
    if topic_context:
        lines.append(
            "更早聊过的话题（不是刚刚说的；候选只有明确承接它时才算有关联）："
            + topic_context
        )
    lines.append("候选主动消息：")
    lines.append(str(message or ""))
    lines.append('只输出 JSON：{"allow": true/false, "reason": "..."}')
    return "\n".join(lines)


def parse_judge_verdict(raw: str) -> JudgeVerdict:
    """严格解析 judge 输出；任何不合契约的形态一律视为不可解析（drop）。"""
    match = re.search(r"\{.*\}", str(raw or ""), re.S)
    if not match:
        return JudgeVerdict(False, "judge_unparsable")
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return JudgeVerdict(False, "judge_unparsable")
    allow = data.get("allow") if isinstance(data, dict) else None
    if not isinstance(allow, bool):
        return JudgeVerdict(False, "judge_unparsable")
    reason = str((data or {}).get("reason") or "")[:200]
    return JudgeVerdict(allow, reason)


def judge_proactive_message(
    message: str,
    *,
    llm: Any,
    recent: list[str] | None = None,
    quiet_hours: Any | None = None,
    session_key: str = "",
    topic_context: str = "",
) -> JudgeVerdict:
    """主动消息终审（同步，供 scheduler 的投递线程调用）。

    - 开关关闭 → 放行（回滚路径，零成本）；
    - ``llm is None`` → **普通主动消息**放行：这是部署态未配置，不是 judge
      判定失败；fail-closed 只覆盖「judge 被调用后失败/超时/不可解析」。
    - **parked 复提**（``topic_context`` 非空）且有候选时 LLM 为空 → **drop**：
      Q3 的第三闸是「judge allow」，没有可调用的 judge 就不存在 allow；
      不能沿用 ``judge_not_configured`` 的放行语义绕过裁决。
    - 其余任何异常 → **drop**（fail-closed，见模块 docstring）。
    """
    enabled, timeout = read_judge_config()
    if not enabled:
        return JudgeVerdict(True, "judge_disabled")
    if llm is None:
        if topic_context:
            return JudgeVerdict(False, "judge_not_configured_for_topic_recall")
        return JudgeVerdict(True, "judge_not_configured")

    prompt = build_judge_prompt(
        message, recent=recent, quiet_hours=quiet_hours, topic_context=topic_context,
    )
    try:
        from proactive.llm_proactive import _llm_chat_sync

        raw = _llm_chat_sync(llm, JUDGE_SYSTEM, prompt, timeout=timeout)
        verdict = parse_judge_verdict(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[proactive_judge] judge 不可用 → fail-closed drop session=%s: %s",
            session_key, e,
        )
        return JudgeVerdict(False, "judge_unavailable")
    logger.info(
        "[proactive_judge] allow=%s reason=%s session=%s",
        verdict.allow, verdict.reason, session_key,
    )
    return verdict

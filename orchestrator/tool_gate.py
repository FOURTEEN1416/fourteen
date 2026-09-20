"""工具意图分级闸门 — 晋级线 / 终审 prompt / 防假承诺守卫。

三级意图管线的 L0+L1 规则载体（纯函数与常量，无状态，独立 owner 便于单测）：

- L0 晋级线（:func:`should_escalate`）：零成本启发式，只负责"晋级"不负责"裁决"
  —— 宁滥勿缺。未命中直接走主聊天链路，零成本零延迟。
- L1 终审（:func:`build_review_messages` / :data:`ASK_USER_TOOL`）：晋级消息带上
  工具 schema（含 ask_user 伪工具）交给 LLM 终审，由模型全权决定
  调真工具 / 发起澄清提问 / 普通闲聊。
- 防假承诺（:func:`contains_promise`）：模型声称会做某事却没调工具时拦下重试
  （2026-09-20 生产实证：「明早六点叫我起床」→「听到啦」，承诺未兑现）。

设计依据（2026-09-20 调研，LOG 留痕）：
- arXiv 2511.08798（SAGE-Agent）：澄清要克制——何时问/问什么/何时停需有判据；
- scallopbot：无工具回执不得声称成功；用户原话提醒保持确定性（原文兜底）；
- OpenAI function calling 生态惯例：意图路由在小意图集下直接让 LLM 终审。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# ── L0 晋级线 ─────────────────────────────────────────────

# 时间表达：只保留强调度信号——绝对钟点（六点/6点/6:30/六点半）与相对偏移
# （半小时后/20分钟以后）。纯日期/星期/号数不单独晋级："今天周几""25号发工资"
# 式闲聊误晋级率高，而真正的托付句（周五提醒我开会）必然含指令动词，由动词组覆盖。
_TIME_PATTERNS = (
    r"\d{1,2}\s*[点:：]",
    r"[零一二两三四五六七八九十]+\s*点",
    r"\d+\s*(分钟|小时|天|周|礼拜)[之]?后",
    r"(半|一)个?(小时|分钟|钟头|天|周|礼拜)[之]?后|一刻钟?[之]?后",
)

# 指令动词：托付/嘱托语义
_COMMAND_PATTERNS = (
    r"提醒|叫我|叫醒|喊我|cue我|别忘|不要忘|记得(?!你)|到点|准时|按时|定个|设个|订个|预约",
)

# 查询/操作类工具意图（自旧版关键词裁决组迁移而来，降级为晋级信号——
# 误晋级由 L1 终审兜底，保持旧行为覆盖面：以前能触发工具的说法仍能晋级）
_QUERY_PATTERNS = (
    r"天气|气温|温度|下雨|降雨|weather",
    r"搜索|查一下|查询资料|网上找|最新消息|新闻|search",
    r"今天几号|星期几|当前日期|现在几点|日期|节假日|农历|工作日|放假",
    r"计算|算一下|等于多少|calculator",
    r"有哪些提醒|查看提醒|我的提醒|remind",
    r"你还记得|记得我|我的偏好|关于我的记忆",
    r"创建角色|角色卡|人物资料|构建角色",
    r"总结网页|概括网页|这个链接|网页摘要",
    r"生成图片|画一张|画个|生成一张图",
    r"安排日程|创建日程|定时任务",
    r"https?://",
)

_TIME_RE = re.compile("|".join(_TIME_PATTERNS))
_COMMAND_RE = re.compile("|".join(_COMMAND_PATTERNS))
_QUERY_RE = re.compile("|".join(_QUERY_PATTERNS))


def should_escalate(query: str, has_pending_intent: bool = False) -> bool:
    """L0 晋级判定。

    Args:
        query: 用户消息原文。
        has_pending_intent: 该会话是否存在待澄清任务——存在则无论新消息
            说什么都晋级（用户的回答要和旧槽位合并判断）。
    """
    if has_pending_intent:
        return True
    text = (query or "").strip()
    if not text:
        return False
    return bool(
        _TIME_RE.search(text) or _COMMAND_RE.search(text) or _QUERY_RE.search(text)
    )


# ── L1 终审 ───────────────────────────────────────────────

ASK_USER_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "当用户的托付（提醒/叫醒/预约等）信息不全时，向用户发起一句自然、"
            "符合你角色口吻的澄清提问。只在确实缺信息时使用；信息齐全直接调对应工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "要用口语发出的澄清问题（一句话，带你的说话风格）",
                },
                "known": {
                    "type": "object",
                    "description": (
                        "用户已说清楚的信息（从用户原话提取），"
                        "如 {\"content\": \"叫我起床\", \"trigger_time\": \"\"}"
                    ),
                    "properties": {
                        "content": {"type": "string", "description": "提醒内容"},
                        "trigger_time": {
                            "type": "string",
                            "description": "已明确的触发时间（北京时间 YYYY-MM-DD HH:MM），未明确留空",
                        },
                    },
                },
                "missing": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "还缺的信息槽位名，如 [\"trigger_time\"]",
                },
            },
            "required": ["question", "known"],
        },
    },
}


def now_beijing() -> str:
    """当前服务器本地时间（=北京时间）的可读串，注入终审 prompt 供模型换算相对时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M (%A)")


def build_review_messages(
    query: str,
    system_prompt: str,
    history: list[dict[str, Any]] | None,
    pending_slots: dict[str, Any] | None = None,
    pending_ask_count: int = 0,
) -> tuple[str, str]:
    """构建终审调用的 (system, query)。

    终审 prompt 在原角色 system_prompt 末尾追加任务裁决规则——带上当前时间
    （模型据此把"明早六点"换算成具体日期时刻）与待澄清槽位（若有）。
    """
    rules = [
        "",
        "# 任务裁决（本节优先级高于闲聊风格要求）",
        f"当前时间：{now_beijing()}（北京时间）。",
        "分析用户这句话是否包含对未来的托付（提醒/叫醒/预约/查询提醒等）:",
        "- 信息齐全 → 直接调用对应工具，不要复述确认。",
        "- 信息不全（典型：缺具体时间）→ 调用 ask_user 问一句，不要猜、不要闲聊、不要空口答应。",
        "- 只是闲聊、没有任何托付 → 一个工具都不调，正常聊天。",
        "- 绝不允许：不调用工具却答应\"好的/听到啦/我会提醒你\"。做不到就问。",
    ]
    if pending_slots:
        rules.append(
            f"待继续任务：用户此前托付了 {pending_slots.get('intent', 'set_reminder')}，"
            f"已收集信息 {pending_slots}，你已经追问 {pending_ask_count} 次。"
            "把这条新消息和已收集信息合并判断：齐了就调工具；"
            "用户在回答别人的话题则不调工具正常聊天。"
        )
        if pending_ask_count >= 1:
            rules.append(
                "这是第二轮：不允许再开放式追问。若仍缺时间等信息，"
                "调用 ask_user 并给出你的最优猜测请用户确认"
                "（例：\"那我明早七点叫你起床行不行？\"），用户回复确认即可生效。"
            )
    rules_text = "\n".join(rules)
    system = f"{system_prompt}\n{rules_text}"
    return system, query


def extract_ask_user(tool_calls: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """从 tool_calls 中摘出 ask_user 调用（若与真工具混出，真工具优先）。"""
    if not tool_calls:
        return None
    real = [tc for tc in tool_calls if _tc_name(tc) != "ask_user"]
    if real:
        return None
    for tc in tool_calls:
        if _tc_name(tc) == "ask_user":
            try:
                import json

                args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                return args if isinstance(args, dict) else None
            except Exception:  # noqa: BLE001
                return None
    return None


def _tc_name(tc: dict[str, Any]) -> str:
    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
    return fn.get("name", "") if isinstance(fn, dict) else ""


# ── 防假承诺守卫 ──────────────────────────────────────────

# 声称会落实的措辞——出现在无工具调用的回复里即视为不可信（重试一次）
_PROMISE_RE = re.compile(
    r"提醒你|叫你|叫醒|喊你|会记得|帮你记|到点(叫|提醒|喊)|准时(叫|提醒|喊)|"
    r"好的[，,！!。~～]*我(会|就|去)|听到[啦了]|收到[啦了~～]|没问题|包在|交给我"
)


def contains_promise(reply: str) -> bool:
    """回复是否含\"承诺会做\"的措辞（用于无工具回执时的拦截）"""
    return bool(_PROMISE_RE.search(reply or ""))

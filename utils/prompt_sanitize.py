"""Prompt 侧清洗：角色归属 + 记忆注入质量。

2026-09-21 生产问题：LLM 分不清「哪句是用户说的 / 哪句是自己说的 / 该回什么」。
根因之一是记忆层被标成「# 对话历史」写入 system，反射/碎片事实看起来像聊天记录。
"""

from __future__ import annotations

import re
from typing import Any

# 反射/事实里出现这类原文对话痕迹 → 不得当「记忆事实」注入
_DIALOGUE_MARKERS = re.compile(
    r"(?:^|\n)\s*(?:User|Assistant|用户|助手|AI|我)\s*[:：]",
    re.IGNORECASE,
)

# 过短/像指令/像残句的「事实」不注入
_TOO_SHORT = 3
_QUESTIONISH = re.compile(r"[？?]\s*$")
# 生产实证残句/口头禅/截断承诺（2026-09-21 审查）
_FRAGMENT = re.compile(
    r"^(明天|后天|今天|今晚)?也?要[吧啊呀呢~～!！。]*$"
    r"|^(明天|后天|今天).{0,3}$"
    r"|^(记得|叫我|喊我|提醒我)[吧啊呀呢~～!！。]*$"
    r"|^叫我起床[吧啊呀呢~～!！。]*$"
)
_EMOTION_SLANG = re.compile(
    r"^(不喜欢你哦|我是委屈啊|我才没有|讨厌你|你好烦)[吧啊呀呢~～!！。]*$"
)
_CMD_LIKE = re.compile(
    r"^(记得多少|一一说来|你先|快说|没有没有|那你)[，,。！？?].*$"
    r"|^(记得多少，一一说来)$"
)


def looks_like_dialogue(text: str) -> bool:
    s = str(text or "")
    return bool(_DIALOGUE_MARKERS.search(s))


def is_injectable_fact(fact: str) -> bool:
    """是否值得作为「我记得的」注入 prompt。"""
    s = str(fact or "").strip()
    if len(s) < _TOO_SHORT:
        return False
    if looks_like_dialogue(s):
        return False
    if _QUESTIONISH.search(s) and len(s) <= 16:
        return False
    if _FRAGMENT.search(s):
        return False
    if _EMOTION_SLANG.search(s):
        return False
    if _CMD_LIKE.search(s):
        return False
    # 截断承诺：以「叫/提醒/喊」结尾且过短（生产 id14「…二十分叫」）。
    # 「催」仅当非「被催」时判残——「不喜欢被催」是正当偏好，不得误杀。
    if len(s) <= 18 and (re.search(r"(叫|提醒|喊)$", s) or re.search(r"[^被]催$", s)):
        return False
    if re.match(r"^(提醒我|叫我|喊我).{0,12}(就好了|就行|吧)[吧啊呀呢~～!！。]*$", s):
        return False
    blocked = {"叫我", "帮我", "什么", "消息", "好的", "嗯", "哦", "明天要", "后天也要", "明天也要"}
    return s not in blocked


def sanitize_fact_list(facts: Any, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for f in facts or []:
        text = (
            str(f.get("fact") or f.get("content") or "").strip()
            if isinstance(f, dict)
            else str(f or "").strip()
        )
        if not is_injectable_fact(text):
            continue
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def sanitize_reflections(reflections: Any, limit: int = 3) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for r in reflections or []:
        text = str(r or "").strip()
        if not text or looks_like_dialogue(text):
            continue
        if len(text) < 4:
            continue
        key = text[:80]
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def sanitize_episodic(episodic: Any, limit: int = 2) -> list[str]:
    out: list[str] = []
    for ep in episodic or []:
        if isinstance(ep, dict):
            meta = ep.get("metadata") or {}
            text = str(meta.get("summary") or ep.get("content") or "").strip()
        else:
            text = str(ep or "").strip()
        if not text or looks_like_dialogue(text):
            continue
        out.append(text[:200])
        if len(out) >= limit:
            break
    return out


_ALLOWED_ROLES = {"user", "assistant"}


def sanitize_llm_history(
    history: Any,
    *,
    current_user_message: str = "",
    max_messages: int = 20,
) -> list[dict[str, str]]:
    """清洗交给 LLM 的 messages 历史，保证角色可归属。

    - 只保留 role∈{user,assistant} 且 content 为非空字符串的条目
    - 去掉与当前 query 完全相同的最后一条 user（防双重「用户消息」）
    - 过滤系统错误占位 / 处理超时等
    - 保证不会以空 content 结尾
    """
    if not history:
        return []
    cleaned: list[dict[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if role not in _ALLOWED_ROLES:
            continue
        if not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        if "处理超时" in text or text.startswith("（处理消息") or text.startswith("（所有 LLM"):
            continue
        # 连续同角色合并（主动/追问/回复都可能是连续 assistant）——
        # 多数对话模型默认 user/assistant 交替，连续同角色会让它「脑补」
        # 对方发言并自问自答。合并后仍保留原文语义，用换行分隔。
        if cleaned and cleaned[-1]["role"] == role:
            cleaned[-1] = {
                "role": role,
                "content": cleaned[-1]["content"] + "\n" + text,
            }
        else:
            cleaned.append({"role": role, "content": text})
    cur = str(current_user_message or "").strip()
    if cur and cleaned and cleaned[-1]["role"] == "user" and cleaned[-1]["content"] == cur:
        cleaned.pop()
    if max_messages > 0 and len(cleaned) > max_messages:
        cleaned = cleaned[-max_messages:]
    return cleaned


ROLE_CLARITY_RULE = (
    "\n\n# 对话角色说明（必须遵守）\n"
    "- 下面 messages 中 role=user 的内容是**用户**说的；role=assistant 是**你（角色）**说的。\n"
    "- system 里出现的「记忆/观察/回忆/示例」都不是本轮用户新消息，不要当作用户刚说的话去回。\n"
    "- 你只需要回复**最后一条 role=user 的消息**；不要复述历史，不要把系统记忆当成用户发言。\n"
    "- 若记忆与用户刚说的话冲突，以用户刚说的为准。\n"
    "- **禁止一人分饰两角**：不得生成用户的台词，不得写「用户：/User:/对方：」剧本体，"
    "不得自己提问再自己回答。只输出**你自己**要说的那一段话。\n"
)

# ── 当前话题续聊（2026-09-23：像人一样把一个话题聊下去）──
# 事实里的 topics 滞后且主链根本没注入；真正缺的是「此刻在聊什么」+ 续聊指令。

_TOPIC_SEEDS = (
    "军训", "加班", "上班", "上学", "考试", "面试", "生病", "感冒", "发烧",
    "医院", "旅行", "出差", "搬家", "生日", "纪念日", "健身", "跑步",
    "睡觉", "起床", "吃饭", "外卖", "做饭", "电影", "游戏", "音乐",
    "猫", "狗", "雨", "雪", "降温", "加班", "项目", "老板", "同事",
    "室友", "家人", "爸妈", "女朋友", "男朋友", "分手", "表白",
    "作业", "论文", "实习", "工资", "房租", "地铁", "堵车",
    "咖啡", "奶茶", "火锅", "烧烤", "蛋糕", "面包",
)

_TOPIC_CJK = re.compile(
    r"(?:聊|说|讲|谈|提到|说起|关于)([一-龥A-Za-z0-9]{2,8})"
)


def extract_current_topics(
    messages: Any,
    *,
    current_user_message: str = "",
    limit: int = 3,
) -> list[str]:
    """从最近对话提取**当前话题**关键词（轻量规则，不走 LLM）。

    取材：近几条用户原话 + 当前用户消息 + 常见实体词表 + 「聊/说/关于 X」捕获。
    用途：注入 prompt 的「# 当前话题」续聊钩子 —— 让模型像人一样沿着一个
    话题说下去（补充想法/经历/自然追问），而不是每轮换一个无关点或自问自答。
    """
    texts: list[str] = []
    for item in messages or []:
        if isinstance(item, dict):
            if str(item.get("role") or "").lower() == "user":
                texts.append(str(item.get("content") or ""))
        else:
            texts.append(str(item or ""))
    if current_user_message:
        texts.append(str(current_user_message))
    blob = " ".join(texts)
    if not blob.strip():
        return []

    found: list[str] = []
    seen: set[str] = set()

    def _push(word: str) -> None:
        w = str(word or "").strip()
        if w and w not in seen:
            seen.add(w)
            found.append(w)

    for w in _TOPIC_SEEDS:
        if w in blob:
            _push(w)
    for m in _TOPIC_CJK.finditer(blob):
        _push(m.group(1))
    # 当前用户消息优先：出现在句首的名词性短语（2~6 字）也当话题
    cur = str(current_user_message or "").strip()
    if cur:
        for m in re.finditer(r"[一-龥A-Za-z]{2,6}", cur):
            token = m.group(0)
            if token in _TOPIC_SEEDS or len(token) >= 2:
                if any(token in w or w in token for w in found):
                    continue
                # 过滤纯虚词/代词
                if token in {"今天", "明天", "昨天", "现在", "怎么", "什么", "可以",
                             "还是", "不是", "一个", "我们", "你们", "这个", "那个",
                             "就是", "因为", "所以", "但是", "如果", "知道", "觉得"}:
                    continue
                _push(token)
                if len(found) >= limit * 2:
                    break
    return found[:limit]


def sanitize_reply_text(text: str) -> str:
    """清洗生成回复：剥掉「双人剧本体」，只保留角色自己要说的话。

    生产症状（2026-09-23）：模型偶尔按 mes_example 格式吐出
    「用户：…\\n角色：…」或自问自答两行；`split_reply_for_wechat` 再按换行
    拆成多条微信 → 用户看到她「说一句又自己接一句」。
    """
    s = str(text or "").strip()
    if not s:
        return s
    # 剥离对话前缀行（用户/User/对方/任意人名：）
    _speaker = re.compile(
        r"^(?:用户|User|对方|她|他|我|小明|小红|[一-龥A-Za-z]{1,6})\s*[:：]\s*"
    )
    _other_speaker = re.compile(
        r"^(?:用户|User|对方|她|他|小明|小红)\s*[:：]"
    )
    lines = s.splitlines()
    cleaned: list[str] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        stripped = _speaker.sub("", raw)
        # 命中「用户/对方」前缀 → 这是对方的台词，整行丢掉
        if _other_speaker.match(raw):
            continue
        # 命中任意「人名：」前缀 → 剥前缀保留正文（角色自己的剧本体行）
        cleaned.append(stripped)
    s = "\n".join(cleaned).strip()
    if not s:
        return str(text or "").strip()

    # 自问自答结构：「问？答」且答段以应答腔开头 → 只保留问句
    m = re.match(
        r"^(.{2,40}?[？?])[\s\n]*(.{2,80})$",
        s,
        flags=re.DOTALL,
    )
    if m:
        question = m.group(1).strip()
        answer = str(m.group(2) or "").strip()
        if re.match(
            r"^(?:嗯+|哦+|是啊|对啊|对的|是的|好啊|好呀|好哒|当然|"
            r"我觉得|我也|我也觉得|好像是|大概是|应该|没错|嘿嘿|哈哈)",
            answer,
        ):
            return question
    return s


TOPIC_CONTINUITY_RULE = (
    "\n# 当前话题（请像真人聊天一样沿着话题说下去）\n"
    "- 优先回应对方**刚说的具体内容**（细节、情绪、事实），不要无视它另起炉灶。\n"
    "- 在同一话题上补充：你的看法、相关经历、感受；或就这件事自然追问一句。\n"
    "- **至少 2~3 轮内守住同一话题**：对方没明确换题就不要跳走；"
    "连续追问/补充比抛新话题更像真人。\n"
    "- 只输出你自己要说的话；不要生成对方台词，不要自问自答。\n"
)

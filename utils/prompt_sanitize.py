"""Prompt 侧清洗：角色归属 + 记忆注入质量。

2026-09-21 生产问题：LLM 分不清「哪句是用户说的 / 哪句是自己说的 / 该回什么」。
根因之一是记忆层被标成「# 对话历史」写入 system，反射/碎片事实看起来像聊天记录。
"""

from __future__ import annotations

import re
from typing import Any

from shisi.core.conversation_turn import HISTORY_RECENT_LIMIT

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
    max_messages: int = HISTORY_RECENT_LIMIT,
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
        # 对方发言并自问自答。
        # ⚠️ 严禁用「；」压行（2026-09-25 回归实证）：历史里的「；」会被模型
        # 当成自己的说话风格照抄进回复（产出「？；；」「好梦。；哼」），
        # 而且 split_reply 认换行不认分号 → 整段糊成一条发出。
        # 保留换行（真人微信就是一条条想）；合并连续条目时用换行连接。
        ts = item.get("timestamp")  # 2026-10-11 时间感知批：落库时间戳必须存活
        ch = item.get("channel")   # 2026-10-12 因果连贯批：出站通道必须存活
        if cleaned and cleaned[-1]["role"] == role:
            cleaned[-1] = {
                "role": role,
                "content": cleaned[-1]["content"] + "\n" + text,
                "timestamp": cleaned[-1].get("timestamp") or ts,
                "channel": cleaned[-1].get("channel") or ch,
            }
        else:
            entry: dict[str, str] = {"role": role, "content": text}
            if ts:
                entry["timestamp"] = str(ts)
            if ch:
                entry["channel"] = str(ch)
            cleaned.append(entry)
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
    "- **禁止角色翻转**：不要把自己刚说的事当成对方说的去回应"
    "（例：自己说「我眯了一会儿」之后，不得接「那你再眯一会儿」）。\n"
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


_TIME_PREFIX_RE = re.compile(r"^\s*\[\d{2}-\d{2} \d{2}:\d{2}\]\s*")


def strip_time_prefixes(text: str) -> str:
    """剥掉行首的 ``[MM-DD HH:MM]`` 时间前缀（可叠加多个，循环剥净）。

    2026-10-13 生产事故根治：历史注入给每行加的 ``[MM-DD HH:MM]`` 前缀被
    模型**学舌**写进回复原文（生产实证 chat_history 2633/2635/2670-2702 等
    14 行，前缀从 1 个滚到 12 个——落库的带前缀正文下一轮又被叠新前缀）。
    前缀是注入侧的元数据，不是角色说的话，出站定稿前必须剥净。
    """
    s = str(text or "")
    while True:
        stripped = _TIME_PREFIX_RE.sub("", s)
        if stripped == s:
            break
        s = stripped
    return s


def sanitize_reply_text(text: str, character_name: str = "") -> str:
    """只清显式发言标签和格式残留，不用人称/问答句式猜测语义归属。

    任意短词加冒号不等于人名；「我吃过了，你也记得吃饭」是正常关心。
    当前角色名由调用方传入，未知人名保留。拒绝行清空后绝不复活原文。
    2026-10-13：行首 ``[MM-DD HH:MM]`` 时间前缀在此剥除（模型学舌回库
    的死循环根治，见 strip_time_prefixes）。
    """
    own_labels = ["助手", "Assistant", "AI", "角色", "我"]
    if character_name:
        own_labels.append(character_name)
    own = re.compile(r"^(?:" + "|".join(re.escape(x) for x in own_labels) + r")\s*[:：]\s*", re.IGNORECASE)
    other = re.compile(r"^(?:用户|User|Human|对方)\s*[:：]", re.IGNORECASE)
    s = str(text or "").strip()
    previous = None
    # 每轮只删字符，有限收敛；标签嵌套/格式清洗新露出的标签也一次处理完。
    while s != previous:
        previous = s
        s = strip_time_prefixes(s)
        # markdown 粗体残留剥除（2026-10-14 生产实证：智谱降级轮把
        # 「1. **冰敷**：…」markdown 列表直接发进微信——**是格式噪声不是人话）。
        s = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", s)
        s = re.sub(r"[；;]{2,}", "", s)
        s = re.sub(r"([？?。！!…])[；;]+", r"\1", s)
        s = re.sub(r"(?m)^\s*[；;]+", "", s)
        s = re.sub(r"[；;](?=[\s]|$)", "", s)
        cleaned = []
        for line in s.splitlines():
            raw = line.strip()
            raw = strip_time_prefixes(raw)
            while own.match(raw):
                raw = own.sub("", raw, count=1)
            if raw and not other.match(raw):
                cleaned.append(raw)
        s = "\n".join(cleaned).strip()
    return s


TOPIC_CONTINUITY_RULE = (
    "\n# 当前话题（请像真人聊天一样沿着话题说下去）\n"
    "- 优先回应对方**刚说的具体内容**（细节、情绪、事实），不要无视它另起炉灶。\n"
    "- 在同一话题上补充：你的看法、相关经历、感受；或就这件事自然追问一句。\n"
    "- **至少 2~3 轮内守住同一话题**：对方没明确换题就不要跳走；"
    "连续追问/补充比抛新话题更像真人。\n"
    "- 只输出你自己要说的话；不要生成对方台词，不要自问自答。\n"
)

# ── 微信表情使用守则（2026-10-14 表情镜像事故）──────────────────
# 生产实证（智谱 glm-4.5-flash 降级窗口 21/25 镜像）：用户发 [流泪] 模型回
# 「[流泪]\n别难过了…」逐条复读。用户裁决：表情能力保留（是优点），但必须
# 用得恰到好处——引导「回应情绪」而非「复读符号」，硬拦截见 strip_emoji_echo。

EMOJI_USE_RULE = (
    "\n# 微信表情使用守则\n"
    "- 表情的具体风格、用多用少**以角色卡设定为准**（角色卡怎么定就怎么用）。\n"
    "- **禁止复读用户刚发的同一条表情来回应**：对方发 [流泪]，你不能也回 [流泪]——"
    "要回应的是情绪本身（安慰/调侃/陪伴），要么不用表情，要么换一个符合你反应的。\n"
    "- 表情后面必须接你要说的话；只有一条光秃秃的表情等于没说话。\n"
)

# ── 用户身份铁则（2026-10-13/14 「夏夏」「十七」事故；与主动链
# proactive/llm_proactive.py 硬规则 8/9 同源同义，主聊链此前为空缺）──

USER_IDENTITY_RULE = (
    "\n# 用户身份铁则（最高优先级）\n"
    "- 用户是真实存在的人（注册账号本人），**不是**角色卡世界观里的人物。\n"
    "- 不得给用户起名字（如自造一个名字当昵称）；对用户的称呼优先用「用户画像」"
    "里的昵称，没有就用「你」。**但「笨蛋」「傻瓜」这类表达亲密的呼语不受此限**"
    "——那是你的语气，不是他的名字，按你的性格和关系亲密度正常使用。\n"
    "- 用户消息里出现过的名字（如某个昵称），除非用户明确自称，不得当作用户的称呼。\n"
    "- 不得编造用户没说过的经历或「我们共同的过去」；**真实记得的事要自然地用**"
    "（那是你的记忆）；不得假设你与用户共处同一物理空间（不能说「门没锁灯给你"
    "留了」「我去给你买」这类在场动作）。\n"
)


_SYSTEM_BRACKET_MARKERS = (
    "已消毒",
    "已过滤",
    "blocked",
    "收到一张图片",
    "你亲眼看到",
    "图片",
    "语音",
    "视频",
    "文件",
    "链接",
    "转发",
)
# 方括号里出现这些字符即视为系统/工具标记而非微信表情（如 [blocked:political]）。
# 注意：数字**不排除**——[666] 是真实微信表情（2026-10-14 生产原文）。
_SYSTEM_BRACKET_CHARS = set(":：/\\<>=%abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _is_emoji_token(token: str) -> bool:
    """判断 [xx] token 是否为微信表情（而非系统占位/工具标记）。

    2026-10-14 误伤修复（test_main_stream 抓出）：首版把一切 [xx] 当表情，
    安全层消毒结果「[已消毒]」被当复读 token 误剥。判据=内容为纯中文/数字/emoji
    且不在系统占位词表、不含路径/冒号/字母等标记特征。数字放行：[666] 是真实
    微信表情（生产实证文本）。
    """
    inner = token[1:-1].strip()
    if not inner or len(inner) > 12:
        return False
    if any(mark in inner for mark in _SYSTEM_BRACKET_MARKERS):
        return False
    return not any(ch in _SYSTEM_BRACKET_CHARS for ch in inner)


def _emoji_tokens_of(line: str) -> list[str]:
    """把一行拆成 [表情] token 序列；行内混有其它文字则返回空表（非纯表情行）。

    仅统计**微信表情**形态的 token（见 _is_emoji_token）；系统占位 [已消毒]
    这类既不计入纯表情判定，也不会被后续剥离。
    """
    tokens: list[str] = []
    rest = str(line or "").strip()
    while rest:
        m = re.match(r"^(\[[^\[\]\n]{1,20}\])\s*", rest)
        if not m:
            return []
        token = m.group(1)
        if not _is_emoji_token(token):
            return []
        tokens.append(token)
        rest = rest[m.end():]
    return tokens


def is_pure_emoji_message(user_msg: Any) -> bool:
    """用户消息是否为纯表情（1~3 个 [表情] token，无其它文字）。"""
    tokens = _emoji_tokens_of(str(user_msg or ""))
    return 1 <= len(tokens) <= 3


def strip_emoji_echo(reply: str, user_msg: Any) -> str:
    """剥掉回复开头对用户刚发表情的机械复读（2026-10-14 降级模型镜像根治）。

    仅当用户消息是纯表情时生效：回复行首若原样出现用户发过的同一条表情，
    剥掉该 token（保留模型自己另配的表情与正文）。剥空则返回空串，交由
    上层 empty_reply 兜底。纯表情用户消息以外的场景零改动。
    """
    if not is_pure_emoji_message(user_msg):
        return str(reply or "")
    echoed = set(_emoji_tokens_of(str(user_msg or "")))
    s = str(reply or "").lstrip()
    while True:
        m = re.match(r"^(\[[^\[\]\n]{1,12}\])\s*", s)
        if not m or m.group(1) not in echoed:
            break
        s = s[m.end():]
    return s.strip()


# ── 系统标记回显剥除（2026-10-16 生产泄漏根治）──────────────────
# 生产实证（2026-10-16 07:12 会话4）：模型把历史注入的行首标记当格式抄进
# 回复原文——「[主动] [10-15 18:27] 七点吃饭啊」直接发给了用户。标记来源：
# orchestrator.context_budget.OUTBOUND_CHANNEL_MARKERS（[主动]/[定时提醒]）
# 与时间注入的 [MM-DD HH:MM] 前缀——都是注入侧元数据，不是角色说的话。
# ⚠️ 标记词表必须由真源生成（lazy import 防循环依赖；真源加新通道标记时本
# 剥除自动跟随）——2026-10-16 独立复核抓出硬编码字面量与真源无交叉约束。
def _system_markers_re() -> re.Pattern[str]:
    """按 ``orchestrator.context_budget.OUTBOUND_CHANNEL_MARKERS`` 单一真源构建
    行首标记正则；真源不可用时回落到既有两个字面量（兼容旧测试形态）。"""
    try:
        from orchestrator.context_budget import OUTBOUND_CHANNEL_MARKERS

        markers = "|".join(re.escape(str(v)) for v in OUTBOUND_CHANNEL_MARKERS.values() if str(v))
        if markers:
            return re.compile(rf"^[ \t]*(?:{markers}|\[\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}\])[ \t]*", re.MULTILINE)
    except Exception:  # noqa: BLE001
        pass
    return _SYSTEM_MARKERS_RE_FALLBACK


_SYSTEM_MARKERS_RE_FALLBACK = re.compile(
    r"^[ \t]*(?:\[主动\]|\[定时提醒\]|\[\d{2}-\d{2} \d{2}:\d{2}\])[ \t]*",
    re.MULTILINE,
)

# 行尾「（距上一条 X）」间隔注记（annotate_history_time_gaps 追加）的真源前缀回退。
_HISTORY_GAP_NOTE_RE_FALLBACK = re.compile(
    r"[ \t]*（距上一条 [^）]*）(?=[ \t]*(?:\n|$))", re.MULTILINE
)


def _history_gap_note_re() -> re.Pattern[str]:
    """行尾「（距上一条 X）」间隔注记正则（由 ``context_budget`` 单一真源构建）。

    该注记由 :func:`orchestrator.context_budget.annotate_history_time_gaps` 追加在
    历史行末尾，与行首标记同属注入侧元数据、同样会被模型回显；前缀取自真源
    ``HISTORY_GAP_NOTE_PREFIX``（lazy import 防循环依赖）。**仅在行尾匹配**——
    正文中部的同形文字（用户转述等）不误伤（2026-10-16 对抗验收抓出：旧剥除只
    覆盖行首，尾随间隔注记漏网）。
    """
    try:
        from orchestrator.context_budget import HISTORY_GAP_NOTE_PREFIX

        prefix = re.escape(str(HISTORY_GAP_NOTE_PREFIX))
        if prefix:
            return re.compile(rf"[ \t]*{prefix}[^）]*）(?=[ \t]*(?:\n|$))", re.MULTILINE)
    except Exception:  # noqa: BLE001
        pass
    return _HISTORY_GAP_NOTE_RE_FALLBACK


def _strip_system_markers(text: str) -> str:
    """剥掉注入侧元数据：**行首**标记与**行尾**间隔注记。

    行首：``[主动]``/``[定时提醒]``/``[MM-DD HH:MM]``（两组可交替连出现，生产
    原文「[主动] [10-15 18:27] 七点吃饭啊」）；行尾：``（距上一条 X）``（时间间隔
    注记）。循环剥到无可剥为止；剥空（只剩元数据）返回空串，由调用方走 empty_reply
    兜底。仅在行首/行尾剥：正文中部的同样字样（「他说[主动]找我了」）与普通方括号
    表情（``[666]``/``[微笑]``，不属以上形态）原样保留。
    """
    s = str(text or "")
    lead = _system_markers_re()
    tail = _history_gap_note_re()
    while True:
        stripped = tail.sub("", lead.sub("", s))
        if stripped == s:
            break
        s = stripped
    return s.strip()


def terminal_reply_guard(
    reply: Any,
    *,
    user_msg: Any = "",
    character_name: str = "",
    session_key: str = "",
    character_id: str = "",
) -> str:
    """出站终审守卫（发送与落库之前的最后一道门，2026-10-14 生产事故批）。

    三类拦截（拦截后返回空串，由调用方走 empty_reply 角色化兜底）：
    1. 供应商链错误哨兵文案（全链失败时网关会把「（所有 LLM 提供商均不可用…）」
       当正常回复 return——生产实证 10-13 13:03 直接发给了情绪低谷中的用户）。
    2. 表情机械复读开头（见 strip_emoji_echo；剥离而非整条丢弃，正文保留）。
    3. 退化回复只剩角色名本身（生产实证 10-13 回复仅「十四」两个字）。

    2026-10-16 系统标记回显批（**最前**，错误哨兵判定之后立即执行）：**行首系统
    标记剥除**——模型把历史注入的行首标记当格式抄进回复原文（生产实证 07:12
    「[主动] [10-15 18:27] 七点吃饭啊」直接发给用户），见
    :func:`_strip_system_markers`。必须排在表情复读/名称退化检查**之前**：
    剥除会改变首行文本（「[主动] 十四」→「十四」），后置会让退化/复读检查
    对着未剥文本判定、漏拦剥后形态（2026-10-16 独立复核实测序洞）。剥空返回
    空串，由调用方走 empty_reply 兜底。

    2026-10-15 称呼根治批（标记剥除之后）：**非法称呼呼语剥除**——session_key
    非空时经 ``utils.address_terms`` 单一真源解析（合法称呼 ∪ 非法称呼集），
    确定性剥掉行首非法称呼（「夏夏，最近…」→「最近…」）；剥空返回空串，
    同样由调用方走 empty_reply 兜底。**缺省不传 session_key 时行为与旧版完全一致**
    （无身份可挂 → 不剥，防误伤无调用上下文的场景）。
    """
    from llm_provider.multi_provider_gateway import _is_error_reply

    s = str(reply or "").strip()
    if not s or _is_error_reply(s):
        return ""
    s = _strip_system_markers(s)
    if not s:
        return ""   # 只剩标记 → 调用方走 empty_reply 兜底
    s = strip_emoji_echo(s, user_msg)
    name = str(character_name or "").strip()
    if name and s in {name, name + "。", name + "！", name + "!"}:
        return ""
    if session_key and s:
        try:
            from utils.address_terms import resolve_address, strip_forbidden_vocatives

            _legal, _forbidden = resolve_address(str(session_key), str(character_id or ""))
            if _forbidden:
                _stripped = strip_forbidden_vocatives(s, _legal, _forbidden)
                if not _stripped:
                    return ""   # 剥空 → 调用方走 empty_reply 兜底
                s = _stripped
        except Exception:  # noqa: BLE001
            pass
    return s

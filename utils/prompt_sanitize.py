"""Prompt 侧清洗：角色归属 + 记忆注入质量。

2026-09-21 生产问题：LLM 分不清「哪句是用户说的 / 哪句是自己说的 / 该回什么」。
根因之一是记忆层被标成「# 对话历史」写入 system，反射/碎片事实看起来像聊天记录。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from shisi.core.conversation_turn import HISTORY_RECENT_LIMIT

logger = logging.getLogger("prompt_sanitize")

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
    # 2026-10-20 二轮（生产原句 2875）：「你是说刚刚我说的『点完名就吃』吗？」——
    # 那句其实是**用户**说的。上一行只禁了「把自己说的当对方说的」，反向
    # （把对方说的当自己说的）没禁，模型于是把用户台词记到自己名下 = 编造
    # 共同经历。两个方向都要写死，否则模型只防一边。
    "- **禁止反向归属**：不要把**对方**说过的话说成「我刚说的/我说过的」；"
    "引用对方的原话时必须写明是对方说的（「你刚说…」）。\n"
    "- **禁止逐字复述对方上一句当回应**：回应要带新信息（看法/经历/具体接话）。\n"
    "- **禁止解释自己的记忆机制**（「我想确认一下你是不是真的记得」"
    "「有时候我会忘记自己说过的话」）：你只是在聊天，不是在测试对方。\n"
)

# ── 记忆使用守则（2026-10-21 FIX-1）──
#
# 缺口：注入层（`shisi/application/persona_service.py` 的 injection_parts）与
# 本模块此前**没有任何一句规范「怎么用记忆」** —— 唯一近似句只有
# `USER_IDENTITY_RULE` 的「真实记得的事要自然地用（那是你的记忆）」以及
# `ROLE_CLARITY_RULE` 的几条禁令。模型因此不知道「上下文里的记忆是系统自动
# 给的」，于是会声明自己在回忆、逐条念记忆条目、说「我的记忆库/数据库里显示」，
# 甚至试图在正文里写记忆指令。
#
# 对照爱语（`aiyu_prompts_full.json` #7「感觉回忆模糊 → 自动 <search:>」与
# #0 的四档记忆规则）。仿 `EMOJI_USE_RULE` 的写法：纯文案，不改任何执行分支。
# 段标题必须与 `persona_service` 实际生成的记忆段一致，模型才对得上号
# （「我记得的」/「我对你的观察」/「相关回忆」/「早期对话摘要」/用户画像段）。
MEMORY_USAGE_RULE = (
    "\n\n# 记忆使用守则\n"
    "- 上下文里「我记得的 / 我对你的观察 / 相关回忆 / 用户画像 / 早期摘要」"
    "是系统**自动**给你的记忆，直接自然使用，不要声明你在回忆。\n"
    "- 不要逐条念出记忆条目，也不要说「我的记忆库/数据库里显示」。\n"
    "- 不要在正文里写任何记忆指令（如 <add:> 之类），也不要声称"
    "「我记住了/我记下来了」——记忆由系统自动维护。\n"
    "- 记忆与用户刚说的话冲突时，以用户刚说的为准。\n"
    "- 想不起来的细节就说记不清，不要编造。\n"
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

# 话题钩子噪声词（2026-10-20 八问题批）：生产实证话题包出现「的话」「哈喽」
# 这类纯功能词/招呼——把噪声词当「当前话题」注入，等于教模型接着寒暄词聊天。
# 词表只收**确定无话题信息**的形态；具体名词/事件词不受影响。
_TOPIC_STOPWORDS = frozenset({
    "的话", "时候", "现在", "今天", "明天", "昨天", "后天", "刚刚", "刚才",
    "之前", "以后", "以前", "就是", "还是", "可以", "什么", "怎么", "这样",
    "那样", "一下", "一点", "有点", "真的", "感觉", "觉得", "知道", "说的",
    "说了", "一个", "我们", "你们", "咱们", "这个", "那个", "因为", "所以",
    "但是", "如果", "不是", "没有", "已经", "自己", "然后", "其实", "应该",
    "可能", "需要", "出来", "起来", "东西", "事情", "哈喽", "你好", "您好",
    "早安", "晚安", "在吗", "嗨", "谢谢", "好的", "好吧",
})
_TOPIC_NOISE_PREFIX_RE = re.compile(
    r"^(?:有什么|有没有|怎么|为什么|要不|是不是|能不能|可不可以|然后|其实|就是|还是|可以)"
)
_TOPIC_NOISE_SUFFIX = "的了吧啊呢吗哦嘛呀哈啦"


def _is_topic_noise(word: str) -> bool:
    """话题词是否无信息量（纯功能词/招呼/疑问前缀/语气尾巴）。"""
    w = str(word or "").strip()
    if len(w) < 2 or w in _TOPIC_STOPWORDS:
        return True
    if w[-1] in _TOPIC_NOISE_SUFFIX:
        return True
    return bool(_TOPIC_NOISE_PREFIX_RE.match(w))


# 话题锚点取材（2026-10-20 八问题批二轮）：只从**用户自己**最近说过的话里取
# 完整短句，而不是全句随机切词。生产实测旧关键词袋给出 `['的话','哈喽']`
# —— 既不是话题，也没有说话人；模型拿到的只是噪声，于是每轮另起炉灶。
_TOPIC_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?…\n，,；;]+")
# 纯寒暄/应答整句：不构成话题，进锚点只会教唆空转（「哈喽」「嗯」「好的」）。
# 2026-10-20 二轮补充「暂时没有／不知道／随便」这类**空应答**：它们带否定词，
# 但不是可接住的具体内容；单纯靠 `_TOPIC_ANCHOR_HINTS` 的「没」会被误放行。
_GREETING_ONLY_RE = re.compile(
    r"^(?:哈喽|哈啰|你好|您好|在吗|在不在|早安|早上好|晚安|晚上好|"
    r"嗯+|哦+|噢+|啊+|好+|行+|对+|是+|收到|知道了|谢谢|没事|随便|"
    r"哈哈+|呵呵+|嘿+|诶+|欸+)[呀啊吧呢哦嘛哈啦了的\s]*$"
)
_EMPTY_ANSWER_RE = re.compile(
    r"^(?:暂时|现在|还)?(?:没有|没|不知道|不清楚|不确定|不记得|忘了|忘了了|"
    r"好像没有|好像没|没想好|随你|都行|都行吧|看情况|不一定)"
    r"[呀啊吧呢哦嘛哈啦了的\s]*$"
)
# 自我介绍/状态短句里的**指称对象**：这些词出现在用户原话里时，说明该句在说
# 一件具体的事（谁/在哪/在做什么），值得作为锚点。
_TOPIC_ANCHOR_HINTS = (
    "在", "没", "有", "去", "来", "吃", "喝", "睡", "课", "班", "考", "试",
    "买", "做", "想", "要", "喜欢", "讨厌", "累", "病", "疼", "冷", "热",
    "忙", "闲", "回", "到", "等", "见", "说", "问", "答应", "提醒",
)


def _extract_user_anchor_sentences(
    messages: Any,
    *,
    current_user_message: str = "",
    max_age_hours: float = 24.0,
    now: Any | None = None,
    limit: int = 3,
) -> list[str]:
    """取出用户最近说过的、**带具体信息**的原话短句（话题锚点真源）。

    与 `extract_current_topics` 的区别（这是本函数存在的理由）：
    - 只取 **user 行**，绝不取 assistant 行 —— 说话人是确定的（生产实证：
      旧关键词袋把角色自己的台词也当话题，模型因此分不清哪句是谁说的）；
    - 返回**完整短句**而不是切词 —— 「你先接住对方最近在说什么」需要的是
      原话，不是「的话」「哈喽」这种碎片；
    - 过滤纯寒暄/应答整句（`_GREETING_ONLY_RE`），但保留含具体动作/状态/
      未完成事项的句子（`_TOPIC_ANCHOR_HINTS` 或长度足够）。

    Args:
        messages: 历史行（dict 需含 role/content，可选 timestamp）。
        current_user_message: 本轮用户原话（总是参与，不受时间窗限制）。
        max_age_hours: 只取该窗口内的话（旧话题不教唆，沿用 v1.53 语义）。
        now: 当前墙钟（naive 北京时间）；None 时不做时间窗过滤。
        limit: 最多返回几条（越新越靠后，调用方通常取尾部）。

    Returns:
        按时间正序的短句列表（不含重复）。
    """
    candidates: list[str] = []
    wall_now = None
    if now is not None:
        wall_now = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) is not None else now
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").lower() != "user":
            continue
        ts = item.get("timestamp")
        if ts and wall_now is not None:
            try:
                from utils.local_time import beijing_wall

                w = beijing_wall(ts)
                if w is None:
                    continue
                if (wall_now - w).total_seconds() > float(max_age_hours) * 3600.0:
                    continue
            except Exception:  # noqa: BLE001
                pass  # 解析失败按无时间窗行处理（fail-open，兼容旧数据）
        candidates.append(str(item.get("content") or ""))
    if current_user_message:
        candidates.append(str(current_user_message))

    anchors: list[str] = []
    seen: set[str] = set()
    for blob in candidates:
        for raw in _TOPIC_SENTENCE_SPLIT_RE.split(str(blob or "")):
            sentence = raw.strip(" \t，,。.！!？?、；;：:~～…·\"'“”‘’")
            if len(sentence) < 2 or len(sentence) > 40:
                continue
            if _GREETING_ONLY_RE.match(sentence):
                continue
            if _EMPTY_ANSWER_RE.match(sentence):
                continue
            if sentence in seen:
                continue
            # 只保留「在说一件具体的事」的句子：含动作/状态词，或本身足够长。
            if len(sentence) < 4 and not any(h in sentence for h in _TOPIC_ANCHOR_HINTS):
                continue
            seen.add(sentence)
            anchors.append(sentence)
    return anchors[-limit:] if limit > 0 else anchors


def extract_current_topics(
    messages: Any,
    *,
    current_user_message: str = "",
    limit: int = 3,
    max_age_hours: float = 24.0,
    now: Any | None = None,
) -> list[str]:
    """从最近对话提取**当前话题**关键词（轻量规则，不走 LLM）。

    取材：近几条用户原话 + 当前用户消息 + 常见实体词表 + 「聊/说/关于 X」捕获。
    用途：注入 prompt 的「# 当前话题」续聊钩子 —— 让模型像人一样沿着一个
    话题说下去（补充想法/经历/自然追问），而不是每轮换一个无关点或自问自答。

    时间窗（2026-10-19 时间感知 3.0 批）：只从距今 ``max_age_hours``（默认 24h）
    内的消息取材 —— 旧时段话题不再被「请沿着聊下去」教唆（生产实证：10-16
    「吃饭」在 10-19 被注入当前话题）。无 timestamp 行不受窗限制（fail-open，
    兼容旧形态/合成行）；当前用户消息始终参与。
    """
    texts: list[str] = []
    wall_now = None
    if now is not None:
        wall_now = now.replace(tzinfo=None) if getattr(now, "tzinfo", None) is not None else now
    for item in messages or []:
        if isinstance(item, dict):
            if str(item.get("role") or "").lower() != "user":
                continue
            ts = item.get("timestamp")
            if ts and wall_now is not None:
                try:
                    from utils.local_time import beijing_wall

                    w = beijing_wall(ts)
                    if w is None:
                        continue
                    age = (wall_now - w).total_seconds()
                    if age > float(max_age_hours) * 3600.0:
                        continue
                except Exception:  # noqa: BLE001
                    pass  # 解析失败按无时间窗行处理（fail-open）
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
        if w and not _is_topic_noise(w) and w not in seen:
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


# 格式噪声正则（``sanitize_reply_text`` 与真流式增量守卫 ``utils.stream_guard``
# 共用同一组真源；两处各抄一份字面量，规则一改就会出现「批式清了、流式漏了」）。
_RE_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_RE_SEMI_DUP = re.compile(r"[；;]{2,}")
_RE_SEMI_AFTER_PUNCT = re.compile(r"([？?。！!…])[；;]+")
_RE_SEMI_HEAD = re.compile(r"(?m)^\s*[；;]+")
_RE_SEMI_TAIL = re.compile(r"[；;](?=[\s]|$)")


def strip_format_noise(text: str) -> str:
    """剥 markdown 粗体与分号残留（局部规则，不依赖整串位置）。

    2026-10-14 生产实证：智谱降级轮把「1. **冰敷**：…」直接发进微信。
    真流式下首句已下发的部分不走终裁，故该规则必须能逐句生效。
    """
    s = str(text or "")
    if not s:
        return s
    s = _RE_MD_BOLD.sub(r"\1", s)
    s = _RE_SEMI_DUP.sub("", s)
    s = _RE_SEMI_AFTER_PUNCT.sub(r"\1", s)
    s = _RE_SEMI_HEAD.sub("", s)
    s = _RE_SEMI_TAIL.sub("", s)
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
        # 「1. **冰敷**：…」markdown 列表直接发进微信——**是格式噪声不是人话**）。
        # 2026-10-21 R4：正则抽到 ``strip_format_noise`` 与真流式增量守卫共用。
        s = strip_format_noise(s)
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
    "- 在同一话题上补充你的看法、相关经历或感受；只有对方话里有可指认的"
    "未完成点时才追问。\n"
    "- **至少 2~3 轮内守住同一话题**：对方没明确换题就不要跳走；"
    "接着对方刚说的细节往下说，不要为了续话题新造问题。\n"
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
        from orchestrator.context_budget import (
            OUTBOUND_CHANNEL_MARKERS,
            SEGMENT_DIVIDER_MARKER,
        )

        markers = "|".join(re.escape(str(v)) for v in OUTBOUND_CHANNEL_MARKERS.values() if str(v))
        seg = re.escape(str(SEGMENT_DIVIDER_MARKER))
        if markers:
            return re.compile(
                rf"^[ \t]*(?:{markers}|{seg}|\[\d{{2}}-\d{{2}} \d{{2}}:\d{{2}}\])[ \t]*",
                re.MULTILINE,
            )
    except Exception:  # noqa: BLE001
        pass
    return _SYSTEM_MARKERS_RE_FALLBACK


_SYSTEM_MARKERS_RE_FALLBACK = re.compile(
    r"^[ \t]*(?:\[主动\]|\[定时提醒\]|\[时段分隔\]|\[\d{2}-\d{2} \d{2}:\d{2}\])[ \t]*",
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


# ── 出站相对时间词改写（2026-10-19 时间感知 3.0 批 · P3）────────────────
#
# 生产实证：距上一回合 51 小时，「（距上一条 2 天）」注记+时间锚点+推理指引
# 三重信号全在，agnes 仍输出「你刚说的那个咳」——软约束对相对时间词彻底失效。
# 闸门哲学（呼应 v1.52 生成侧判死）：>45min 时「刚才/刚刚/刚X(动词)」确定性
# 降为「之前」；「刚好/刚巧/刚强」等非时间义形态负向断言保护。

_TIME_WORD_AGE_THRESHOLD_SECONDS = 45 * 60.0
_GANG_VERB_RE = re.compile(r"(?<![好巧强毅])刚(?=(?:说|讲|提|问|聊|发|告诉))")
_JUST_NOW_RE = re.compile(r"刚才|刚刚")


def _recalibrate_time_words(text: str) -> str:
    """相对时间词确定性降级：刚才/刚刚 → 之前；单字刚+动词 → 之前。

    仅改写时间义形态；「刚好/刚巧/刚强/才刚」等受负向断言保护原样保留。
    """
    s = str(text or "")
    if not s:
        return s
    s = _JUST_NOW_RE.sub("之前", s)
    s = _GANG_VERB_RE.sub("之前", s)
    return s


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


# ── 语义终审（2026-10-20 八问题批 · P0 最后防线）────────────────────
#
# 生产实证（10-19~10-20 会话 2862~2875）：模型输出「是的，我想确认一下你是不是
# 真的记得，有时候我会忘记自己说过的话 你今天有没有什么特别的事情想要和我分享
# 的呢？」与「你呢，有没有什么特别想吃的？」。前者把聊天变成记忆测试，后者在
# 用户没提问时反向虚构提问。prompt 侧软约束已收紧（reply_mode / 话题规则），
# 但生成侧约束对降级模型仍会失效 —— 出站确定性兜底是最后一道门。

# 句界字符单一真源：批式切分（`_SENTENCE_SPLIT_RE`）与真流式的增量守卫
# （`utils.stream_guard`，R4 2026-10-21）共用同一组终止符——两处若各写一份
# 字面量，切句口径一改就会「批式删了、流式放了」。
SENTENCE_TERMINATORS = "。！？!?…\n"

_SENTENCE_SPLIT_RE = re.compile("(?<=[" + re.escape(SENTENCE_TERMINATORS) + "])")

# 元记忆解释：解释自己的记忆/验证意图（生产原句「我想确认一下你是不是真的记得，
# 有时候我会忘记自己说过的话」）。只匹配「解释记忆机制」形态，不碰正常关心
# （「确认一下你到家了没」无「是不是/是否」不命中）。
_META_MEMORY_RE = re.compile(
    r"我(?:想|要)?确认一下你(?:是不是|是否)"
    r"|你是不是真的记得"
    r"|我(?:有时候|偶尔|也)?(?:会|可能)?忘记(?:自己)?说过的话"
    r"|我(?:有时候|偶尔)?记性(?:不太好|不好|差)"
)
# 零信息问卷：把话题甩回用户的调查式提问（生产原句「你今天有没有什么特别的事情
# 想要和我分享的呢」）。限「特别的事情/想说的」等调查形态；「想跟我说说吗」这类
# 具体关心不命中。
_SURVEY_QUESTION_RE = re.compile(
    r"有没有什么(?:特别的事情|特别的|想说的|开心的事|有趣的事)"
    r"(?:想要|想)?(?:和我)?(?:分享|说)"
    r"|有什么(?:特别的事情|想说的|开心的事|有趣的事)(?:想要|想)?(?:和我)?(?:分享|说)"
)
# 反向虚构提问：「你呢，…？」。仅当用户本轮未提问时判无由头 —— 用户先问了
# 「你吃什么」，回复「你呢」才是对等回应。
# 2026-10-20 二轮：生产原句 2871「刚吃完饭／你呢／没课的时候别又错过吃饭时间了」
# 是**裸「你呢」独占一行、不带问号**，旧式必带 `？` 的匹配漏网（实测该形态漏 1/1）。
# 只加「整行仅你呢」这一精确形态：后接逗号的「你呢，别太累了」是陈述句而非提问，
# 匹配进去会误删（首版即被既有用例 test_non_question_ni_ne_kept 抓住）。
_RECIPROCAL_QUESTION_RE = re.compile(
    r"(?:那|可是|不过)?你呢\s*[，,]?[^。！？!?\n]{0,40}[？?]"
    r"|^[ \t]*(?:那|可是|不过)?你呢[ \t]*$",
    re.MULTILINE,
)
_USER_QUESTION_RE = re.compile(r"[？?]|(?:吗|呢|么|什么|怎么|为什么|为啥|哪|谁|多少|几时|几点)\s*[。！!]?\s*$")

# 归属翻转（2026-10-20 生产实证 2875）：「哦，你是说刚刚**我说的**『点完名就吃，
# 别饿着自己』吗？」—— 被复述的那句其实是**用户**上一轮说的。模型把用户的台词
# 记到自己名下，等同于编造共同经历；比单纯复述更糟，因为它同时篡改了归属。
# 只匹配「（你）是说 + 我刚才说的/我说过的 + 引号引文」形态，不碰正常的
# 「你是说…吗？」确认问句（那是对用户刚说的内容做澄清，无需归属断言）。
_ATTRIBUTION_FLIP_RE = re.compile(
    r"你(?:是|的意思)?说(?:刚刚|刚才|之前|先前)?"
    r"我(?:说|讲过|说过|提到)的"
    r"|(?:刚刚|刚才|之前|先前)我(?:说|讲过|说过)的"
    r"[「“\"'][^」”\"']{0,60}[」”\"']"
)


def _normalize_for_echo(text: str) -> str:
    """复述判定用的归一化：去掉空白与标点，保留文字本身。"""
    return re.sub(r"[\s，,。.！!？?、；;：:~～…·\-—_（）()\"'“”‘’]+", "", str(text or ""))


def _strip_verbatim_echo(text: str, user_msg: Any) -> str:
    """剥掉回复开头对用户原话的逐字复述（2026-10-20 生产实证）。

    只比对**首行**且要求归一化后等值（长度 ≥5），避免把「我吃过了」这类
    正常短回应当复述误杀；剥空返回空串由上层兜底。
    """
    s = str(text or "")
    cur = _normalize_for_echo(user_msg)
    if len(cur) < 5:
        return s
    lines = s.splitlines()
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        if _normalize_for_echo(line) == cur:
            del lines[idx]
            return "\n".join(lines).strip()
        break
    return s


def _strip_meta_memory_talk(text: str) -> str:
    """删除解释自身记忆/验证意图与零信息问卷句；句级删除，保留其余正文。"""
    s = str(text or "")
    if not s:
        return s
    kept: list[str] = []
    removed = False
    for sentence in _SENTENCE_SPLIT_RE.split(s):
        if not sentence.strip():
            continue
        compact = _normalize_for_echo(sentence)
        if _META_MEMORY_RE.search(compact) or _SURVEY_QUESTION_RE.search(compact):
            removed = True
            continue
        kept.append(sentence)
    if not removed:
        return s
    return "".join(kept).strip().lstrip("，,。.；;、 ")


def _strip_attribution_flip(text: str) -> str:
    """删除把用户台词记到角色名下的句子（生产原句 2875，2026-10-20 二轮）。

    2875:「哦，你是说刚刚我说的"点完名就吃，别饿着自己"吗？」—— 那句其实
    是**用户**上一轮说的。首轮守卫只拦了复述与元解释，这一句残留并被发送，
    等于是「我复述你的话 + 说成是我说的」双重错误。归到角色名下等于编造
    共同经历，比单纯复述更严重，按句删除。
    """
    s = str(text or "")
    if not s or not _ATTRIBUTION_FLIP_RE.search(s):
        return s
    kept: list[str] = []
    removed = False
    for sentence in _SENTENCE_SPLIT_RE.split(s):
        if not sentence.strip():
            continue
        if _ATTRIBUTION_FLIP_RE.search(sentence):
            removed = True
            continue
        kept.append(sentence)
    if not removed:
        return s
    return "".join(kept).strip().lstrip("，,。.；;、 ")


# 尾巴行行首角色前缀（structured_memory.get_cross_session_tail 的产出形态：
# ``- 角色（cid）（MM-DD HH:MM）：content``）。判据只看**行首角色词**，不依赖
# 时间戳是否存在（缺时间戳时该行同样要净化）。
_TAIL_HEAD_STRIP_RE = re.compile(r"^[\s\-—•*]*")


def _is_assistant_tail_head(head: str) -> bool:
    """尾巴行首（``：`` 之前）是否 assistant 侧。"""
    h = _TAIL_HEAD_STRIP_RE.sub("", str(head or "")).strip().lower()
    return h.startswith(("角色", "助手", "assistant"))


def scrub_history_rows(rows: Any, immersive: bool) -> Any:
    """逐行净化**即将回灌给模型**的历史（chat_history 行 / 跨会话尾巴行）。

    2026-10-21 FIX-3：单一真源。此前两处各写一套——

    - 主链 ``_prepare_context`` 的内联循环（只作用于 ``get_chat_context`` 的
      chat_history），
    - 跨会话尾巴 ``structured_memory.get_cross_session_tail`` → ``format_session_tail``
      注入，**完全没有净化**（尾巴只过滤「处理超时」占位）。

    尾巴的命中条件恰是 ``hist_len < 2``（新会话 / 近史浅），正是冷启动最易出
    问题的场景；已落库的坏回复（沉浸式括号动作 / 元记忆解释 / 归属翻转）会被
    当「我自己说过的话」few-shot 回去，形成自我强化回路。

    支持两种行长：

    - ``dict``：chat_history 形态（``{"role", "content", ...}``）——role 为
      ``assistant`` 时做语义净化；行内其它键（``timestamp``/``channel``）保留。
    - ``str``：已格式化的尾巴行 ``- 角色（cid）（MM-DD HH:MM）：content``
      ——**只净化 ``：`` 之后的 content**，行首角色/时间前缀原样保留（前缀是
      10-11 时间感知批刻意加的，整体 strip 会把时间戳一起剥掉）。

    规则（与出站守卫同源，模式无关项照旧）：

    1. ``immersive`` 时剥离括号/星号动作段（``strip_stage_directions`` 单一真源）；
    2. assistant 侧再过 ``_strip_meta_memory_talk``（元记忆解释/零信息问卷句）
       与 ``_strip_attribution_flip``（把用户台词记到自己名下）——**与模式无关**；
    3. 净化后为空的行**丢弃**（不留空壳行）；
    4. 非 list 输入原样返回（调用方 fail-open，不炸）。

    纯函数：不改入参、不动库，返回新列表——原文仍可排障。
    """
    if not isinstance(rows, list):
        return rows
    immersive = bool(immersive)
    strip_sd = None
    if immersive:
        from utils.reply_mode import strip_stage_directions

        strip_sd = strip_stage_directions

    out: list[Any] = []
    for row in rows:
        if isinstance(row, dict):
            content = str(row.get("content") or "")
            if not content:
                out.append(row)
                continue
            if strip_sd is not None:
                content = strip_sd(content)
            if str(row.get("role") or "").strip().lower() == "assistant":
                content = _strip_meta_memory_talk(content)
                content = _strip_attribution_flip(content)
            if not content.strip():
                continue
            out.append({**row, "content": content})
        elif isinstance(row, str):
            head, sep, body = row.partition("：")
            if not sep:
                # 无角色前缀（非本函数产出形态）：整行当正文处理
                head, body = "", row
            if strip_sd is not None:
                body = strip_sd(body)
            if sep and _is_assistant_tail_head(head):
                body = _strip_meta_memory_talk(body)
                body = _strip_attribution_flip(body)
            if not body.strip():
                continue
            out.append(f"{head}{sep}{body}" if sep else body)
        else:
            out.append(row)
    return out


def _strip_ungrounded_reciprocal(text: str, user_msg: Any) -> str:
    """用户本轮未提问时，截掉回复里「你呢，…？」这类反向虚构提问及其后文。

    仅在**有用户发言的回合一**启用：主动/提醒链 user_msg 为空，没有「回应哪句」
    的语境，「我这边下雨了，你呢，带伞没」是合法主动开场，不做剥离。
    """
    s = str(text or "")
    user = str(user_msg or "").strip()
    if not s or not user or _USER_QUESTION_RE.search(user):
        return s
    m = _RECIPROCAL_QUESTION_RE.search(s)
    if not m:
        return s
    # 裸「你呢」占整行时只删该行（后面可能还有正常内容）；带问号的形态连其后文
    # 一起截断 —— 那之后的句子属于同一个甩锅式反问。
    if s[m.start():m.end()].strip() in {"你呢", "那呢", "可是你呢", "不过你呢"}:
        kept = [
            line
            for line in s.splitlines()
            if _RECIPROCAL_QUESTION_RE.fullmatch(line.strip() or "x") is None
        ]
        return "\n".join(kept).strip()
    head = s[: m.start()].rstrip(" \t，,；;、")
    return head.strip()


def _strip_immersive_stage_directions(text: str, reply_mode: str) -> str:
    """沉浸式模式下剥掉括号/星号动作段；小说模式原样保留。"""
    if str(reply_mode or "").strip().lower() != "immersive":
        return str(text or "")
    from utils.reply_mode import strip_stage_directions

    return strip_stage_directions(text)


_EXTRA_ERROR_SENTINELS: tuple[str, ...] = (
    # 空链形态（multi_provider_gateway.chat_stream 首行 ``yield``，不是 raise）：
    # 它不是 ``_ERROR_SENTINELS`` 成员，批式与流式都会把它当正常回复发出去。
    "没有可用的 LLM 提供商",
)


def _is_extra_error_sentinel(text: str) -> bool:
    """``_ERROR_SENTINELS`` 之外的已知哨兵（保持单一网关真源不重复登记）。"""
    stripped = str(text or "").strip()
    if not (stripped.startswith("（") and stripped.endswith("）")):
        return False
    return any(sentinel in stripped for sentinel in _EXTRA_ERROR_SENTINELS)


def _guard_log(rule: str, before: str, after: str) -> None:
    """记录守卫命中（只记规则名与长度，不落用户原文/回复原文）。"""
    if after == before:
        return
    logger.info(
        "reply_guard_hit rule=%s before_len=%d after_len=%d",
        rule,
        len(str(before or "")),
        len(str(after or "")),
    )


def terminal_reply_guard(
    reply: Any,
    *,
    user_msg: Any = "",
    character_name: str = "",
    session_key: str = "",
    character_id: str = "",
    last_turn_age_seconds: float | None = None,
    reply_mode: str = "",
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

    2026-10-20 语义终审批（时间词改写之后）：复述原话、元记忆解释、无由头反向
    提问、沉浸式动作段四类语义缺陷确定性兜底；``reply_mode`` 缺省空串时不做
    动作段剥离（兼容无模式上下文的调用方），前两类与模式无关。
    """
    from llm_provider.multi_provider_gateway import _is_error_reply

    s = str(reply or "").strip()
    if not s or _is_error_reply(s) or _is_extra_error_sentinel(s):
        return ""
    s = _strip_system_markers(s)
    if not s:
        return ""   # 只剩标记 → 调用方走 empty_reply 兜底
    # 沉浸式模式：括号/星号动作段是小说模式专属格式，出站剥离（小说模式不动）。
    _before = s
    s = _strip_immersive_stage_directions(s, reply_mode)
    _guard_log("immersive_stage_direction", _before, s)
    if not s:
        return ""
    # 2026-10-19 时间感知 3.0：距上一回合 >45min 时相对时间词确定性降级
    # （刚才/刚刚/刚X → 之前）。None（主动链/提醒链无会话间隔语境）不动。
    if (
        last_turn_age_seconds is not None
        and float(last_turn_age_seconds) > _TIME_WORD_AGE_THRESHOLD_SECONDS
    ):
        s = _recalibrate_time_words(s)
    # 2026-10-20 语义终审：逐字复述用户原话 / 元记忆解释 / 无由头反问。
    _before = s
    s = _strip_verbatim_echo(s, user_msg)
    _guard_log("verbatim_echo", _before, s)
    if not s:
        return ""
    _before = s
    s = _strip_meta_memory_talk(s)
    _guard_log("meta_memory_talk", _before, s)
    if not s:
        return ""
    _before = s
    s = _strip_attribution_flip(s)
    _guard_log("attribution_flip", _before, s)
    if not s:
        return ""
    _before = s
    s = _strip_ungrounded_reciprocal(s, user_msg)
    _guard_log("ungrounded_reciprocal", _before, s)
    if not s:
        return ""
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

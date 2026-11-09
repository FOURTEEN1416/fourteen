"""语音朗读稿（voice script）—— 把聊天回复转成「能念出来」的文本。

**唯一 owner**：哪些内容该念、哪些不该念，只在这个模块定义一次。
对话链（``orchestrator``）与主动线（``utils.humanize_delivery`` /
``utils.proactive_voice_delivery``）都必须走这里，禁止各自 ``reply[:200]``。

为什么要这一层（2026-11-09 生产取证）：
- 线上送进 TTS 的是 ``reply[:200]`` **原样**文本——聊天里的换行、Markdown、
  链接、表情、以及小说模式的括号动作（``（她轻轻叹了口气）``）都会被照读；
- 真人发语音不会念「括号里发生的事」，也不会念 URL 和 ``**加粗**``。

**本层只做「减法」，不做「加法」**：不插语气词、不补气口、不改写句子。
2026-11-09 实测教训：上一版在「我才不是」后面塞了逗号和「嗯…」，把
「我才不是非说给你听的」切成两个孤儿短语，用户听感是「停顿很怪」——
凭空造停顿比不造更伤。停顿只跟随文本**自带的标点**。

剥除复用既有单一真源，不新开词表：
- 行首/行尾注入元数据 → ``utils.prompt_sanitize._strip_system_markers``
  （``[主动]``/``[定时提醒]``/``[MM-DD HH:MM]``/``（距上一条 X）``）；
- 括号/星号动作段 → ``utils.reply_mode.strip_stage_directions``。
"""
from __future__ import annotations

import re

# 代码块整块丢弃（念代码没有意义，且会读出符号）
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
# Markdown 结构符号（只去符号，留正文）
_HEADER_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_QUOTE_RE = re.compile(r"(?m)^\s{0,3}>\s?")
_LIST_RE = re.compile(r"(?m)^\s*(?:[-*+·]|\d+[.、)])\s+")
_EMPHASIS_RE = re.compile(r"\*\*|__|~~|\*|`")
# 加粗/下划线保留**正文**（必须在动作段剥离之前处理）：``strip_stage_directions``
# 的星号规则 ``\*[^*\n]{1,200}\*`` 会把 ``**好**`` 里的「好」当动作整段吃掉，
# 结果只剩一个「啊」——念出来就是漏字。
_STRONG_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
# 链接不念（模型偶尔会编，念出来只是噪声）
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
# 表情符号（含 ZWJ 组合与变体选择符）不念
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\uFE0F\u200D]+"
)
# 句末/句中停顿标点：换行后是否直接相连由「上一行是否已自带停顿」决定
_BREAK_PUNCT = "。！？…!?．，、；：,;:"
_DUP_PUNCT_RE = re.compile(r"([，。！？、；：])\1+")
_SPACE_RE = re.compile(r"[ \t\u3000]{2,}")
# 剥链接/表情后残留的「空格 + 标点」收掉，避免把孤空格交给 TTS
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([，。！？、；：…])")


def to_spoken_script(text: object, *, max_chars: int = 200) -> str:
    """聊天文本 → 语音朗读稿。

    Args:
        text: 模型回复原文（可含换行/Markdown/动作括号/元数据标记）。
        max_chars: 截断上限，默认 200（与历史 ``reply[:200]`` 口径一致，
            截断发生在**剥除之后**，避免把长度预算浪费在不念的符号上）。

    Returns:
        可直接送 TTS 的文本；全部内容都是非朗读物（纯动作/纯标记）时返回空串，
        由调用方决定是否放弃本轮语音（**不要**把空串当「静音语音」发出去）。
    """
    from utils.prompt_sanitize import _strip_system_markers
    from utils.reply_mode import strip_stage_directions

    s = str(text or "")
    if not s.strip():
        return ""

    s = _CODE_FENCE_RE.sub("", s)
    # 行首注入元数据（[主动] 等）先剥，避免被动作段正则连正文一起吃掉
    s = _strip_system_markers(s)
    # 加粗/下划线去符号留正文（先于动作段剥离，否则正文会被星号规则吃掉）
    s = _STRONG_RE.sub(lambda m: m.group(1) or m.group(2) or "", s)
    # 括号/星号动作段与方括号标记整段丢弃（单一真源，不在这里另写正则）
    s = strip_stage_directions(s)
    s = _INLINE_CODE_RE.sub(r"\1", s)
    s = _HEADER_RE.sub("", s)
    s = _QUOTE_RE.sub("", s)
    s = _LIST_RE.sub("", s)
    s = _EMPHASIS_RE.sub("", s)
    s = _URL_RE.sub("", s)
    s = _EMOJI_RE.sub("", s)

    # 换行 → 停顿：上一行已自带停顿标点就直接相连，否则补一个逗号。
    # 不要在这里补「嗯」「…」之类——凭空加气口会把语义块切开。
    parts: list[str] = []
    for raw_line in s.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not parts:
            parts.append(line)
        elif parts[-1] and parts[-1][-1] in _BREAK_PUNCT:
            parts[-1] = parts[-1] + line
        else:
            parts[-1] = f"{parts[-1]}，{line}"

    out = "".join(parts)
    out = _DUP_PUNCT_RE.sub(r"\1", out)
    out = _SPACE_RE.sub(" ", out)
    out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = out.strip()
    return out[:max_chars]

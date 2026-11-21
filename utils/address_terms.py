"""用户称呼权威解析与白名单机制（2026-10-15 称呼根治批）。

**问题本质**：系统里「怎么称呼用户」这个槽位没有唯一权威来源，模型从上下文
里**看见什么用什么**——角色卡里的名字（林挽夏→自造「夏夏」称呼用户）、历史里
的旧自称（「我叫十七」）、模型自造（「亲亲」）、历史里的错误称呼（自我强化回灌）。

**为什么此前三批防线（10-13 身份锚 / 10-14 画像否决门 / 10-14 出站守卫）都会
被绕过**——它们全是**黑名单**结构，三重结构性依赖：
  1. 依赖用户**先发现**错误并否认（被动——「夏夏」用户 10-12 就否认过，10-15 仍复犯）；
  2. 依赖检测窗口**不滑出**（用户消息 30 天 prune 后否认失忆）；
  3. 依赖模型**听话**（负面禁令在长上下文里执行力弱，历史惯性压过规则）。

**本模块把机制翻转为白名单，四层闭环（不再依赖以上任何一条）**：
  1. **解析**（本模块唯一 owner）：``legal`` = 画像昵称 ∪ {「你」}——一个确定值；
     ``forbidden`` = 双源否认 ∪ **角色名派生词**（角色名/名字里的字/叠字/
     「小X阿X」变体自动禁用——「夏夏」= 林挽夏的叠字，自动覆盖，**无需用户
     发现**，这是根因的结构性修复）。
  2. **注入**：所有生成线注入【用户称呼】唯一合法段（正面指定一个值，而非禁令清单）。
  3. **出站**：``strip_forbidden_vocatives`` 确定性剥除非法称呼呼语——不依赖模型配合。
  4. **持久**：否认经 ``persist_detected_denials`` 落账本永久保留（见 runtime.py）。

消费方（禁止在别处再造第二份称呼解析）：
  - 注入：``persona_service``（主链）/ ``llm_proactive``（决策线）/
    ``ase_engine``（生成线）/ ``scheduler._send_date_wish``（祝福）
  - 出站：``terminal_reply_guard``（主链定稿 + 主动链 _deliver 共用真源）
  - 历史净化：``sanitize_llm_history``（防错误称呼自我强化回灌）
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("address_terms")

# 无画像昵称时的唯一合法称呼
LEGAL_FALLBACK = "你"
# 亲密语气呼语白名单（是语气不是称呼；与 USER_IDENTITY_RULE 的豁免条款同义）
AFFECTIONATE_TERMS = frozenset(
    {"笨蛋", "傻瓜", "傻瓜蛋", "小笨蛋", "小傻瓜", "笨死了", "呆子", "傻丫头", "小坏蛋"}
)

# 行首称呼位：可选引号/空白 + 1~6 中文/字母/数字 + 分隔标点
_VOCATIVE_RE = re.compile(
    r"^[\s『「“\"'【\[（(]*([\u4e00-\u9fffA-Za-z0-9]{1,6})\s*[，,、：:！!~～]"
)
# 角色卡「叫你 X / 喊你 X」自称提取（2026-11-21 称呼语义矫正）
# 卡文里的「你」指角色本人，所以这里的 X 是**角色自己的称呼**，不是用户名字。
_SELF_VOCATIVE_RE = re.compile(
    r"(?:叫你|喊你|称呼你(?:为|作)?|管你叫)\s*[「『“\"'【\[]?"
    r"([\u4e00-\u9fffA-Za-z0-9]{1,8})"
)
# 提取结果尾部常见语气/连词清理（防「阿茹和」这类长尾被当成名字）
_SELF_VOCATIVE_TRIM = "和跟与的吧呀啊呢哦嘛啦了，,。.！!？?~～"
_SELF_VOCATIVE_STOP = {
    "你", "您", "什么", "怎么", "老师", "同学", "大家",
    # 常见动作补语，避免「叫你吃饭」被当成名字
    "起床", "吃饭", "回家", "过来", "出去", "进来", "睡觉", "上课", "放学",
    "作业", "一声", "一下", "名字",
}
# 用户自述名提取（"我是默默，做安全的" → 默默）；只在明确的「我叫X」句式取，
# 避免把「我是做后端的」整句当名字。
_SELF_INTRO_NAME_RE = re.compile(
    r"(?:我叫|我的名字是|名字叫|(?<!别)(?<!不要)叫我)\s*"
    r"([\u4e00-\u9fffA-Za-z0-9]{1,6})(?=[，,。.！!？?、\s]|$)"
)
_SELF_INTRO_STOP = {
    "什么", "怎么", "这个", "那个", "一个", "上班", "学生", "军训", "后端",
    "前端", "安全", "工作", "老师", "同学",
}
# 称呼剥除的「词边界」字符（汉字相邻 = 词内成分，不剥——防误伤「十七岁」「无名指」）
_BOUNDARY_CHARS = r"\s，,、：:；;。！!？?~～·…—\-—"
# 剥除后的残留标点清理
_PUNCT_PAIR_RE = re.compile(r"([，,、：:；;])\s*([。！!？?~～])")
_PUNCT_DUP_RE = re.compile(r"([，,、：:；;])[\s，,、：:；;]*")
_PUNCT_HEAD_RE = re.compile(r"^[\s，,、：:；;。！!？?~～]+")
_SPACE_DUP_RE = re.compile(r"[ \t]{2,}")


def extract_self_vocatives(text: str) -> set[str]:
    """从角色卡文本提取「叫你 X / 喊你 X」里的自称（2026-11-21）。

    卡文中的「你」指**角色本人**，因此提取出的 X 是角色自己的称呼。它只能
    用于角色自称，绝不能进入「AI 对用户」的合法称呼集合。
    """
    out: set[str] = set()
    for m in _SELF_VOCATIVE_RE.finditer(str(text or "")):
        name = m.group(1).strip().strip(_SELF_VOCATIVE_TRIM)
        if name and name not in _SELF_VOCATIVE_STOP and 1 <= len(name) <= 8:
            out.add(name)
    return out


def character_card_self_names(character_id: str) -> set[str]:
    """角色卡 `creator_notes` / `mes_example` / `first_mes` 中的自称集合。"""
    cid = str(character_id or "").strip()
    if not cid:
        return set()
    try:
        from utils.character_resolver import characters_dir

        path = characters_dir() / f"{cid}.json"
        if not path.exists():
            return set()
        import json

        card = json.loads(path.read_text(encoding="utf-8"))
        fields = ("creator_notes", "mes_example", "first_mes")
        text = "\n".join(str(card.get(f) or "") for f in fields)
        return extract_self_vocatives(text)
    except Exception as e:  # noqa: BLE001
        logger.debug("character card self names resolve failed: %s", e)
        return set()


def card_vocative_guard(*texts: str) -> str:
    """卡文自称守卫（仅当卡文确实出现「叫你 X」时返回非空串）。"""
    names: set[str] = set()
    for text in texts:
        names |= extract_self_vocatives(text)
    if not names:
        return ""
    return (
        "⚠️ 卡文中的「叫你 X」指**你自己**的称呼（角色名/自称），"
        "不是用户的名字；绝不能用来称呼用户。"
    )


def ai_self_name_from_profile(profile: dict | None) -> str:
    """画像里的「用户对 AI 的称呼」——只允许干净短名，异常值一律丢弃。"""
    p = profile or {}
    name = str(p.get("user_calls_ai") or "").strip()
    if not name or name in {"你", "您"} or len(name) > 12:
        return ""
    return name


def self_intro_name(text: str) -> str:
    """从用户自述提取名字（「我叫默默」「我的名字是默默」）。

    只接受明确自述句式 + 短名 + 标点边界；「我是做后端的」这类不会命中。
    """
    m = _SELF_INTRO_NAME_RE.search(str(text or ""))
    if not m:
        return ""
    name = m.group(1).strip().strip("吧啊呀呢哦嘛啦了")
    if name in _SELF_INTRO_STOP or "的" in name or len(name) > 6:
        return ""
    return name


def resolve_user_address_from_profile(
    profile: dict | None,
    character_id: str = "",
    denied: set[str] | frozenset[str] | None = None,
) -> str:
    """纯函数：画像 → AI 对用户的唯一合法称呼（2026-11-21 称呼语义矫正）。

    规则：
    - `nickname` 只有在**语义已升级**（address_semantics >= 2）时才可信；
      旧投影若已存在 `user_calls_ai`，说明 nickname 是历史污染值，回落「你」。
    - 角色名派生词、角色卡「叫你 X」自称、用户否认名一律不得成为用户称呼。
    """
    p = profile or {}
    nick = str(p.get("nickname") or "").strip()
    blocked = set(denied or ())
    blocked |= character_name_derivatives(character_id)
    blocked |= character_card_self_names(character_id)
    semantics = int(p.get("_address_semantics") or p.get("address_semantics") or 0)
    nick_trusted = bool(
        nick
        and nick not in blocked
        and nick not in {"你", "您"}
        and not (semantics < 2 and str(p.get("user_calls_ai") or "").strip())
    )
    if nick_trusted:
        return nick
    # nickname 不可信时，回落到用户自述名（「我叫默默」）；仍受同一禁用集约束。
    intro = self_intro_name(str(p.get("self_intro") or ""))
    if intro and intro not in blocked:
        return intro
    return LEGAL_FALLBACK


def _profile_projection(session_key: str) -> dict:
    """画像投影（主链同款；异常返回空字典）。"""
    try:
        from shisi.agent_plane.runtime import project_profile_for

        return project_profile_for(str(session_key or "")) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug("profile projection resolve failed: %s", e)
        return {}


def _profile_nickname(session_key: str) -> str:
    """用户明确自述、且语义已升级的称呼（否则回落「你」）。

    2026-11-21：`nickname` 历史上被 profile_tool 写成「用户对 AI 的称呼」，
    因此只要投影里已有 `user_calls_ai`（迁移信号），旧 nickname 一律不再作为
    用户称呼使用。新写入（address_semantics=2）仍可正常使用。
    """
    return resolve_user_address_from_profile(_profile_projection(session_key))


def _profile_user_calls_ai(session_key: str) -> str:
    """用户对 AI 的称呼（user_calls_ai；旧库迁移后同样可读）。"""
    return ai_self_name_from_profile(_profile_projection(session_key))


def _denied_names(session_key: str) -> set[str]:
    """被否认称呼（双源：窗口检测 + ledger 持久否决）。"""
    try:
        from shisi.agent_plane.runtime import denied_names_for

        return {
            str(x).strip() for x in denied_names_for(str(session_key or "")) if str(x).strip()
        }
    except Exception as e:  # noqa: BLE001
        logger.debug("denied names resolve failed: %s", e)
        return set()


def character_name_derivatives(character_id: str) -> set[str]:
    """角色名派生词（自动禁用集）：**角色名只能指角色自己，永远不能称呼用户**。

    这是「夏夏」事故（林挽夏 → 叠字夏夏被用来称呼用户）的结构性修复——
    无需用户发现、无需否认记录、无需模型自觉。

    生成规则（宁多勿漏；剥除只作用于称呼位/词边界，代价低）：
      - 单字叠词：林挽夏 → 夏夏/挽挽/林林
      - 相邻双字：林挽夏 → 林挽/挽夏；角色名本身
      - 末字「小/阿/老/大」变体：夏 → 小夏/阿夏/老夏/大夏
    """
    # 空角色 id 表示「调用方没有角色上下文」：无法派生，宁缺勿滥。
    # （内置默认角色只在调用方显式传 "default" 时参与派生。）
    if not str(character_id or "").strip():
        return set()
    try:
        from utils.character_resolver import display_name

        name = str(display_name(str(character_id or "")) or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("character name resolve failed: %s", e)
        name = ""
    out: set[str] = set()
    chars = [c for c in name if "\u4e00" <= c <= "\u9fff"]
    if len(chars) < 2:
        return out
    for c in chars:
        out.add(c + c)
    for i in range(len(chars) - 1):
        out.add(chars[i] + chars[i + 1])
    last = chars[-1]
    for prefix in ("小", "阿", "老", "大"):
        out.add(prefix + last)
    out.add(name)
    return out


def resolve_address(session_key: str, character_id: str = "") -> tuple[str, frozenset[str]]:
    """解析 ``(唯一合法称呼, 非法称呼冻结集)``。

    裁决优先级：**用户否认 > 画像昵称 > 角色名派生/卡文自称自动禁用**。
    - 画像昵称非空且未被否认、且不在角色名派生/卡文自称集 → legal = 昵称；
    - 昵称被否认 / 昵称为空 / 昵称命中角色自称 → legal = 「你」；
    - forbidden = 角色名派生 ∪ 角色卡「叫你 X」自称 ∪ 双源否认。
    """
    nick = _profile_nickname(session_key)
    denied = _denied_names(session_key)
    forbidden: set[str] = (
        character_name_derivatives(character_id)
        | character_card_self_names(character_id)
        | denied
    )
    legal = nick if nick and nick not in denied and nick not in forbidden else LEGAL_FALLBACK
    # legal 自身不是非法称呼（出站/历史剥除不得误伤合法称呼）
    forbidden.discard(legal)
    return legal, frozenset(forbidden)


def address_prompt_block(session_key: str, character_id: str = "") -> str:
    """【用户称呼】唯一合法段（正面指定 + 角色名/卡文自称自动禁用）。"""
    if not str(session_key or "").strip():
        return ""
    legal, _ = resolve_address(session_key, character_id)
    denied = _denied_names(session_key)
    ai_name = _profile_user_calls_ai(session_key)
    card_names = character_card_self_names(character_id)
    lines = [
        "# 用户称呼（最高优先级，唯一合法来源）",
        f"- 称呼用户**只能**用：「{legal}」；其余场合一律用代词「你/你的」。",
        "- **你自己的名字（以及名字里的字、字的各种叠写与变体）永远只能指你自己**，"
        "任何情况下不得用来称呼用户，也不得当成用户的名字。",
    ]
    if ai_name and ai_name != legal:
        lines.append(
            f"- 用户平时叫你「{ai_name}」——这是**你**的称呼，不是用户的名字；"
            "不得反过来用它称呼用户。"
        )
    if card_names:
        lines.append(
            "- 角色卡里出现的「叫你 X」（"
            + "、".join(f"「{n}」" for n in sorted(card_names))
            + "）是**角色自己的称呼**，不是用户的名字，绝不能用来称呼用户。"
        )
    else:
        lines.append(
            "- 角色卡/人设文本里若出现「叫你 X」，X 指**你自己**，"
            "不是用户的名字，绝不能用来称呼用户。"
        )
    lines.append(
        "- 自称只能用「我」（或角色名本身）；不得用「你」指代自己，"
        "也不得把自己的名字/卡文自称当成对用户的称呼。"
    )
    if denied:
        lines.append(
            "【被否认称呼】以下名字已被用户明确否认（用户说过「谁是X/X是谁/我不是X/别叫我X」）："
            + "、".join(f"「{d}」" for d in sorted(denied))
            + "。任何场景下都不得再用这些名字称呼用户——包括道歉、复盘、引用历史时；"
            "对用户只能称「你」。"
        )
    lines.append("- 亲密语气呼语（如「笨蛋」「傻瓜」）是语气不是称呼，不受此限。")
    return "\n".join(lines)


def strip_illegal_vocative(
    text: str, legal: str, forbidden: frozenset[str] | set[str]
) -> str:
    """剥除行首**非法称呼呼语**（"夏夏，最近…" → "最近…"）。

    合法称呼、亲密呼语、普通词一律不动。只剥称呼词与紧随的分隔标点。
    """
    s = str(text or "")
    m = _VOCATIVE_RE.match(s)
    if not m:
        return s
    token = m.group(1)
    if token in AFFECTIONATE_TERMS or token == legal:
        return s
    if token in set(forbidden):
        rest = s[m.end(1):]
        rest = _PUNCT_HEAD_RE.sub("", rest)
        return rest
    return s


def strip_forbidden_vocatives(
    text: str, legal: str, forbidden: frozenset[str] | set[str]
) -> str:
    """确定性剥除非法称呼（出站终审 / 历史注入净化共用最小动作）。

    两层：
      1. 亲密呼语组合（「笨蛋十七」→「笨蛋」）——呼语保留、名字剥除；
      2. **词边界匹配**：非法称呼两侧是标点/空白/行边界时才剥——
         「夏夏，最近…」「你，十七。」命中；「十七岁」「无名指」「我是十四」
         这类词内成分不命中（不误伤正常语言）。
    剥除后清理残留标点与空白；结果不足 2 字符返回空串（调用方走兜底）。
    """
    s = str(text or "")
    if not s:
        return s
    targets = sorted(
        (t for t in set(forbidden) if t and t != legal),
        key=len,
        reverse=True,
    )
    # 1) 亲密呼语 + 名字 组合 → 只留呼语
    for aff in AFFECTIONATE_TERMS:
        for tok in targets:
            if aff + tok in s:
                s = s.replace(aff + tok, aff)
    # 2) 词边界剥除（前边界：串首/行首/标点/空白；后边界：标点/空白/串尾）
    for tok in targets:
        pat = re.compile(
            rf"(?:(?<=^)|(?<=\n)|(?<=[{_BOUNDARY_CHARS}]))"
            + re.escape(tok)
            + rf"(?=[{_BOUNDARY_CHARS}]|$)"
        )
        s = pat.sub("", s)
    # 3) 残留标点清理
    s = _PUNCT_PAIR_RE.sub(r"\1", s)
    s = _PUNCT_DUP_RE.sub(r"\1", s)
    s = _PUNCT_HEAD_RE.sub("", s)
    s = _SPACE_DUP_RE.sub(" ", s)
    s = s.strip()
    if len(s) < 2:
        return ""
    return s


def scrub_text(
    text: str, legal: str, forbidden: frozenset[str] | set[str]
) -> str:
    """存量文档净化（历史行 / 摘要 / 状态文件清洗专用，比出站剥除更彻底）。

    在 ``strip_forbidden_vocatives`` 之上追加：
      - 删除**含非法称呼的整段括号旁白**（「（拍拍十七的肩膀）」→ 整段移除；
        连旁白里的错误称呼也不留，防自我强化回灌）。
    """
    s = str(text or "")
    if not s:
        return s
    tokens = [t for t in set(forbidden) if t and t != legal]
    for tok in sorted(tokens, key=len, reverse=True):
        s = re.sub(rf"[（(][^（()）]*{re.escape(tok)}[^（()）]*[）)]", "", s)
    return strip_forbidden_vocatives(s, legal, forbidden)

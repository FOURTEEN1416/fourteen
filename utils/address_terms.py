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
# 称呼剥除的「词边界」字符（汉字相邻 = 词内成分，不剥——防误伤「十七岁」「无名指」）
_BOUNDARY_CHARS = r"\s，,、：:；;。！!？?~～·…—\-—"
# 剥除后的残留标点清理
_PUNCT_PAIR_RE = re.compile(r"([，,、：:；;])\s*([。！!？?~～])")
_PUNCT_DUP_RE = re.compile(r"([，,、：:；;])[\s，,、：:；;]*")
_PUNCT_HEAD_RE = re.compile(r"^[\s，,、：:；;。！!？?~～]+")
_SPACE_DUP_RE = re.compile(r"[ \t]{2,}")


def _profile_nickname(session_key: str) -> str:
    """画像昵称（主链同款投影；内部已含读侧否决守卫）。"""
    try:
        from shisi.agent_plane.runtime import project_profile_for

        prof = project_profile_for(str(session_key or ""))
        return str((prof or {}).get("nickname") or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("profile nickname resolve failed: %s", e)
        return ""


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

    裁决优先级：**用户否认 > 画像昵称 > 角色名派生自动禁用**。
    - 画像昵称非空且未被否认 → legal = 昵称（用户真实身份优先于角色名派生
      冲突，例如用户真名恰与角色名叠字相同时也以用户为准）；
    - 昵称被否认 / 昵称为空 → legal = 「你」；
    - forbidden = 角色名派生 ∪ 双源否认（含被否认的画像昵称）。
    """
    nick = _profile_nickname(session_key)
    denied = _denied_names(session_key)
    forbidden: set[str] = character_name_derivatives(character_id) | denied
    if nick and nick not in denied:
        legal = nick
        forbidden.discard(nick)
    else:
        legal = LEGAL_FALLBACK
    return legal, frozenset(forbidden)


def address_prompt_block(session_key: str, character_id: str = "") -> str:
    """【用户称呼】唯一合法段（正面指定 + 角色名自动禁用；替代黑名单式禁令）。"""
    if not str(session_key or "").strip():
        return ""
    legal, _ = resolve_address(session_key, character_id)
    denied = _denied_names(session_key)
    lines = [
        "# 用户称呼（最高优先级，唯一合法来源）",
        f"- 称呼用户**只能**用：「{legal}」；其余场合一律用代词「你/你的」。",
        "- **你自己的名字（以及名字里的字、字的各种叠写与变体）永远只能指你自己**，"
        "任何情况下不得用来称呼用户，也不得当成用户的名字。",
    ]
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

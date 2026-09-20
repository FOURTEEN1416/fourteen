"""系统旁路句角色化（包 Q · A2）。

问题：counter_rebuttal / 空回复 / 超时 / 异常罐头句非角色口吻（H4），
高频失败时用户连续收到同一句 → 机器人感。

契约：
- 输入：character_id, kind(empty_reply|timeout|exception|rebuttal), reply_mode, count
- 输出：1 句符合沉浸式约束（**无括号动作**）的口语
- 当日同 (character_id, kind) 去重，池子用尽后回退允许重复
- 外部角色优先从卡 catchphrases / mes_example 风格变体取词
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from utils.local_time import now_local
from utils.reply_mode import REPLY_MODE_NOVEL

logger = logging.getLogger("fallback_lines")

KINDS = ("empty_reply", "timeout", "exception", "rebuttal")

# 默认池：全部无括号动作，沉浸式安全
_POOLS: dict[str, list[str]] = {
    "empty_reply": [
        "刚才没接上，你再说一句？",
        "诶，刚才那条好像没收到，再发一次？",
        "嗯？你说什么，我这边刚没看清",
        "刚才有点卡，你再说一遍好不好",
    ],
    "timeout": [
        "等我一下，刚才有点卡",
        "不好意思，刚才走神了，你刚说什么？",
        "稍等，我这边反应慢了半拍",
        "刚好像卡住了，你再说一次？",
    ],
    "exception": [
        "刚才好像出问题了，再说一次好吗",
        "嗯？好像没处理好，再发一遍？",
        "抱歉，刚才那下没接住，再来一句",
        "这边刚才乱了一下，你再说说？",
    ],
    "rebuttal": [
        "都第{count}次说没事了…真的没事吗？",
        "你又说没事，这是第{count}次了，跟我说实话好不好",
        "第{count}次了哦，我不信你真的没事",
        "都说了{count}次没事…要不换一句说说？",
    ],
}

_CHARS_DIR = Path(__file__).resolve().parent.parent / "config" / "characters"

_lock = threading.Lock()
_day_key: str = ""
_used: dict[tuple[str, str], set[str]] = {}
_card_cache: dict[str, dict[str, Any]] = {}


def _local_day() -> str:
    return now_local().strftime("%Y-%m-%d")


def _reset_if_new_day() -> None:
    global _day_key
    day = _local_day()
    if day != _day_key:
        _day_key = day
        _used.clear()


def _load_card(character_id: str | None) -> dict[str, Any]:
    if not character_id:
        return {}
    cid = str(character_id).strip()
    if not cid or cid in ("default", "demo"):
        return {}
    if cid in _card_cache:
        return _card_cache[cid]
    card: dict[str, Any] = {}
    try:
        if _CHARS_DIR.exists():
            direct = _CHARS_DIR / f"{cid}.json"
            if direct.exists():
                with open(direct, encoding="utf-8") as fh:
                    card = json.load(fh) or {}
            else:
                for f in _CHARS_DIR.glob("*.json"):
                    try:
                        with open(f, encoding="utf-8") as fh:
                            data = json.load(fh)
                        if str(data.get("id")) == cid:
                            card = data
                            break
                    except (OSError, json.JSONDecodeError):
                        continue
    except OSError:
        card = {}
    _card_cache[cid] = card
    return card


def _extract_catchphrase(card: dict[str, Any]) -> str:
    """从角色卡提取一句短口头禅（≤12 字），失败返回空。"""
    cps = card.get("catchphrases") or []
    if isinstance(cps, str):
        cps = [cps]
    for c in cps:
        s = str(c).strip().strip("。！？…")
        if 2 <= len(s) <= 12 and "（" not in s and "(" not in s:
            return s
    return ""


def _character_variants(character_id: str | None, kind: str, count: int | None) -> list[str]:
    """基于角色卡生成轻量变体（不硬编码角色性格）。"""
    card = _load_card(character_id)
    if not card:
        return []
    name = str(card.get("name") or "").strip()
    cp = _extract_catchphrase(card)
    n = count if count is not None else 5
    variants: list[str] = []
    if kind == "rebuttal":
        if cp and name:
            variants.append(f"{cp}…都第{n}次了，你真的没事吗？")
        elif name:
            variants.append(f"都第{n}次说没事了，我是认真的哦")
        else:
            variants.append(f"都第{n}次了，真的没事吗？")
    elif kind == "empty_reply":
        if cp:
            variants.append(f"{cp}，刚才那条没看清，再说一次？")
        elif name:
            variants.append("嗯？刚才没接上，再说一句？")
    elif kind == "timeout":
        if cp:
            variants.append(f"{cp}，我刚有点卡，你再说一次？")
    elif kind == "exception" and name:
        variants.append("刚才好像乱了一下，再说一次好吗？")
    # 过滤括号动作（沉浸式硬约束）
    clean = []
    for v in variants:
        v = str(v).strip()
        if not v or "（" in v or "(" in v:
            continue
        clean.append(v)
    return clean


def _novel_variant(kind: str, count: int | None) -> str | None:
    """小说式允许极短旁白，但仍避免长动作描写。"""
    if kind != "rebuttal":
        return None
    n = count if count is not None else 5
    return f"（微微皱眉）都第{n}次说没事了……真的没事吗？"


def get_fallback_line(
    character_id: str | None = None,
    kind: str = "empty_reply",
    reply_mode: str | None = None,
    count: int | None = None,
) -> str:
    """按角色与回复模式取一句系统旁路句；当日同 kind 去重。"""
    if kind not in KINDS:
        kind = "empty_reply"

    with _lock:
        _reset_if_new_day()
        key = (str(character_id or ""), kind)
        used = _used.setdefault(key, set())

        candidates = _character_variants(character_id, kind, count)
        if reply_mode == REPLY_MODE_NOVEL:
            nv = _novel_variant(kind, count)
            if nv:
                candidates = [nv, *candidates]

        for pool_line in _POOLS[kind]:
            line = pool_line.format(count=count if count is not None else 5)
            candidates.append(line)

        # 沉浸式：剔除含括号的候选（池子本身无括号，角色变体已滤）
        if reply_mode != REPLY_MODE_NOVEL:
            candidates = [c for c in candidates if "（" not in c and "(" not in c]

        for c in candidates:
            if c and c not in used:
                used.add(c)
                return c

        # 池子用尽：允许重复第一句（保证非空，优先角色变体）
        fallback = candidates[0] if candidates else _POOLS["empty_reply"][0]
        return fallback


def all_pool_lines(count_values: range | list[int] | None = None) -> set[str]:
    """展开默认池全部文案（含 count 占位），供记忆管线识别系统占位。"""
    counts = list(count_values) if count_values is not None else list(range(1, 21))
    out: set[str] = set()
    for lines in _POOLS.values():
        for line in lines:
            if "{count}" in line:
                for c in counts:
                    out.add(line.format(count=c))
            else:
                out.add(line)
    # 历史罐头句（v1.21 及更早）
    out.update(
        {
            "抱歉，处理超时，请稍后重试",
            "（消息处理异常，请稍后重试）",
            "（处理消息时出现异常, 请稍后重试）",
        }
    )
    return out


# 预展开集合（模块加载一次）
FALLBACK_POOL_LINES: frozenset[str] = frozenset(all_pool_lines())

_SYSTEM_MARKERS = (
    "处理超时",
    "消息处理异常",
    "处理消息时出现异常",
    "请稍后重试",
)

# 短系统旁路特征（空回复/超时/异常兜底；不含反诘角色句）
_SYSTEMISH = (
    "再说一次",
    "再说一句",
    "再说一遍",
    "再发一次",
    "再发一遍",
    "再来一句",
    "没接上",
    "没收到",
    "没看清",
    "没接住",
    "反应慢了半拍",
    "走神了",
    "有点卡",
    "好像乱了一下",
    "好像出问题了",
    "好像没处理好",
)


def is_system_fallback_line(reply: str | None) -> bool:
    """是否为系统旁路占位（空/超时/异常兜底），不得写成 assistant 发言。

    注：rebuttal 池经 system 注入由模型转述，正常不会作为系统占位直接回给 after_chat；
    这里刻意不把含「没事」的反诘句当系统错误，避免误伤真实角色台词。
    """
    text = (reply or "").strip()
    if not text:
        return True
    if any(m in text for m in _SYSTEM_MARKERS):
        return True
    if text in FALLBACK_POOL_LINES:
        return True
    return len(text) <= 40 and "没事" not in text and any(s in text for s in _SYSTEMISH)


def reset_daily_dedup() -> None:
    """测试用：清空当日去重状态。"""
    global _day_key
    with _lock:
        _day_key = ""
        _used.clear()
        _card_cache.clear()


def inject_rebuttal_constraint(system_prompt: str, count: int) -> str:
    """把「连续否认 N 次」写入本轮 system 段（反诘不再硬编码 append）。

    生成前注入，由模型以角色口吻追问；不直接往 reply 尾部拼机器腔。
    """
    n = int(count) if count else 5
    block = (
        "\n\n# 情绪关切约束（本轮生效）\n"
        f"用户已经连续 {n} 次用「没事/还行」之类的敷衍词回避真实情绪。\n"
        "你可以在保持角色口吻的前提下，温柔但坚定地追问真实状态；"
        "不要使用客服腔，不要写死系统提示，不要输出括号动作。"
    )
    if not system_prompt:
        return block.strip()
    return f"{system_prompt}{block}"

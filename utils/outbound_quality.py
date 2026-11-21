"""出站回复质量守卫（2026-11-21 出站质量批）。

审计结论（代码实况复核后）：
- ``terminal_reply_guard`` 的真实 owner 是 :mod:`utils.prompt_sanitize`
  （单轮纯函数），不存在独立的 ``utils/terminal_reply_guard.py``；
- ``_finalize_reply`` 此前没有 assistant 历史入参，模型跨轮逐字重复 288 字
  （chat_history 5641/5717）两轮都能原样放行；
- 沉浸式动作段被剥空后只走 empty_reply 兜底，没有「只有动作没有台词」的
  最小重生成路径。

本模块只放**纯函数/单一职责**的质量信号与最小重生成，不接管编排：
- 闸一：与最近 N 条 assistant 历史的规范化相似度 + 句子级去重；
- 闸二：沉浸式动作-only 判定 + 同模型一次重试（失败回退安全句）；
- 闸三：模板句去重表 + 最小信息增量判据。

``terminal_reply_guard`` 与 ``IncrementalReplyGuard`` 都从这里取同一份规则，
避免批式/流式两套阈值漂移。
"""

from __future__ import annotations

import inspect
import logging
import re
from difflib import SequenceMatcher
from typing import Any

from utils.prompt_sanitize import SENTENCE_TERMINATORS, _is_extra_error_sentinel

logger = logging.getLogger("outbound_quality")

_CJK_ALNUM_RE = re.compile(r"[^\u4e00-\u9fffA-Za-z0-9]+")


def normalize_quality_text(text: Any) -> str:
    """质量判定用的归一化：只留汉字/字母/数字，去掉空白与标点。"""
    return _CJK_ALNUM_RE.sub("", str(text or "")).lower()


# ── 闸一：历史重复 ─────────────────────────────────────────
#: 只取最近 8 条 assistant 行（审计建议 N=8）；更早的重复不再追。
HISTORY_LIMIT = 8
#: 整段比较的最短规范化长度：短句不走相似度，避免「在呢」被误杀。
REPEAT_MIN_CHARS = 20
#: 字符 3-gram Jaccard 阈值（5641/5717 同文 = 1.0；正常不同长文远低于此）。
REPEAT_JACCARD_THRESHOLD = 0.72
#: SequenceMatcher 兜底阈值（处理语序微调但内容仍高度重复的形态）。
REPEAT_SEQUENCE_THRESHOLD = 0.86
#: 句级去重：只处理 >=10 字的长句，短句重复不判。
SENTENCE_DUP_MIN_CHARS = 10
SENTENCE_DUP_THRESHOLD = 0.92
#: 流式单片段：>=12 字且与历史句高度相似才丢，避免正常短句被吞。
FRAGMENT_REPEAT_MIN_CHARS = 12
FRAGMENT_REPEAT_THRESHOLD = 0.92

# ── 闸二：动作-only 最小重生成 ─────────────────────────────
SAFE_MINIMAL_REPLY = "嗯，我在呢。你接着说。"
ACTION_ONLY_RETRY_INSTRUCTION = (
    "刚才那条回复只有动作/神态描写，没有真正说出口的台词。"
    "请只补一句你真正要对用户说的话，不要写任何括号动作、神态或旁白。"
    "直接输出这句话。"
)

# ── 闸三：模板句去重 + 最小信息增量 ────────────────────────
#: 模板句真源（归一化后比较）。只收「放到任何语境都成立的空泛关心」；
#: 带具体人物/事件/情绪的句子不进表，避免把正常关心误判成模板。
_TEMPLATE_SENTENCES: tuple[str, ...] = tuple(
    normalize_quality_text(s)
    for s in (
        "把手机放下",
        "放下手机",
        "早点休息",
        "早点睡",
        "别熬夜",
        "不要熬夜",
        "好好休息",
        "记得吃饭",
        "按时吃饭",
        "多喝热水",
        "多喝点热水",
        "注意身体",
        "照顾好自己",
        "注意保暖",
        "多穿点",
        "别太累了",
        "别想太多",
        "不要想太多",
        "我在这里",
        "我一直都在",
        "有我呢",
        "一切都会好的",
        "会好起来的",
        "抱抱你",
        "么么哒",
    )
)
#: 裸确认词：单独出现没有信息增量；连续复读要拦。
_MINIMAL_ACKS: frozenset[str] = frozenset(
    {"嗯", "嗯嗯", "哦", "哦哦", "好", "好的", "行", "可以", "收到", "知道了", "在", "在的", "在呢"}
)
#: 情绪/状态词：出现任意一个即视为有情绪信息。
_EMOTION_WORDS: tuple[str, ...] = (
    "开心",
    "高兴",
    "难过",
    "伤心",
    "累",
    "困",
    "烦",
    "担心",
    "心疼",
    "生气",
    "委屈",
    "害怕",
    "紧张",
    "孤单",
    "难受",
    "放心",
    "安心",
    "想你",
    "喜欢",
    "舍不得",
    "舒服",
)
#: 计算用户原话内容二元组时剔除的高频虚词（避免「今天」「一个」这类噪声
#: 把任何回复都判成有话题推进）。
_STOP_CHARS = frozenset(
    "的了呢吗啊吧哦呀嘛是在有和就都也还又很太我你他她它这那一个不没要会能可以"
)

_SENTENCE_SPLIT_RE = re.compile("(?<=[" + re.escape(SENTENCE_TERMINATORS) + "])")
_VOCATIVE_RE = re.compile(r"^[\u4e00-\u9fff]{2,4}[，,、]")
_QUESTION_RE = re.compile(r"[？?]|(?:吗|呢|什么|怎么|为什么|哪|谁|多少)\s*[。！!]?\s*$")
_DIGIT_RE = re.compile(r"[0-9二三四五六七八九十百千万]")


def _split_sentences(text: Any) -> list[str]:
    """按与 ``prompt_sanitize.SENTENCE_TERMINATORS`` 同源的句界切分。"""
    return [s for s in _SENTENCE_SPLIT_RE.split(str(text or "")) if s.strip()]


def char_ngram_jaccard(a: str, b: str, n: int = 3) -> float:
    """字符 n-gram Jaccard（无第三方依赖）。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) < n or len(b) < n:
        return 0.0
    ga = {a[i : i + n] for i in range(len(a) - n + 1)}
    gb = {b[i : i + n] for i in range(len(b) - n + 1)}
    union = ga | gb
    if not union:
        return 0.0
    return len(ga & gb) / len(union)


def extract_recent_assistant_messages(rows: Any, limit: int = HISTORY_LIMIT) -> list[str]:
    """从 chat_history 行提取最近 N 条 assistant 正文（排除机器兜底句）。

    失败/非 list 一律返回空列表（调用方 fail-open）。
    """
    if not isinstance(rows, list):
        return []
    try:
        from utils.fallback_lines import is_machine_sentinel_line
    except Exception:  # noqa: BLE001 - 极端环境下退化为只按 role 过滤
        is_machine_sentinel_line = None  # type: ignore[assignment]
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "").strip().lower() != "assistant":
            continue
        text = str(row.get("content") or "").strip()
        if not text:
            continue
        if is_machine_sentinel_line is not None and is_machine_sentinel_line(text):
            continue
        out.append(text)
    try:
        want = max(1, int(limit))
    except Exception:  # noqa: BLE001
        want = HISTORY_LIMIT
    return out[-want:]


def is_repetitive_text(reply: Any, candidates: Any) -> bool:
    """闸一整段判定：回复是否与任一历史 assistant 回复高度重复。"""
    text = str(reply or "")
    norm = normalize_quality_text(text)
    if len(norm) < REPEAT_MIN_CHARS:
        return False
    if not isinstance(candidates, (list, tuple)):
        candidates = [candidates]
    for candidate in candidates:
        cand = str(candidate or "")
        cand_norm = normalize_quality_text(cand)
        if len(cand_norm) < REPEAT_MIN_CHARS:
            continue
        if norm == cand_norm:
            return True
        jaccard = char_ngram_jaccard(norm, cand_norm)
        if jaccard >= REPEAT_JACCARD_THRESHOLD:
            return True
        if SequenceMatcher(None, norm, cand_norm).ratio() >= REPEAT_SEQUENCE_THRESHOLD:
            return True
    return False


def _sentence_matches_any(sentence: str, candidate_sentences: list[str]) -> bool:
    norm = normalize_quality_text(sentence)
    if len(norm) < 4:
        return False
    for cand in candidate_sentences:
        cand_norm = normalize_quality_text(cand)
        if not cand_norm:
            continue
        # 短句只做全等（>=4 字），避免「嗯」这类裸确认词在历史长文里误命中。
        if norm == cand_norm:
            return True
        if len(norm) < SENTENCE_DUP_MIN_CHARS or len(cand_norm) < SENTENCE_DUP_MIN_CHARS:
            continue
        if char_ngram_jaccard(norm, cand_norm) >= SENTENCE_DUP_THRESHOLD:
            return True
        if SequenceMatcher(None, norm, cand_norm).ratio() >= SENTENCE_DUP_THRESHOLD:
            return True
    return False


def strip_repeated_reply(reply: Any, candidates: Any) -> str:
    """闸一改写：删掉与历史重复的长句；全重复返回空串由上层兜底。

    先做句级去重（保留新增句），再对整段做高相似判定；已发前缀不在本函数
    处理范围内（流式由 ``IncrementalReplyGuard`` 在句界前判定）。
    """
    text = str(reply or "")
    if not text or not candidates:
        return text
    if not isinstance(candidates, (list, tuple)):
        candidates = [candidates]
    cand_texts = [str(c or "").strip() for c in candidates if str(c or "").strip()]
    if not cand_texts:
        return text

    candidate_sentences: list[str] = []
    for cand in cand_texts:
        candidate_sentences.extend(_split_sentences(cand))

    kept: list[str] = []
    dropped = False
    for sentence in _split_sentences(text):
        if _sentence_matches_any(sentence, candidate_sentences):
            dropped = True
            continue
        kept.append(sentence)
    if dropped:
        out = "".join(kept).strip()
        if not out:
            return ""
        # 句级删完只剩极短残片、而整段仍与历史高度相似 → 视为全重复。
        if len(normalize_quality_text(out)) < 4 and is_repetitive_text(text, cand_texts):
            return ""
        return out
    if is_repetitive_text(text, cand_texts):
        return ""
    return text


def is_repeated_fragment(fragment: Any, candidates: Any) -> bool:
    """流式句界判定：该片段是否已在历史/已发前缀里出现过（首屏前可丢）。"""
    norm = normalize_quality_text(fragment)
    if len(norm) < 6:
        return False
    if not isinstance(candidates, (list, tuple)):
        candidates = [candidates]
    for candidate in candidates:
        cand = str(candidate or "")
        units = [cand, *_split_sentences(cand)]
        for unit in units:
            unit_norm = normalize_quality_text(unit)
            if len(unit_norm) < 6:
                continue
            # 短句只做全等（>=6 字）；相似度只对长句启用，避免正常短句被吞。
            if norm == unit_norm:
                return True
            if len(norm) < FRAGMENT_REPEAT_MIN_CHARS or len(unit_norm) < FRAGMENT_REPEAT_MIN_CHARS:
                continue
            if char_ngram_jaccard(norm, unit_norm) >= FRAGMENT_REPEAT_THRESHOLD:
                return True
            if SequenceMatcher(None, norm, unit_norm).ratio() >= FRAGMENT_REPEAT_THRESHOLD:
                return True
    return False


def _split_clauses(text: Any) -> list[str]:
    """按逗号/分号/句界切成最小短语（模板句常以「把手机放下，早点休息」连写）。"""
    return [c for c in re.split(r"[，,；;、。！？!?…\n]+", str(text or "")) if c.strip()]


def _is_template_clause(clause: Any) -> bool:
    norm = normalize_quality_text(clause)
    if not norm:
        return False
    if norm in _TEMPLATE_SENTENCES:
        return True
    for template in _TEMPLATE_SENTENCES:
        if template and template in norm:
            extra = norm.replace(template, "", 1)
            if len(extra) <= 2 and all(ch in "吧了呢啊哦呀嘛哈" for ch in extra):
                return True
    return False


def is_template_sentence(sentence: Any) -> bool:
    """单句是否属于模板句（允许逗号连写的多个模板短语）。"""
    clauses = _split_clauses(sentence)
    if not clauses:
        return False
    return all(_is_template_clause(clause) for clause in clauses)


def is_minimal_ack(sentence: Any) -> bool:
    """单句是否裸确认词（嗯/好的/收到…）。"""
    return normalize_quality_text(sentence) in _MINIMAL_ACKS


def is_template_only_reply(reply: Any) -> bool:
    """整条回复是否由模板句/裸确认词构成。"""
    sentences = _split_sentences(reply)
    if not sentences:
        return False
    return all(is_template_sentence(sentence) or is_minimal_ack(sentence) for sentence in sentences)


def _content_bigrams(text: Any) -> set[str]:
    """用户原话的内容二元组（剔除高频虚词，降低「今天」类噪声）。"""
    norm = normalize_quality_text(text)
    grams: set[str] = set()
    for i in range(len(norm) - 1):
        gram = norm[i : i + 2]
        if any(ch in _STOP_CHARS for ch in gram):
            continue
        grams.add(gram)
    return grams


def has_minimum_information(reply: Any, user_msg: Any = "") -> bool:
    """最小信息增量：情绪/称呼/话题推进/具体数字至少一项。"""
    text = str(reply or "")
    norm = normalize_quality_text(text)
    if not norm:
        return False
    if any(word in norm for word in _EMOTION_WORDS):
        return True
    if "你" in norm or "您" in norm:
        return True
    if _VOCATIVE_RE.match(text.strip()):
        return True
    if _QUESTION_RE.search(text):
        return True
    if _DIGIT_RE.search(norm):
        return True
    user_grams = _content_bigrams(user_msg)
    return bool(any(gram in norm for gram in user_grams))


def _norm_in_candidates(norm: str, candidate_norms: list[str]) -> bool:
    for cand in candidate_norms:
        if norm == cand:
            return True
        # 短确认词只做全等，禁止「嗯」在任意历史长文里命中。
        if len(norm) >= 4 and norm in cand:
            return True
    return False


def strip_repeated_templates(reply: Any, candidates: Any, *, user_msg: Any = "") -> str:
    """闸三：模板句去重 + 最小信息增量。

    - 模板句已在最近 assistant 历史里出现 → 删除该句；
    - 整条只剩低信息模板且历史里已有同类模板 → 返回空串（由上层兜底）；
    - 首次低信息多句模板 → 缩短为第一句（「缩短」而非整段照发）。
    """
    text = str(reply or "")
    if not text or not candidates:
        return text
    if not isinstance(candidates, (list, tuple)):
        candidates = [candidates]
    cand_texts = [str(c or "").strip() for c in candidates if str(c or "").strip()]
    if not cand_texts:
        return text
    candidate_norms = [normalize_quality_text(c) for c in cand_texts]

    kept: list[str] = []
    dropped = False
    for sentence in _split_sentences(text):
        norm = normalize_quality_text(sentence)
        if not norm:
            continue
        if (is_template_sentence(sentence) or is_minimal_ack(sentence)) and _norm_in_candidates(
            norm, candidate_norms
        ):
            dropped = True
            continue
        kept.append(sentence)
    if dropped:
        text = "".join(kept).strip()
        if not text:
            return ""

    if is_template_only_reply(text) and not has_minimum_information(text, user_msg):
        if any(
            is_template_only_reply(c) and not has_minimum_information(c, user_msg)
            for c in cand_texts
        ):
            return ""
        clauses = _split_clauses(text)
        if len(clauses) > 1:
            first = clauses[0].strip()
            if not first.endswith(("。", "！", "？", "!", "?", "…")):
                first += "。"
            return first
    return text


def strip_repeated_template_fragment(fragment: Any, candidates: Any) -> str:
    """流式句界：模板句/裸确认词已在历史出现 → 丢弃该片段。"""
    text = str(fragment or "")
    norm = normalize_quality_text(text)
    if not norm or not candidates:
        return text
    if not (is_template_sentence(text) or is_minimal_ack(text)):
        return text
    if not isinstance(candidates, (list, tuple)):
        candidates = [candidates]
    candidate_norms = [normalize_quality_text(c) for c in candidates if str(c or "").strip()]
    if _norm_in_candidates(norm, candidate_norms):
        return ""
    return text


def is_action_only_reply(reply: Any, reply_mode: Any) -> bool:
    """沉浸式下「只有括号/星号动作、没有台词」的回复。

    novel 模式动作是合法格式，永远返回 False（不得用沉浸式闸杀）。
    """
    if str(reply_mode or "").strip().lower() != "immersive":
        return False
    raw = str(reply or "").strip()
    if not raw:
        return False
    # 错误哨兵可能整段被圆括号包裹（如「（所有 LLM 提供商均不可用）」），
    # 剥动作后会误判成「无台词」。这类文本必须走原有错误分支，不触发生成。
    if _is_extra_error_sentinel(raw):
        return False
    if any(marker in raw for marker in ("处理消息", "处理超时", "请稍后重试", "未生成角色回复")):
        return False
    try:
        from llm_provider.multi_provider_gateway import _is_error_reply

        if _is_error_reply(raw):
            return False
    except Exception:  # noqa: BLE001 - 网关导入失败时按动作-only 判定，不影响主链
        pass
    from utils.reply_mode import strip_stage_directions

    stripped = strip_stage_directions(raw)
    return not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", stripped)


async def regenerate_action_only_reply(
    llm: Any,
    *,
    previous_reply: Any,
    user_msg: Any,
    system_prompt: str = "",
    history: Any = None,
    reply_mode: Any = "immersive",
    max_tokens: int = 256,
) -> str:
    """闸二：动作-only 时用**同一条模型链**补一次台词；失败回退安全句。

    只做一次（``max_retries=1`` 的语义）。``llm`` 必须是当前请求的 gateway
    （``request_llm``），不得换成新的全局实例——否则角色/供应商上下文漂移。
    """
    if not is_action_only_reply(previous_reply, reply_mode):
        return str(previous_reply or "")
    chat = getattr(llm, "chat", None)
    if not callable(chat):
        logger.warning("[quality][step=action_only_no_llm] 网关不可用，回退安全句")
        return SAFE_MINIMAL_REPLY
    query = (
        f"{ACTION_ONLY_RETRY_INSTRUCTION}\n\n"
        f"用户刚才说：{str(user_msg or '').strip() or '（无）'}\n"
        "只输出你真正要说的一句话。"
    )
    try:
        result = chat(
            query=query,
            system_prompt=str(system_prompt or ""),
            history=list(history or []),
            temperature=0.7,
            max_tokens=max_tokens,
        )
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:  # noqa: BLE001 - 重生成失败必须安全回退
        logger.warning("[quality][step=action_only_retry_failed] %s", exc)
        return SAFE_MINIMAL_REPLY
    text = str(result or "").strip()
    if not text or is_action_only_reply(text, reply_mode):
        logger.info("[quality][step=action_only_retry_empty] 重试仍无台词，回退安全句")
        return SAFE_MINIMAL_REPLY
    return text

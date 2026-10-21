"""增量出站守卫（R4 主链真流式，2026-10-21）。

**问题**：旧实现「上游流式攒稿 → 完整 ``_finalize_reply`` → 按 8 字符切块」，
首字节延迟 = 整轮生成长度。要提前首字节，只能在句子边界上边生成边下发；但
``terminal_reply_guard`` 的规则分三类，**只有第一类可以在句界判定**：

1. **可局部判定**（句内自足）：系统标记剥除、沉浸式动作段剥除、相对时间词降级、
   元记忆解释/零信息问卷（句级删）、归属翻转（句级删）、称呼剥除、行首表情复读、
   逐字复述（首行）。
2. **命中即截断其后**：无由头反问 ``你呢…？``——批式语义是 ``s[:m.start()]``
   （命中句及其**后文**全删）。逐句放行时「后文」尚未生成，等价实现只能是
   **命中即终止上游 + 丢弃全部未发**（``stop_requested``）。
3. **需要全文/全文位置**：错误哨兵（整串括号包裹）、仅剩角色名（整串等值）、
   首行复述的行边界、行首系统标记的 ``re.MULTILINE`` 锚、称呼剥除的
   ``len<2 → ""`` 兜底。→ 只能延迟到**流末终裁**。

因此本模块**不声称「守卫等效」**，而是显式登记取舍（设计复核 2026-10-21）：

- 已发前缀 ⇒ 必已通过第 1 类规则与第 2 类（截断）判定；终裁只处理**未发余量**
  （``finish()`` 的返回值），已发内容永不回滚、永不重判。
- 第 3 类规则在真流式下对已发前缀**不再复核**（降级项 A）。为把代价压到最低，
  状态机额外做了两件事：① 整串形态可能退化的首句（角色名/哨兵）**扣住不发**，
  等后续内容证明它不退化、或流末交给终裁判死（降级项 B）；
  ② 逻辑行首状态跨片段传递，使系统标记与发言标签只在真正的行首生效（等价于
  整段 ``re.MULTILINE`` 语义）。
- 已知两处「逐句 vs 整段」判定面差异（复核实测）按**更安全方向**钉死并在此登记：
  ① ``strip_forbidden_vocatives`` 的 ``len<2 → ""`` 只对**整串**生效，逐句不做
  （否则「嗯」「好」这类单字行整行消失——复核实测 ``'嗯'`` → ``''``）；
  ② ``sanitize_reply_text`` 的发言标签/时间前缀/``len<2`` 语义按**逻辑行**判定，
  与整段的 ``splitlines()`` 同义。

**与降级/超时/断线的配合**：上游中断按**流末**处理（``finish()`` 补发已收到内容），
不重试、不换供应商重生成；适配器断开由 ``_ack``/``accepted_end`` 原语义兜底
（只落已确认前缀）。

**为什么放在 ``utils/``**：AGENTS §2 把 ``utils/`` 划给「工具与脚本」owner，
本模块是被 ``orchestrator/_stream_mixin.py`` 与 ``utils/prompt_sanitize.py``
共用的规则执行器（不是编排逻辑），与 ``utils/prompt_sanitize.py`` 同居一层。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from utils.project_paths import project_path
from utils.prompt_sanitize import (
    _ATTRIBUTION_FLIP_RE,
    _META_MEMORY_RE,
    _RECIPROCAL_QUESTION_RE,
    _SURVEY_QUESTION_RE,
    _TIME_WORD_AGE_THRESHOLD_SECONDS,
    SENTENCE_TERMINATORS,
    _guard_log,
    _history_gap_note_re,
    _recalibrate_time_words,
    _strip_attribution_flip,
    _strip_immersive_stage_directions,
    _strip_meta_memory_talk,
    _strip_system_markers,
    _strip_verbatim_echo,
    strip_emoji_echo,
    strip_format_noise,
    strip_time_prefixes,
)

logger = logging.getLogger("stream_guard")

#: 软句界：一逗到底的长句若一直等不到硬句界，首字节就退回「整轮生成」。
#: 达到该字符数后在最近的逗号/分号处放行；含可删句规则触发词时**不放行**
#: （否则半句已经出去，句子级删除失效）。
DEFAULT_SOFT_FLUSH_CHARS = 24
_SOFT_BOUNDARY_CHARS = "，,；;：:、 \t"

#: 逻辑行首才生效的发言标签（``sanitize_reply_text`` 的 own/other 两组）。
_OTHER_LABEL_RE = re.compile(r"^(?:用户|User|Human|对方)\s*[:：]", re.IGNORECASE)
_OWN_LABELS = ("助手", "Assistant", "AI", "角色", "我")


def _own_label_re(character_name: str = "") -> re.Pattern[str]:
    labels = list(_OWN_LABELS)
    if character_name:
        labels.append(character_name)
    return re.compile(
        r"^(?:" + "|".join(re.escape(x) for x in labels) + r")\s*[:：]\s*",
        re.IGNORECASE,
    )


# ── 配置开关（chat.stream_mode）────────────────────────────────────
#
# 与 ``llm.stream_enabled``（供应商层是否用 SSE 拉取）正交：本开关决定**编排层**
# 是「边生成边下发」还是「攒稿后下发」。``observability.config_models.SystemConfig``
# 没有 ``chat`` 字段（本批 files 不含该文件），故只能读 ``get_config_dict()``
# 的原始/生效字典（含 schema 扩展键，见 ``config_manager.get_config_dict`` 注释），
# 或在无 config 组件时直读 ``config/system.yaml``。两条路径都不依赖 SystemConfig。

STREAM_MODE_TRUE = "true"
STREAM_MODE_BUFFERED = "buffered"
STREAM_MODES = (STREAM_MODE_TRUE, STREAM_MODE_BUFFERED)
DEFAULT_STREAM_MODE = STREAM_MODE_TRUE

_CONFIG_PATH: Path | None = project_path("config", "system.yaml")


def _normalize_mode(value: Any) -> str | None:
    if isinstance(value, bool):
        return STREAM_MODE_TRUE if value else STREAM_MODE_BUFFERED
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in ("1", "yes", "on", "stream", "realtime"):
        return STREAM_MODE_TRUE
    if text in ("0", "no", "off", "buffer", "buffered"):
        return STREAM_MODE_BUFFERED
    return text if text in STREAM_MODES else None


def _mode_from_config_manager(config_manager: Any) -> str | None:
    getter = getattr(config_manager, "get_config_dict", None)
    if not callable(getter):
        return None
    try:
        data = getter(resolve_env=False) or {}
    except TypeError:
        try:
            data = getter() or {}
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
    chat = data.get("chat") if isinstance(data, dict) else None
    if isinstance(chat, dict):
        return _normalize_mode(chat.get("stream_mode"))
    return None


def _mode_from_yaml(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return None
    chat = raw.get("chat") if isinstance(raw, dict) else None
    if isinstance(chat, dict):
        return _normalize_mode(chat.get("stream_mode"))
    return None


def read_stream_mode(config_manager: Any = None) -> str:
    """按请求读取 ``chat.stream_mode``；缺失/非法一律回落默认（真流式）。"""
    mode = _mode_from_config_manager(config_manager)
    if mode is None:
        mode = _mode_from_yaml(_CONFIG_PATH)
    return mode or DEFAULT_STREAM_MODE


def true_streaming_enabled(config_manager: Any = None) -> bool:
    return read_stream_mode(config_manager) == STREAM_MODE_TRUE


# ── 状态机 ────────────────────────────────────────────────────


def _degenerate_name_forms(character_name: str) -> tuple[str, ...]:
    name = str(character_name or "").strip()
    if not name:
        return ()
    return (name, name + "。", name + "！", name + "!")


def _split_fragments(text: str) -> tuple[list[str], bool]:
    """按硬句界切分；返回 ``(片段列表, 末段是否已完整)``。

    **括号/星号未闭合时不切**：动作段可跨句号（``（想了想。还在想）``），提前在
    句号处切开会让 ``strip_stage_directions`` 只看到半截括号而漏剥或误剥。
    """
    out: list[str] = []
    start = 0
    depth = 0
    star_open = False
    for i, ch in enumerate(text):
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        elif ch == "*":
            star_open = not star_open
        if ch in SENTENCE_TERMINATORS and depth == 0 and not star_open:
            # 纯换行单独成段：它是结构性行边界（行首规则依赖），必须原样保留，
            # 且不能被后续 ``_strip_*`` 的 ``.strip()`` 吃掉。
            out.append(text[start:i + 1])
            start = i + 1
    closed = start >= len(text)
    if not closed:
        out.append(text[start:])
    return out, closed


class IncrementalReplyGuard:
    """按句界放行的出站守卫状态机（只判定**未发余量**，永不改已发文本）。

    ``feed()`` 吞上游 token、返回可立即下发的片段；``finish()`` 返回流末仍未
    下发的余量（字符串），由调用方送 ``_finalize_reply`` 做全文终裁。
    """

    def __init__(
        self,
        *,
        user_msg: Any = "",
        character_name: str = "",
        reply_mode: str = "",
        last_turn_age_seconds: float | None = None,
        legal: str = "你",
        forbidden: frozenset[str] | set[str] = frozenset(),
        safety_check: Callable[[str], str | None] | None = None,
        soft_flush_chars: int = DEFAULT_SOFT_FLUSH_CHARS,
    ) -> None:
        self.user_msg = str(user_msg or "")
        self.character_name = str(character_name or "").strip()
        self.reply_mode = str(reply_mode or "")
        self.last_turn_age_seconds = last_turn_age_seconds
        self.legal = str(legal or "你")
        self.forbidden = frozenset(t for t in (forbidden or ()) if t)
        self._safety_check = safety_check
        self._soft_flush_chars = max(1, int(soft_flush_chars))

        self._pending = ""      # 最后一个句界之后的不完整尾巴
        self._held = ""         # 已过增量守卫、但因形态可疑被扣住不发
        self._emitted = ""      # 已放行正文（左邻上下文 / 首行 / 整串形态判定）
        self._received = False
        self._at_line_start = True
        self._first_line_open = True
        self._own_label = _own_label_re(self.character_name)
        self._time_gate = (
            last_turn_age_seconds is not None
            and float(last_turn_age_seconds) > _TIME_WORD_AGE_THRESHOLD_SECONDS
        )
        self.stop_requested = False
        self.safety_replacement: str | None = None

    # ── 对外状态 ──
    @property
    def emitted_text(self) -> str:
        return self._emitted

    def has_received(self) -> bool:
        """是否收到过任何上游内容（区分「首 token 前失败」与「已发后中断」）。"""
        return self._received or bool(self._pending) or bool(self._held)

    # ── 输入 ──
    def feed(self, chunk: str) -> list[str]:
        text = str(chunk or "")
        if not text or self.stop_requested:
            return []
        self._received = True
        self._pending += text
        released: list[str] = []

        while True:
            pieces, closed = _split_fragments(self._pending)
            # ``closed`` 为假时最后一段是不完整尾巴（或未闭合括号段），留在 pending
            complete = pieces if closed else pieces[:-1]
            if not complete:
                break
            self._pending = "" if closed else pieces[-1]
            for fragment in complete:
                released.extend(self._process(fragment))
                if self.stop_requested:
                    self._pending = ""
                    return released
            if closed:
                break

        soft = self._soft_flush()
        if soft:
            released.extend(self._process(soft))
        return released

    def finish(self) -> str:
        """流末：返回仍需全文终裁的未发余量（不改已发内容）。"""
        if self.stop_requested:
            self._pending = ""
            self._held = ""
            return ""
        remainder = self._held + self._pending
        self._held = ""
        self._pending = ""
        return remainder

    # ── 内部：软边界 ──
    def _soft_flush(self) -> str:
        pending = self._pending
        if len(pending) < self._soft_flush_chars:
            return ""
        if self._unsafe_to_split(pending):
            return ""
        cut = -1
        for idx in range(min(len(pending), self._soft_flush_chars * 2) - 1, 0, -1):
            if pending[idx] in _SOFT_BOUNDARY_CHARS:
                cut = idx
                break
        if cut <= 0:
            return ""
        fragment = pending[: cut + 1]
        # 软边界放行后，剩余 pending 必须仍能表达「自己在流首」——否则下一段
        # 会被当成整串开头重跑退化/复述/表情判定（错位）。放行即标记已发。
        self._pending = pending[cut + 1:]
        return fragment

    def _unsafe_to_split(self, text: str) -> bool:
        """软边界放行的安全闸（放出去就收不回来）。"""
        from llm_provider.multi_provider_gateway import _is_error_reply
        from utils.prompt_sanitize import _is_extra_error_sentinel

        if _is_extra_error_sentinel(text) or _is_error_reply(text):
            return True
        # 括号/星号未闭合：错误哨兵（「（xxx 未配置 API Key…）」）与动作段都以
        # 括号包裹，在逗号处切开等于把包裹壳拆散，句子级/整串判定全部失效。
        if text.count("（") != text.count("）") or text.count("(") != text.count(")"):
            return True
        if (
            _META_MEMORY_RE.search(text)
            or _SURVEY_QUESTION_RE.search(text)
            or _RECIPROCAL_QUESTION_RE.search(text)
            or _ATTRIBUTION_FLIP_RE.search(text)
        ):
            return True
        if text.count("**") % 2 == 1:
            return True
        from my_character.consistency_checker import _HARD_AI_MARKERS

        if any(marker in text for marker in _HARD_AI_MARKERS):
            return True
        if not self._emitted:
            # 首句可能是复述用户原话的前缀：等句子完整再判（逐字复述只看首行）。
            from utils.prompt_sanitize import _normalize_for_echo

            cur = _normalize_for_echo(text)
            user = _normalize_for_echo(self.user_msg)
            if cur and user and (user.startswith(cur) or cur.startswith(user)):
                return True
        return False

    # ── 内部：单片段规则（顺序与 terminal_reply_guard + _finalize_reply 同源）──
    def _process(self, fragment: str) -> list[str]:
        if not fragment:
            return []
        at_line_start = self._at_line_start
        self._advance_line_state(fragment)
        # 各 ``_strip_*`` 真源都在整串上工作并 ``.strip()``；增量下片段被单独
        # 处理，**行尾换行是结构性信息**（行首规则与落库文本都依赖它），必须
        # 原样补回，否则「我吃过了\n你也记得吃饭」会被粘成一行。
        tail_nl = "\n" if fragment.endswith("\n") else ""
        # 纯空白/换行片段：批式语义是「整串首尾 strip、中间保留」。增量下它单独
        # 成段，必须原样放行（否则行边界消失），首部空白仍丢弃。
        if not fragment.strip():
            if self._held:
                self._held += "\n"
                return []
            if not self._emitted:
                return []
            self._emitted += "\n"
            return ["\n"]
        # ① 逻辑行首：时间前缀 / 发言标签（sanitize_reply_text 的行级语义）
        s = fragment
        if at_line_start:
            s = strip_time_prefixes(s)
            if _OTHER_LABEL_RE.match(s):
                _guard_log("speaker_label", fragment, "")
                return []
            while True:
                stripped = self._own_label.sub("", s, count=1)
                if stripped == s:
                    break
                s = stripped
            if not s.strip():
                _guard_log("speaker_label", fragment, "")
                return []
        # ② 系统标记（行首才剥；行尾间隔注记始终剥）
        before = s
        # 逐句判定面差异登记（设计复核 2026-10-21）：整段时行中标记保留；逐句时
        # 若不加行首守卫，`re.MULTILINE` 的 `^` 会命中句首把正常内容当标记剥掉。
        s = (
            _strip_system_markers(s)
            if at_line_start
            else _history_gap_note_re().sub("", s)
        )
        _guard_log("system_marker", before, s)
        if not s.strip():
            return []
        # ③ 沉浸式动作段（小说模式保留）
        before = s
        s = _strip_immersive_stage_directions(s, self.reply_mode)
        _guard_log("immersive_stage_direction", before, s)
        if not s.strip():
            return []
        # ③b 格式噪声（markdown 粗体/分号残留；与 sanitize_reply_text 同源）
        before = s
        s = strip_format_noise(s)
        _guard_log("format_noise", before, s)
        if not s.strip():
            return []
        # ④ 相对时间词（带左邻上下文，兼容「好/巧/强/毅」负向断言）
        if self._time_gate:
            s = self._recalibrate_with_left_context(s)
        # ⑤ 首行逐字复述
        if self._first_line_open:
            before = s
            s = _strip_verbatim_echo(s, self.user_msg)
            _guard_log("verbatim_echo", before, s)
            if not s.strip():
                return []
        # ⑥ 元记忆解释 / 零信息问卷（句级删）
        before = s
        s = _strip_meta_memory_talk(s)
        _guard_log("meta_memory_talk", before, s)
        if not s.strip():
            return []
        # ⑦ 归属翻转（句级删）
        before = s
        s = _strip_attribution_flip(s)
        _guard_log("attribution_flip", before, s)
        if not s.strip():
            return []
        # ⑧ 无由头反问：命中 → 截断 + 终止上游
        s, stopped = self._truncate_reciprocal(s)
        if stopped:
            self.stop_requested = True
        if not s.strip():
            return []
        # ⑨ 行首表情复读（仅整段开头）
        if not self._emitted:
            before = s
            s = strip_emoji_echo(s, self.user_msg)
            _guard_log("emoji_echo", before, s)
            if not s.strip():
                return []
        # ⑩ 退化形态扣留（仅整段开头；有后续内容即解除）
        if not self._emitted:
            forms = _degenerate_name_forms(self.character_name)
            if forms and s in forms:
                self._held += s + tail_nl
                return []
        # ⑪ 自称 AI（句级删除，与 light_sanitize_hard_violation 同源）
        s = self._strip_self_as_ai(s)
        if not s.strip():
            return []
        # ⑫ 称呼剥除（词边界；``len<2 → ""`` 只对整串生效，见 ``_strip_vocatives``）
        before = s
        s = self._strip_vocatives(s)
        _guard_log("forbidden_vocative", before, s)
        if not s.strip():
            return []
        # ⑬ 输出安全闸（命中即终止后续；已发前缀不可回滚）
        if self._safety_check is not None:
            replacement = self._safety_check(s)
            if replacement is not None:
                self.safety_replacement = replacement
                self.stop_requested = True
                return []
        # ⑭ 错误哨兵（整串括号包裹形态）。批式只在整串上判；真流式下首句若已是
        # 完整哨兵就必须扣住——放出去就成了「角色说：未配置 API Key」。
        from llm_provider.multi_provider_gateway import _is_error_reply
        from utils.prompt_sanitize import _is_extra_error_sentinel

        if not self._emitted and (_is_error_reply(s) or _is_extra_error_sentinel(s)):
            self._held += s + tail_nl
            return []

        if self._held:
            s = self._held + s
            self._held = ""
        if tail_nl and not s.endswith("\n"):
            s += tail_nl
        self._emitted += s
        return [s]

    # ── 内部辅助 ──
    def _advance_line_state(self, text: str) -> None:
        if text:
            self._at_line_start = text.endswith("\n")
            if "\n" in text:
                self._first_line_open = False

    def _recalibrate_with_left_context(self, text: str) -> str:
        """时间词降级带左邻上下文：``_recalibrate_time_words`` 幂等，故用
        「已发文本 + 本片段」整体改写后取尾段，保证 ``(?<![好巧强毅])刚`` 的
        左邻字符不因句界而丢失。"""
        prefix = self._emitted
        if not prefix:
            return _recalibrate_time_words(text)
        combined = _recalibrate_time_words(prefix + text)
        if combined.startswith(prefix):
            return combined[len(prefix):]
        return _recalibrate_time_words(text)

    def _truncate_reciprocal(self, text: str) -> tuple[str, bool]:
        """无由头反问：返回 (保留文本, 是否终止上游)。

        语义对齐批式 ``_strip_ungrounded_reciprocal``：用户本轮未提问才生效；
        裸「你呢」整行只删该行（不截断）；带问号形态连其后文一起丢。
        逐句实现下「其后文」尚未生成 → 只能终止上游。
        """
        user = self.user_msg.strip()
        if not text or not user:
            return text, False
        from utils.prompt_sanitize import _USER_QUESTION_RE

        if _USER_QUESTION_RE.search(user):
            return text, False
        m = _RECIPROCAL_QUESTION_RE.search(text)
        if not m:
            return text, False
        hit = text[m.start():m.end()].strip()
        if hit in {"你呢", "那呢", "可是你呢", "不过你呢"}:
            kept = [
                line
                for line in text.splitlines()
                if _RECIPROCAL_QUESTION_RE.fullmatch(line.strip() or "x") is None
            ]
            return "\n".join(kept).strip(), False
        head = text[: m.start()].rstrip(" \t，,；;、")
        _guard_log("ungrounded_reciprocal", text, head)
        return head.strip(), True

    def _strip_self_as_ai(self, text: str) -> str:
        try:
            from my_character.consistency_checker import (
                _HARD_AI_MARKERS,
                light_sanitize_hard_violation,
            )
        except Exception:  # noqa: BLE001
            return text
        if not any(marker in text for marker in _HARD_AI_MARKERS):
            return text
        cleaned = light_sanitize_hard_violation(text)
        _guard_log("self_as_ai", text, cleaned)
        return cleaned

    def _strip_vocatives(self, text: str) -> str:
        """逐句称呼剥除：复用 ``strip_forbidden_vocatives`` 同一真源。

        与整段调用的唯一差异是 ``len<2 → ""`` 的兜底**只对整串生效**（复核实测
        ``strip_forbidden_vocatives('嗯', ...) == ''``；逐句套用会吞掉单字行）。
        """
        if not self.forbidden:
            return text
        if not any(tok in text for tok in self.forbidden):
            return text
        from utils.address_terms import strip_forbidden_vocatives

        stripped = strip_forbidden_vocatives(text, self.legal, self.forbidden)
        if not stripped and len(text.strip()) < 2:
            # 单字行本身可能就是被否认的称呼：只在该情形下真删。
            return "" if text.strip() in self.forbidden else text
        return stripped or text


def build_safety_check(safety: Any) -> Callable[[str], str | None] | None:
    """把 ``ContentSafetyFilter`` 包成 ``text -> replacement|None``（供状态机用）。"""
    if safety is None:
        return None

    def _check(text: str) -> str | None:
        try:
            result = safety.check_output(text)
        except Exception:  # noqa: BLE001
            return None
        if getattr(result, "is_safe", True):
            return None
        try:
            return safety.safe_alternative(result.category)
        except Exception:  # noqa: BLE001
            return None

    return _check


__all__ = [
    "DEFAULT_SOFT_FLUSH_CHARS",
    "DEFAULT_STREAM_MODE",
    "IncrementalReplyGuard",
    "STREAM_MODES",
    "STREAM_MODE_BUFFERED",
    "STREAM_MODE_TRUE",
    "build_safety_check",
    "read_stream_mode",
    "true_streaming_enabled",
]

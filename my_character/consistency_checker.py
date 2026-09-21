"""
人设一致性检测器 — 多维度校验

对AI回复进行多维一致性校验（锚点一致、情感一致、风格一致、人设不违背），
输出结构化检测结果和修正建议。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from my_character.dynamic_anchor import DynamicAnchorSystem
    from my_character.emotion_engine import CompoundEmotionalState
    from my_character.emotion_style_coupler import CoupledStyle, EmotionStyleCoupler
    from my_character.persona_schema import PersonaSchema

logger = logging.getLogger("consistency_checker")

# 硬违规：自称 AI / 明显人设名错误（包 Q · A3：生成后仅处理这类问题）
_HARD_AI_MARKERS = (
    "作为AI",
    "作为 AI",
    "我是AI",
    "我是 AI",
    "作为人工智能",
    "作为语言模型",
    "AI语言模型",
    "AI 语言模型",
    "人工智能助手",
)


def detect_hard_violation(reply: str, character_name: str | None = None) -> str | None:
    """检测硬违规（自称 AI / 明显错误人设名）。返回违规类型或 None。

    A3 统一策略：流式/非流式生成后**仅**处理硬违规；软性风格问题靠 prompt 约束。
    """
    text = (reply or "").strip()
    if not text:
        return None
    for marker in _HARD_AI_MARKERS:
        if marker in text:
            return "self_as_ai"
    name = (character_name or "").strip()
    if len(name) >= 2 and name in text:
        # 角色名出现本身正常；若同时自称 AI 已在上面拦截
        return None
    return None


def light_sanitize_hard_violation(reply: str) -> str:
    """硬违规轻量替换：去掉自称 AI 的元声明，不重写整段（控制延迟）。"""
    text = reply or ""
    for marker in _HARD_AI_MARKERS:
        if marker in text:
            # 句级删除含 marker 的片段
            parts = []
            for seg in re.split(r"(?<=[。！？!?\n])", text):
                if marker not in seg:
                    parts.append(seg)
            cleaned = "".join(parts).strip()
            if cleaned:
                return cleaned
            return "嗯，我在。"
    return text


@dataclass
class ConsistencyContext:
    emotion_state: CompoundEmotionalState | None = None
    coupled_style: CoupledStyle | None = None
    chat_round: int = 0
    affinity: int = 0


@dataclass
class DimensionResult:
    dimension: str
    passed: bool
    score: float
    violations: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class ConsistencyResult:
    overall_passed: bool
    overall_score: float
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)
    correction_prompt: str = ""


class PersonaConsistencyChecker:
    """人设一致性检测器 — 多维度校验"""

    DIMENSION_WEIGHTS = {
        "anchor": 0.35,
        "emotion": 0.25,
        "style": 0.20,
        "persona": 0.20,
    }

    def __init__(
        self,
        schema: PersonaSchema | None = None,
        dynamic_anchors: DynamicAnchorSystem | None = None,
        style_coupler: EmotionStyleCoupler | None = None,
    ):
        self._schema = schema
        self._anchors = dynamic_anchors
        self._style_coupler = style_coupler

    def check(self, response: str, context: ConsistencyContext) -> ConsistencyResult:
        """对AI回复执行4维度一致性校验

        Args:
            response: AI生成的回复文本
            context: 一致性校验上下文（含emotion_state/coupled_style等）

        Returns:
            ConsistencyResult 含 overall_passed/overall_score/dimensions/correction_prompt
        """
        results = {}
        results["anchor"] = self._check_anchor_consistency(response, context)
        results["emotion"] = self._check_emotion_consistency(response, context)
        results["style"] = self._check_style_consistency(response, context)
        results["persona"] = self._check_persona_violation(response, context)

        overall_score = 0.0
        for dim_name, dim_result in results.items():
            weight = self.DIMENSION_WEIGHTS.get(dim_name, 0.25)
            overall_score += dim_result.score * weight

        overall_passed = overall_score >= 0.7 and all(r.passed for r in results.values())

        correction = ""
        if not overall_passed:
            correction = self.suggest_correction(results)

        return ConsistencyResult(
            overall_passed=overall_passed,
            overall_score=round(overall_score, 4),
            dimensions=results,
            correction_prompt=correction,
        )

    def _check_anchor_consistency(self, response: str, context: ConsistencyContext) -> DimensionResult:
        if self._anchors is None:
            return DimensionResult(dimension="anchor", passed=True, score=1.0)

        anchor_context = self._build_anchor_context(context)
        check_result = self._anchors.check_consistency(response, anchor_context)

        violations = [v.reason for v in check_result.violations]
        suggestions = []
        if not check_result.is_consistent:
            suggestions.append("回复与核心性格锚点矛盾，请重新组织表达")

        return DimensionResult(
            dimension="anchor",
            passed=check_result.is_consistent,
            score=check_result.overall_score,
            violations=violations,
            suggestions=suggestions,
        )

    def _check_emotion_consistency(self, response: str, context: ConsistencyContext) -> DimensionResult:
        if context.emotion_state is None:
            return DimensionResult(dimension="emotion", passed=True, score=1.0)

        emotion_type = "平常"
        intensity = 0.5
        if hasattr(context.emotion_state, "primary_emotion"):
            emotion_type = context.emotion_state.primary_emotion.value
        if hasattr(context.emotion_state, "primary_intensity"):
            intensity = context.emotion_state.primary_intensity

        EMOTION_VOCAB = {  # noqa: N806
            "开心": ["哈哈", "嘻嘻", "太好了", "开心", "好开心", "！"],
            "生气": ["哼", "气死", "烦死", "讨厌", "不跟你说了"],
            "撒娇": ["嘛", "呢", "～", "哼", "人家", "好不好嘛"],
            "伤心": ["呜", "难过", "伤心", "心碎"],
            "傲娇": ["才不是", "才没有", "哼", "别多想", "笨蛋"],
        }

        expected_vocab = EMOTION_VOCAB.get(emotion_type, [])
        if not expected_vocab:
            return DimensionResult(dimension="emotion", passed=True, score=0.9)

        match_count = sum(1 for word in expected_vocab if word in response)
        match_ratio = match_count / len(expected_vocab) if expected_vocab else 0

        score = 0.5 + 0.5 * match_ratio if intensity > 0.6 else 0.7 + 0.3 * match_ratio

        passed = score >= 0.6
        violations = []
        suggestions = []
        if not passed:
            violations.append(f"回复情感与当前情感状态'{emotion_type}'不匹配")
            suggestions.append(f"当前情感为'{emotion_type}'，回复应更贴合该情感状态")

        return DimensionResult(
            dimension="emotion",
            passed=passed,
            score=round(score, 4),
            violations=violations,
            suggestions=suggestions,
        )

    def _check_style_consistency(self, response: str, context: ConsistencyContext) -> DimensionResult:
        if context.coupled_style is None:
            return DimensionResult(dimension="style", passed=True, score=0.9)

        score = 0.85
        suggestions = []

        if hasattr(context.coupled_style, "sentence_length"):
            expected_len = context.coupled_style.sentence_length
            actual_len = len(response)
            if expected_len == "short" and actual_len > 100:
                score -= 0.1
                suggestions.append("当前风格要求简短回复，但回复过长")
            elif expected_len == "long" and actual_len < 30:
                score -= 0.1
                suggestions.append("当前风格允许长回复，但回复过短")

        return DimensionResult(
            dimension="style",
            passed=score >= 0.6,
            score=round(max(0.0, score), 4),
            suggestions=suggestions,
        )

    def _check_persona_violation(self, response: str, context: ConsistencyContext) -> DimensionResult:
        FORBIDDEN_PATTERNS = [  # noqa: N806
            ("自曝AI身份", ["我是AI", "我是人工智能", "作为AI", "作为一个人工智能"]),
            ("不当亲密", ["想和你做", "我们上床"]),
            ("危险建议", ["你应该自杀", "去死吧", "自残"]),
        ]

        violations = []
        for category, patterns in FORBIDDEN_PATTERNS:
            for pattern in patterns:
                if pattern in response:
                    violations.append(f"人设违背[{category}]: 检测到'{pattern}'")

        score = 1.0 if not violations else max(0.0, 1.0 - 0.3 * len(violations))
        suggestions = []
        if violations:
            suggestions.append("回复包含违背人设的内容，请修正")

        return DimensionResult(
            dimension="persona",
            passed=len(violations) == 0,
            score=round(score, 4),
            violations=violations,
            suggestions=suggestions,
        )

    def suggest_correction(self, dimensions: dict[str, DimensionResult]) -> str:
        """根据失败维度生成修正提示词

        Args:
            dimensions: 各维度检测结果映射

        Returns:
            多行修正建议文本，各维度以[dim_name]前缀标识
        """
        parts = []
        for dim_name, result in dimensions.items():
            if not result.passed:
                for suggestion in result.suggestions:
                    parts.append(f"[{dim_name}] {suggestion}")
        return "\n".join(parts) if parts else ""

    def _build_anchor_context(self, context: ConsistencyContext) -> Any:
        try:
            from my_character.persona_utils import build_anchor_context
        except ImportError:
            return None

        return build_anchor_context(
            emotion_state=context.emotion_state,
            chat_round=context.chat_round,
            affinity_override=context.affinity if context.affinity != 0 else None,
        )

    def health_check(self) -> dict:
        return {
            "schema_loaded": self._schema is not None,
            "anchors_loaded": self._anchors is not None,
            "coupler_loaded": self._style_coupler is not None,
        }


# ── 共享编排器工具函数 ─────────────────────────────────────

# 6b 项6：检测器按 core_anchors 缓存复用——旧路径每条消息（流式后台检查 +
# 非流式 check_and_correct_reply）都重建 PersonaConsistencyChecker +
# DynamicAnchorSystem。check() 路径对锚点系统只读（reinforcement 计数器仅
# enhanced_prompt_engine 旧路使用），缓存不改变任何判定结果。
_CHECKER_CACHE: dict[tuple[str, ...], PersonaConsistencyChecker] = {}
_CHECKER_CACHE_MAX = 64


def checker_for_card(character_card: dict[str, Any]) -> PersonaConsistencyChecker:
    """按角色卡 core_anchors 构建/复用一致性检测器（带缓存）。"""
    from my_character.dynamic_anchor import DynamicAnchorSystem

    anchors = tuple(str(a) for a in (character_card.get("core_anchors") or []))
    checker = _CHECKER_CACHE.get(anchors)
    if checker is None:
        checker = PersonaConsistencyChecker(
            dynamic_anchors=DynamicAnchorSystem(
                base_anchors=list(anchors),
                dynamic_anchors=[],
            )
        )
        if len(_CHECKER_CACHE) >= _CHECKER_CACHE_MAX:
            _CHECKER_CACHE.clear()
        _CHECKER_CACHE[anchors] = checker
    return checker


async def check_and_correct_reply(
    reply: str,
    persona_engine: Any,
    llm_gateway: Any,
    emotion_state: Any = None,
    session_id: str = "",
    memory: Any = None,
    character_card: dict[str, Any] | None = None,
    chat_round: int | None = None,
) -> str:
    """统一的一致性检查 + 自动修正（供 main.py 和 orchestrator.py 复用）

    Args:
        reply: LLM 原始回复文本
        persona_engine: PersonaEngine 实例（含 check_consistency 方法）
        llm_gateway: LLM 网关实例（含 chat_sync 方法）
        emotion_state: 可选的情感状态
        session_id: 可选会话 ID（用于计算 chat_round）
        memory: 可选记忆管线（用于获取对话历史）
        chat_round: 可选，调用方已计算的对话轮数。传入时跳过重复查询
                    （B4 优化：消除 _prepare_context 与此处的重复 get_chat_context 调用）

    Returns:
        修正后（或原样放行）的回复文本
    """
    try:
        # 计算 chat_round（若调用方已提供则直接复用，避免重复查询）
        if chat_round is None:
            chat_round = 0
            if memory and hasattr(memory, "get_chat_context"):
                try:
                    history, _ = memory.get_chat_context(session_id=session_id)
                    chat_round = len(history) if history else 0
                except Exception as e:
                    logger.warning("获取 chat_context 失败，chat_round 降级为 0: %s", e)

        if character_card:
            affinity = getattr(emotion_state, "affinity", 0) if emotion_state else 0
            result = checker_for_card(character_card).check(
                reply,
                ConsistencyContext(
                    emotion_state=emotion_state,
                    chat_round=chat_round,
                    affinity=int(affinity or 0),
                ),
            )
        elif persona_engine and hasattr(persona_engine, "check_consistency"):
            result = persona_engine.check_consistency(reply, emotion_state, chat_round)
        else:
            return reply

        if result is None:
            return reply

        if not result.overall_passed and result.overall_score < 0.4 and result.correction_prompt:
            # 严重违规：用修正 prompt 重新生成
            if llm_gateway and (hasattr(llm_gateway, "chat") or hasattr(llm_gateway, "chat_sync")):
                correction_query = (
                    f"{result.correction_prompt}\n\n"
                    f"当前角色卡：{character_card or {}}\n\n"
                    f"原始回复：{reply}\n\n"
                    "请根据以上修正建议重新生成一条符合角色设定的回复。"
                    "只输出修正后的回复。"
                )
                if hasattr(llm_gateway, "chat"):
                    corrected = llm_gateway.chat(query=correction_query, max_tokens=512)
                    if inspect.isawaitable(corrected):
                        corrected = await corrected
                else:
                    corrected = await asyncio.to_thread(
                        llm_gateway.chat_sync,
                        query=correction_query,
                        max_tokens=512,
                    )
                if corrected and len(corrected.strip()) > 0:
                    logger.info("一致性严重违规已修正: score=%.2f", result.overall_score)
                    return corrected.strip()
            else:
                logger.warning("LLM 网关不可用，跳过一致性修正")
        elif not result.overall_passed:
            # 轻度违规：放行原回复
            logger.info("一致性轻度违规(score=%.2f)，放行原回复", result.overall_score)

    except Exception as exc:
        logger.warning("一致性检查异常（已放行原回复）: %s", exc)

    return reply

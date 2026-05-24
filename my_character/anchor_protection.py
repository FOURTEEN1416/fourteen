"""
增强版锚点保护 — 语义+关键词双重检测

扩展原 PersonaEngine 的 SHA256 哈希校验，
增加语义层面锚点偏离检测和运行时回复一致性校验。
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("anchor_protection")


@dataclass
class AnchorCheckResult:
    anchor: str
    is_consistent: bool
    semantic_score: float = 0.0
    keyword_violation: bool = False
    violation_keywords: List[str] = None

    def __post_init__(self):
        if self.violation_keywords is None:
            self.violation_keywords = []


ANCHOR_VIOLATION_KEYWORDS: Dict[str, List[str]] = {
    "傲娇": ["坦率", "直说", "明说", "老实说", "说真的", "我承认", "我真心"],
    "温柔": ["冷漠", "不在乎", "无所谓", "关我什么事"],
    "嘴硬心软": ["我不在乎", "我无所谓", "随便你", "你爱怎样怎样"],
    "在乎": ["不想管", "懒得管", "不关心"],
}


class EnhancedAnchorProtection:
    """增强版锚点保护"""

    def __init__(
        self,
        anchors: Optional[List[str]] = None,
        semantic_threshold: float = 0.3,
        use_llm_check: bool = False,
        llm_gateway: Any = None,
    ):
        self._anchors = anchors or []
        self._anchor_hashes = {
            a: hashlib.sha256(a.encode("utf-8")).hexdigest() for a in self._anchors
        }
        self._semantic_threshold = semantic_threshold
        self._use_llm = use_llm_check and llm_gateway is not None
        self._llm = llm_gateway
        self._violation_keywords = ANCHOR_VIOLATION_KEYWORDS

    def set_anchors(self, anchors: List[str]) -> None:
        """设置锚点"""
        self._anchors = anchors
        self._anchor_hashes = {
            a: hashlib.sha256(a.encode("utf-8")).hexdigest() for a in anchors
        }

    def verify_anchors_integrity(self, current_anchors: List[str]) -> bool:
        """验证锚点文本完整性（SHA256校验）"""
        for anchor in current_anchors:
            expected_hash = self._anchor_hashes.get(anchor)
            if expected_hash is None:
                logger.warning("New anchor detected (not in original): %s", anchor[:20])
                continue
            actual_hash = hashlib.sha256(anchor.encode("utf-8")).hexdigest()
            if actual_hash != expected_hash:
                logger.error("Anchor integrity violation: %s", anchor[:20])
                return False
        return True

    def check_response_consistency(
        self,
        response: str,
        anchors: Optional[List[str]] = None,
    ) -> Tuple[bool, float, List[AnchorCheckResult]]:
        """检测回复是否违背锚点 — 语义相似度 + 关键词匹配双重检测"""
        check_anchors = anchors or self._anchors
        results = []
        overall_consistent = True
        total_score = 0.0

        for anchor in check_anchors:
            result = self._check_single_anchor(anchor, response)
            results.append(result)
            total_score += result.semantic_score
            if not result.is_consistent:
                overall_consistent = False

        avg_score = total_score / len(check_anchors) if check_anchors else 1.0
        return overall_consistent, avg_score, results

    def _check_single_anchor(
        self, anchor: str, response: str,
    ) -> AnchorCheckResult:
        """检测单个锚点"""
        semantic_score = self._compute_semantic_similarity(anchor, response)
        keyword_violation, violation_kws = self._check_keyword_violation(anchor, response)

        is_consistent = semantic_score >= self._semantic_threshold and not keyword_violation

        return AnchorCheckResult(
            anchor=anchor,
            is_consistent=is_consistent,
            semantic_score=semantic_score,
            keyword_violation=keyword_violation,
            violation_keywords=violation_kws,
        )

    def _compute_semantic_similarity(self, anchor: str, response: str) -> float:
        """计算锚点与回复的语义相似度（规则版，无需向量模型）"""
        anchor_lower = anchor.lower()
        response_lower = response.lower()

        anchor_chars = set(anchor_lower)
        response_chars = set(response_lower)
        if not anchor_chars:
            return 1.0

        overlap = len(anchor_chars & response_chars)
        jaccard = overlap / len(anchor_chars | response_chars) if (anchor_chars | response_chars) else 0

        anchor_bigrams = set()
        for i in range(len(anchor_lower) - 1):
            anchor_bigrams.add(anchor_lower[i:i+2])
        response_bigrams = set()
        for i in range(len(response_lower) - 1):
            response_bigrams.add(response_lower[i:i+2])
        bigram_overlap = 0.0
        if anchor_bigrams:
            common = anchor_bigrams & response_bigrams
            bigram_overlap = len(common) / len(anchor_bigrams)

        anchor_substrings = set()
        cn_chars = re.findall(r'[\u4e00-\u9fff]', anchor_lower)
        for w in cn_chars:
            anchor_substrings.add(w)
        for i in range(len(cn_chars) - 1):
            anchor_substrings.add(cn_chars[i] + cn_chars[i+1])
        substring_match = 0.0
        if anchor_substrings:
            matched = sum(1 for s in anchor_substrings if s in response_lower)
            substring_match = matched / len(anchor_substrings)

        score = 0.2 * jaccard + 0.3 * bigram_overlap + 0.5 * substring_match

        if self._use_llm:
            llm_score = self._llm_semantic_check(anchor, response)
            if llm_score is not None:
                score = 0.3 * score + 0.7 * llm_score

        return min(1.0, score)

    def _check_keyword_violation(
        self, anchor: str, response: str,
    ) -> Tuple[bool, List[str]]:
        """关键词违背检测"""
        violations = []
        for anchor_key, keywords in self._violation_keywords.items():
            if anchor_key in anchor:
                for kw in keywords:
                    if kw in response:
                        violations.append(kw)

        return len(violations) > 0, violations

    def _llm_semantic_check(self, anchor: str, response: str) -> Optional[float]:
        """LLM语义检测"""
        if not self._llm:
            return None
        try:
            prompt = f"""判断以下AI回复是否违背角色设定。

角色设定：{anchor}
AI回复：{response}

评分(0-1)：1=完全符合，0=严重违背
只输出数字："""
            if hasattr(self._llm, "chat_sync"):
                result = self._llm.chat_sync(query=prompt, max_tokens=10, temperature=0.1)
            elif callable(self._llm):
                result = self._llm(prompt)
            else:
                return None
            score = float(result.strip())
            return max(0.0, min(1.0, score))
        except Exception as e:
            logger.debug("LLM semantic check failed: %s", e)
            return None

    def generate_reinforcement_prompt(self, anchors: Optional[List[str]] = None) -> str:
        """生成锚点强化提示词（用于长对话中周期性注入）"""
        check_anchors = anchors or self._anchors
        if not check_anchors:
            return ""
        anchor_text = "；".join(check_anchors)
        return f"【角色锚点提醒】记住你的核心性格：{anchor_text}。无论对话如何发展，都必须保持这些核心特征。"

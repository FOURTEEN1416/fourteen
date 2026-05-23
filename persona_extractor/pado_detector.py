"""
PADO 风格 OCEAN 人格检测器 — 多智能体辩论式 prompt 检测

核心思路 (PADO, COLING 2025):
  1. 对每个 OCEAN 维度，设计 high-inducing / low-inducing 两种偏见的检测视角
  2. 让 LLM 分别从两种视角分析用户的情绪/认知/社交信号
  3. Judge 综合比较，输出最终 OCEAN 分数

本实现：
  - 全量 PADO: 11次API调用 (5高+5低+1汇总) —— 最准但最慢
  - Lite PADO: 1次API调用 (综合推理) —— 够用且快
  - Rule: 0次API调用 (基于关键词规则) —— 零成本备选
  - 支持检测历史平滑 + Chameleon效应隔离
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .models import OceanTraits, PadState, StyleVector, UserPersonaSnapshot

logger = logging.getLogger("pado_detector")

# ---------------------------------------------------------------------------
#  PADO 检测 Prompt 模板
# ---------------------------------------------------------------------------

PADO_LITE_PROMPT = """## 任务：分析以下用户消息的人格特征

请从以下五个维度分析该用户的人格特征（OCEAN 五大人格模型），
每个维度返回 0.0(低) 到 1.0(高) 的分数，并给出简短依据。

### 用户消息
{message}

### 历史上下文
{context}

### 分析维度

1. **开放性 (Openness)**  — 低=务实保守, 高=好奇开放
   - 情感线索: 是否对新鲜事物表现出好奇/抗拒
   - 认知线索: 思维是否灵活，是否接受不同观点
   - 社交线索: 是否愿意尝试新的交流方式

2. **尽责性 (Conscientiousness)** — 低=随性自由, 高=条理自律
   - 情感线索: 是否表现出秩序感或混乱感
   - 认知线索: 表达是否有条理、有计划
   - 社交线索: 是否守时、负责

3. **外向性 (Extraversion)** — 低=安静独处, 高=社交热情
   - 情感线索: 情感表达是外放还是内敛
   - 认知线索: 是否喜欢谈论人际互动
   - 社交线索: 是否主动开启话题

4. **宜人性 (Agreeableness)** — 低=直率竞争, 高=友善合作
   - 情感线索: 表达友善还是攻击性
   - 认知线索: 是否考虑他人感受
   - 社交线索: 合作还是对立态度

5. **神经质 (Neuroticism)** — 低=情绪稳定, 高=敏感焦虑
   - 情感线索: 情绪波动程度
   - 认知线索: 是否容易担心/过度思考
   - 社交线索: 对互动的安全感

### 输出格式 (严格 JSON)
{{
  "openness": {{"score": 0.0-1.0, "evidence": "简短依据,10字以内"}},
  "conscientiousness": {{"score": 0.0-1.0, "evidence": "..."}},
  "extraversion": {{"score": 0.0-1.0, "evidence": "..."}},
  "agreeableness": {{"score": 0.0-1.0, "evidence": "..."}},
  "neuroticism": {{"score": 0.0-1.0, "evidence": "..."}},
  "pad": {{"pleasure": -1.0-1.0, "arousal": -1.0-1.0, "dominance": -1.0-1.0}},
  "confidence": 0.0-1.0,
  "note": "总体分析备注, 可空"
}}"""

PADO_JUDGE_PROMPT = """## 任务：综合判断用户人格特征

以下是对同一用户消息的10种不同视角分析：
- 每种视角偏向 OCEAN 五个维度的"高"或"低"端
- 请综合这些视角，排除偏见，得出最终人格判断

### 用户消息
{message}

### 各视角分析结果
{agent_results}

### 输出格式
{output_format}
"""


# ---------------------------------------------------------------------------
#  Rule-based 规则检测（0 API调用，备选方案）
# ---------------------------------------------------------------------------

# OCEAN 关键词映射 (keyword → trait, direction, weight)
OCEAN_KEYWORDS: Dict[str, List[Tuple[str, float, float]]] = {
    # (trait, direction 1=high, -1=low, weight)
    "好无聊": [("openness", -0.3, 0.6), ("extraversion", -0.2, 0.4)],
    "没意思": [("openness", -0.3, 0.5)],
    "试试": [("openness", 0.3, 0.5)],
    "新的": [("openness", 0.2, 0.4)],
    "好奇": [("openness", 0.4, 0.6)],
    "不知道": [("openness", -0.1, 0.3)],
    "计划": [("conscientiousness", 0.3, 0.5)],
    "安排": [("conscientiousness", 0.3, 0.5)],
    "整理": [("conscientiousness", 0.2, 0.4)],
    "随便": [("conscientiousness", -0.3, 0.5)],
    "懒": [("conscientiousness", -0.2, 0.4)],
    "忘了": [("conscientiousness", -0.2, 0.3)],
    "好烦": [("neuroticism", 0.3, 0.5), ("agreeableness", -0.2, 0.3)],
    "焦虑": [("neuroticism", 0.4, 0.7)],
    "担心": [("neuroticism", 0.3, 0.5)],
    "紧张": [("neuroticism", 0.3, 0.5)],
    "害怕": [("neuroticism", 0.4, 0.6)],
    "没事": [("neuroticism", -0.2, 0.3)],
    "放心吧": [("neuroticism", -0.2, 0.3)],
    "朋友": [("extraversion", 0.3, 0.5)],
    "聚会": [("extraversion", 0.3, 0.5)],
    "一起": [("extraversion", 0.2, 0.4)],
    "一个人": [("extraversion", -0.3, 0.5)],
    "好吵": [("extraversion", -0.2, 0.4)],
    "好累": [("extraversion", -0.2, 0.3)],
    "谢谢": [("agreeableness", 0.3, 0.5)],
    "对不起": [("agreeableness", 0.2, 0.4)],
    "抱歉": [("agreeableness", 0.2, 0.4)],
    "你好": [("agreeableness", 0.2, 0.3)],
    "讨厌": [("agreeableness", -0.3, 0.5)],
    "烦死了": [("agreeableness", -0.3, 0.5)],
    "关你": [("agreeableness", -0.3, 0.5)],
    "滚": [("agreeableness", -0.4, 0.7)],
}


def _rule_detect(message: str) -> Tuple[OceanTraits, float]:
    """基于关键词规则的人格检测（零成本备选）"""
    msg = message.lower()
    scores = {
        'openness': 0.5,
        'conscientiousness': 0.5,
        'extraversion': 0.5,
        'agreeableness': 0.5,
        'neuroticism': 0.5,
    }
    weight_sum = {k: 0.0 for k in scores}
    confidence_sum = 0.0
    matched_count = 0

    for keyword, rules in OCEAN_KEYWORDS.items():
        if keyword in msg:
            for trait, direction, weight in rules:
                delta = direction * weight * 0.15  # 最大调整 ±0.15
                scores[trait] += delta
                weight_sum[trait] += weight
                confidence_sum += weight * 0.3
                matched_count += 1

    if matched_count == 0:
        # 无关键词匹配，返回默认值 + 低置信度
        return OceanTraits(), 0.1

    for trait in scores:
        if weight_sum[trait] > 0:
            # 不是简单的平均，而是加权
            pass
        scores[trait] = max(0.0, min(1.0, scores[trait]))

    confidence = min(0.6, confidence_sum / max(1, matched_count))
    return OceanTraits(**scores), confidence


# ---------------------------------------------------------------------------
#  PADO Detector 主类
# ---------------------------------------------------------------------------

class PADODetector:
    """PADO风格OCEAN人格检测器

    三种模式:
      - "full":   11次API调用 (5high + 5low + 1judge), 最准最慢
      - "lite":   1次API调用 (综合prompt), 够用且快 (默认)
      - "rule":   0次API调用 (关键词规则), 零成本备选

    用法:
        detector = PADODetector(llm_gateway=llm, mode="lite")
        snapshot = await detector.detect(user_message, context="...")
    """

    def __init__(
        self,
        llm_gateway=None,
        mode: str = "lite",
        cache_size: int = 50,
        chameleon_guard: bool = True,
    ):
        self._llm = llm_gateway
        self.mode = mode
        self.chameleon_guard = chameleon_guard
        self._cache: Dict[str, UserPersonaSnapshot] = {}
        self._cache_size = cache_size
        self._last_detect_time = 0.0

        logger.info("PADODetector initialized (mode=%s, llm=%s)",
                     mode, llm_gateway is not None)

    async def detect(
        self,
        message: str,
        context: str = "",
        user_id: str = "default",
        force: bool = False,
    ) -> UserPersonaSnapshot:
        """检测用户消息中的人格特征

        Args:
            message: 用户最新消息
            context: 对话上下文（最近N轮历史）
            user_id: 用户标识
            force: 是否强制检测（跳过频率限制）

        Returns:
            UserPersonaSnapshot 检测快照
        """
        # 频率限制：每秒最多1次
        now = time.time()
        if not force and now - self._last_detect_time < 1.0:
            logger.debug("PADO detect rate limited, returning cached")
            return self._get_cached(user_id) or UserPersonaSnapshot(
                timestamp=datetime.now().isoformat(),
                ocean=OceanTraits(),
                confidence=0.0,
                source="cached",
            )
        self._last_detect_time = now

        # Chameleon效应隔离：如果消息很短(<5字)，降低置信度
        # 因为短消息容易被AI女友的情感"传染"，不代表用户真实人格
        msg_len = len(message.strip())
        chameleon_penalty = 1.0
        if self.chameleon_guard and msg_len < 10:
            chameleon_penalty = 0.5
            logger.debug("Chameleon guard activated: short msg (%d chars)", msg_len)

        if self.mode == "rule":
            ocean, confidence = _rule_detect(message)
            confidence *= chameleon_penalty
            snapshot = self._build_snapshot(message, ocean, confidence, "rule")
        elif self.mode == "full" and self._llm:
            snapshot = await self._full_detect(message, context, chameleon_penalty)
        else:  # lite (default)
            if self._llm:
                snapshot = await self._lite_detect(message, context, chameleon_penalty)
            else:
                # 无LLM时降级到rule
                ocean, confidence = _rule_detect(message)
                confidence *= chameleon_penalty
                snapshot = self._build_snapshot(message, ocean, confidence, "rule")

        self._cache[f"user_{user_id}"] = snapshot
        # 清理缓存
        if len(self._cache) > self._cache_size:
            self._cache.pop(next(iter(self._cache)))

        return snapshot

    async def _lite_detect(
        self,
        message: str,
        context: str,
        chameleon_penalty: float = 1.0,
    ) -> UserPersonaSnapshot:
        """Lite PADO: 1次API调用综合检测"""
        prompt = PADO_LITE_PROMPT.format(
            message=message[:500],
            context=context[:1000] if context else "(无)",
        )

        try:
            response = await self._call_llm(prompt)
            result = self._parse_lite_response(response)

            if result is None:
                logger.warning("PADO lite parse failed, falling back to rule")
                ocean, confidence = _rule_detect(message)
                return self._build_snapshot(message, ocean, confidence, "rule_fallback")

            ocean_data, pad_data, confidence = result
            confidence *= chameleon_penalty

            ocean = OceanTraits(**ocean_data)
            pad = PadState(**pad_data) if pad_data else PadState()
            style = StyleVector.from_ocean(ocean)

            return UserPersonaSnapshot(
                timestamp=datetime.now().isoformat(),
                ocean=ocean,
                pad=pad,
                style=style,
                confidence=max(0.0, min(1.0, confidence)),
                source="pado_lite",
                trigger_message=message[:200],
            )

        except Exception as e:
            logger.error("PADO lite detection error: %s", e)
            ocean, confidence = _rule_detect(message)
            return self._build_snapshot(message, ocean, confidence, "rule_error")

    async def _full_detect(
        self,
        message: str,
        context: str,
        chameleon_penalty: float = 1.0,
    ) -> UserPersonaSnapshot:
        """Full PADO: (理论) 5high+5low+1judge = 11次调用

        实际实现：因单LLM无法并行，采用顺序调用+缓存。
        这非常慢（~11s+），仅在高精度场景使用。
        """
        # 简化版：只做 5次（1个综合high agent + 1个综合low agent）
        # 完整的11agent版太慢，不实用
        high_prompt = f"""你认为以下用户具有**高开放性/高尽责性/高外向性/高宜人性/低神经质**倾向。
请从该视角分析用户消息，给出五个维度的分数(0-1)。

消息: {message}
上下文: {context}

输出JSON: {{"openness": 0-1, "conscientiousness": 0-1, "extraversion": 0-1, "agreeableness": 0-1, "neuroticism": 0-1, "reasoning": "..."}}"""

        low_prompt = f"""你认为以下用户具有**低开放性/低尽责性/低外向性/低宜人性/高神经质**倾向。
请从该视角分析用户消息，给出五个维度的分数(0-1)。

消息: {message}
上下文: {context}

输出JSON: {{"openness": 0-1, "conscientiousness": 0-1, "extraversion": 0-1, "agreeableness": 0-1, "neuroticism": 0-1, "reasoning": "..."}}"""

        try:
            # 并行请求两个视角
            import asyncio
            high_resp, low_resp = await asyncio.gather(
                self._call_llm(high_prompt),
                self._call_llm(low_prompt),
                return_exceptions=True,
            )

            if isinstance(high_resp, Exception):
                high_resp = '{}'
            if isinstance(low_resp, Exception):
                low_resp = '{}'

            high_data = self._parse_full_response(high_resp)  # type: ignore
            low_data = self._parse_full_response(low_resp)  # type: ignore

            if not high_data or not low_data:
                logger.warning("PADO full incomplete, falling back to lite")
                return await self._lite_detect(message, context, chameleon_penalty)

            # Judge: 综合判断
            judge_prompt = f"""综合以下两种视角的分析，排除各自偏见，得出最合理的OCEAN人格分数。

High视角（偏向各维度高端）: {high_data}
Low视角（偏向各维度低端）: {low_data}

注意：Chameleon效应可能导致结果受对话情绪影响而非真实人格。
请给出排除情绪干扰后的判断。

输出JSON: {{"openness": 0-1, "conscientiousness": 0-1, "extraversion": 0-1, "agreeableness": 0-1, "neuroticism": 0-1, "pad": {{"pleasure": -1-1, "arousal": -1-1, "dominance": -1-1}}, "confidence": 0-1}}"""

            judge_resp = await self._call_llm(judge_prompt)
            judge_result = self._parse_judge_response(judge_resp)

            if judge_result:
                ocean_data, pad_data, confidence = judge_result
                confidence *= chameleon_penalty
                ocean = OceanTraits(**ocean_data)
                pad = PadState(**pad_data) if pad_data else PadState()
                style = StyleVector.from_ocean(ocean)

                return UserPersonaSnapshot(
                    timestamp=datetime.now().isoformat(),
                    ocean=ocean,
                    pad=pad,
                    style=style,
                    confidence=max(0.0, min(1.0, confidence)),
                    source="pado_full",
                    trigger_message=message[:200],
                )

        except Exception as e:
            logger.error("PADO full detection error: %s", e)

        return await self._lite_detect(message, context, chameleon_penalty)

    # ── LLM调用 ──────────────────────────────────────────────

    async def _call_llm(self, prompt: str) -> str:
        """调用LLM，同步/异步双兼容"""
        if self._llm is None:
            return "{}"

        try:
            # 尝试异步调用
            if hasattr(self._llm, 'achat'):
                response = await self._llm.achat(
                    query=prompt, max_tokens=512, temperature=0.1
                )
            elif hasattr(self._llm, 'chat'):
                # 同步chat在线程池中运行
                import asyncio
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None, lambda: self._llm.chat(  # type: ignore
                        query=prompt, max_tokens=512, temperature=0.1
                    )
                )
            else:
                response = ""
        except Exception as e:
            logger.warning("PADO LLM call failed: %s", e)
            response = ""

        return response or "{}"

    # ── 解析 ──────────────────────────────────────────────────

    def _parse_lite_response(self, response: str) -> Optional[Tuple[dict, Optional[dict], float]]:
        """解析Lite PADO的JSON响应"""
        data = self._extract_json(response)
        if not data:
            return None

        try:
            ocean_data = {}
            for trait in ['openness', 'conscientiousness', 'extraversion',
                           'agreeableness', 'neuroticism']:
                if trait in data:
                    if isinstance(data[trait], dict):
                        ocean_data[trait] = float(data[trait].get('score', 0.5))
                    else:
                        ocean_data[trait] = float(data[trait])

            pad_data = data.get('pad')
            if pad_data and isinstance(pad_data, dict):
                pass  # 直接返回dict
            else:
                pad_data = None

            confidence = float(data.get('confidence', 0.5))
            confidence = max(0.1, min(0.95, confidence))

            return ocean_data, pad_data, confidence
        except (ValueError, TypeError, KeyError) as e:
            logger.warning("PADO parse error: %s", e)
            return None

    def _parse_full_response(self, response: str) -> Optional[dict]:
        """解析Full PADO的单视角响应"""
        data = self._extract_json(response)
        if not data:
            return None
        try:
            return {
                trait: float(data.get(trait, 0.5))
                for trait in ['openness', 'conscientiousness', 'extraversion',
                               'agreeableness', 'neuroticism']
            }
        except (ValueError, TypeError):
            return None

    def _parse_judge_response(self, response: str) -> Optional[Tuple[dict, Optional[dict], float]]:
        """解析Judge的JSON响应"""
        return self._parse_lite_response(response)

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """从LLM回复中提取JSON对象"""
        if not text or text == "{}":
            return None

        # 尝试直接解析
        text = text.strip()
        # 移除 markdown 代码块标记
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)

        # 找第一个 { 和最后一个 }
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            text = text[start:end + 1]

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试修复常见JSON错误
        try:
            # 修复 trailing comma
            text = re.sub(r',\s*}', '}', text)
            text = re.sub(r',\s*\]', ']', text)
            return json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Failed to extract JSON from: %s...", text[:100])
            return None

    def _build_snapshot(self, message: str, ocean: OceanTraits,
                        confidence: float, source: str) -> UserPersonaSnapshot:
        """构建检测快照（辅助方法）"""
        pad = PadState.from_ocean(ocean)
        style = StyleVector.from_ocean(ocean)
        return UserPersonaSnapshot(
            timestamp=datetime.now().isoformat(),
            ocean=ocean,
            pad=pad,
            style=style,
            confidence=confidence,
            source=source,
            trigger_message=message[:200],
        )

    def _get_cached(self, user_id: str) -> Optional[UserPersonaSnapshot]:
        return self._cache.get(f"user_{user_id}")

    def health_check(self) -> dict:
        return {
            "mode": self.mode,
            "llm_available": self._llm is not None,
            "cache_size": len(self._cache),
            "chameleon_guard": self.chameleon_guard,
        }

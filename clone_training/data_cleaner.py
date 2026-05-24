"""
DataCleaner — LLM Judge 数据质量评分

对 QA 对进行 1-5 分质量评分，过滤低质量训练数据

用法:
    from clone_training.data_cleaner import DataCleaner

    # 有 LLM
    cleaner = DataCleaner()
    score = cleaner.score_pair("你好", "你好呀～今天怎么啦？")

    # 无 LLM（自动用启发式回退）
    cleaner = DataCleaner(llm=None)
    cleaned = cleaner.clean(conversations)
"""

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("clone.cleaner")

# 启发式低质量检测
_HEURISTIC_SHORT_MSG_THRESHOLD = 3  # 少于这个字符数视为无效
_PUNCTUATION_ONLY_RE = re.compile(r"^[，。！？、；：""''．…—·,\\.!\\?;:'\"`~\\-—\\s]+$")
_EMOJI_ONLY_RE = re.compile(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF\uFE00-\uFE0F\U0001F600-\U0001F64F\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\u200d\U0000200B-\U0000200D\U0000FE0F]+$")
_AUTO_REPLY_PATTERNS = [
    r"^收到$",
    r"^好的$",
    r"^嗯$",
    r"^OK$",
    r"^ok$",
    r"^好的呢$",
    r"^知道了$",
    r"^明白$",
    r"^👍$",
    r"^收到收到$",
    r"^好的好的$",
]


class DataCleaner:
    """LLM Judge data quality scorer — 对 QA 对进行 1-5 分质量评分"""

    CLEAN_PROMPT = """You are a strict data quality judge. Score the following conversation from 1-5 based on relevance and logic quality.
5: Excellent — highly relevant, coherent, meaningful exchange
4: Good — relevant and mostly coherent
3: Average — somewhat relevant
2: Poor — weak relevance (default reject threshold)
1: Very Poor — irrelevant, gibberish, spam

Q: {user_msg}
A: {reply_msg}

Return ONLY a single number (1-5):"""

    def __init__(self, llm: Any | None = None, accept_score: int = 2):
        """
        Args:
            llm: 一个具有 .chat(query, system_prompt) 方法的 LLM 实例。
                 如果为 None，则尝试从 llm_provider.get_llm() 获取；
                 若仍不可用，则回退到启发式评分。
            accept_score: 最低保留分数（默认 2，即 score < 2 的会被过滤）
        """
        self.accept_score = accept_score
        self._llm = llm
        if self._llm is None:
            try:
                from llm_provider import get_llm
                self._llm = get_llm()
                logger.info("DataCleaner: 使用 llm_provider 的 LLM 实例")
            except (ImportError, Exception) as e:
                self._llm = None
                logger.warning("DataCleaner: 无法获取 LLM，回退到启发式评分: %s", e)

    # ── 核心评分方法 ──

    def score_pair(self, user_msg: str, reply_msg: str) -> int:
        """评分单个 QA 对，返回 1-5 分

        Args:
            user_msg: 用户消息
            reply_msg: 回复消息

        Returns:
            1-5 的质量分数
        """
        # 先过启发式检查
        heuristic_score = self._heuristic_check(user_msg, reply_msg)
        if heuristic_score is not None:
            return heuristic_score

        # 有 LLM 则用 LLM Judge
        if self._llm is not None:
            return self._llm_score(user_msg, reply_msg)

        # 无 LLM：基于简单规则评分
        return self._rule_based_score(user_msg, reply_msg)

    def score_batch(self, conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """批量评分，为每条对话添加 'score' 键

        Args:
            conversations: 对话列表，每条包含 'user_msg' 和 'reply_msg'（或 'user' 和 'reply'）

        Returns:
            添加了 'score' 键的对话列表
        """
        scored = []
        for i, conv in enumerate(conversations):
            user_msg = conv.get("user_msg") or conv.get("user", "")
            reply_msg = conv.get("reply_msg") or conv.get("reply", "")
            try:
                score = self.score_pair(user_msg, reply_msg)
            except Exception as e:
                logger.warning("评分异常 (第 %d 条): %s, 默认 1 分", i, e)
                score = 1
            conv["score"] = score
            scored.append(conv)
        return scored

    def clean(self, conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """过滤掉低于 accept_score 阈值的对话

        Args:
            conversations: 对话列表

        Returns:
            仅保留 score >= accept_score 的对话
        """
        # 如果还没有评分，先评分
        if conversations and "score" not in conversations[0]:
            conversations = self.score_batch(conversations)

        before = len(conversations)
        filtered = [c for c in conversations if c.get("score", 0) >= self.accept_score]
        after = len(filtered)

        if before > 0:
            logger.info(
                "DataCleaner: 过滤 %d/%d 条 (%.1f%%), 阈值=%d",
                before - after, before, (before - after) / before * 100,
                self.accept_score,
            )
        return filtered

    def score_from_dataset(self, dataset_path: str) -> str:
        """从 JSON 数据集文件评分，保存过滤结果为 {原名}_cleaned.json

        Args:
            dataset_path: JSON 数据集文件路径

        Returns:
            清洗后的文件路径
        """
        path = Path(dataset_path)
        if not path.exists():
            logger.error("数据集文件不存在: %s", dataset_path)
            return ""

        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            conversations = data
        elif isinstance(data, dict):
            conversations = data.get("conversations", data.get("data", [data]))
        else:
            logger.error("不支持的 JSON 格式: %s", type(data))
            return ""

        scored = self.score_batch(conversations)
        cleaned = self.clean(scored)

        output_path = str(path.parent / f"{path.stem}_cleaned.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)

        logger.info(
            "数据集清洗完成: %d → %d 条, 保存至 %s",
            len(scored), len(cleaned), output_path,
        )
        return output_path

    # ── 内部方法 ──

    def _heuristic_check(self, user_msg: str, reply_msg: str) -> int | None:
        """启发式快速判定低质量，返回分数或 None（需要进一步判断）"""
        user_msg = user_msg.strip() if user_msg else ""
        reply_msg = reply_msg.strip() if reply_msg else ""

        # 空消息
        if not user_msg or not reply_msg:
            logger.debug("启发式: 空消息 → 1 分")
            return 1

        # 过短的消息
        if len(reply_msg) < _HEURISTIC_SHORT_MSG_THRESHOLD:
            logger.debug("启发式: 回复过短 (%d 字符) → 1 分", len(reply_msg))
            return 1

        # 纯标点符号
        if _PUNCTUATION_ONLY_RE.match(reply_msg):
            logger.debug("启发式: 纯标点回复 → 1 分")
            return 1

        # 纯表情/emoji
        if _EMOJI_ONLY_RE.match(reply_msg):
            logger.debug("启发式: 纯 emoji 回复 → 2 分")
            return 2

        # 明显自动回复
        for pat in _AUTO_REPLY_PATTERNS:
            if re.match(pat, reply_msg.strip()):
                logger.debug("启发式: 疑似自动回复 → 2 分")
                return 2

        return None

    def _llm_score(self, user_msg: str, reply_msg: str) -> int:
        """通过 LLM Judge 评分"""
        prompt = self.CLEAN_PROMPT.format(
            user_msg=user_msg[:500],  # 截断防止超长
            reply_msg=reply_msg[:500],
        )
        try:
            result = self._llm.chat_sync(query=prompt, system_prompt="")  # type: ignore
            result = result.strip()
            # 提取数字
            nums = re.findall(r"[1-5]", result)
            if nums:
                return int(nums[0])
            logger.warning("LLM 返回无法解析: %s, 默认 3 分", result[:50])
            return 3
        except Exception as e:
            logger.warning("LLM 评分失败: %s, 回退到规则评分", e)
            return self._rule_based_score(user_msg, reply_msg)

    def _rule_based_score(self, user_msg: str, reply_msg: str) -> int:
        """无 LLM 时的基于规则评分（1-5 分）"""
        score = 3  # 默认中等

        user_len = len(user_msg.strip())
        reply_len = len(reply_msg.strip())

        # +1: 回复有内容，不空洞
        if reply_len > 10 and user_len > 5:
            score += 1

        # +1: 回复与用户消息有上下文关联（关键词重叠）
        user_words = set(user_msg.lower().split())
        reply_words = set(reply_msg.lower().split())
        overlap = user_words & reply_words
        if len(overlap) >= 2:
            score += 1

        # -1: 回复过于简短
        if reply_len <= 5:
            score -= 1

        # -1: 用户消息太短，无实质内容
        if user_len <= 3:
            score -= 1

        # -1: 回复全部大写或全部小写且很长（疑似机器生成）
        if reply_len > 50 and (reply_msg.isupper() or reply_msg.islower()):
            score -= 1

        return max(1, min(5, score))

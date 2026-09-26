"""
事实提取器 — 从对话中提取用户事实

使用 LLM 或规则从对话中提取关于用户的信息：
- 偏好（"我喜欢吃火锅"）
- 习惯（"我每天12点睡"）
- 事件（"下周要去出差"）
- 个人信息（"我住在上海"）
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("fact_extractor")

# 事实类别
FACT_CATEGORIES = [
    "preference",   # 偏好
    "habit",        # 习惯
    "event",        # 事件
    "personal",     # 个人信息
    "relationship", # 关系
    "work",         # 工作
    "health",       # 健康
    "commitment",   # 承诺/约定（提醒、约定、答应过的事）
    "general",      # 通用
]

# 规则模式：提取常见事实
PATTERNS = {
    "preference": [
        r"(?:喜欢|爱[好]?|最[爱喜]|超[级]?[喜爱]).{0,20}(?:吃|喝|玩|去|看|听|做|打)",
        r"(?:不[喜爱]|讨厌|受不了).{0,20}",
    ],
    "habit": [
        r"(?:每天|平时|经常|总是|习惯).{0,30}(?:睡觉|起床|吃饭|运动|工作|学习|打游戏)",
        r"(?:熬夜|早起|午睡|健身|跑步)",
    ],
    "event": [
        r"(?:下周|明天|后天|下个月|这周末|周末).{0,30}(?:去|要|打算|准备|计划)",
        r"(?:出差|旅行|搬家|考试|面试|约会|聚会)",
    ],
    "personal": [
        r"(?:我[叫是]|名字).{0,10}",
        r"(?:住在|来自|家在).{0,15}",
        r"(?:今年|岁|出生).{0,10}",
    ],
    "work": [
        r"(?:上班|工作|公司|同事|老板|项目|加班)",
        r"(?:学生|上学|考试|专业|毕业|学校)",
    ],
    "health": [
        r"(?:生病|感冒|发烧|头疼|肚子疼|不舒服|医院|吃药)",
        r"(?:失眠|睡不着|累|疲惫|乏力)",
    ],
    # 承诺/约定（2026-09-20）：用户请求「提醒我 X」「叫我」或双方「说好了/约好」。
    # 这类事实是「你不是答应提醒我吗」追问的全部答案来源，此前无类别可落、
    # 提取器直接漏掉（生产实证：user_facts 全库仅 3 条）。
    "commitment": [
        # 必须含时间/对象动作的完整托付；禁止「叫我」「记得」裸句入库
        r"(?:提醒我|叫我|喊我|叫醒我|催我)[^。！？\n]{0,20}(?:起床|吃饭|睡觉|上班|上课|打卡|带|交|回)",
        r"(?:明早|明天早上|明天|今晚|睡前|下班|到点|早上)?\s*\d{1,2}[:：点]\d{0,2}[^。！？\n]{0,20}(?:叫我|提醒|喊我|叫醒)",
        r"(?:记得|别忘了)[^。！？\n]{0,10}(?:带|交|回|吃|吃药|打卡|上课|上班)[^。！？\n]{0,15}",
        r"(?:说好了|说定了|约好|答应[了你]?)[^。！？\n]{2,40}",
    ],
}

_COMMITMENT_MIN_LEN = 8
# 只禁「整句即残句」；完整托付（如「提醒我明早六点叫我起床」）不得被前缀误杀
_COMMITMENT_FORBID = re.compile(r"^(?:叫我|叫我起床|提醒我|记得|明天要|后天也要|记得多少)[，。！？!?,.\s]*$")


def normalize_commitment_text(text: str) -> str | None:
    """承诺类事实过短/截断/口头禅 → 不入库（生产「…二十分叫」）。"""
    s = str(text or "").strip()
    if len(s) < _COMMITMENT_MIN_LEN:
        return None
    if _COMMITMENT_FORBID.match(s):
        return None
    if re.search(r"(叫|提醒|喊|催)$", s) and len(s) < 20:
        return None
    return s


class FactExtractor:
    """
    事实提取器

    支持两种模式：
    1. LLM 模式（推荐）：使用大模型提取事实
    2. 规则模式：基于正则表达式提取
    """

    def __init__(self, llm_func: Callable | None = None):
        """
        Args:
            llm_func: LLM 调用函数，接受 prompt 返回文本
                      若为 None 则使用规则模式
        """
        self.llm_func = llm_func

    def extract_facts(self, user_messages: list[str]) -> list[dict[str, Any]]:
        """
        从用户消息中提取事实

        Args:
            user_messages: 用户消息列表

        Returns:
            [{"fact": str, "category": str, "confidence": float, "source": str}, ...]
        """
        facts = (
            self._extract_with_llm(user_messages)
            if self.llm_func
            else self._extract_with_rules(user_messages)
        )
        out = []
        for f in facts or []:
            if not isinstance(f, dict):
                continue
            cat = str(f.get("category") or "general")
            fact = str(f.get("fact") or "").strip()
            if cat == "commitment":
                norm = normalize_commitment_text(fact)
                if not norm:
                    continue
                f = dict(f)
                f["fact"] = norm
            try:
                from utils.prompt_sanitize import is_injectable_fact

                if fact and not is_injectable_fact(f["fact"] if cat == "commitment" else fact):
                    continue
            except Exception:  # noqa: BLE001
                pass
            out.append(f)
        return out

    def extract_from_chat(self, chat_history: list[dict[str, str]]) -> list[dict[str, Any]]:
        """
        从聊天历史中提取事实

        Args:
            chat_history: [{"role": "user"/"assistant", "content": str}, ...]

        Returns:
            事实列表
        """
        user_msgs = [
            m["content"] for m in chat_history
            if m.get("role") == "user"
        ]
        return self.extract_facts(user_msgs)

    # ── LLM 模式 ─────────────────────────────────────────

    def _extract_with_llm(self, messages: list[str]) -> list[dict[str, Any]]:
        """使用 LLM 提取事实"""
        if not self.llm_func or not messages:
            return []

        # 合并消息
        text = json.dumps([{"source_index": i, "speaker": "user", "content": text} for i, text in enumerate(messages)], ensure_ascii=False)

        prompt = f"""从以下对话中提取关于用户的事实信息。
只提取明确提到的、有具体内容的事实。
发送者不等于事件主体；引用、转述、假设、小说角色的话不得当成用户经历。
否定不能丢，日期不能混；相对日期无法确定时保留原表述，不推断具体日期。
对每个事实给出类别、置信度(0~1)和话题标签 topics。

输出 JSON 数组格式：
[
  {{"fact": "用户喜欢吃火锅", "category": "preference", "confidence": 0.9, "topics": ["美食", "火锅"]}},
  {{"fact": "用户约定明早叫他起床", "category": "commitment", "confidence": 0.95, "topics": ["起床", "约定"]}}
]

类别: {', '.join(FACT_CATEGORIES)}
topics: 1~3 个短关键词，便于续聊。

对话内容:
{text}

JSON:"""

        try:
            result = self.llm_func(prompt)
            # 尝试解析 JSON
            facts = self._parse_json_result(result)
            if not isinstance(facts, list):
                raise ValueError("事实抽取没有返回合法数组，保留水位等待重试")
            for f in facts:
                if isinstance(f, dict):
                    f["source"] = "llm"
            return facts
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM fact extraction failed: %s", e)
            raise

    # ── 规则模式 ─────────────────────────────────────────

    def _extract_with_rules(self, messages: list[str]) -> list[dict[str, Any]]:
        """使用正则规则提取事实。

        命中模式时以「整句用户原话」作为事实文本（我→用户），而非正则捕获的
        残片段——否则「我明天去北京出差」只会得到「明天去」这类残句，
        被注入闸门判为碎片后事件类事实整体丢失（生产回归）。
        """
        facts = []  # type: ignore[var-annotated]

        for msg in messages:
            for category, patterns in PATTERNS.items():
                if not any(re.search(pattern, msg) for pattern in patterns):
                    continue
                fact_text = self._rule_fact_text(msg)
                if len(fact_text) < 4:
                    continue
                if any(f["fact"] == fact_text for f in facts):
                    continue
                topics = self._topics_from_text(fact_text, category)
                facts.append({
                    "fact": fact_text,
                    "category": category,
                    "confidence": 0.5,
                    "source": "rule",
                    "topics": topics,
                })

        return facts

    @staticmethod
    def _rule_fact_text(msg: str) -> str:
        """整句去噪：首人称→「用户」，截断到合理长度，去掉尾部标点。"""
        s = re.sub(r"\s+", " ", str(msg or "")).strip()
        # 规则只能确认是谁发了这段话，不能猜句内的主语或丢掉转折/否定。
        return f"用户曾说：{s}"

    @staticmethod
    def _topics_from_text(text: str, category: str) -> list[str]:
        """从事实文本粗提话题标签（B-b 续聊钩子）。"""
        topics: list[str] = []
        text = str(text or "")
        # 常见实体词粗匹配
        for w in (
            "火锅", "咖啡", "出差", "旅行", "考试", "加班", "健身", "跑步",
            "睡觉", "起床", "生日", "纪念日", "上班", "学校", "工作",
            "上海", "北京", "云南", "昆明",
        ):
            if w in text:
                topics.append(w)
        if category in ("commitment", "relationship") and "约定" not in topics:
            topics.append("约定" if category == "commitment" else "我们")
        return topics[:3]

    # ── 工具方法 ─────────────────────────────────────────

    @staticmethod
    def deduplicate(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        去重并合并相似事实

        合并规则：
        - 相同类别且文本相似度高的合并
        - 保留置信度高的
        """
        if not facts:
            return []

        # 按类别分组
        by_category: dict[str, list] = {}
        for f in facts:
            cat = f.get("category", "general")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(f)

        result = []
        for cat, items in by_category.items():  # noqa: B007
            # 按置信度排序
            items.sort(key=lambda x: x.get("confidence", 0), reverse=True)

            seen_texts = set()
            for item in items:
                # 简单去重：相同文本只保留置信度最高的
                text = item["fact"].strip()
                if text not in seen_texts:
                    seen_texts.add(text)
                    result.append(item)

        return result

    @staticmethod
    def _parse_json_result(text: str) -> list[dict[str, Any]] | None:
        """尝试从 LLM 输出中解析 JSON"""
        # 直接解析
        try:
            return json.loads(text)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 块
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass

        # 尝试提取 [...] 数组
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                pass

        return None

    def health_check(self) -> dict[str, Any]:
        """健康检查"""
        return {
            "llm_available": self.llm_func is not None,
            "rule_patterns": sum(len(p) for p in PATTERNS.values()),
        }

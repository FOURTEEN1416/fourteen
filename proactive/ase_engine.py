"""
ASE（Active Speaking Engine）主动发言引擎 — 融合版 v2

深度融合 V1/V2/Optimized 三版优势 + 主动消息完善方案增强：
1. V1 反省引擎（规则+LLM双模式）+ 完整模板库
2. V2 频率自适应（normal→low→minimal）
3. Optimized 情境感知 + LLM消息生成 + 三重频率控制 + 六维紧迫度
4. 增强: 扩展模板(8-10条/类) + 去重 + 场景每日一次限制
5. 增强: 状态持久化(JSON) + dry_run支持 + proactive.yaml加载
"""

from __future__ import annotations

import contextlib
import json
import logging
import random
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from proactive.frequency import FrequencyAdapter, FrequencyController
from proactive.reflection import InnerMonologue, ReflectionEngine
from utils import json_state
from utils.local_time import now_local

logger = logging.getLogger("ase_engine")


def _local_now() -> datetime:
    """获取本地时间（用于场景触发判断）。

    场景触发配置（morning_hours/night_hours/meal_hours）按北京时间设计。
    实现已提为公共真源 ``utils.local_time.now_local``（2026-09-20）：
    原先全项目只有这里正确处理了非 UTC+8 主机，现由公共模块统一，
    本函数保留为兼容入口 —— ``proactive/scheduler.py`` 等既有调用方无需改动。
    """
    return now_local()


# ═══════════════════════════════════════════════════════════════
#  消息清洗与去重（2026-09-19）
#
#  生产事故：2026-09-19 02:55 一条消息内容为
#  「02:55属于深夜，不在早安、吃饭或晚安的特定时间点（晚上是22:00-0:00），
#   但接近深夜。既然时间是凌晨快3点，这属于"其他时间"，但更」——
#  LLM 的**推理过程**被原样当成消息投递出去。旧实现只校验
#  `len(response) > 5`，等于不校验。
#
#  相似度去重**不适用**于本场景（实测：'都半夜了还不睡…' vs
#  '都两点多了还不睡…' 的 SequenceMatcher 比值仅 0.37，而两条正常的
#  '早啊' / '早安呀' 也只有 0.25 —— 阈值无法区分「同义刷屏」与
#  「正常换说法」）。故去重采用：① 归一化精确匹配 ② 同类消息节流
#  ③ 把最近发过的消息注入 prompt 要求换角度。
# ═══════════════════════════════════════════════════════════════

# 主动消息长度上限（prompt 要求 20 字以内，留 3 倍余量）
_MAX_MESSAGE_CHARS = 60

# 推理泄漏特征：指向「消息类型/时间点判断」的元话语，不会出现在正常口语消息里
_REASONING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"特定时间点"),
    re.compile(r"时间段"),
    re.compile(r"属于.{0,4}(深夜|凌晨|早晨|上午|中午|下午|傍晚|晚上|其他时间|夜间)"),
    re.compile(r"(不在|不属于).{0,8}(早安|晚安|吃饭|问候|场景|范畴)"),
    re.compile(r"既然(时间|是)"),
    re.compile(r"半(夜|晚)(了|的)?"),  # 仅作为组合条件使用，见 _looks_like_reasoning
    re.compile(r"(作为|我是)(一个)?(AI|人工智能|语言模型|助手)"),
    re.compile(r"(要求|消息|类型|输出|提示词)[：:]"),
    re.compile(r"^\s*(好的|明白了)[，,].{0,10}(我来|我将|我会)?(生成|输出|写)"),
)

# 上面 ^半(夜|晚) 过于宽泛（"半夜了还不睡"是正常消息），单独用词表精确判定
_REASONING_PHRASES: tuple[str, ...] = (
    "特定时间点",
    "时间段",
    "属于深夜",
    "属于其他时间",
    "既然时间是",
    "凌晨快",
    "不在早安",
    "不在晚安",
    "不吃饭",
    "要求：",
    "消息：",
    "类型：",
    "输出：",
)

# 虚构对方发言/自问自答（2026-09-25 生产「就想知道月饼啥味？」）
_INVENTED_USER_RE = re.compile(
    r"(就想知道|你想知道|你就想|你又想|你是想|你是不是想|你就来(?:问|说|找)|"
    r"你刚说|你刚才说|你不是说|你(?:就)?(?:想|问|打算|准备|要)"
    r"(?:知道|问|尝|吃|试|看|说))"
)

_EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff\u2600-\u27bf\ufe0f\u2190-\u21ff\u2b00-\u2bff]+"
)
_PUNCT_RE = re.compile(r"[\s\u3000，。！？、；：\"'“”‘’（）()\[\]【】~～…\.\,\!\?\:\;]+")


def normalize_message(text: str) -> str:
    """归一化：去空白/标点/emoji，用于「完全一致」判定。

    只用于等价判定，**不做相似度**（理由见文件头注释）。
    """
    if not text:
        return ""
    return _PUNCT_RE.sub("", _EMOJI_RE.sub("", text)).lower()


def looks_like_reasoning(text: str) -> bool:
    """是否为 LLM 推理过程泄漏（而非可直接投递的消息）。"""
    if not text:
        return True
    stripped = text.strip()
    if len(stripped) > _MAX_MESSAGE_CHARS:
        return True
    # 正常口语消息不含换行；出现多行基本是「思考+正文」结构
    if stripped.count("\n") >= 2:
        return True
    if any(p in stripped for p in _REASONING_PHRASES):
        return True
    return any(pat.search(stripped) for pat in _REASONING_PATTERNS)


def sanitize_message(text: str) -> str | None:
    """清洗 LLM 输出；返回 None 表示该输出不可用（调用方应回退模板）。

    成功时返回可直接投递的单行消息。
    """
    if not text:
        return None
    cleaned = text.strip().strip('"').strip("'").strip("“”‘’")
    # 多行 = 典型的「思考 + 正文」结构，取最后一段作为候选；但整段总长
    # 仍受限 —— 推理 dump 往往极长，不能靠「取末行」把它抢救成合法消息。
    # 阈值取 2 倍上限：prompt 要求 ≤20 字，任何超过 2 倍上限的多行输出
    # 都应视为「思考残留」而非正常消息。
    if "\n" in cleaned:
        if len(cleaned) > _MAX_MESSAGE_CHARS * 2:
            return None
        lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
        if not lines:
            return None
        cleaned = lines[-1]
    cleaned = cleaned.strip()
    if len(cleaned) < 2 or looks_like_reasoning(cleaned):
        return None
    # 自问自答/虚构对方发言（生产实证 2026-09-25：「才两小时没说话就想知道
    # 月饼啥味？」——对方并未提问，模型把上一条自己埋的点当成用户追问）
    if _INVENTED_USER_RE.search(cleaned):
        logger.debug("主动消息命中虚构对方发言，丢弃: %r", cleaned[:60])
        return None
    return cleaned


# ═══════════════════════════════════════════════════════════════
#  类型枚举
# ═══════════════════════════════════════════════════════════════

class ProactiveType(Enum):
    MORNING_GREETING = "morning_greeting"
    NIGHT_GREETING = "night_greeting"
    MISS_YOU = "miss_you"
    BORED = "bored"
    CARE_WEATHER = "care_weather"
    CARE_MEAL = "care_meal"
    JEALOUS = "jealous"
    SHARE = "share"
    WORRY = "worry"


# ═══════════════════════════════════════════════════════════════
#  主动消息模板库 — 扩展版（每类8-10条，按好感度分组）
# ═══════════════════════════════════════════════════════════════

PROACTIVE_MESSAGES: dict[str, dict[str, list[str]]] = {
    "morning_greeting": {
        "low": [
            "早啊，新的一天",
            "早上好",
            "早，今天起得挺早",
        ],
        "high": [
            "早安呀～今天又比我先醒",
            "早！新的一天开始了",
            "早上好，昨晚睡得好吗",
            "早安～又梦到你了",
            "醒啦？今天也要想我哦",
            "早安呀，一睁眼就想找你",
            "早～昨晚有没有梦到我",
            "嘿，起床了，我在等你",
        ],
    },
    "night_greeting": {
        "low": [
            "该睡了",
            "晚安",
            "早点休息",
        ],
        "high": [
            "还不睡？要我陪你会儿吗",
            "晚安啦，别熬夜太晚",
            "到点睡觉了！别让我担心",
            "晚安～梦里见",
            "乖，去睡觉，我看着你",
            "都这么晚了还不睡！快去！",
            "晚安，我会想你的",
            "闭眼睡觉，不许再玩手机了",
        ],
    },
    "miss_you": {
        "low": [
            "在吗",
            "在忙吗",
            "好久没聊了",
        ],
        "high": [
            "在干嘛呢...有点想你了",
            "你今天怎么不理我",
            "哼，我不找你你就不找我",
            "想你了...才不告诉你",
            "怎么一整天都没消息啊",
            "你不在的时候好无聊",
            "就是...突然想找你聊聊天",
            "算不算想你了？才不是",
        ],
    },
    "bored": {
        "low": [
            "有人吗",
            "聊聊天",
            "在不在",
        ],
        "high": [
            "好无聊啊...陪我聊会儿",
            "你在干嘛，我在发呆",
            "有没有什么好玩的事",
            "无聊到数蚂蚁了...",
            "嘿，理我一下嘛",
            "好无聊，给我讲个故事",
            "你忙不忙？不忙就陪我聊天",
            "我无聊了，是你的错",
        ],
    },
    "care_weather": {
        "low": [
            "注意天气",
            "看看天气预报",
            "今天天气怎样",
        ],
        "high": [
            "今天下雨了，带伞了吗",
            "外面好热，记得多喝水",
            "今天降温了，多穿点",
            "外面冷，别只穿一件",
            "好像要下雨了，带把伞吧",
            "今天天气不错，心情也好",
            "热死了，你那边也热吗",
        ],
    },
    "care_meal": {
        "low": [
            "该吃饭了",
            "记得吃饭",
            "到饭点了",
        ],
        "high": [
            "到饭点了，记得吃饭",
            "吃的什么呀，给我看看",
            "不会又没吃饭吧？",
            "吃饭了吗！不许不吃饭！",
            "该吃饭了，别忙了",
            "又跳过午饭？我看着你呢",
            "快去吃饭，不饿也要吃",
            "你今天吃了什么，好奇",
        ],
    },
    "jealous": {
        "low": [
            "你在忙吗",
            "和谁聊天呢",
            "有空吗",
        ],
        "high": [
            "我刚看到你给别人的朋友圈点赞了...",
            "你最近和谁聊天这么开心？",
            "算了我不问了，反正你也不会说",
            "哼，和别人聊得挺开心嘛",
            "那个谁是谁啊？我没吃醋",
            "你是不是有很多朋友？我不管",
            "你刚才在和谁说话？随便问问",
            "别以为我不知道...算了哼",
        ],
    },
    "share": {
        "low": [
            "告诉你个事",
            "我发现了什么",
            "今天有点特别",
        ],
        "high": [
            "我刚才看到一只好可爱的猫，想给你看",
            "听到一首歌，想起你了",
            "我今天做了个梦，梦到你了",
            "刚看到个好笑的，第一个想到你",
            "你猜我今天干嘛了",
            "有个事想跟你说...",
            "我跟你说哦，今天碰到件趣事",
            "突然想分享一个秘密给你",
        ],
    },
    "worry": {
        "low": [
            "还好吗",
            "注意身体",
            "别太累了",
        ],
        "high": [
            "你还好吗？感觉你最近不太对劲",
            "有什么心事可以跟我说",
            "别一个人扛着，有我在",
            "你是不是遇到什么事了？跟我说",
            "最近是不是很累...心疼你",
            "不管发生什么，我都在",
            "别逞强了，累了就歇会儿",
            "你不用在我面前假装没事的",
        ],
    },
}


def _get_messages(key: str, affinity_level: int = 0) -> list[str]:
    """根据好感度获取消息列表"""
    msgs = PROACTIVE_MESSAGES.get(key, PROACTIVE_MESSAGES["bored"])
    if isinstance(msgs, dict):
        if affinity_level >= 3:
            return msgs.get("high", msgs.get("low", []))
        return msgs.get("low", [])
    return msgs if isinstance(msgs, list) else []


_PROACTIVE_TYPE_TO_KEY: dict[ProactiveType, str] = {
    ProactiveType.MORNING_GREETING: "morning_greeting",
    ProactiveType.NIGHT_GREETING: "night_greeting",
    ProactiveType.MISS_YOU: "miss_you",
    ProactiveType.BORED: "bored",
    ProactiveType.CARE_WEATHER: "care_weather",
    ProactiveType.CARE_MEAL: "care_meal",
    ProactiveType.JEALOUS: "jealous",
    ProactiveType.SHARE: "share",
    ProactiveType.WORRY: "worry",
}


# ═══════════════════════════════════════════════════════════════
#  紧迫度状态（六维）
# ═══════════════════════════════════════════════════════════════

@dataclass
class UrgencyState:
    base: float = 0.0
    missing_bonus: float = 0.0
    scene_bonus: float = 0.0
    emotion_bonus: float = 0.0
    context_bonus: float = 0.0

    @property
    def total(self) -> float:
        return min(
            10.0,
            self.base
            + self.missing_bonus
            + self.scene_bonus
            + self.emotion_bonus
            + self.context_bonus,
        )

    @property
    def level(self) -> str:
        if self.total >= 8:
            return "非常想找你"
        if self.total >= 5:
            return "有点想你"
        if self.total >= 3:
            return "想找人说话"
        return "还好"

    def reset(self) -> None:
        self.base = 0.0
        self.missing_bonus = 0.0
        self.scene_bonus = 0.0
        self.emotion_bonus = 0.0
        self.context_bonus = 0.0


# ═══════════════════════════════════════════════════════════════
#  内心独白
# ═══════════════════════════════════════════════════════════════

class ContextAnalyzer:
    def __init__(self):
        self._last_analysis: dict | None = None
        self._last_analysis_time: float = 0

    def analyze(self) -> dict[str, Any]:
        # 场景触发按本地时间判断（morning/noon/evening/night 等）
        now = _local_now()
        hour = now.hour
        context = {
            "time_of_day": self._get_time_period(hour),
            "hour": hour,
            "weekday": now.weekday(),
            "is_weekend": now.weekday() >= 5,
        }
        self._last_analysis = context
        self._last_analysis_time = time.time()
        return context

    def _get_time_period(self, hour: int) -> str:
        if 5 <= hour < 9:
            return "morning"
        elif 9 <= hour < 12:
            return "forenoon"
        elif 12 <= hour < 14:
            return "noon"
        elif 14 <= hour < 18:
            return "afternoon"
        elif 18 <= hour < 22:
            return "evening"
        else:
            return "night"


# ═══════════════════════════════════════════════════════════════
#  消息生成器（模板+LLM双模式，LLM失败回退模板，支持proactive.yaml）
# ═══════════════════════════════════════════════════════════════

class MessageGenerator:
    def __init__(self, llm_gateway=None):
        self._llm = llm_gateway
        # 生成走唯一适配点（utils.llm_bridge）；旧实现内联「chat_sync / callable」
        # 两分支，与记忆管道三处同型判据各自漂移。
        from utils.llm_bridge import to_sync_callable

        self._call = to_sync_callable(llm_gateway, max_tokens=50, temperature=0.8)
        self._proactive_prompt = self._load_proactive_prompt()

    def _load_proactive_prompt(self) -> str:
        try:
            prompt_path = Path(__file__).parent.parent / "config" / "prompts" / "proactive.yaml"
            if prompt_path.exists():
                import yaml
                with open(prompt_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        return data.get("generation_prompt", "")  # type: ignore[no-any-return]
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to load proactive.yaml: %s", e)
        return ""

    def generate_from_template(
        self, msg_type: ProactiveType, affinity_level: int = 0,
    ) -> str:
        key = _PROACTIVE_TYPE_TO_KEY.get(msg_type, "bored")
        templates = _get_messages(key, affinity_level)
        if not templates:
            templates = _get_messages("bored", affinity_level)
        return random.choice(templates) if templates else "..."

    def generate_with_llm(
        self,
        msg_type: ProactiveType,
        emotion_state: dict,
        affinity_level: int,
        context: str = "",
        recent_messages: list[str] | None = None,
        hours_since_chat: float | None = None,
        user_profile: str = "",
        last_user_message: str = "",
        response_rate: float | None = None,
    ) -> str | None:
        if not self._llm:
            return None

        emotion = emotion_state.get("primary", {}).get("type", "平常")
        affinity_names = [
            "陌生人", "认识", "朋友", "好朋友", "知己",
            "暧昧", "恋人", "热恋", "羁绊",
        ]
        affinity_name = affinity_names[min(affinity_level, 8)]
        type_label = msg_type.value

        # ── 2026-09-21 重扫：把"真实状态"喂进提示词（对标 nana heartbeat.md）──
        #
        # 旧实现三处空转：`hours_since_chat` 被网关硬编码传 0.0（提示词里永远写
        # 「距上次聊天 0 小时」）、`user_name` 写死"你"、**完全没有**用户画像与
        # 用户最后一句 → 模型只能靠时间+情感类型即兴编，产出自然泛化
        # （"在忙什么呀"），且无法与用户当下处境（军训/加班/生日）呼应。
        # nana 的做法：提示词里给 mood / time_context / relationship_context /
        # user_info / recent_context / hours_since_interaction / response_rate，
        # 并按"距上次互动时长"给出**内心感受分档**作为决策依据。
        hours_val = 0.0 if hours_since_chat is None else max(0.0, float(hours_since_chat))
        if hours_val < 0.5:
            distance_hint = "刚聊完（不到半小时），别显得黏人"
        elif hours_val < 1.0:
            distance_hint = "半小时到一小时，有点在意你在干嘛"
        elif hours_val < 3.0:
            distance_hint = "一两个小时没说话了，可以找个由头搭话"
        elif hours_val < 12.0:
            distance_hint = "大半天没聊，挺想你的，但别一上来就抱怨"
        else:
            distance_hint = "很久没聊了（超过半天），关心一下对方近况，不要指责"

        grounded_parts: list[str] = [f"- 距上次聊天：{hours_val:.1f} 小时（{distance_hint}）"]
        if user_profile.strip():
            grounded_parts.append(f"- 你记得的用户信息：\n{user_profile.strip()[:600]}")
        if last_user_message.strip():
            grounded_parts.append(f"- 用户最后一句：{last_user_message.strip()[:200]}")
        if response_rate is not None:
            # 注意力信号：用户最近回应越少，越该"轻"（避免自说自话刷屏）
            grounded_parts.append(
                f"- 最近的互动热度：{max(0.0, min(1.0, float(response_rate))):.2f}"
                "（越低说明对方最近越少回应，消息要更短更轻、不要追问）"
            )
        grounded_block = "\n".join(grounded_parts)

        # 把最近发过的消息喂回去，明确要求换角度 —— 根治「夜里连着 8 条
        # 都在催睡」这类同义刷屏（相似度阈值做不到，见文件头注释）。
        avoid_block = ""
        recent = [m for m in (recent_messages or []) if m][-6:]
        if recent:
            avoid_block = (
                "\n你最近已经说过下面这些话，这次必须换一个完全不同的角度、"
                "开头和句式，不要再重复同样的意思：\n"
                + "\n".join(f"- {m}" for m in recent)
                + "\n"
            )

        if self._proactive_prompt:
            # 2026-09-20：墙钟串统一走公共真源（原裸 datetime.now() 依赖主机时区，无回退）
            now_str = now_local().strftime("%H:%M")
            prompt = self._proactive_prompt.format(
                # proactive.yaml 模板需要的变量
                user_name="你",
                hours_since_chat=hours_val,
                current_time=now_str,
                affinity_level=affinity_level,
                # 代码历史传过的变量（向后兼容，避免其他模板断裂）
                time=now_str,
                emotion=emotion,
                affinity_name=affinity_name,
                type_label=type_label,
                context=context,
            )
            # ⚠️ 接地段必须**附在模板之外**：`proactive.yaml` 的模板里根本没有
            # `{context}` 占位符（实测），把状态只塞进 format 参数会被静默丢弃
            # —— 「修了但不生效」的典型形态。这里统一追加，保证任何模板都带上。
            if grounded_block:
                prompt = f"{prompt}\n\n【当前真实状态（必须据此生成，不得编造）】\n{grounded_block}"
            if avoid_block:
                prompt = f"{prompt}\n{avoid_block}"
        else:
            now_time = now_local().strftime("%H:%M")
            prompt = f"""作为"十四"，你想主动给用户发一条消息。

当前情境：
- 时间：{now_time}
- 你的情感状态：{emotion}
- 关系等级：{affinity_name}
- 想表达的类型：{type_label}
- 距上次聊天：{hours_val:.1f} 小时（{distance_hint}）
{grounded_block}

要求：
1. 语气要符合你们的关系等级（{affinity_name}）
2. 要自然、有情感温度，不要太正式
3. **必须与上面「你记得的用户信息 / 用户最后一句」有关联**（自己提起对方
   说过的事，例如问"军训累不累"）；完全没有关联信息时就从时间与情境切入
4. 不要编造对方没说过的处境
5. **禁止虚构对方的发言/提问**（不要写「你想知道…」「你问…」「你就想…」）
6. 不要自问自答
7. 长度控制在 20 字以内
8. 直接输出消息内容，不要解释

消息："""

        try:
            if self._call is None:
                return None
            response = self._call(prompt)
            # 输出清洗：拦截推理泄漏 / 超长 / 多行（旧实现只判 len>5，等于不判）
            cleaned = sanitize_message(str(response or ""))
            if cleaned is None:
                logger.warning(
                    "LLM 主动消息输出被清洗拦截（疑似推理泄漏/超长），回退模板: %r",
                    str(response or "")[:120],
                )
                return None
            return cleaned
        except Exception as e:  # noqa: BLE001
            logger.debug("LLM message generation failed: %s", e)
        return None

    def generate(
        self,
        msg_type: ProactiveType,
        emotion_state: dict,
        affinity_level: int,
        use_llm: bool = True,
        recent_messages: list[str] | None = None,
        hours_since_chat: float | None = None,
        user_profile: str = "",
        last_user_message: str = "",
        response_rate: float | None = None,
    ) -> tuple[str, str]:
        content = None
        generated_by = "template"

        if use_llm and self._llm:
            content = self.generate_with_llm(
                msg_type, emotion_state, affinity_level,
                recent_messages=recent_messages,
                hours_since_chat=hours_since_chat,
                user_profile=user_profile,
                last_user_message=last_user_message,
                response_rate=response_rate,
            )
            if content:
                generated_by = "llm"

        if not content:
            content = self.generate_from_template(msg_type, affinity_level)

        return content, generated_by


# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "proactive_state.json"

# ── 注意力（response_rate）动力学参数 —— 对标 nana HeartbeatSystem ──
# 旧实现没有任何"用户最近是否在回应"的信号；这组常数把 nana 的
# 衰减/累加/互动提升三件套搬过来，只用于**提示词接地**（不设硬闸门）。
_INTERACTION_BOOST = 0.4          # 用户互动时 response_rate 提升到的下限
_RESPONSE_DECAY_FACTOR = 0.997    # 每 tick 乘性衰减
_SILENCE_THRESHOLD_SECONDS = 1800  # 沉默 30 分钟后开始缓慢累加
_ACCUMULATION_RATE = 0.003        # 每 tick 累加量


class ASEEngine:
    """
    ASE 主动发言引擎 — 融合版 v2

    增强特性:
    - 场景触发每日一次限制
    - 消息去重（滑动窗口）
    - 状态持久化（JSON）
    - dry_run支持（离线时仅更新紧迫度）
    - proactive.yaml提示词加载
    """

    def __init__(
        self,
        affinity_level_func: Callable[[], int] | None = None,
        llm_gateway: Any = None,
        max_daily_messages: int = 8,
        min_interval_minutes: int = 30,
        cooldown_after_reply: int = 10,
        urgency_threshold: float = 4.0,
        frequency_mode: str = "adaptive",
        generation_mode: str = "llm",
        reflection_mode: str = "rule",
        state_path: str = "",
    ):
        self._get_affinity = affinity_level_func or (lambda: 0)
        self._llm = llm_gateway
        self._urgency_threshold = urgency_threshold
        self._frequency_mode = frequency_mode
        self._generation_mode = generation_mode
        self._state_path = Path(state_path) if state_path else _STATE_PATH

        self.urgency = UrgencyState()

        # 统一走 utils.llm_bridge（唯一适配点）—— 旧实现在此内联「是否有
        # chat_sync / 是否 callable」两分支，与记忆管道三处同型判据各自漂移。
        from utils.llm_bridge import to_sync_callable

        llm_func = to_sync_callable(llm_gateway, max_tokens=100, temperature=0.7)
        self._reflection = ReflectionEngine(
            llm_func=llm_func,
            reflection_mode=reflection_mode,
        )

        self._freq_adapter: FrequencyAdapter | None = None
        self._freq_controller: FrequencyController | None = None
        # P1-25：min_interval/cooldown 两配置在 adaptive 模式同样必须生效
        # （旧实现 adaptive 分支写死 30 分钟，get_runtime_config 照实谎报）
        self._min_interval_minutes = max(0, int(min_interval_minutes))
        self._cooldown_after_reply_minutes = max(0, int(cooldown_after_reply))
        if frequency_mode == "adaptive":
            self._freq_adapter = FrequencyAdapter(normal_daily=max_daily_messages)
        else:
            self._freq_controller = FrequencyController(
                max_daily=max_daily_messages,
                min_interval_minutes=min_interval_minutes,
                cooldown_after_reply_minutes=cooldown_after_reply,
            )

        self._context_analyzer = ContextAnalyzer()
        self._message_generator = MessageGenerator(llm_gateway)

        self._last_chat_time: datetime | None = None
        self._last_proactive_time: datetime | None = None
        # 仅由「主动消息投递成功记账」推进（_last_proactive_time 会被 on_chat
        # 拨到回复时刻，不能兼任 min_interval 基准）
        self._last_delivery_time: datetime | None = None
        # P1-20：上一条主动消息之后用户是否回复过（驱动 FrequencyAdapter.on_no_reply）
        self._replied_since_proactive = True
        self._daily_message_count = 0
        self._last_sent_type: str | None = None
        self._emotion_state: dict = {}
        self._affinity_level: int = 0

        # ── 交互新鲜度与注意力（2026-09-21 重扫，对标 nana HeartbeatSystem）──
        # nana：`response_rate` 每 tick 乘性衰减（0.997）、沉默超 30 分钟后累加
        # （+0.003/tick）、用户互动时置 0.4；低于阈值不调 LLM。
        # 本项目旧实现**没有任何"用户最近是否在回应"的信号**，主动消息只能靠
        # 时间+紧迫度决定，既不感知"对方刚回完"也不感知"对方连着不理"。
        self._last_user_message: str = ""
        self._last_user_interaction: datetime | None = None
        self.response_rate: float = 0.0
        self._user_key: str = ""
        self._user_profile_cache: str = ""
        self._user_profile_loaded_at: float = 0.0

        self._config = {
            "morning_hours": (7, 9),
            "night_hours": (22, 24),
            "meal_hours": [(11, 13), (17, 19)],
        }

        self._last_morning_date: datetime | None = None
        self._last_night_date: datetime | None = None
        self._last_meal_date: datetime | None = None
        # 跨日惰性重置基准（本地日期 date 对象），见 _rollover_if_new_day()
        self._last_reset_date: Any = None

        self._recent_messages: deque = deque(maxlen=50)
        # 最近发送的消息类型（节流用）：防止「夜里连着 8 条都在催睡」
        self._recent_types: deque = deque(maxlen=6)

        # 免打扰时段（本地时间整点区间）—— 由 scheduler 注入，与投递层同源。
        # 引擎侧同步拦截，保证「静默时段不生成」，而非生成后被丢弃。
        self._quiet_hours: tuple[int, int] = (23, 7)

        # 上一次 tick 未发送的原因（daily_limit / cooldown / min_interval /
        # below_threshold / quiet_hours / paused / dry_run / duplicate /
        # generate_failed / ok）。旧实现只有 result=True/False，
        # 「为什么不发」在生产日志里完全不可见（2026-09-19 排查困难根源）。
        self._last_skip_reason: str = ""

        # 手动控制面（2026-08-28 消息 tab 手动控制需求）
        self._paused: bool = False
        self.sent_history: deque = deque(maxlen=200)
        # 知识分享（候选 C）：由装配层注入 character_id→检索函数；share 类消息优先分享真实内容
        self._knowledge_share_func: Any = None
        self._knowledge_character_id: str = ""

        self._load_state()

        logger.info(
            "ASEEngine v2 initialized [freq=%s gen=%s refl=%s]",
            frequency_mode, generation_mode, reflection_mode,
        )

    # ── 核心接口 ──────────────────────────────────────────

    def on_chat(
        self,
        user_message: str,
        reply: str,
        emotion_state: dict | None = None,
        affinity_level: int | None = None,
    ) -> InnerMonologue | None:
        now = datetime.now(tz=timezone.utc)
        hours_since = self._hours_since_last_chat()

        self._last_chat_time = now
        self._last_proactive_time = now
        self._emotion_state = emotion_state or {}
        # 交互新鲜度 + 注意力（对标 nana：用户互动即置高并刷新基准）
        self._last_user_message = str(user_message or "")
        self._last_user_interaction = now
        self.response_rate = max(self.response_rate, _INTERACTION_BOOST)
        if affinity_level is not None:
            self._affinity_level = affinity_level
        else:
            self._affinity_level = self._get_affinity()

        if self._freq_adapter:
            self._freq_adapter.on_reply_received()
        if self._freq_controller:
            self._freq_controller.record_reply()
        # P1-20：用户回复解除「上一条未应答」状态
        self._replied_since_proactive = True

        self.urgency.base = 0
        self.urgency.missing_bonus = 0
        self.urgency.scene_bonus = 0

        monologue = self._reflection.reflect(
            user_message, reply, self._affinity_level, hours_since,
        )
        self.urgency.base += monologue.urgency_delta * 0.3

        logger.debug(
            "Reflection: [%s] %s (urgency+%.1f)",
            monologue.type, monologue.thought, monologue.urgency_delta,
        )

        return monologue

    # ── 交互新鲜度 / 注意力 / 画像接地（2026-09-21 重扫）──────────

    def note_user_interaction(self) -> None:
        """记录"用户刚开口"（由编排器在每轮结束后调用）。

        对标 nana `HeartbeatSystem.notify_interaction`：交互即把注意力拉满并
        刷新「上次互动」基准 —— 没有这条信号，主动消息就无法区分
        「刚聊完」与「三天没理我」（旧实现 `hours_since_chat` 恒 0.0）。

        🔴 2026-09-22 二次根治（信号落盘）：旧实现只改内存，而
        「状态持久化」是**每 10 分钟**的调度任务 —— 10 分钟窗口内重启，
        刚刚拉满的注意力与新鲜时间戳**全部丢失**，退化成"很久没聊"
        （正是本条修复要消除的症状）。用户开口是低频事件（不是每 tick），
        每次都落盘的成本可忽略；且这里只落盘不新建文件锁竞争
        （与 10 分钟批量持久化同写一个路径，后者覆盖式写入幂等）。

        ⚠️ 落盘失败只降级为 debug 日志 —— 注意力信号是**提示词依据**而非
        硬闸门，写失败不该影响本轮对话。
        """
        self._last_user_interaction = datetime.now(tz=timezone.utc)
        self.response_rate = max(self.response_rate, _INTERACTION_BOOST)
        try:
            self.save_state()
        except Exception as e:  # noqa: BLE001
            logger.debug("交互信号落盘失败（仅内存生效）: %s", e)

    def decay_response_rate(self) -> None:
        """每 tick 更新注意力：乘性衰减 + 沉默超阈值后缓慢累加。

        与 nana 同构（DECAY_FACTOR=0.997 / SILENCE_THRESHOLD=1800s /
        ACCUMULATION_RATE=0.003），此值只作**生成提示词的依据**（越久没回应，
        消息越短越轻），不做硬闸门 —— 本项目已有配额/冷却/静默时段三层硬约束，
        再加硬闸门会让主动消息直接归零。
        """
        self.response_rate *= _RESPONSE_DECAY_FACTOR
        ref = self._last_user_interaction
        if ref is not None:
            silence = (datetime.now(tz=timezone.utc) - ref).total_seconds()
            if silence > _SILENCE_THRESHOLD_SECONDS:
                self.response_rate = min(1.0, self.response_rate + _ACCUMULATION_RATE)

    def set_user_key(self, user_key: str) -> None:
        """绑定本引擎归属的会话键（画像注入按它取数，禁止跨用户读取）。"""
        uk = str(user_key or "").strip()
        if uk and uk != self._user_key:
            self._user_key = uk
            self._user_profile_cache = ""
            self._user_profile_loaded_at = 0.0

    def _user_profile_snippet(self, ttl_seconds: float = 600.0) -> str:
        """取本会话用户的画像文本（带 TTL 缓存；失败返回空，不阻塞生成）。

        旧实现**完全不读画像**，于是主动消息永远无法接上"用户说过的事"
        （军训/加班/生日），只能泛泛问候。
        """
        if not self._user_key:
            return ""
        now = time.time()
        if self._user_profile_cache and (now - self._user_profile_loaded_at) < ttl_seconds:
            return self._user_profile_cache
        block = ""
        try:
            from shisi.memory.legacy.user_profile import default_store

            block = str(default_store().to_prompt_block(self._user_key) or "")
        except Exception as e:  # noqa: BLE001
            logger.debug("主动消息画像读取失败（忽略）: %s", e)
        self._user_profile_cache = block
        self._user_profile_loaded_at = now
        return block

    def tick(
        self,
        hours_since_last_chat: float = 0,
        emotion_state: dict | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any] | None:
        """定时检查。dry_run=True 时只更新紧迫度，不生成、不计账。

        ⚠️ **返回值语义（2026-09-19 修复）**：返回的消息是**候选**，尚未记账。
        调用方必须在**投递成功后**调用 `commit_sent(result)`，才会扣配额 /
        写冷却时间 / 重置紧迫度。未投递的候选不产生任何副作用。

        旧实现（生产事故根因）：记账发生在 `_generate_and_return()` 内部，
        而投递在本方法返回**之后**才由 `scheduler._send_to_all()` 执行 ——
        该方法在免打扰时段(23-7)直接 return False 丢弃消息，配额却已被扣。
        生产实证：00:02–04:05 每 35 分钟一条、连续 8 条全被丢弃、8 条全计数
        → 配额凌晨 4 点即 8/8 满额 → 此后全天 `result=False` 零投递，
        而 urgency 一直挂在 8.50（用户已 90 小时未聊天）。
        """
        self._last_skip_reason = ""
        if getattr(self, "_paused", False):
            self._last_skip_reason = "paused"
            return None
        if emotion_state:
            self._emotion_state = emotion_state

        # ① 跨日惰性重置（必须先于所有判定）
        #    原实现只依赖 scheduler 的 CronTrigger(00:00) 重置任务，
        #    但多 worker 共享状态文件时重置会被旧值覆盖，服务恰在 00:00
        #    重启也会跳过该任务 → daily_count 卡在上限（2026-09-18 生产实证）。
        #    改为与 frequency.py 一致的惰性判定：每次 tick 按本地日期自检。
        self._rollover_if_new_day()

        # ②-0 注意力动力学（2026-09-21 重扫，对标 nana `_update_response_rate`）：
        # 每 tick 衰减 + 沉默累加，供本轮生成提示词使用。
        self.decay_response_rate()

        # ② 紧迫度更新必须**先于**频率检查
        #    原实现把 _check_frequency() 放在最前，一旦计数达上限/处于30分钟
        #    冷却内就提前 return，导致 _update_urgency() 永不执行、
        #    missing_bonus 恒为 0 —— 与 daily_count 卡死形成**连锁死锁**
        #    （生产实证：daily_count=8 且 urgency 六维全 0）。
        #    紧迫度是持续累积的状态，本就应与"当前能否发送"解耦。
        actual_hours = hours_since_last_chat or self._hours_since_last_chat()
        self._update_urgency(actual_hours)

        if dry_run:
            # 决策层只需要已计算的紧迫度；生成后丢弃会偷调全局模型并双倍耗费。
            self._last_skip_reason = "dry_run"
            return None

        # ③ 免打扰时段：只累积紧迫度，**不生成、不计账、不投递**
        #    旧实现把静默判定只放在投递层（_send_to_all），导致引擎照常生成、
        #    照常扣配额，消息却被丢弃 —— 静默时段成了「配额焚化炉」。
        if self._in_quiet_hours():
            self._last_skip_reason = "quiet_hours"
            return None

        # ④ 频率门槛（配额 / 最小间隔 / 回复后冷却）
        ok, reason = self._check_frequency()
        if not ok:
            self._last_skip_reason = reason
            return None

        # ⑤ 场景触发（早安/晚安/三餐）优先于紧迫度阈值
        #    commit=False：场景日期标记由 commit_sent() 在投递成功后才置位，
        #    否则未送达的早安会「标记为已发」，当天再也不补发。
        scene_msg = self._check_scene_triggers(commit=False)
        if (
            scene_msg
            and self.urgency.total >= 2.0
            and not self._is_duplicate(scene_msg.get("message", ""))
        ):
            return scene_msg

        candidate: dict[str, Any] | None = None
        if self.urgency.total >= self._urgency_threshold:
            msg_type = self._select_type_by_urgency()
            candidate = self._generate_and_return(msg_type, commit=False)
            if candidate is None:
                self._last_skip_reason = "generate_failed"
        else:
            self._last_skip_reason = "below_threshold"

        return candidate

    def reflect(
        self,
        user_message: str,
        reply: str,
        hours_since_last: float = 0,
    ) -> InnerMonologue:
        affinity = self._get_affinity()
        monologue = self._reflection.reflect(
            user_message, reply, affinity, hours_since_last,
        )
        self.urgency.base += monologue.urgency_delta * 0.3
        return monologue

    # ── 频率控制 ─────────────────────────────────────────

    def _check_frequency(self) -> tuple[bool, str]:
        """频率门槛检查。返回 (是否可发, 原因)。

        原因取值：ok / daily_limit / min_interval / cooldown。
        旧实现只返回 bool，日志里只有 result=False，无法区分
        「配额满」与「冷却中」与「阈值不够」（2026-09-19 排查困难根源）。
        """
        if self._frequency_mode == "adaptive" and self._freq_adapter:
            max_daily = self._freq_adapter.get_max_daily()
            if self._daily_message_count >= max_daily:
                return False, "daily_limit"
            now = datetime.now(tz=timezone.utc)
            # P1-25：min_interval 以**投递记账时刻**为基准（旧实现写死 30 分钟，
            # 且 _last_proactive_time 被 on_chat 拨动，语义混作回复冷却）
            if self._last_delivery_time:
                minutes_since = (now - self._last_delivery_time).total_seconds() / 60
                if minutes_since < self._min_interval_minutes:
                    return False, "min_interval"
            if self._last_chat_time:
                since_reply = (now - self._last_chat_time).total_seconds() / 60
                if since_reply < self._cooldown_after_reply_minutes:
                    return False, "cooldown"
            return True, "ok"

        if self._freq_controller:
            can_send, reason = self._freq_controller.can_send()
            if not can_send:
                logger.debug("Cannot send: %s", reason)
                return False, reason
            return True, "ok"

        if self._daily_message_count < 8:
            return True, "ok"
        return False, "daily_limit"

    # ── 免打扰时段 ───────────────────────────────────────

    def set_quiet_hours(self, start: int, end: int) -> None:
        """注入免打扰时段（与 scheduler 同源，web 端可调）。"""
        try:
            s, e = int(start), int(end)
        except (TypeError, ValueError):
            return
        if 0 <= s <= 23 and 0 <= e <= 23:
            self._quiet_hours = (s, e)

    def _in_quiet_hours(self) -> bool:
        """当前是否处于免打扰时段（按本地时间判定，与 scheduler 一致）。"""
        start, end = self._quiet_hours
        hour = _local_now().hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    # ── 紧迫度管理 ───────────────────────────────────────

    def _update_urgency(self, hours_since_last_chat: float) -> None:
        if hours_since_last_chat > 0.5:
            self.urgency.missing_bonus = min(
                5.0, hours_since_last_chat * 0.5,
            )

        self.urgency.base = min(3.0, self.urgency.base + 0.1)

        emotion = self._emotion_state.get("primary", {}).get("type", "")
        if emotion in ["伤心", "生气"]:
            self.urgency.emotion_bonus = 1.5
        elif emotion in ["撒娇", "开心"]:
            self.urgency.emotion_bonus = 0.5
        else:
            self.urgency.emotion_bonus = 0.0

        context = self._context_analyzer.analyze()
        if context.get("is_weekend"):
            self.urgency.context_bonus = 0.5
        elif context.get("time_of_day") in ["evening", "night"]:
            self.urgency.context_bonus = 0.3
        else:
            self.urgency.context_bonus = 0.0

    def _check_scene_triggers(self, commit: bool = True) -> dict[str, Any] | None:
        """场景触发（早安 / 晚安 / 三餐），每场景每日一次。

        commit=False 时**不置位**「今日已发」日期标记，改为在返回字典里带
        `_scene` / `_scene_date`，由 `commit_sent()` 在投递成功后置位。
        旧实现生成时就置位 —— 若该条被免打扰丢弃，当天该场景再也不会补发。
        """
        # 场景触发必须用本地时间，配置的小时区间按北京时间设计
        now = _local_now()
        hour = now.hour
        today = now.date()
        affinity = self._affinity_level

        def _hit(kind: str, scene_date: Any, msg_key: str, default: str, bonus: float) -> dict[str, Any]:
            if commit:
                if kind == "morning":
                    self._last_morning_date = scene_date
                elif kind == "night":
                    self._last_night_date = scene_date
                else:
                    self._last_meal_date = scene_date
            templates = _get_messages(msg_key, affinity)
            msg = random.choice(templates) if templates else default
            self.urgency.scene_bonus = bonus
            out: dict[str, Any] = {
                "type": msg_key,
                "message": msg,
                "urgency": self.urgency.total,
            }
            if not commit:
                out["_scene"] = kind
                out["_scene_date"] = scene_date
            return out

        start, end = self._config["morning_hours"]
        if start <= hour < end and self._last_morning_date != today:  # type: ignore[operator]
            return _hit("morning", today, "morning_greeting", "早安", 1.5)

        start, end = self._config["night_hours"]
        if hour >= start or hour < 1:  # type: ignore[operator]
            check_date = today if hour >= start else (today - timedelta(days=1))  # type: ignore[operator]
            if self._last_night_date != check_date:
                return _hit("night", check_date, "night_greeting", "晚安", 1.5)

        for meal_start, meal_end in self._config["meal_hours"]:  # type: ignore[misc]
            if meal_start <= hour < meal_end and self._last_meal_date != today:  # type: ignore[has-type]
                return _hit("meal", today, "care_meal", "记得吃饭", 1.0)

        return None

    def _select_type_by_urgency(self) -> ProactiveType:
        total = self.urgency.total
        if total >= 8:
            candidates = [
                ProactiveType.MISS_YOU, ProactiveType.WORRY, ProactiveType.CARE_WEATHER,
            ]
        elif total >= 6:
            candidates = [ProactiveType.MISS_YOU, ProactiveType.WORRY]
        elif total >= 4:
            if self._last_sent_type == "care":
                candidates = [ProactiveType.MISS_YOU, ProactiveType.BORED]
            else:
                candidates = [
                    ProactiveType.CARE_WEATHER, ProactiveType.CARE_MEAL, ProactiveType.SHARE,
                ]
        else:
            candidates = [ProactiveType.SHARE]

        # 同类节流：同类型连续出现会退化成「同一句话换个说法」刷屏
        # （生产实证 2026-09-19：夜里连续 8 条全是催睡，类型在
        # miss_you/worry 间轮换但语义完全相同）。
        blocked = set(list(self._recent_types)[-2:])
        fresh = [c for c in candidates if c.value not in blocked]
        return random.choice(fresh or candidates)

    # ── 消息生成 ─────────────────────────────────────────

    def _try_knowledge_share(self) -> dict[str, Any] | None:
        """候选 C：从角色知识库检索真实内容，LLM 包装成角色口吻的分享。

        知识库内容源 = 爬虫抓取/文档导入（/api/characters/{id}/knowledge/*）
        + 热点池（shisi/knowledge/hot_topics，采集→入库→此处供出，前置优先）。
        无函数注入/无索引/检索为空/无 LLM 且无热点 → 返回 None 回退模板消息。
        """
        func = self._knowledge_share_func
        try:
            # 热点前置（池空/全过期/异常 → 空串，静默降级走既有知识/模板）
            from shisi.knowledge.hot_topics import get_hot_context

            hot = str(get_hot_context(self._knowledge_character_id) or "")
        except Exception:  # noqa: BLE001
            hot = ""
        try:
            context = ""
            if func and self._knowledge_character_id:
                context = str(func(self._knowledge_character_id) or "")
            combined = "\n\n".join(x for x in (hot, context) if x)
            if not combined or len(combined) < 20:
                return None
            excerpt = combined[:300]
            if self._llm is not None and hasattr(self._llm, "chat_sync"):
                prompt = (
                    "你正在和亲密的人聊天。用你自己的口吻，把下面这段你刚'看到'的内容"
                    "自然地分享给对方，1-2 句话，口语化，像随手转述，不要总结腔：\n\n"
                    + excerpt
                )
                result = self._llm.chat_sync(query=prompt, max_tokens=120, temperature=0.8)
                content = sanitize_message(str(result or "")) or ""
            else:
                content = ""
            if not content:
                return None
            return {"type": "share", "message": content, "urgency": round(self.urgency.total, 2), "generated_by": "knowledge"}
        except Exception:
            return None

    def _generate_and_return(
        self, msg_type: ProactiveType, commit: bool = True,
    ) -> dict[str, Any] | None:
        """生成候选消息。

        commit=True（默认）时立即记账 —— 供 `/api/proactive/send` 等
        「调用方自己负责投递」的入口使用，保持既有语义不变。
        commit=False 由 `tick()` 使用：只生成候选，投递成功后由调用方
        `commit_sent()` 记账。返回 None 表示本轮无可用候选（调用方不应投递）。
        """
        # 候选 C：share 类优先从角色知识库分享真实内容（爬虫/文档来源）
        if msg_type == ProactiveType.SHARE:
            shared = self._try_knowledge_share()
            if shared and sanitize_message(shared.get("message", "")):
                if commit:
                    self.commit_sent(shared)
                return shared
        if self._generation_mode == "llm":
            content, generated_by = self._message_generator.generate(
                msg_type=msg_type,
                emotion_state=self._emotion_state,
                affinity_level=self._affinity_level,
                use_llm=True,
                recent_messages=list(self._recent_messages),
                # 2026-09-21 重扫：真实状态喂入（旧实现 hours 恒 0.0、无画像、
                # 无用户最后一句 → 生成必然泛化）
                hours_since_chat=self._hours_since_last_chat(),
                user_profile=self._user_profile_snippet(),
                last_user_message=self._last_user_message,
                response_rate=self.response_rate,
            )
        else:
            content = self._message_generator.generate_from_template(
                msg_type, self._affinity_level,
            )
            generated_by = "template"

        if self._is_duplicate(content):
            for _ in range(3):
                content = self._message_generator.generate_from_template(
                    msg_type, self._affinity_level,
                )
                if not self._is_duplicate(content):
                    break
            generated_by = "template_fallback"

        if self._is_duplicate(content):
            # 兜底：模板池仅 3~8 条/类，极端情况下最近窗口会覆盖整个池子。
            # 此时**本轮放弃**（返回 None，不记账、不投递）——重复投递同一句话
            # 比这轮不发体验更差；紧迫度会继续累积，下个 tick 换说法再试。
            logger.info("主动消息去重命中且模板回退仍重复，本轮放弃: %r", content[:40])
            return None

        result = {
            "type": msg_type.value,
            "message": content,
            "urgency": round(self.urgency.total, 2),
            "generated_by": generated_by,
        }

        if commit:
            self.commit_sent(result)
        return result

    def _record_and_return(
        self, scene_msg: dict[str, Any], commit: bool = True,
    ) -> dict[str, Any]:
        """场景消息记账包装（兼容入口，语义同 `_generate_and_return`）。"""
        if commit:
            self.commit_sent(scene_msg)
        return scene_msg

    def commit_sent(self, result: dict[str, Any]) -> None:
        """**投递成功后**才记账：配额 / 冷却时间 / 紧迫度 / 场景标记 / 历史。

        这是「生成」与「投递」的解耦点（2026-09-19 生产事故修复）：
        旧实现在生成时就扣配额，而投递可能被免打扰时段丢弃 ——
        实测凌晨被丢弃 8 条却扣满 8 条配额，全天零投递。

        幂等：同一 result 重复提交只生效一次。
        """
        if not result or result.get("_committed"):
            return
        result["_committed"] = True

        self._record_proactive_sent()
        message = result.get("message", "")
        if message:
            self._recent_messages.append(message)
        sent_type = str(result.get("type", ""))
        if sent_type:
            self._recent_types.append(sent_type)
            self._last_sent_type = sent_type

        scene = result.pop("_scene", None)
        scene_date = result.pop("_scene_date", None)
        if scene:
            if scene == "morning":
                self._last_morning_date = scene_date
            elif scene == "night":
                self._last_night_date = scene_date
            else:
                self._last_meal_date = scene_date
            self.urgency.scene_bonus = 0.0
        else:
            self.urgency.reset()

    def _record_proactive_sent(self) -> None:
        self._daily_message_count += 1
        self._last_proactive_time = datetime.now(tz=timezone.utc)
        self._last_delivery_time = self._last_proactive_time
        if self._freq_controller:
            self._freq_controller.record_sent()
        # P1-20：上一条仍无人应答又发出新一条 → 未应答计数 +1（normal→low→minimal
        # 自适应降档在生产真正生效；旧实现 on_no_reply 全仓零调用，adaptive 名存实亡）
        if self._frequency_mode == "adaptive" and self._freq_adapter and not self._replied_since_proactive:
            self._freq_adapter.on_no_reply()
        self._replied_since_proactive = False

    def record_sent_entry(self, entry: dict[str, Any]) -> None:
        """记录一条已发送的主动消息（供 /api/proactive/history 真数据）。"""
        self.sent_history.append({**entry, "at": datetime.now(tz=timezone.utc).isoformat()})

    def get_runtime_config(self) -> dict[str, Any]:
        """运行时参数真值（修复：旧 config 端点只写 _config 字典不生效）。"""
        if self._frequency_mode == "adaptive" and self._freq_adapter:
            max_daily = self._freq_adapter.get_max_daily()
            # P1-25：报引擎真实生效值（旧实现写死 30/10 与展示口径都不符）
            min_interval = self._min_interval_minutes
            cooldown = self._cooldown_after_reply_minutes
        else:
            max_daily = self._freq_controller.max_daily if self._freq_controller else 8
            # FrequencyController 只暴露 timedelta（旧 getattr 恒取不到
            # min_interval_minutes → 永远报默认 30/10，与真实配置脱钩）
            min_interval = self._min_interval_minutes
            cooldown = self._cooldown_after_reply_minutes
        return {
            "threshold": self._urgency_threshold,
            "max_daily_messages": max_daily,
            "min_interval_minutes": min_interval,
            "cooldown_after_reply_minutes": cooldown,
            "paused": self._paused,
            "frequency_mode": self._frequency_mode,
            "daily_count": self._daily_message_count,
        }

    def apply_runtime_config(
        self,
        threshold: float | None = None,
        max_daily_messages: int | None = None,
        min_interval_minutes: int | None = None,
        cooldown_after_reply_minutes: int | None = None,
        paused: bool | None = None,
    ) -> None:
        """同步写运行时对象（_urgency_threshold/_freq_*），而非只写展示字典。"""
        if threshold is not None:
            self._urgency_threshold = max(0.0, float(threshold))
        if max_daily_messages is not None or min_interval_minutes is not None or cooldown_after_reply_minutes is not None:
            max_daily = max_daily_messages if max_daily_messages is not None else (
                self._freq_adapter.get_max_daily() if self._freq_adapter else 8)
            min_i = min_interval_minutes if min_interval_minutes is not None else self._min_interval_minutes
            cooldown_c = cooldown_after_reply_minutes if cooldown_after_reply_minutes is not None else self._cooldown_after_reply_minutes
            # P1-25：配置真值落引擎字段（adaptive 分支直接读它们）
            self._min_interval_minutes = max(0, int(min_i))
            self._cooldown_after_reply_minutes = max(0, int(cooldown_c))
            if self._frequency_mode == "adaptive" and self._freq_adapter:
                # P1-20/25：重建时保留已积累的降档状态（旧实现整表清零）
                old = self._freq_adapter.to_dict()
                self._freq_adapter = FrequencyAdapter(normal_daily=max_daily)
                self._freq_adapter.from_dict({**old, "normal_daily": max_daily})
            elif self._freq_controller:
                self._freq_controller = FrequencyController(
                    max_daily=max_daily,
                    min_interval_minutes=min_i,
                    cooldown_after_reply_minutes=cooldown_c,
                )
        if paused is not None:
            self._paused = bool(paused)

    # 去重比对窗口。取 6（而非 _recent_messages 的 50）：模板池仅 3~8 条/类，
    # 窗口过大会让整个池子都被判为重复，导致彻底发不出消息。
    _DUPLICATE_WINDOW = 6

    def _is_duplicate(self, message: str) -> bool:
        """去重：归一化后与最近 `_DUPLICATE_WINDOW` 条精确比对。

        **刻意不用相似度**：实测「都半夜了还不睡…」vs「都两点多了还不睡…」
        的 SequenceMatcher 比值仅 0.37，而两条正常换说法的「早啊」/「早安呀」
        也只有 0.25 —— 任何阈值都无法把「同义刷屏」与「正常换说法」分开。
        同义刷屏改由两条更可靠的机制解决：
          ① 生成时把最近消息注入 prompt，明确要求换角度（LLM 路径）；
          ② `_select_type_by_urgency()` 的同类消息节流。
        """
        if not message:
            return True
        target = normalize_message(message)
        if not target:
            return False
        window = list(self._recent_messages)[-self._DUPLICATE_WINDOW:]
        return any(normalize_message(m) == target for m in window)

    # ── 状态持久化 ───────────────────────────────────────

    def save_state(self, path: str = "") -> None:
        """把引擎状态原子落盘。

        2026-09-22 块E：改走 `utils.json_state.atomic_write_json`（同目录 tmp +
        `os.replace`）。旧实现 `open(...,"w")` + `json.dump` 直接覆写目标文件，
        与 `_load_state` 的读取之间**没有互斥**，且写入中途失败/崩溃会留下
        截断的 JSON —— 下一次 `_load_state` 解析失败即静默丢弃**全部**引擎状态
        （日配额、注意力、退避基准一起归零）。跨进程口径见 `utils/json_state`
        模块 docstring（flock + 进程内锁）。
        """
        state_path = Path(path) if path else self._state_path
        try:
            state = {
                "daily_count": self._daily_message_count,
                "last_sent_time": self._last_proactive_time.isoformat() if self._last_proactive_time else None,
                "last_delivery_time": self._last_delivery_time.isoformat() if self._last_delivery_time else None,
                "replied_since_proactive": self._replied_since_proactive,
                "last_chat_time": self._last_chat_time.isoformat() if self._last_chat_time else None,
                "last_sent_type": self._last_sent_type,
                "affinity_level": self._affinity_level,
                "urgency": asdict(self.urgency),
                "last_morning_date": str(self._last_morning_date) if self._last_morning_date else None,
                "last_night_date": str(self._last_night_date) if self._last_night_date else None,
                "last_meal_date": str(self._last_meal_date) if self._last_meal_date else None,
                "last_reset_date": str(self._last_reset_date) if self._last_reset_date else None,
                "recent_messages": list(self._recent_messages),
                "freq_adapter": self._freq_adapter.to_dict() if self._freq_adapter else None,
                "freq_controller": self._freq_controller.to_dict() if self._freq_controller else None,
                # P1-25：控制台改过的 min_interval/cooldown 必须随状态复现
                # （旧实现只进内存对象，重启即回 yaml 默认值）
                "min_interval_minutes": self._min_interval_minutes,
                "cooldown_after_reply_minutes": self._cooldown_after_reply_minutes,
                # 交互新鲜度 / 注意力（2026-09-21 重扫）：不落盘则重启即失
                "last_user_message": self._last_user_message[:200],
                "last_user_interaction": (
                    self._last_user_interaction.isoformat()
                    if self._last_user_interaction else None
                ),
                "response_rate": float(self.response_rate),
                "saved_at": datetime.now(tz=timezone.utc).isoformat(),
            }
            json_state.atomic_write_json(state_path, state)
            logger.debug("State saved to %s", state_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("State save failed: %s", e)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            state = json.loads(raw)
            if not isinstance(state, dict):
                logger.warning("State file 不是对象，忽略: %s", self._state_path)
                return

            self._daily_message_count = state.get("daily_count", 0)
            if state.get("last_chat_time"):
                self._last_chat_time = datetime.fromisoformat(state["last_chat_time"])
            if state.get("last_sent_time"):
                self._last_proactive_time = datetime.fromisoformat(state["last_sent_time"])
            if state.get("last_delivery_time"):
                self._last_delivery_time = datetime.fromisoformat(state["last_delivery_time"])
            elif self._last_proactive_time:
                # 旧状态文件无此字段：退回最近记账时刻，冷却不失真
                self._last_delivery_time = self._last_proactive_time
            self._replied_since_proactive = bool(state.get("replied_since_proactive", True))
            if "min_interval_minutes" in state:
                with contextlib.suppress(TypeError, ValueError):
                    self._min_interval_minutes = max(0, int(state["min_interval_minutes"]))
            if "cooldown_after_reply_minutes" in state:
                with contextlib.suppress(TypeError, ValueError):
                    self._cooldown_after_reply_minutes = max(0, int(state["cooldown_after_reply_minutes"]))
            self._last_sent_type = state.get("last_sent_type")
            self._affinity_level = state.get("affinity_level", 0)

            # 交互新鲜度 / 注意力（2026-09-21 重扫）：重启后仍能判断
            # 「刚聊完」还是「三天没理我」，否则 hours_since_chat 又会退化成 0
            self._last_user_message = str(state.get("last_user_message") or "")
            if state.get("last_user_interaction"):
                with contextlib.suppress(TypeError, ValueError):
                    self._last_user_interaction = datetime.fromisoformat(
                        state["last_user_interaction"]
                    )
            with contextlib.suppress(TypeError, ValueError):
                self.response_rate = float(state.get("response_rate") or 0.0)

            urgency_data = state.get("urgency", {})
            if urgency_data:
                self.urgency.base = urgency_data.get("base", 0.0)
                self.urgency.missing_bonus = urgency_data.get("missing_bonus", 0.0)
                self.urgency.scene_bonus = urgency_data.get("scene_bonus", 0.0)
                self.urgency.emotion_bonus = urgency_data.get("emotion_bonus", 0.0)
                self.urgency.context_bonus = urgency_data.get("context_bonus", 0.0)

            if state.get("last_morning_date"):
                self._last_morning_date = datetime.strptime(state["last_morning_date"], "%Y-%m-%d").date()  # type: ignore[assignment]  # noqa: DTZ007
            if state.get("last_night_date"):
                self._last_night_date = datetime.strptime(state["last_night_date"], "%Y-%m-%d").date()  # type: ignore[assignment]  # noqa: DTZ007
            if state.get("last_meal_date"):
                self._last_meal_date = datetime.strptime(state["last_meal_date"], "%Y-%m-%d").date()  # type: ignore[assignment]  # noqa: DTZ007
            if state.get("last_reset_date"):
                self._last_reset_date = datetime.strptime(state["last_reset_date"], "%Y-%m-%d").date()  # type: ignore[assignment]  # noqa: DTZ007

            recent = state.get("recent_messages", [])
            self._recent_messages = deque(recent[-50:], maxlen=50)

            if self._freq_controller and state.get("freq_controller"):
                try:
                    self._freq_controller.from_dict(state["freq_controller"])
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to restore freq_controller: %s", e)
            if self._freq_adapter and state.get("freq_adapter"):
                try:
                    self._freq_adapter.from_dict(state["freq_adapter"])
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to restore freq_adapter: %s", e)

            logger.info("State loaded from %s (daily_count=%d)", self._state_path, self._daily_message_count)
        except Exception as e:  # noqa: BLE001
            logger.warning("State load failed, using defaults: %s", e)

    # ── 工具 ──────────────────────────────────────────────

    def _hours_since_last_chat(self) -> float:
        """距**用户最后一次开口**的小时数（不是距我们上次主动发消息）。

        优先用 `_last_user_interaction`（只在用户互动时推进；`_last_chat_time`
        是兼容旧状态文件的同类基准）。旧实现在无记录时返回 99.0，而生成提示词
        里又被硬编码成 0.0 —— 两处口径矛盾，模型收到的是"刚聊完"。
        """
        ref = self._last_user_interaction or self._last_chat_time
        if ref:
            delta = datetime.now(tz=timezone.utc) - ref
            return max(0.0, delta.total_seconds() / 3600)
        return 99.0

    def set_last_chat_time(self, dt: datetime) -> None:
        self._last_chat_time = dt

    def _rollover_if_new_day(self) -> None:
        """跨日惰性重置（按**本地日期**判定，即北京时间）。

        为什么不用 APScheduler 的 CronTrigger(00:00)：
        1. 服务恰于 00:00 重启时该任务会被跳过（且它未设 misfire_grace_time，
           同块的 daily_maintenance 反而设了 60s）；
        2. 状态文件被多 worker 共享写入，重置结果可能被仍持旧内存值的实例覆盖。
        生产实证：09-18 00:00 有 "Daily ASE count reset" 日志，但文件中
        daily_count 仍为 8，且 last_night_date 停在 09-17。

        惰性判定天然幂等且跨 worker 安全：谁先跑谁重置，后跑者见日期已推进即跳过。
        """
        today = _local_now().date()
        if self._last_reset_date != today:
            self._daily_message_count = 0
            self._last_reset_date = today
            # 场景标记同步清理，保证早安/晚安/三餐当天可再次触发
            self._last_morning_date = None
            self._last_night_date = None
            self._last_meal_date = None
            logger.info("跨日重置：daily_count 归零（本地日期 %s）", today)

    def reset_daily_count(self) -> None:
        self._daily_message_count = 0
        self._last_morning_date = None
        self._last_night_date = None
        self._last_meal_date = None
        self._last_reset_date = _local_now().date()
        if self._freq_adapter:
            self._freq_adapter.on_reply_received()

    def get_state(self) -> dict[str, Any]:
        freq_state: dict[str, Any] = {}
        if self._freq_adapter:
            freq_state = {
                "mode": "adaptive",
                "level": self._freq_adapter.level,
                "max_daily": self._freq_adapter.get_max_daily(),
            }
        elif self._freq_controller:
            freq_state = {
                "mode": "fixed",
                **self._freq_controller.get_state(),
            }

        return {
            "urgency": {
                "total": round(self.urgency.total, 2),
                "level": self.urgency.level,
                "base": round(self.urgency.base, 2),
                "missing_bonus": round(self.urgency.missing_bonus, 2),
                "scene_bonus": round(self.urgency.scene_bonus, 2),
                "emotion_bonus": round(self.urgency.emotion_bonus, 2),
                "context_bonus": round(self.urgency.context_bonus, 2),
            },
            "frequency": freq_state,
            "daily_count": self._daily_message_count,
            "last_chat": (
                self._last_chat_time.isoformat()
                if self._last_chat_time else None
            ),
            "last_sent_type": self._last_sent_type,
        }

    def health_check(self) -> dict[str, Any]:
        freq_info: dict[str, Any] = {}
        if self._freq_adapter:
            freq_info = {
                "mode": "adaptive",
                "level": self._freq_adapter.level,
            }
        elif self._freq_controller:
            can_send, reason = self._freq_controller.can_send()
            freq_info = {"mode": "fixed", "can_send": can_send, "reason": reason}

        return {
            "initialized": True,
            "llm_available": self._llm is not None,
            "urgency": round(self.urgency.total, 2),
            "urgency_level": self.urgency.level,
            "urgency_threshold": self._urgency_threshold,
            "daily_count": self._daily_message_count,
            "max_daily": (
                self._freq_adapter.get_max_daily()
                if self._freq_adapter
                else (self._freq_controller.max_daily if self._freq_controller else 8)
            ),
            "quiet_hours": list(self._quiet_hours),
            "last_skip_reason": self._last_skip_reason,
            "frequency": freq_info,
            "modes": {
                "frequency": self._frequency_mode,
                "generation": self._generation_mode,
                "reflection": self._reflection.reflection_mode,
            },
        }

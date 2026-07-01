"""
角色卡数据清洗与规范化工具

前后端共用（frontend 通过 API 消费后端已清洗的数据），
主要处理从 SillyTavern 等工具导出的嵌套 JSON 以及文件名/名称中的噪声。
"""
from __future__ import annotations

import re
from typing import Any


def sanitize_character_name(name: str) -> str:
    """清洗角色名称中的文件扩展名、人设后缀、作者署名、时间戳、描述性括号等噪声。"""
    if not isinstance(name, str):
        name = str(name) if name is not None else ""

    # 1. 去掉首尾空白与 .json 扩展名
    cleaned = name.strip().removesuffix(".json").strip()

    # 2. 去掉常见元数据前缀
    cleaned = re.sub(r"^[Pp]ersona_\s*", "", cleaned)
    cleaned = re.sub(r"^[（(]人设[)）]\s*", "", cleaned)

    # 3. 去掉括号/全角括号里的作者、定制、by 等标记
    cleaned = re.sub(r"[（(][^）)]*(?:by|BY|定制|作者|著)[^）)]*[）)]", "", cleaned)
    # 再去掉剩余纯元数据的括号块（如 (1)、（正常）、(2)、(学校)）
    cleaned = re.sub(r"[（(]\s*\d+\s*[）)]", "", cleaned)
    cleaned = re.sub(r"[（(]\s*(?:正常|学校|女友|旅游|定制|by|BY|作者|著)\s*[）)]", "", cleaned)
    # 去掉描述性/签名性括号块（如 （慢热毒舌直球抽象社牛）、(听得见)、(绘梨衣的世界地图心愿)）
    # 策略：括号内全为中文且长度 >= 2，或包含已知签名/作者关键词
    cleaned = re.sub(
        r"[（(][^）)]{2,}?[）)]",
        "",
        cleaned,
    )
    # 再次清理已知签名词（可能不在括号内或残留）
    cleaned = re.sub(r"听得见|银子著|银子|BY诗|by诗", "", cleaned, flags=re.IGNORECASE)

    # 4. 去掉尾部作者署名，如 -银子著、_听得见、_作者xxx
    cleaned = re.sub(r"[-_–—]\s*(?:作者|著|by|BY|银子|听得见|诗)\S*$", "", cleaned, flags=re.IGNORECASE)

    # 5. 去掉尾部时间戳 _1774701604527
    cleaned = re.sub(r"[_-]\d{13,15}$", "", cleaned)

    # 6. 去掉首尾装饰标点（如 ： 伊蕾娜·艾斯特莱雅 ·）
    cleaned = re.sub(r"^[\s：:·•\-–—_]+", "", cleaned)
    cleaned = re.sub(r"[\s：:·•\-–—_]+$", "", cleaned)

    # 7. 清理多余空格、下划线、连接号
    cleaned = re.sub(r"[\s_–—]+", " ", cleaned).strip()

    return cleaned or name.strip().removesuffix(".json").strip() or "未命名角色"


# 常见作者/署名/定制标记词
_AUTHOR_KEYWORDS = [
    "by", "BY", "定制", "作者", "著", "听得见", "银子", "银子著", "BY诗", "by诗",
]


def sanitize_character_text(text: str) -> str:
    """清洗角色卡文本字段中的作者署名、定制标记等噪声。

    用于 description、creator_notes、scenario、first_mes、personality_text 等字段。
    """
    if not isinstance(text, str):
        text = str(text) if text is not None else ""

    # 1. 移除括号/全角括号内的作者、定制、by 等标记
    text = re.sub(r"[（(][^）)]*(?:by|BY|定制|作者|著|听得见|银子|BY诗|by诗)[^）)]*[）)]", "", text)
    # 2. 移除尾部署名，如 （作者：听得见）、-银子著、_听得见、 by诗 等
    text = re.sub(r"[-_–—\s]*(?:作者|著|by|BY|银子|听得见|诗)\S*$", "", text, flags=re.IGNORECASE)
    # 3. 清理残留的常见署名关键词
    pattern = "|".join(re.escape(k) for k in _AUTHOR_KEYWORDS)
    text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    # 4. 清理因移除产生的多余空格与空括号
    text = re.sub(r"\(\s*\)|（\s*）", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return text


def _extract_nested_data(data: dict[str, Any]) -> dict[str, Any] | None:
    """从 SillyTavern / Chara Card 导出格式中提取实际人设数据块。"""
    # 格式 A: { "data": { "prompts": { "...": { "data": { name, description... } } } } }
    top_data = data.get("data")
    if isinstance(top_data, dict):
        prompts = top_data.get("prompts")
        if isinstance(prompts, dict):
            for prompt_block in prompts.values():
                if isinstance(prompt_block, dict):
                    nested = prompt_block.get("data")
                    if isinstance(nested, dict) and nested.get("name"):
                        return nested
        # 格式 B: { "data": { "name": "...", ... } }
        if isinstance(top_data, dict) and top_data.get("name"):
            return top_data
    return None


def _safe_float_dict(value: Any) -> dict[str, float] | None:
    """如果 value 是字典且值可转为 float，返回 float 字典。"""
    if not isinstance(value, dict):
        return None
    result: dict[str, float] = {}
    for k, v in value.items():
        try:
            result[str(k)] = float(v)
        except (TypeError, ValueError):
            return None
    return result


def normalize_character_card(data: dict[str, Any]) -> dict[str, Any]:
    """把各种来源的角色卡统一展平为前端/后端都能理解的结构。"""
    if not isinstance(data, dict):
        data = {}

    src = _extract_nested_data(data) or data
    top = data

    # 基础字段
    raw_name = src.get("name") or top.get("name") or ""
    name = sanitize_character_name(raw_name)

    description = sanitize_character_text(
        src.get("description")
        or top.get("description")
        or src.get("creator_notes")
        or top.get("creator_notes")
        or ""
    )

    creator_notes = sanitize_character_text(src.get("creator_notes") or top.get("creator_notes") or "")
    # 如果 creator_notes 已经被用作 description，避免重复输出
    if creator_notes == description:
        creator_notes = ""

    scenario = sanitize_character_text(src.get("scenario") or top.get("scenario") or src.get("world_scenario") or "")

    first_mes = sanitize_character_text(src.get("first_mes") or top.get("first_mes") or "")

    # 性格维度（优先字典，退化成文本）
    personality = src.get("personality") or top.get("personality") or {}
    personality_text = ""
    if isinstance(personality, str):
        personality_text = sanitize_character_text(personality)
        personality = {}
    else:
        parsed = _safe_float_dict(personality)
        if parsed is None and personality:
            personality_text = sanitize_character_text(str(personality))
            personality = {}
        else:
            personality = parsed or {}

    # 说话风格
    speaking_style = src.get("speaking_style") or top.get("speaking_style") or {}
    speaking_style_text = ""
    catchphrases: list[str] = []
    if isinstance(speaking_style, str):
        speaking_style_text = sanitize_character_text(speaking_style)
        speaking_style = {}
    elif isinstance(speaking_style, dict):
        catchphrases = speaking_style.get("catchphrases") or []
        # 如果其余值不是可量化数字，保留原文本
        numeric = _safe_float_dict({k: v for k, v in speaking_style.items() if k != "catchphrases"})
        if numeric is None and speaking_style:
            speaking_style_text = sanitize_character_text(str(speaking_style))
            speaking_style = {}
        else:
            speaking_style = numeric or {}
    if not catchphrases:
        catchphrases = src.get("catchphrases") or top.get("catchphrases") or []
    if isinstance(catchphrases, str):
        catchphrases = [catchphrases]
    catchphrases = [sanitize_character_text(str(c)) for c in catchphrases if c][:8]

    # 核心锚点：优先 core_anchors，其次 tags，最后从 personality/description 中拆关键词
    core_anchors = src.get("core_anchors") or top.get("core_anchors") or []
    if not core_anchors:
        tags = src.get("tags") or top.get("tags") or []
        if isinstance(tags, list):
            core_anchors = [str(t) for t in tags if t][:8]
    if not core_anchors and personality_text:
        # 用常见分隔符拆性格文本
        for sep in ["、", "，", ",", "；", ";", " "]:
            if sep in personality_text:
                parts = [p.strip() for p in personality_text.split(sep) if len(p.strip()) > 1]
                if len(parts) >= 2:
                    core_anchors = parts[:8]
                    break
    if isinstance(core_anchors, str):
        core_anchors = [core_anchors]
    core_anchors = [sanitize_character_text(str(a)) for a in core_anchors if a][:8]

    normalized: dict[str, Any] = {
        **data,
        "name": name,
        "description": description,
        "creator_notes": creator_notes,
        "personality": personality,
        "personality_text": personality_text,
        "speaking_style": speaking_style,
        "speaking_style_text": speaking_style_text,
        "catchphrases": catchphrases,
        "core_anchors": core_anchors,
        "scenario": scenario,
        "first_mes": first_mes,
    }

    # 清理临时空字段，保持响应干净
    if not creator_notes:
        normalized.pop("creator_notes", None)
    if not personality_text:
        normalized.pop("personality_text", None)
    if not speaking_style_text:
        normalized.pop("speaking_style_text", None)
    if not catchphrases:
        normalized.pop("catchphrases", None)
    if not scenario:
        normalized.pop("scenario", None)
    if not first_mes:
        normalized.pop("first_mes", None)

    return normalized

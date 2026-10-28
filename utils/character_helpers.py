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


# 常见作者/署名/定制标记词（残留清理用）
# ⚠️ P1-审查 item37：裸 "by"/"BY" **不在**此列 —— 旧实现把它当无约束子串删除，
# baby/standby/abyss 等英文词连同正文一起被抠掉，且 activate 循环把清洗结果
# 写回磁盘后损坏不可逆（角色卡真源被污染）。by 类署名只由第 1/2 步的
# 「括号内」「词尾锚定 $」模式处理，那两处有明确边界、不会误伤。
_AUTHOR_KEYWORDS = [
    "定制", "作者", "著", "听得见", "银子", "银子著", "BY诗", "by诗",
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


# ── 行为蒸馏（CSP 移植 · 2026-10-28）──────────────────────────
#
# 行为蒸馏 = 从角色卡里提炼「这个角色遇到 X 会怎么做」（trigger → behavior（why））
# 与「不知道就不编」的诚实边界。三件事必须同时成立，否则本段就是净负担：
#   1. **卡上看到的 = 模型看到的**：字段上限与渲染上限是同一套常量，写卡侧的
#      生成 prompt 也声明同一套数字。不做「渲染时按预算淘汰条目」——那会让
#      「卡上有 7 条、模型只看到 2 条」，属于静默丢内容。
#   2. **整段硬上限 ≤ 400 字符**：本段位于对话历史之前（# 性格 之后），约束力
#      低于 post-history 位的 creator_notes，堆规则只会加剧指令稀释；宁可
#      每条短，也不要把 prompt 撑长。
#   3. **边界行前置且豁免**：honesty_boundary 排在条目之前、永不被条目挤掉。
#
# 单条最坏形态 "- {t} → {b}（{w}）" = 2 + 12 + 3 + 16 + 1 + 6 + 1 = 41 字符；
# 7 条 + 段头(21) + 护栏(12) + 边界行(3+60) + 9 个换行 = 392 ≤ 400。
# 改动任一常量都必须重算这个式子（tests/test_character_behavior_distillation.py
# 的满长用例会先把超预算变成红灯）。
MAX_BEHAVIOR_PATTERNS = 7
MAX_BEHAVIOR_TRIGGER_CHARS = 12
MAX_BEHAVIOR_TEXT_CHARS = 16
MAX_BEHAVIOR_WHY_CHARS = 6
HONESTY_BOUNDARY_MAX_CHARS = 60
BEHAVIOR_SECTION_MAX_CHARS = 400

# 段头**中性**：本仓角色卡不只有女性角色（42 卡中有男性卡），写死「她」会把
# 男性角色说成女性；同时不引入第二人称称谓，避免与 utils/address_terms.py 的
# 称呼白名单（2026-10-15 称呼根治批）打架。
BEHAVIOR_SECTION_HEADER = "# 行为规则（一贯反应倾向，不是台词模板）"
BEHAVIOR_SECTION_GUARDRAIL = "只判断怎么做，不要念出来"
BEHAVIOR_BOUNDARY_PREFIX = "边界："

# 行内文本的极简清洗（本批只做「不把标签/脚本带进 prompt」这一层；
# 完整的 XSS/注入闸归 shisi/character/validator.py，那是另一个 owner）。
_INLINE_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
_INLINE_UNSAFE_RE = re.compile(
    r"<\s*/?\s*script[^>]*>|<\s*/?\s*iframe[^>]*>|javascript\s*:|on\w+\s*=",
    re.IGNORECASE,
)
_INLINE_TRAILING_RE = re.compile(r"[\s，,、；;：:。\.\-—]+$")


def _coerce_inline_text(value: Any) -> str:
    """把任意标量/容器摊成一行可读文本（**禁止** repr 进 prompt）。

    list/tuple → 非空项用「；」连接；dict → 取 values 用「；」连接；
    None → 空串；其余标量 → str()。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return "；".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return "；".join(str(item).strip() for item in value.values() if str(item).strip())
    return str(value)


def _clean_inline(value: Any, limit: int) -> str:
    """单行文本清洗：控符折空格 → 去标签/脚本残片 → 折叠空白 → 截断到 limit。"""
    text = _coerce_inline_text(value)
    text = _INLINE_CONTROL_RE.sub(" ", text)
    text = _INLINE_UNSAFE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit > 0 and len(text) > limit:
        text = _INLINE_TRAILING_RE.sub("", text[:limit].rstrip())
    return text


def sanitize_honesty_boundary(raw: Any) -> str:
    """诚实边界 → 单行短文本（≤ HONESTY_BOUNDARY_MAX_CHARS）。

    非字符串输入**不得**变成 repr：`["不知道天气","不编造行程"]` 必须是
    「不知道天气；不编造行程」，不能是 `['不知道天气', '不编造行程']`。
    """
    if raw is None or raw == "" or raw == [] or raw == {}:
        return ""
    return _clean_inline(raw, HONESTY_BOUNDARY_MAX_CHARS)


def _behavior_pattern_from_string(value: Any) -> dict[str, str] | None:
    """字符串条目 → {trigger, behavior, why}。

    切分规格（写死，避免各路径各切一套）：
    - 分隔符「→」，`->` 等价；
    - 无分隔符 → 整串当 behavior；
    - 恰 2 段 → trigger、behavior；
    - ≥3 段 → trigger=第 1 段、behavior=第 2 段、why=其余段用「；」连接
      （丢掉多余箭头但**保留全部文字**）；
    - behavior 为必需项，清洗后为空则整条丢弃。
    """
    text = _coerce_inline_text(value)
    if not text.strip():
        return None
    parts = [part.strip() for part in text.replace("->", "→").split("→")]
    if len(parts) == 1:
        trigger, behavior, rest = "", parts[0], []
    elif len(parts) == 2:
        trigger, behavior, rest = parts[0], parts[1], []
    else:
        trigger, behavior, rest = parts[0], parts[1], parts[2:]
    why = "；".join(part for part in rest if part)
    trigger_clean = _clean_inline(trigger, MAX_BEHAVIOR_TRIGGER_CHARS)
    behavior_clean = _clean_inline(behavior, MAX_BEHAVIOR_TEXT_CHARS)
    why_clean = _clean_inline(why, MAX_BEHAVIOR_WHY_CHARS)
    if not behavior_clean:
        return None
    return {"trigger": trigger_clean, "behavior": behavior_clean, "why": why_clean}


def _behavior_pattern_from_dict(value: dict[str, Any]) -> dict[str, str] | None:
    """dict 条目只认 trigger/behavior/why 三个键，**绝不**再按「→」切分。"""
    behavior_clean = _clean_inline(value.get("behavior"), MAX_BEHAVIOR_TEXT_CHARS)
    if not behavior_clean:
        return None
    return {
        "trigger": _clean_inline(value.get("trigger"), MAX_BEHAVIOR_TRIGGER_CHARS),
        "behavior": behavior_clean,
        "why": _clean_inline(value.get("why"), MAX_BEHAVIOR_WHY_CHARS),
    }


def normalize_behavior_patterns(raw: Any) -> list[dict[str, str]]:
    """任意形态的行为蒸馏条目 → 规范化列表（≤ MAX_BEHAVIOR_PATTERNS 条）。

    接受：单条 dict、dict 列表、字符串（多行 = 多条）、`{"patterns": [...]}`。
    归一化幂等：`normalize(normalize(x)) == normalize(x)`。
    空/非法 → `[]`（调用方据此「不落盘空键、不渲染空段」）。
    """
    if raw is None or raw == "" or raw == [] or raw == {}:
        return []

    items: list[Any]
    if isinstance(raw, dict):
        nested = next(
            (raw[key] for key in ("patterns", "rules", "items")
             if isinstance(raw.get(key), (list, tuple))),
            None,
        )
        items = list(nested) if nested is not None else [raw]
    elif isinstance(raw, str):
        items = [line for line in raw.splitlines() if line.strip()]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [raw]

    out: list[dict[str, str]] = []
    for item in items:
        pattern = (
            _behavior_pattern_from_dict(item)
            if isinstance(item, dict)
            else _behavior_pattern_from_string(item)
        )
        if pattern:
            out.append(pattern)
        if len(out) >= MAX_BEHAVIOR_PATTERNS:
            break
    return out


def _format_behavior_line(pattern: dict[str, str]) -> str:
    trigger = pattern.get("trigger", "")
    behavior = pattern.get("behavior", "")
    why = pattern.get("why", "")
    if trigger and why:
        return f"- {trigger} → {behavior}（{why}）"
    if trigger:
        return f"- {trigger} → {behavior}"
    if why:
        return f"- {behavior}（{why}）"
    return f"- {behavior}"


def render_behavior_section(
    patterns: Any = None, honesty_boundary: Any = "",
) -> str:
    """渲染「# 行为规则」段（整段 ≤ BEHAVIOR_SECTION_MAX_CHARS，无内容返回空串）。

    返回的字符串**不含**段前空行（由调用方按 parts 习惯补），不含段后空行。
    段内顺序：段头 → 护栏 → 边界（前置且豁免）→ 条目。
    """
    items = normalize_behavior_patterns(patterns)
    boundary = sanitize_honesty_boundary(honesty_boundary)
    if not items and not boundary:
        return ""

    lines = [BEHAVIOR_SECTION_HEADER, BEHAVIOR_SECTION_GUARDRAIL]
    if boundary:
        lines.append(f"{BEHAVIOR_BOUNDARY_PREFIX}{boundary}")
    lines.extend(_format_behavior_line(item) for item in items)

    section = "\n".join(lines)
    if len(section) <= BEHAVIOR_SECTION_MAX_CHARS:
        return section

    # 防御路径：常量为误改时按行收口（段头/护栏/边界先占位，永不被条目挤掉）。
    # 正常路径由上面的字段上限保证不可达 —— 满长 7 条用例锁死这一点。
    head_len = 2 + (1 if boundary else 0)
    kept = lines[:head_len]
    for extra in lines[head_len:]:
        if len("\n".join([*kept, extra])) > BEHAVIOR_SECTION_MAX_CHARS:
            break
        kept.append(extra)
    return "\n".join(kept)


def _as_float(value: Any) -> float | None:
    """可转 float 则返回值，否则 None（None 表示「这不是数值维度」）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_float_dict(value: Any) -> dict[str, float] | None:
    """如果 value 是字典且值可转为 float，返回 float 字典。"""
    if not isinstance(value, dict):
        return None
    result: dict[str, float] = {}
    for k, v in value.items():
        f = _as_float(v)
        if f is None:
            return None
        result[str(k)] = f
    return result


def _split_numeric_mapping(value: Any) -> tuple[dict[str, float], list[str]]:
    """把混合字典拆成「数值维度」与「非数值散文片段」。

    旧实现 `_safe_float_dict` 一旦遇到任何非数值值就整块退化成 `str(dict)`（Python
    repr 入 prompt），并把同块里的合法数值一起丢掉。这里按值逐条分流：数值保留为
    维度，非数值转成人可读的片段，两边都不丢。
    """
    if isinstance(value, str):
        return {}, [value] if value.strip() else []
    if not isinstance(value, dict):
        return {}, [str(value)] if str(value).strip() else []
    numeric: dict[str, float] = {}
    prose: list[str] = []
    for k, v in value.items():
        f = _as_float(v)
        if f is None:
            if isinstance(v, (list, tuple)):
                prose.extend(str(item) for item in v if str(item).strip())
            elif v not in (None, ""):
                prose.append(f"{k}：{v}")
        else:
            numeric[str(k)] = f
    return numeric, prose


def _flatten_text_prose(parts: list[Any]) -> str:
    """把散文片段拼成一段文本（去重、保序）。"""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        text = str(part).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return "，".join(out)


def _make_prompts_writer(
    data: dict[str, Any],
    top_data: dict[str, Any],
    prompts: dict[str, Any],
    key: str,
    block: dict[str, Any],
    nested: dict[str, Any],
):
    """格式 A 的写回器（独立函数而非循环内闭包：避免晚绑定读到下一个 block）。"""

    def _sync(canonical: dict[str, Any]) -> Any:
        merged = {**nested, **canonical}
        new_prompts = {**prompts, key: {**block, "data": merged}}
        return {**data, "data": {**top_data, "prompts": new_prompts}}

    return _sync


def _resolve_source(data: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    """定位人设数据所在块，并返回「把规范值同步回原位」的写回器。

    支持：
    - 扁平卡（现役 41 卡）：块 = 顶层，写回器 = None
    - 格式 A `{data: {prompts: {k: {data: {...}}}}}`
    - 格式 B `{data: {...}}`
    """
    top_data = data.get("data")
    if isinstance(top_data, dict):
        prompts = top_data.get("prompts")
        if isinstance(prompts, dict):
            for key, block in prompts.items():
                if isinstance(block, dict):
                    nested = block.get("data")
                    if isinstance(nested, dict) and nested.get("name"):
                        return nested, _make_prompts_writer(
                            data, top_data, prompts, key, block, nested
                        )
        if top_data.get("name"):
            def _sync_b(canonical: dict[str, Any]) -> Any:
                return {**data, "data": {**top_data, **canonical}}
            return top_data, _sync_b
    return data, None


def _pick(src: dict[str, Any], top: dict[str, Any], key: str) -> Any:
    """取字段：**顶层扁平值优先**，缺失才回退嵌套块。

    优先级契约（2026-09-27 W8）：编辑与保存路径（`api/routers/character_routes.py`
    的 PUT /characters/{id} 与 PUT /characters/{id}/persona）写的都是顶层扁平字段，
    因此顶层是权威编辑位；嵌套块仅在顶层缺失该字段时供导入取值，并被同步为同值，
    避免出现「嵌套旧值覆盖扁平新值」的编辑丢失。
    """
    value = top.get(key)
    if value not in (None, "", {}, []):
        return value
    return src.get(key)


def _normalize_numeric_field(
    raw: Any, explicit_text: str,
) -> tuple[dict[str, float], str]:
    """数值维度字段 → (数值字典, 应作为文本保留的散文)。

    散文为空时回退显式文本；两者都有时把散文并入显式文本，任一侧都不丢弃。
    """
    if raw in (None, "", {}, []):
        return {}, explicit_text
    numeric, prose = _split_numeric_mapping(raw)
    merged = sanitize_character_text(_flatten_text_prose([explicit_text, *prose]))
    return numeric, merged or explicit_text


def normalize_character_card(data: dict[str, Any]) -> dict[str, Any]:
    """把各种来源的角色卡统一展平为**无损且幂等**的规范 DTO。

    不变式（2026-09-27 W8 根治，改前必读）：
    1. **无损**：磁盘/入参上已有的字段（`personality_text`、`speaking_style_text`、
       `mes_example`、数值维度、未知扩展键）只会被补全或规范化，**不会被重算删除**。
       数值 `personality` 与长文本 `personality_text` 是**并存**的两件事，
       不是二选一（现役 41 卡中 40 卡同时具备）。
    2. **幂等**：`normalize(normalize(x)) == normalize(x)`。
    3. **优先级**：顶层扁平字段 > 嵌套块；嵌套块在输出里被同步为同一份规范值，
       未知扩展键原样保留。
    4. **行为蒸馏两字段（2026-10-28）**：`behavior_patterns` 走
       `normalize_behavior_patterns`（字符串按「→」切分、dict 只取三键、上限 7 条、
       单字段截断），`honesty_boundary` 走 `sanitize_honesty_boundary`（非字符串
       摊成「；」连接，**不得** repr 进 prompt）。规范化后为空则在下方清理循环里
       整个键 pop —— 「缺省/空数组不落盘空段」的落点就在这里。
    """
    if not isinstance(data, dict):
        data = {}

    src, sync = _resolve_source(data)
    top = data

    # 基础字段
    raw_name = _pick(src, top, "name") or ""
    name = sanitize_character_name(str(raw_name))

    description = sanitize_character_text(
        _pick(src, top, "description") or _pick(src, top, "creator_notes") or ""
    )

    creator_notes = sanitize_character_text(_pick(src, top, "creator_notes"))
    # 如果 creator_notes 已经被用作 description，避免重复输出
    if creator_notes == description:
        creator_notes = ""

    scenario = sanitize_character_text(
        _pick(src, top, "scenario") or _pick(src, top, "world_scenario")
    )

    first_mes = sanitize_character_text(_pick(src, top, "first_mes"))
    mes_example = str(_pick(src, top, "mes_example") or "").strip()

    # 性格：显式长文本与数值维度并存（重算并置空文本 = 41 卡 40 卡性格段消失的根因）
    explicit_personality_text = sanitize_character_text(_pick(src, top, "personality_text"))
    personality, personality_text = _normalize_numeric_field(
        _pick(src, top, "personality"), explicit_personality_text
    )

    # 说话风格：同上，文本与数值并存
    explicit_style_text = sanitize_character_text(_pick(src, top, "speaking_style_text"))
    raw_style = _pick(src, top, "speaking_style")
    catchphrases: list[Any] = []
    style_for_numeric: Any = raw_style
    if isinstance(raw_style, dict):
        catchphrases = list(raw_style.get("catchphrases") or [])
        style_for_numeric = {k: v for k, v in raw_style.items() if k != "catchphrases"}
    speaking_style, speaking_style_text = _normalize_numeric_field(
        style_for_numeric, explicit_style_text
    )

    if not catchphrases:
        catchphrases = _pick(src, top, "catchphrases") or []
    if isinstance(catchphrases, str):
        catchphrases = [catchphrases]
    catchphrases = [sanitize_character_text(str(c)) for c in catchphrases if c][:8]

    # 核心锚点：优先 core_anchors，其次 tags，最后从 personality/description 中拆关键词
    core_anchors = _pick(src, top, "core_anchors") or _pick(src, top, "tags") or []
    if isinstance(core_anchors, str):
        core_anchors = [core_anchors]
    core_anchors = [sanitize_character_text(str(a)) for a in core_anchors if a][:8]
    if not core_anchors and personality_text:
        # 用常见分隔符拆性格文本
        for sep in ["、", "，", ",", "；", ";", " "]:
            if sep in personality_text:
                parts = [p.strip() for p in personality_text.split(sep) if len(p.strip()) > 1]
                if len(parts) >= 2:
                    core_anchors = [sanitize_character_text(p) for p in parts[:8]]
                    break

    canonical: dict[str, Any] = {
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
        "mes_example": mes_example,
        # 行为蒸馏（2026-10-28 CSP 移植）：与 scenario/personality_text 同一条
        # 「顶层优先、嵌套回退、无损幂等」路径。清洗规格在各自 docstring 里。
        "behavior_patterns": normalize_behavior_patterns(
            _pick(src, top, "behavior_patterns")
        ),
        "honesty_boundary": sanitize_honesty_boundary(
            _pick(src, top, "honesty_boundary")
        ),
    }

    # 嵌套块同步为同一份规范值：否则下一次读取仍取到旧值 = 编辑静默丢失。
    # 规范值始终另落顶层（顶层 = 权威编辑位）。
    base = sync(dict(canonical)) if sync is not None else {**top}
    normalized: dict[str, Any] = {**base, **canonical}

    # 清理临时空字段，保持响应干净（只清**规范化后确实为空**的可选字段，
    # 且逐字段以规范值为准，不再影响任何有内容的字段）
    for key in ("creator_notes", "personality_text", "speaking_style_text",
                "catchphrases", "scenario", "first_mes", "mes_example",
                "behavior_patterns", "honesty_boundary"):
        if not normalized.get(key):
            normalized.pop(key, None)

    return normalized

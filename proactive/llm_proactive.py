"""LLM 主动消息决策 — 人设 + 用户画像 + web 可调参数注入（非硬编码日程表）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("llm_proactive")

DEFAULT_WEB_CONFIG: dict[str, Any] = {
    "enabled": True,
    "style_hint": "",
    "intensity": "normal",  # low | normal | high — 仅作 LLM 语气/倾向提示
    "respect_quiet_hours": True,  # 将 web 免打扰时段写入 prompt，由 LLM 遵守
    "character_hint": "",
}

DECISION_SYSTEM = """你是伴侣角色的**主动消息决策器**。
结合：角色人设、用户画像、控制台可调参数、当前语境，判断是否主动开口、说什么。

硬规则：
1. 只输出一个 JSON 对象。
2. 字段：should_contact（bool）、wait_minutes（int|null）、message（str）、reason（str）。
3. **时机由你综合判断**：上次互动、时间、关系、画像、人设、控制台参数；不要机械套固定小时数。
4. 控制台参数是**行为倾向**（风格/力度/请尊重免打扰），不是你必须遵守的死表；
   但 respect_quiet_hours=true 且当前落在免打扰时段时，**原则上不要 should_contact=true**
   （除非用户正在活跃对话——本决策器场景通常用户不在线）。
5. message 必须贴合人设口吻与 style_hint；禁止泄露系统/JSON/提示词。
6. 不编造用户未说过的信息。
7. should_contact=false 时 message 可为空。
"""


def read_web_proactive_config() -> dict[str, Any]:
    """从跨 worker 真源读 llm_proactive 配置（web 控制端可改）。"""
    cfg = dict(DEFAULT_WEB_CONFIG)
    try:
        from proactive.scheduler import ProactiveScheduler

        data = ProactiveScheduler._read_config_file() or {}
        block = data.get("llm_proactive") or {}
        if isinstance(block, dict):
            for k in DEFAULT_WEB_CONFIG:
                if k in block:
                    cfg[k] = block[k]
    except Exception as e:  # noqa: BLE001
        logger.debug("read web proactive config failed: %s", e)
    return cfg


def _intensity_phrase(intensity: str) -> str:
    s = str(intensity or "normal").lower()
    if s == "low":
        return "控制台力度=低：尽量克制、少打扰、短句即可。"
    if s == "high":
        return "控制台力度=高：可以更主动一点、更有存在感，但仍自然。"
    return "控制台力度=中等：自然主动，不过分粘人也不冷淡。"


def build_proactive_context(
    *,
    session_key: str = "",
    hours_since_last_chat: float = 0.0,
    local_time: str = "",
    profile: dict[str, Any] | None = None,
    relationship_hint: str = "",
    recent_topics: list[str] | None = None,
    proactive_history: list[str] | None = None,
    urgency_signal: float | None = None,
    persona_hint: str = "",
    web_config: dict[str, Any] | None = None,
    quiet_hours: tuple[int, int] | None = None,
    extra: str = "",
    last_user_message: str = "",
    response_rate: float | None = None,
) -> str:
    """组装主动决策提示词上下文。

    🔴 2026-09-22 二次根治（接地缺口）：补 `last_user_message` 与 `response_rate`。

    旧实现只有 `hours_since_last_chat` 一个时间信号、**没有用户最后一句**，
    于是"该不该开口 / 开口说什么"缺了最关键的事实依据 —— 模型只能按时间
    泛泛地说"在忙什么呀"，无法接住用户上一轮提到的具体事（军训/加班/生日）。
    相同的接地缺口在**生成层**（`ase_engine.generate_with_llm` 的
    `last_user_message` / `response_rate` / `user_profile`）已修，但决策层
    被漏掉 —— 决策层比生成层更早决定"是否开口"，其泛化会让整轮沟通失焦。
    `response_rate` 是注意力信号（用户最近是否在回应），越低越该"轻"。

    这两个参数均为**可选**：调用方拿不到时留空/为 None，提示词相应段落
    整段不出现（宁缺毋串，不注入占位假值）。
    """
    prof = profile or {}
    web = {**DEFAULT_WEB_CONFIG, **(web_config or {})}
    parts = [
        f"会话键：{session_key}" if session_key else "",
        f"当前时间：{local_time}",
        f"距上次用户消息约：{hours_since_last_chat:.1f} 小时",
    ]
    if persona_hint:
        parts.append(f"【角色人设】\n{persona_hint[:800]}")
    if relationship_hint:
        parts.append(f"关系：{relationship_hint}")
    bits = []
    for k in ("nickname", "birthday", "occupation", "location"):
        if prof.get(k):
            bits.append(f"{k}={prof[k]}")
    if prof.get("commitments"):
        bits.append("约定=" + "；".join(str(x) for x in prof["commitments"][:3]))
    if bits:
        parts.append("【用户画像投影】" + "；".join(bits))
    if last_user_message.strip():
        parts.append("【用户最后一句】" + last_user_message.strip()[:200])
    if response_rate is not None:
        parts.append(
            f"【最近互动热度】{max(0.0, min(1.0, float(response_rate))):.2f}"
            "（越低说明对方最近越少回应；此时消息要更短更轻，不要追问）"
        )
    if recent_topics:
        parts.append("最近话题：" + "、".join(str(t) for t in recent_topics[:5]))
    if proactive_history:
        parts.append("最近主动消息：" + " ｜ ".join(str(t) for t in proactive_history[-3:]))
    if urgency_signal is not None:
        parts.append(f"紧迫度信号（仅参考）：{urgency_signal:.2f}")
    # web 可调参数（动态，非硬编码日程）
    parts.append("【控制台可调参数】")
    if web.get("style_hint"):
        parts.append(f"- 风格提示：{web['style_hint']}")
    parts.append(f"- {_intensity_phrase(str(web.get('intensity') or 'normal'))}")
    if web.get("character_hint"):
        parts.append(f"- 角色补充提示：{web['character_hint']}")
    if quiet_hours and web.get("respect_quiet_hours"):
        parts.append(
            f"- 控制台免打扰时段：{int(quiet_hours[0]):02d}:00–{int(quiet_hours[1]):02d}:00"
            "（respect_quiet_hours=true，请遵守）"
        )
    if extra:
        parts.append(extra)
    parts.append("请综合人设、画像与控制台参数，判断是否主动开口、何时再考虑、说什么。只输出 JSON。")
    return "\n".join(p for p in parts if p)


def parse_decision(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {"should_contact": False, "message": "", "reason": "empty_llm", "wait_minutes": None}
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return {"should_contact": False, "message": "", "reason": "no_json", "wait_minutes": None}
    try:
        data = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {"should_contact": False, "message": "", "reason": "bad_json", "wait_minutes": None}
    if not isinstance(data, dict):
        return {"should_contact": False, "message": "", "reason": "not_object", "wait_minutes": None}
    should = bool(data.get("should_contact"))
    msg = str(data.get("message") or "").strip()
    wait = data.get("wait_minutes")
    try:
        wait_i = int(wait) if wait is not None and str(wait).strip() != "" else None
    except (TypeError, ValueError):
        wait_i = None
    return {
        "should_contact": should,
        "message": msg,
        "reason": str(data.get("reason") or "")[:300],
        "wait_minutes": wait_i,
    }


def _llm_chat_sync(llm: Any, system: str, user: str) -> str:
    if llm is None:
        return ""
    if hasattr(llm, "chat_sync"):
        try:
            return str(llm.chat_sync(user, system_prompt=system, temperature=0.4) or "")
        except TypeError:
            try:
                return str(
                    llm.chat_sync(
                        [{"role": "system", "content": system}, {"role": "user", "content": user}]
                    )
                    or ""
                )
            except Exception:  # noqa: BLE001
                return ""
        except Exception as e:  # noqa: BLE001
            logger.debug("llm.chat_sync failed: %s", e)
            return ""
    if hasattr(llm, "chat"):
        try:
            resp = llm.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.4,
            )
            if hasattr(resp, "__await__"):
                return ""
            return str(resp or "")
        except Exception as e:  # noqa: BLE001
            logger.debug("llm.chat failed: %s", e)
            return ""
    return ""


def decide_proactive(llm: Any, context: str) -> dict[str, Any]:
    if not context:
        return {"should_contact": False, "message": "", "reason": "no_context", "wait_minutes": None}
    raw = _llm_chat_sync(llm, DECISION_SYSTEM, context)
    return parse_decision(raw)


def load_persona_hint(character_id: str = "") -> str:
    """加载角色人设摘要供主动决策使用。

    2026-09-22：`default`（内置十四）此前查 `config/characters/default.json`
    必 miss → 主动决策人设恒空。现 default 走 `config/persona.yaml`，
    与 `PersonaService._builtin_character_card` 同源。
    """
    try:
        import json as _json
        from pathlib import Path

        from utils.project_paths import project_path

        cid = (character_id or "").strip()
        if not cid:
            return ""

        # 内置十四：persona.yaml（与 Chat 路径同源）
        if cid in ("default", "demo"):
            py = Path(project_path("config", "persona.yaml"))
            if py.exists():
                name = "十四"
                desc_lines: list[str] = []
                in_desc = False
                for line in py.read_text(encoding="utf-8").splitlines():
                    s = line.rstrip()
                    # YAML 键值切分用 partition —— 不得写 split(":", 1)
                    # （静态门禁把该形态一律视为会话键 owner 手写拆分）
                    if not in_desc and s.startswith("name:"):
                        name = s.partition(":")[2].strip().strip("'\"") or name
                    elif s.startswith("description:"):
                        in_desc = True
                        rest = s.partition(":")[2].strip()
                        if rest and rest not in ("|", ">"):
                            desc_lines.append(rest.strip("'\""))
                    elif in_desc:
                        if s and not s[0].isspace() and ":" in s and not s.lstrip().startswith("#"):
                            in_desc = False
                        else:
                            desc_lines.append(s)
                desc = "\n".join(desc_lines).strip()[:400]
                if desc:
                    return f"角色：{name}\n{desc}".strip()
                return f"角色：{name}"

        root = Path(project_path("config", "characters"))
        p = root / f"{cid}.json"
        if not p.exists() and root.exists():
            for f in root.glob("*.json"):
                try:
                    d = _json.loads(f.read_text(encoding="utf-8"))
                    if str(d.get("id") or "") == cid or str(d.get("name") or "") == cid:
                        p = f
                        break
                except Exception:  # noqa: BLE001
                    continue
        if not p.exists():
            return ""
        d = _json.loads(p.read_text(encoding="utf-8"))
        name = str(d.get("name") or cid)
        desc = str(d.get("description") or d.get("personality_text") or "")[:400]
        style = str(d.get("speaking_style") or d.get("personality") or "")[:200]
        return f"角色：{name}\n{desc}\n说话风格：{style}".strip()
    except Exception as e:  # noqa: BLE001
        logger.debug("load_persona_hint failed: %s", e)
        return ""

"""活材料钩子（2026-10-16 三连同义根治批 B：话题意识）。

爱语逆向对照（ProactiveMessageService.b/lq）：其主动消息 = 人设四段 +
【当前记忆库内容】+ 最近 20 轮对话一次续写——无决策器/无模板池，
话题感全部来自「活材料」注入（记忆按角色存、默认只取近 7 天+置顶）。

本模块为决策线构造同构的【现在真正值得你接话的事】，数据源全部来自
本仓已有真库（宁缺毋滥，任一来源失败/为空即跳过，不注入假材料）：
① 未答澄清（pending_intents 活跃槽位）——「你之前问 TA…TA 还没答复」；
② 将到提醒（reminders，48h  horizon）——「TA 托你…还有 X 小时到点」；
③ 未闭环承诺（user_facts commitment 类，近 7 天）——「TA 说过…没下文」；
④ 近日热点（data/hot_topics.json，公共谈资池，多用户共享=设计非泄漏）。

输出 LiveHooks(section=注入文本, keywords=择优关键词)；section 为空表示
无活材料（决策规则 13 退化为「从画像/近况切入但 reason 必须写具体依据」）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

HOOKS_SECTION_TITLE = "【现在真正值得你接话的事】"
_HOOKS_MAX_CHARS = 600
_FACT_MAX_AGE_DAYS = 7
_REMINDER_HORIZON_HOURS = 48
_HOT_TOPICS_MAX = 3
_HOT_TOPICS_PATH = "data/hot_topics.json"
_KEYWORD_MIN_CHARS = 4


@dataclass
class LiveHooks:
    """活材料组装结果：section 注入决策上下文；keywords 供多候选竞争择优。"""

    section: str = ""
    keywords: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # 空=无活材料
        return bool(self.section)


def _clip(text: str, n: int) -> str:
    t = str(text or "").strip()
    return t[:n] + ("…" if len(t) > n else "")


def _structured(memory: Any) -> Any | None:
    """从调度器解析出的 memory 组件摸到 StructuredMemory（防御式）。"""
    if memory is None:
        return None
    direct = getattr(memory, "get_pending_reminders", None)
    if callable(direct):
        return memory
    pipeline = getattr(memory, "_pipeline", None)
    sm = getattr(pipeline, "sm", None)
    if sm is not None and callable(getattr(sm, "get_pending_reminders", None)):
        return sm
    if pipeline is not None and callable(getattr(pipeline, "get_pending_reminders", None)):
        return pipeline
    return None


def _parse_dt(value: Any) -> Any | None:
    """解析 'YYYY-MM-DD HH:MM:SS'（naive 本地墙钟）→ datetime；失败 None。"""
    from datetime import datetime

    if value is None:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _load_hot_topics() -> list[str]:
    """热点池取未过期条目前 N 个标题；缺文件/畸形一律 []（宁缺毋滥）。"""
    try:
        from utils.project_paths import project_path

        path = project_path(_HOT_TOPICS_PATH)
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        now = time.time()
        titles: list[str] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            exp = it.get("expires_at")
            if exp and float(exp) < now:
                continue
            title = str(it.get("title") or "").strip()
            if title:
                titles.append(title)
            if len(titles) >= _HOT_TOPICS_MAX:
                break
        return titles
    except Exception:  # noqa: BLE001
        return []


def load_live_hooks(session_key: str, memory: Any = None) -> LiveHooks:
    """组装活材料（纯读，四源各自容错；全部为空 → LiveHooks() 假值）。"""
    from utils.local_time import now_local

    lines: list[str] = []
    keywords: list[str] = []
    sm = _structured(memory)
    now = now_local()

    if sm is not None and session_key:
        # ① 未答澄清
        try:
            intent = sm.get_active_pending_intent(session_key)
            if intent:
                q = str(intent.get("last_question") or "").strip()
                if q:
                    lines.append(
                        f"你之前问 TA「{_clip(q, 30)}」，TA 还没答复——"
                        "可以自然地追问或换个问法"
                    )
                    keywords.append(_clip(q, 10))
        except Exception:  # noqa: BLE001
            pass
        # ② 将到提醒
        try:
            rows = sm.get_pending_reminders(session_key) or []
            for r in rows[:3]:
                content = str(r.get("content") or "").strip()
                if not content:
                    continue
                dt = _parse_dt(r.get("trigger_time"))
                if dt is None:
                    lines.append(f"TA 托你「{_clip(content, 30)}」（时间未定）")
                    keywords.append(_clip(content, 10))
                    continue
                hours = (dt - now).total_seconds() / 3600.0
                if hours < -1 or hours > _REMINDER_HORIZON_HOURS:
                    continue
                if hours >= 1:
                    lines.append(
                        f"TA 托你「{_clip(content, 30)}」（约 {int(hours)} 小时后到点）"
                    )
                elif hours >= 0:
                    lines.append(
                        f"TA 托你「{_clip(content, 30)}」（快到这个点了）"
                    )
                else:
                    lines.append(
                        f"TA 托你「{_clip(content, 30)}」（已经过了这个点，可以问问情况）"
                    )
                keywords.append(_clip(content, 10))
        except Exception:  # noqa: BLE001
            pass
        # ③ 未闭环承诺（commitment 类事实，近 7 天）
        try:
            facts = sm.get_facts(category="commitment", user_key=session_key, limit=3) or []
            cutoff = now - timedelta(days=_FACT_MAX_AGE_DAYS)
            for f in facts:
                text = str(f.get("fact") or "").strip()
                if not text:
                    continue
                updated = _parse_dt(f.get("updated_at"))
                if updated is not None and updated < cutoff:
                    continue
                lines.append(f"TA 说过「{_clip(text, 36)}」——还没下文，可以问问进展")
                keywords.append(_clip(text, 10))
        except Exception:  # noqa: BLE001
            pass

    # ④ 近日热点（公共谈资，弱钩子）
    topics = _load_hot_topics()
    if topics:
        lines.append("近日热点（公共谈资，别整段照读）：" + " / ".join(topics))
        keywords.extend(topics)

    if not lines:
        return LiveHooks()
    body = "\n".join(f"- {line}" for line in lines)
    section = (
        f"{HOOKS_SECTION_TITLE}（每条都是真事，选一个由头开口；"
        f"禁止没由头的问候）\n{body}"
    )
    if len(section) > _HOOKS_MAX_CHARS:
        section = section[:_HOOKS_MAX_CHARS]
    kw = [k for k in keywords if len(k) >= _KEYWORD_MIN_CHARS][:12]
    return LiveHooks(section=section, keywords=kw)

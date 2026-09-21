"""夜间/定时记忆整理 curator — 合并碎片事实、归档垃圾、写回账本。

用户裁决：P2 与主动消息参数、画像清洗一并做完。
确定性部分：垃圾过滤 / near-dup 合并阈值；LLM 部分：可选摘要合并（失败降级纯规则）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("memory_curator")

_GARBAGE = re.compile(
    r"^(叫我|叫你|上班|上学|学生|军训|嗯|好的|知道了|在吗)[吧啊呀呢~～!！。]*$"
)


def _norm(text: str) -> str:
    return re.sub(r"[\s,，。！？、;；:：\"'“”‘’]+", "", str(text or "")).lower()


def bigram_overlap(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if len(na) < 2 or len(nb) < 2:
        return 0.0
    ba = {na[i : i + 2] for i in range(len(na) - 1)}
    bb = {nb[i : i + 2] for i in range(len(nb) - 1)}
    if not ba:
        return 0.0
    return len(ba & bb) / len(ba)


def curate_facts_rule_based(facts: list[dict[str, Any]], *, dup_threshold: float = 0.85) -> dict[str, Any]:
    """规则整理：丢垃圾、近重复只保留一条（可选标记 reinforce）。"""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    merged: list[dict[str, Any]] = []
    for f in facts:
        text = str(f.get("fact") or f.get("content") or "").strip()
        if not text or _GARBAGE.match(text) or len(text) < 2:
            dropped.append({**f, "reason": "garbage"})
            continue
        hit = None
        for k in kept:
            if bigram_overlap(text, str(k.get("fact") or k.get("content") or "")) >= dup_threshold:
                hit = k
                break
        if hit is not None:
            merged.append({**f, "merged_into": hit.get("id"), "reason": "near_dup"})
            # 强化保留项
            try:
                hit["confidence"] = min(1.0, float(hit.get("confidence") or 0.7) + 0.05)
                hit["access_count"] = int(hit.get("access_count") or 0) + 1
            except Exception:  # noqa: BLE001
                pass
            continue
        kept.append(dict(f))
    return {"kept": kept, "dropped": dropped, "merged": merged}


def run_curator_for_session(
    session_key: str,
    sm: Any = None,
    llm: Any = None,
    *,
    apply: bool = True,
) -> dict[str, Any]:
    """整理单会话 user_facts：规则为主；可选 LLM 摘要（失败忽略）。"""
    from shisi.agent_plane.runtime import get_ledger
    from shisi.memory.legacy.structured_memory import StructuredMemory

    uk = StructuredMemory.user_key_from_session(str(session_key or ""))
    if not uk or sm is None:
        return {"ok": False, "error": "no_memory"}
    try:
        rows = sm.get_facts(user_key=uk, min_confidence=0.0, limit=500)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    result = curate_facts_rule_based(rows)
    archived = 0
    if apply:
        for d in result["dropped"]:
            fid = d.get("id")
            if fid is None:
                continue
            try:
                if sm.delete_fact(int(fid), recycle=True, user_key=uk):
                    archived += 1
            except Exception:  # noqa: BLE001
                continue
        for m in result["merged"]:
            fid = m.get("id")
            if fid is None:
                continue
            try:
                if sm.delete_fact(int(fid), recycle=True, user_key=uk):
                    pass
            except Exception:  # noqa: BLE001
                continue
        # B3/R3：near-dup 保留项在库侧强化（不是只改内存）
        import contextlib

        for k in result["kept"]:
            kid = k.get("id")
            if kid is None:
                continue
            if hasattr(sm, "update_fact_confidence"):
                with contextlib.suppress(Exception):
                    sm.update_fact_confidence(
                        int(kid), min(1.0, float(k.get("confidence") or 0.7) + 0.05)
                    )
    summary = ""
    if llm is not None:
        try:
            texts = [str(k.get("fact") or "") for k in result["kept"][:20]]
            prompt = "用一句话总结用户稳定信息（不超过60字），只输出总结：\n" + "\n".join(texts)
            if hasattr(llm, "chat_sync"):
                summary = str(llm.chat_sync(prompt, system_prompt="你是记忆压缩器。", temperature=0.2) or "")[:200]
        except Exception as e:  # noqa: BLE001
            logger.debug("curator llm summary failed: %s", e)
    import contextlib

    with contextlib.suppress(Exception):
        get_ledger().append(
            session_key=str(session_key),
            event_type="memory_curate",
            actor="curator",
            payload={
                "user_key": uk,
                "kept": len(result["kept"]),
                "dropped": len(result["dropped"]),
                "merged": len(result["merged"]),
                "archived": archived,
                "summary": summary,
            },
        )
    return {
        "ok": True,
        "user_key": uk,
        "kept": len(result["kept"]),
        "dropped": len(result["dropped"]),
        "merged": len(result["merged"]),
        "archived": archived,
        "summary": summary,
    }


def run_curator_all_known(
    sm: Any,
    llm: Any = None,
    session_keys: list[str] | None = None,
) -> dict[str, Any]:
    keys = list(session_keys or [])
    if not keys and sm is not None:
        try:
            # 从 facts 表 distinct user_key
            import sqlite3
            from contextlib import closing
            from pathlib import Path

            db = getattr(sm, "db_path", None) or getattr(sm, "_db_path", None)
            if db and Path(str(db)).exists():
                with closing(sqlite3.connect(str(db))) as conn:
                    rows = conn.execute(
                        "SELECT DISTINCT user_key FROM user_facts WHERE user_key IS NOT NULL AND user_key != ''"
                    ).fetchall()
                    keys = [r[0] for r in rows]
        except Exception as e:  # noqa: BLE001
            logger.warning("curator list keys failed: %s", e)
    done = []
    for uk in keys:
        done.append(run_curator_for_session(uk, sm=sm, llm=llm, apply=True))
    return {"sessions": len(done), "results": done}

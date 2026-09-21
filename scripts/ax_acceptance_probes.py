"""AX 验收探针 — 因果回放 / 串台 / 画像更正 / 工具真执行（骨架可跑）。

用法（worktree）：
  PYTHONPATH= python scripts/ax_acceptance_probes.py --self-test
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shisi.agent_plane.event_ledger import (  # noqa: E402
    EVENT_MEMORY_WRITE,
    EVENT_PROFILE_CORRECT,
    EVENT_PROMPT_SLOTS,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EventLedger,
)


def probe_causal_replay(ledger: EventLedger, session_key: str, turn_id: str) -> dict:
    bundle = ledger.replay(session_key=session_key, turn_id=turn_id)
    s = bundle.summary()
    ok = s["event_count"] > 0 and ("slots" in bundle.slots or s["profile_ops"] or s["tool_ops"])
    return {"probe": "causal_replay", "ok": ok, "summary": s, "slots": bundle.slots}


def probe_isolation(ledger: EventLedger, owner_session: str, foreign_session: str, turn_id: str) -> dict:
    owner_rows = ledger.query(session_key=owner_session, turn_id=turn_id)
    foreign_rows = ledger.query(session_key=foreign_session, turn_id=turn_id)
    ok = len(owner_rows) >= 1 and len(foreign_rows) == 0
    return {
        "probe": "dual_user_isolation",
        "ok": ok,
        "owner_events": len(owner_rows),
        "foreign_events": len(foreign_rows),
    }


def probe_profile_correct(ledger: EventLedger, session_key: str, turn_id: str) -> dict:
    events = ledger.query(session_key=session_key, event_type=EVENT_PROFILE_CORRECT, turn_id=turn_id)
    ok = False
    detail = {}
    if events:
        payload = events[-1].payload
        detail = payload
        ok = bool(payload.get("new")) and payload.get("cleared") is not None
    return {"probe": "profile_correction", "ok": ok, "detail": detail}


def probe_tool_execution(ledger: EventLedger, session_key: str, turn_id: str) -> dict:
    calls = ledger.query(session_key=session_key, event_type=EVENT_TOOL_CALL, turn_id=turn_id)
    results = ledger.query(session_key=session_key, event_type=EVENT_TOOL_RESULT, turn_id=turn_id)
    call_names = {c.payload.get("name") for c in calls}
    success = any(r.payload.get("success") for r in results)
    ok = bool(calls) and success
    return {
        "probe": "tool_real_execution",
        "ok": ok,
        "calls": sorted(str(n) for n in call_names),
        "result_success": success,
        "note": "生产探针还需查 reminders/DB 行；本骨架以 ledger 回执为准",
    }


def run_self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        ledger = EventLedger(Path(td) / "probe.db")
        sk_a = "N:probe-userA"
        sk_b = "N:probe-userB"
        turn = "turn-1"
        ledger.append_many(
            [
                {
                    "session_key": sk_a,
                    "event_type": EVENT_PROMPT_SLOTS,
                    "turn_id": turn,
                    "payload": {"slots": {"memory_item_count": 1, "affinity_level": 2}},
                },
                {
                    "session_key": sk_a,
                    "event_type": EVENT_MEMORY_WRITE,
                    "turn_id": turn,
                    "payload": {"fact": "用户生日是腊月初一"},
                },
                {
                    "session_key": sk_a,
                    "event_type": EVENT_PROFILE_CORRECT,
                    "turn_id": turn,
                    "payload": {"field": "birthday", "new": "腊月初一", "cleared": "11月14"},
                },
                {
                    "session_key": sk_a,
                    "event_type": EVENT_TOOL_CALL,
                    "turn_id": turn,
                    "payload": {"name": "set_reminder", "trigger_time": "2026-09-22 06:00"},
                },
                {
                    "session_key": sk_a,
                    "event_type": EVENT_TOOL_RESULT,
                    "turn_id": turn,
                    "payload": {"name": "set_reminder", "success": True, "id": 1},
                },
            ]
        )
        results = [
            probe_causal_replay(ledger, sk_a, turn),
            probe_isolation(ledger, sk_a, sk_b, turn),
            probe_profile_correct(ledger, sk_a, turn),
            probe_tool_execution(ledger, sk_a, turn),
        ]
        failed = [r for r in results if not r["ok"]]
        for r in results:
            print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['probe']}: { {k:v for k,v in r.items() if k!='probe'} }")
        return 0 if not failed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="AX acceptance probes")
    parser.add_argument("--self-test", action="store_true", help="用临时 ledger 跑通四探针")
    args = parser.parse_args()
    if args.self_test:
        return run_self_test()
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

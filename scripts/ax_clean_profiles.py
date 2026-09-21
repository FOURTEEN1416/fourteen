"""生产画像/事实全面清洗 + seed 进 EventLedger（用户裁决 2026-09-21）。

用法（生产/本地）：
  PYTHONPATH= python scripts/ax_clean_profiles.py --apply
  PYTHONPATH= python scripts/ax_clean_profiles.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 规则垃圾 / 过短无信息量事实（与宪法 v1.32 归档策略同向）
_GARBAGE_FACT_EXACT = {
    "叫我", "叫你", "叫你叫", "上班", "上学", "学生", "军训", "起不来",
    "嗯", "好的", "知道了", "你好", "在吗",
}
_GARBAGE_FACT_RE = [
    re.compile(r"^叫我[吧啊呀呢~～!！。]*$"),
    re.compile(r"^(用户)?(在)?(上班|上学|军训)$"),
    re.compile(r"^[0-9]{1,2}[:：][0-9]{2}$"),
]
_BAD_BIRTHDAY_RE = re.compile(r"^(不知道|没有|无|暂无|保密|\?+)$")
_BAD_OCC_RE = re.compile(r"^(无|没有|不知道|暂无)$")


def _is_garbage_fact(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) < 2:
        return True
    if t in _GARBAGE_FACT_EXACT:
        return True
    return any(p.search(t) for p in _GARBAGE_FACT_RE)


def _clean_profile_row(row: dict) -> tuple[dict, list[str]]:
    notes = []
    out = dict(row)
    b = str(out.get("birthday") or "").strip()
    if b and _BAD_BIRTHDAY_RE.search(b):
        out["birthday"] = ""
        notes.append(f"clear_birthday:{b}")
    occ = str(out.get("occupation") or "").strip()
    if occ and _BAD_OCC_RE.search(occ):
        out["occupation"] = ""
        notes.append(f"clear_occupation:{occ}")
    # 昵称过长像整句 → 清空
    nk = str(out.get("nickname") or "").strip()
    if len(nk) > 12:
        out["nickname"] = ""
        notes.append("clear_long_nickname")
    prefs = [str(x).strip() for x in (out.get("preferences") or []) if str(x).strip()]
    prefs = [p for p in prefs if not _is_garbage_fact(p)][:20]
    if len(prefs) != len(out.get("preferences") or []):
        notes.append("filter_preferences")
    out["preferences"] = prefs
    commits = [str(x).strip() for x in (out.get("commitments") or []) if str(x).strip()]
    # 约定保留提醒类完整句；丢掉裸「叫我」
    commits = [c for c in commits if not _is_garbage_fact(c)][:8]
    out["commitments"] = commits
    return out, notes


def clean_user_profile_table(db_path: Path, apply: bool) -> list[dict]:
    results = []
    if not db_path.exists():
        return [{"error": f"missing {db_path}"}]
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM user_profile").fetchall()
        except sqlite3.OperationalError:
            # 表尚未创建：触发 schema 后再读
            try:
                from shisi.memory.legacy.user_profile import UserProfileStore

                UserProfileStore(db_path)
                rows = conn.execute("SELECT * FROM user_profile").fetchall()
            except Exception as e:  # noqa: BLE001
                return [{"error": f"user_profile table: {e}"}]
        for r in rows:
            row = dict(r)
            for k in ("preferences", "commitments"):
                try:
                    row[k] = json.loads(row.get(k) or "[]")
                except Exception:  # noqa: BLE001
                    row[k] = []
            cleaned, notes = _clean_profile_row(row)
            entry = {
                "user_key": row.get("user_key"),
                "changed": bool(notes),
                "notes": notes,
                "before_birthday": row.get("birthday"),
                "after_birthday": cleaned.get("birthday"),
            }
            results.append(entry)
            if apply and notes:
                conn.execute(
                    """
                    UPDATE user_profile SET
                      birthday=?, occupation=?, nickname=?,
                      preferences=?, commitments=?, updated_at=?
                    WHERE user_key=?
                    """,
                    (
                        cleaned.get("birthday") or "",
                        cleaned.get("occupation") or "",
                        cleaned.get("nickname") or "",
                        json.dumps(cleaned.get("preferences") or [], ensure_ascii=False),
                        json.dumps(cleaned.get("commitments") or [], ensure_ascii=False),
                        __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                        row.get("user_key"),
                    ),
                )
        if apply:
            conn.commit()
    return results


def clean_user_facts(sm_db: Path, apply: bool) -> list[dict]:
    results = []
    if not sm_db.exists():
        return [{"error": f"missing {sm_db}"}]
    with closing(sqlite3.connect(str(sm_db))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, fact, user_key, status FROM user_facts"
            ).fetchall()
        except Exception as e:  # noqa: BLE001
            return [{"error": f"user_facts query: {e}"}]
        for r in rows:
            fact = str(r["fact"] or "")
            if _is_garbage_fact(fact):
                results.append({"id": r["id"], "fact": fact, "user_key": r["user_key"], "action": "archive"})
                if apply:
                    try:
                        conn.execute(
                            "UPDATE user_facts SET status='archived' WHERE id=?",
                            (r["id"],),
                        )
                    except Exception as e:  # noqa: BLE001
                        results[-1]["error"] = str(e)
        if apply:
            conn.commit()
    return results


def seed_profiles_to_ledger(db_users: Path, ledger_path: Path) -> dict:
    from shisi.agent_plane.event_ledger import EventLedger
    from shisi.agent_plane.profile_projection import seed_profile_baseline
    from shisi.memory.legacy.user_profile import UserProfileStore

    store = UserProfileStore(db_users)
    ledger = EventLedger(ledger_path)
    seeded = 0
    keys = []
    with closing(sqlite3.connect(str(db_users))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            keys = [r["user_key"] for r in conn.execute("SELECT user_key FROM user_profile")]
        except Exception:  # noqa: BLE001
            keys = []
    for uk in keys:
        row = store.get(uk)
        if not row:
            continue
        try:
            seed_profile_baseline(ledger, uk, row)
            seeded += 1
        except Exception as e:  # noqa: BLE001
            logger_err = f"{uk}: {e}"
            print("seed fail", logger_err)
    return {"seeded": seeded, "keys": len(keys), "ledger": str(ledger_path)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--users-db", default=str(ROOT / "data" / "sqlite.db"))
    ap.add_argument("--agent-db", default=str(ROOT / "data" / "agent_plane.db"))
    args = ap.parse_args()
    apply = bool(args.apply) and not args.dry_run
    users_db = Path(args.users_db)
    agent_db = Path(args.agent_db)
    print("=== user_profile clean ===")
    prof = clean_user_profile_table(users_db, apply)
    changed = [x for x in prof if x.get("changed")]
    print(f"rows={len(prof)} changed={len(changed)} apply={apply}")
    for x in changed[:30]:
        print(" ", x)
    print("=== user_facts garbage archive ===")
    facts = clean_user_facts(users_db, apply)
    garb = [x for x in facts if x.get("action") == "archive"]
    print(f"garbage_facts={len(garb)} apply={apply}")
    for x in garb[:30]:
        print(" ", x)
    print("=== seed ledger ===")
    seed_info = seed_profiles_to_ledger(users_db, agent_db)
    print(seed_info)
    report = {
        "profile": prof,
        "facts": facts,
        "seed": seed_info,
        "apply": apply,
    }
    out = ROOT / "data" / "ax_profile_clean_report.json"
    try:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("report:", out)
    except Exception as e:  # noqa: BLE001
        print("report write fail", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

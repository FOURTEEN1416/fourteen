"""生产称呼污染清洗（2026-10-15 称呼根治批 · 包D）。

**背景**：用户已明确否认的三个称呼（夏夏 / 十七 / 无名）在多个留存面残留——
历史回复（chat_history.role='assistant'）会被当上下文回灌、日记摘要与 ASE
状态文件进主动链、user_facts 里的「用户名叫X」断言式事实进检索。出站守卫与
注入净化（utils/address_terms）只截未来，存量污染需本脚本一次性清洗。

**设计原则（写死）**：
  - **默认 dry-run**：只读（SQLite ``mode=ro``），逐行打印将变更的内容；
    ``--apply`` 才落盘，且自动先把相关库文件备份到
    ``data/backups/contamination-<日期>-<时分>/``；
  - **保留原则**：``role='user'`` 的行原样保留（用户真实发言 = 检测源 +
    审计真实性）；``outbound_commands`` 原样保留（已投递事实审计，不回灌
    prompt）；含「十七岁」「无名指」「七十」等**词内成分**的行不误伤——
    清洗动作全部复用 ``utils.address_terms.strip_forbidden_vocatives``
    的词边界逻辑（**本脚本不写剥除正则**）；
  - **占位不删行**（chat_history / daily_summaries）：剥后为空或 <2 字符的
    文本改为 ``[已清理：称呼污染]`` 占位，保持历史连续性（与 10-13 清理批
    「[已清理：身份混淆污染]」惯例一致）；
  - **只写不执行生产**：本脚本在服务器由主控终审执行；**不自动重启服务**。

dry-run 输出分三块：「将净化」「将占位」「人工复核」+ 末尾按表汇总统计。

用法：
  python scripts/purge_address_contamination.py --dry-run            # 预览（默认）
  python scripts/purge_address_contamination.py --apply              # 落盘（先自动备份）
  python scripts/purge_address_contamination.py --names 夏夏,十七     # 覆盖目标词
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 用户已否认的三个称呼（生产口径；--names 可覆盖）
DEFAULT_NAMES = "夏夏,十七,无名"
PLACEHOLDER = "[已清理：称呼污染]"
# 占位阈值：净化结果不足 2 字符（strip_forbidden_vocatives 契约：<2 返回空串）
_MIN_KEEP = 2

# user_facts 断言式判据：自称「用户名叫X」类 = 污染事实（删除）；
# 否认/询问类 = 正确认知记录（保留）；其余含目标词的事实 = 人工复核（保守）。
_FACT_DENY_QUERY_MARKERS = (
    "不是", "否认", "澄清", "纠正", "误会", "询问", "问", "谁", "别叫", "不要再",
    "不要叫", "拒绝", "不喜欢", "别再", "从没叫", "没叫过",
)
_FACT_ASSERTION_MARKERS = ("名叫", "名字叫", "名字是", "名为", "姓名是", "姓名叫", "昵称是", "昵称为", "叫", "应该是", "自称")

# runtime_plane 人工复核：跳过已投递事实审计表（不回灌 prompt，保留原样）
_RUNTIME_SKIP_TABLES = {"outbound_commands", "sqlite_sequence"}


def _parse_names(raw: str) -> list[str]:
    out: list[str] = []
    for piece in str(raw or "").replace("，", ",").split(","):
        token = piece.strip()
        if token and token not in out:
            out.append(token)
    return out


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    """只读连接（dry-run 全程不写；缺库直接抛错，缺资源只报不建）。"""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _clean_text(text: str, forbidden: set[str]) -> str:
    """复用冻结 API 做词边界剥除（脚本不自己写正则）。"""
    from utils.address_terms import LEGAL_FALLBACK, strip_forbidden_vocatives

    return strip_forbidden_vocatives(str(text or ""), LEGAL_FALLBACK, frozenset(forbidden))


def _match_kind(original: str, cleaned: str) -> str:
    """变更分类：''（无变更）/ 'clean'（净化）/ 'placeholder'（占位）。"""
    if cleaned == original:
        return ""
    if not cleaned or len(cleaned) < _MIN_KEEP:
        return "placeholder"
    return "clean"


class _Plan:
    """清洗计划（dry-run 与 apply 共用；只存动作与理由，不提前写）。"""

    def __init__(self) -> None:
        # 将净化 / 将占位：{label: [ {..} ]}
        self.clean: list[dict] = []
        self.placeholder: list[dict] = []
        self.review: list[dict] = []
        self.stats: dict[str, dict[str, int]] = {}

    def _stat(self, table: str) -> dict[str, int]:
        return self.stats.setdefault(table, {"hits": 0, "changes": 0})

    def hit(self, table: str) -> None:
        self._stat(table)["hits"] += 1

    def changed(self, table: str) -> None:
        self._stat(table)["changes"] += 1


def _plan_chat_history(conn: sqlite3.Connection, names: list[str], plan: _Plan) -> list[dict]:
    """chat_history.role='assistant' 逐行净化；user 行原样保留（检测源+审计）。"""
    actions: list[dict] = []
    forbidden = set(names)
    rows = conn.execute(
        "SELECT id, content FROM chat_history WHERE role='assistant' ORDER BY id"
    ).fetchall()
    for row_id, content in rows:
        text = str(content or "")
        if not any(tok in text for tok in names):
            continue
        plan.hit("chat_history")
        cleaned = _clean_text(text, forbidden)
        kind = _match_kind(text, cleaned)
        if not kind:
            continue  # 词内成分（如「十七岁」）—— 不误伤、不计变更
        plan.changed("chat_history")
        item = {
            "table": "chat_history",
            "row": int(row_id),
            "original": text,
            "cleaned": cleaned if kind == "clean" else PLACEHOLDER,
            "kind": kind,
        }
        (plan.clean if kind == "clean" else plan.placeholder).append(item)
        actions.append(item)
    return actions


def _plan_daily_summaries(conn: sqlite3.Connection, names: list[str], plan: _Plan) -> list[dict]:
    actions: list[dict] = []
    forbidden = set(names)
    rows = conn.execute("SELECT date, summary FROM daily_summaries ORDER BY date").fetchall()
    for date_key, summary in rows:
        text = str(summary or "")
        if not any(tok in text for tok in names):
            continue
        plan.hit("daily_summaries")
        cleaned = _clean_text(text, forbidden)
        kind = _match_kind(text, cleaned)
        if not kind:
            continue
        plan.changed("daily_summaries")
        item = {
            "table": "daily_summaries",
            "row": str(date_key),
            "original": text,
            "cleaned": cleaned if kind == "clean" else PLACEHOLDER,
            "kind": kind,
        }
        (plan.clean if kind == "clean" else plan.placeholder).append(item)
        actions.append(item)
    return actions


def _classify_fact(fact: str, names: list[str]) -> tuple[str, str]:
    """user_facts 单行处置：('delete'|'keep'|'review', 理由)。

    判据（保守优先）：
      - 否认/询问类（「用户称自己不是X」「用户询问X是谁」）= 正确认知的记录 → 保留；
      - 断言式（「用户名叫X/用户应该是X/用户叫X」）声称用户名为污染词 → 删除
        （走服务 API：回收站 + 删除水位 + 失效派生召回）；
      - 其余含目标词的事实（如「X喜欢喝奶茶」）拿不准语境 → 人工复核。
    """
    text = str(fact or "")
    hits = [tok for tok in names if tok in text]
    if not hits:
        return "keep", ""
    if any(marker in text for marker in _FACT_DENY_QUERY_MARKERS):
        return "keep", "否认/询问类事实（正确认知的记录，保留）"
    for tok in hits:
        if any((marker + tok) in text for marker in _FACT_ASSERTION_MARKERS):
            return "delete", f"断言式污染：声称用户名为「{tok}」（删除并失效派生召回）"
    return "review", f"含目标词但非明确断言式（{text[:40]}），交人工复核"


def _plan_user_facts(conn: sqlite3.Connection, names: list[str], plan: _Plan) -> list[dict]:
    actions: list[dict] = []
    rows = conn.execute(
        "SELECT id, user_key, fact, status FROM user_facts ORDER BY id"
    ).fetchall()
    for fact_id, user_key, fact, status in rows:
        disposition, reason = _classify_fact(str(fact or ""), names)
        if not reason:
            continue  # 不含目标词
        plan.hit("user_facts")
        if disposition == "delete" and str(status or "active") == "active":
            plan.changed("user_facts")
            item = {
                "table": "user_facts",
                "row": int(fact_id),
                "user_key": str(user_key or ""),
                "original": str(fact or ""),
                "kind": "delete",
                "reason": reason,
            }
            plan.clean.append(item)
            actions.append(item)
        elif disposition == "delete":
            plan.review.append({
                "table": "user_facts",
                "row": int(fact_id),
                "user_key": str(user_key or ""),
                "original": str(fact or ""),
                "kind": "review",
                "reason": f"断言式污染但行非 active（status={status}），交人工复核",
            })
        else:
            # keep / review 均进复核块（keep 是「保留」的显式处置，spec：列出每条处置与理由）
            plan.review.append({
                "table": "user_facts",
                "row": int(fact_id),
                "user_key": str(user_key or ""),
                "original": str(fact or ""),
                "kind": disposition,
                "reason": reason if disposition == "review"
                else f"保留（无需操作）：{reason}",
            })
    return actions


def _plan_ase_states(state_dir: Path, names: list[str], plan: _Plan) -> list[dict]:
    """ase_states/*.json：recent_messages 列表 + last_user_message 字段净化。

    净化后为空则从列表移除该项；last_user_message 空则置空串。
    """
    import json

    actions: list[dict] = []
    forbidden = set(names)
    if not state_dir.is_dir():
        return actions
    for path in sorted(state_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(data, dict):
            continue
        changed = False
        changes: list[dict] = []
        recent = data.get("recent_messages")
        if isinstance(recent, list):
            new_recent: list = []
            for msg in recent:
                text = str(msg or "")
                if not any(tok in text for tok in names):
                    new_recent.append(msg)
                    continue
                plan.hit("ase_states")
                cleaned = _clean_text(text, forbidden)
                kind = _match_kind(text, cleaned)
                if not kind:
                    new_recent.append(msg)
                    continue
                plan.changed("ase_states")
                changed = True
                if kind == "placeholder":
                    # 净化后为空 → 从列表移除该项（ace_states 无「占位」概念）
                    changes.append({"field": "recent_messages", "original": text,
                                    "cleaned": "（移除该项）", "remove": True})
                    continue
                changes.append({"field": "recent_messages", "original": text, "cleaned": cleaned})
                new_recent.append(cleaned)
            data["recent_messages"] = new_recent
        last_user = data.get("last_user_message")
        if isinstance(last_user, str) and last_user and any(tok in last_user for tok in names):
            plan.hit("ase_states")
            cleaned = _clean_text(last_user, forbidden)
            kind = _match_kind(last_user, cleaned)
            if kind:
                plan.changed("ase_states")
                changed = True
                data["last_user_message"] = cleaned if kind == "clean" else ""
                changes.append({"field": "last_user_message", "original": last_user,
                                "cleaned": data["last_user_message"]})
        if changed:
            for ch in changes:
                item = {"table": f"ase_states/{path.name}", "row": ch["field"],
                        "original": ch["original"], "cleaned": ch["cleaned"], "kind": "clean"}
                if ch.get("remove"):
                    item["remove"] = True
                plan.clean.append(item)
                actions.append(item)
    return actions


def _plan_runtime_plane(db_path: Path, names: list[str], plan: _Plan) -> None:
    """runtime_plane.db 全表扫描：非 outbound_commands 表含目标词的行 → 人工复核。

    保守口径：**默认只列不改**（这些表是控制面状态，语义需人工判定是否回灌
    prompt）；outbound_commands 跳过（已投递事实审计）。
    """
    if not db_path.exists():
        return
    with closing(_connect_ro(db_path)) as conn:
        tables = [
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        for table in tables:
            if table in _RUNTIME_SKIP_TABLES:
                continue
            try:
                cols = [str(r[1]) for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]  # noqa: S608
            except sqlite3.Error:
                continue
            text_cols = [c for c in cols if c]
            if not text_cols:
                continue
            sel = ", ".join(f'"{c}"' for c in text_cols)
            try:
                rows = conn.execute(f'SELECT rowid, {sel} FROM "{table}"').fetchall()  # noqa: S608
            except sqlite3.Error:
                continue
            for row in rows:
                rowid = row[0]
                for col, value in zip(text_cols, row[1:], strict=False):
                    text = "" if value is None else str(value)
                    if not text or not any(tok in text for tok in names):
                        continue
                    plan.hit(f"runtime_plane.{table}")
                    plan.review.append({
                        "table": f"runtime_plane.{table}",
                        "row": f"rowid={rowid} col={col}",
                        "original": text[:200],
                        "kind": "review",
                        "reason": "控制面状态含目标词（默认只列不改，人工判定是否回灌 prompt）",
                    })


def _print_block(title: str, items: list[dict], *, show_reason: bool = False) -> None:
    print(f"\n── {title}（{len(items)} 项）──")
    if not items:
        print("  （无）")
        return
    for item in items:
        row_desc = f"{item['table']} #{item['row']}"
        if item.get("user_key"):
            row_desc += f" user_key={item['user_key']}"
        if item.get("kind") == "delete":
            row_desc += "  [删除]"
        print(f"  · {row_desc}")
        print(f"      原文: {str(item.get('original') or '')[:200]}")
        if "cleaned" in item:
            print(f"      改后: {str(item.get('cleaned') or '')[:200]}")
        if show_reason and item.get("reason"):
            print(f"      理由: {item['reason']}")


def _backup(data_dir: Path, names: list[str]) -> Path:
    """--apply 前置备份：相关库 + ase_states/*.json 副本。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    dest = data_dir / "backups" / f"contamination-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("sqlite.db", "agent_plane.db", "runtime_plane.db"):
        src = data_dir / name
        if not src.exists():
            continue
        for suffix in ("", "-wal", "-shm"):
            side = Path(str(src) + suffix)
            if side.exists():
                shutil.copy2(side, dest / side.name)
    state_dir = data_dir / "ase_states"
    if state_dir.is_dir():
        out_dir = dest / "ase_states"
        out_dir.mkdir(parents=True, exist_ok=True)
        for src in state_dir.glob("*.json"):
            shutil.copy2(src, out_dir / src.name)
    print(f"[备份] 已完成 → {dest}")
    return dest


def _apply_actions(
    sqlite_db: Path, data_dir: Path, actions: list[dict], failures: list[str]
) -> int:
    """落盘：chat_history/daily_summaries 就地 UPDATE；user_facts 走服务 API 删除。

    各分节独立容错（failures 记录不中断）：一节失败不影响其余节落盘，
    最终汇总可见——生产执行时主控能据此决定补跑。
    """
    applied = 0
    sqlite_actions = [
        a for a in actions
        if a["table"] in ("chat_history", "daily_summaries")
        and a["kind"] in ("clean", "placeholder")
    ]
    if sqlite_actions:
        try:
            with closing(sqlite3.connect(str(sqlite_db))) as conn:
                for item in sqlite_actions:
                    if item["table"] == "chat_history":
                        conn.execute(
                            "UPDATE chat_history SET content = ? WHERE id = ?",
                            (item["cleaned"], int(item["row"])),
                        )
                    else:
                        conn.execute(
                            "UPDATE daily_summaries SET summary = ? WHERE date = ?",
                            (item["cleaned"], str(item["row"])),
                        )
                    applied += 1
                conn.commit()
        except Exception as e:  # noqa: BLE001
            failures.append(f"sqlite 文本净化节失败：{e}")
    # user_facts：走 ShisiMemoryService API（回收站 + 删除水位 + 失效派生召回）
    fact_deletes = [a for a in actions if a["kind"] == "delete" and a["table"] == "user_facts"]
    if fact_deletes:
        try:
            from shisi.agent_plane.event_ledger import EventLedger, set_default_ledger
            from shisi.application.memory_service import ShisiMemoryService
            from shisi.memory.legacy.structured_memory import StructuredMemory

            # 审计事件必须落到**目标数据目录**的账本——服务默认走
            # EventLedger.default_path()（仓库 data/agent_plane.db），
            # 在 --data-dir 指向别处时会把审计写进无关库（本脚本自测实证）。
            target_ledger = data_dir / "agent_plane.db"
            if sqlite_db.parent != (ROOT / "data"):
                set_default_ledger(EventLedger(target_ledger))
            svc = ShisiMemoryService(
                structured_memory=StructuredMemory(db_path=str(sqlite_db)),
                db_path=str(sqlite_db),
                chroma_path=str(data_dir / "chroma_db"),
            )
            for item in fact_deletes:
                try:
                    receipt = svc.forget_fact(
                        int(item["row"]),
                        session_key=str(item.get("user_key") or ""),
                        reason="2026-10-15 称呼污染清洗（断言式「用户名叫X」事实）",
                    )
                    print(f"  [删除] user_facts#{item['row']} ok={receipt.get('ok')} "
                          f"action={receipt.get('action')}")
                    if receipt.get("ok"):
                        applied += 1
                    else:
                        failures.append(
                            f"user_facts#{item['row']} 删除未生效（action={receipt.get('action')}）"
                        )
                except Exception as e:  # noqa: BLE001
                    failures.append(f"user_facts#{item['row']} 删除异常：{e}")
        except Exception as e:  # noqa: BLE001
            failures.append(f"删除服务装配失败（fact 删除整节跳过）：{e}")
    return applied


def _apply_ase_states(state_dir: Path, actions: list[dict], failures: list[str]) -> int:
    """ase_states 落盘（JSON 原子写回；按文件聚合一次写入；失败记入 failures）。"""
    import json

    from utils.json_state import atomic_write_json

    by_file: dict[str, list[dict]] = {}
    for item in actions:
        if item["table"].startswith("ase_states/"):
            by_file.setdefault(item["table"].split("/", 1)[1], []).append(item)
    applied = 0
    for fname, items in by_file.items():
        path = state_dir / fname
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            recent = data.get("recent_messages")
            if isinstance(recent, list):
                new_recent: list = []
                for msg in recent:
                    text = str(msg or "")
                    match = None
                    for item in items:
                        if item["row"] == "recent_messages" and item["original"] == text:
                            match = item
                            break
                    if match is None:
                        new_recent.append(msg)
                    elif match.get("remove"):
                        applied += 1
                        continue  # 净化后为空 → 从列表移除
                    else:
                        new_recent.append(match["cleaned"])
                        applied += 1
                data["recent_messages"] = new_recent
            last_user = data.get("last_user_message")
            if isinstance(last_user, str):
                for item in items:
                    if item["row"] == "last_user_message" and item["original"] == last_user:
                        data["last_user_message"] = item["cleaned"]
                        applied += 1
                        break
            atomic_write_json(path, data)
        except Exception as e:  # noqa: BLE001
            failures.append(f"ase_states/{fname} 写回失败：{e}")
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="生产称呼污染清洗（默认 dry-run；--apply 才落盘并自动备份）"
    )
    parser.add_argument("--apply", action="store_true", help="落盘执行（默认 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="只读预览（默认行为）")
    parser.add_argument("--names", default=DEFAULT_NAMES,
                        help=f"目标称呼（逗号分隔，默认 {DEFAULT_NAMES}）")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="数据目录（默认 <root>/data）")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    sqlite_db = data_dir / "sqlite.db"
    ase_dir = data_dir / "ase_states"
    names = _parse_names(args.names)
    if not names:
        print("[错误] --names 为空，无事可做")
        return 2

    print("=" * 72)
    print("称呼污染清洗（2026-10-15 称呼根治批）")
    print(f"  模式: {'APPLY（落盘）' if args.apply else 'DRY-RUN（只读预览）'}")
    print(f"  目标词: {names}")
    print(f"  数据目录: {data_dir}")
    print("=" * 72)

    plan = _Plan()
    if sqlite_db.exists():
        with closing(_connect_ro(sqlite_db)) as conn:
            _plan_chat_history(conn, names, plan)
            _plan_daily_summaries(conn, names, plan)
            _plan_user_facts(conn, names, plan)
    else:
        print(f"[提示] 缺库 {sqlite_db}（跳过 sqlite.db 清洗）")
    _plan_ase_states(ase_dir, names, plan)
    _plan_runtime_plane(data_dir / "runtime_plane.db", names, plan)

    _print_block(
        "将净化（含事实删除）",
        [i for i in plan.clean if i.get("kind") in ("clean", "delete")],
        show_reason=True,
    )
    _print_block("将占位", plan.placeholder)
    _print_block("人工复核（默认只列不改）", plan.review, show_reason=True)

    print("\n── 汇总统计 ──")
    if plan.stats:
        for table in sorted(plan.stats):
            st = plan.stats[table]
            print(f"  · {table}: 命中 {st['hits']} 行 / 变更 {st['changes']} 行")
    else:
        print("  （无命中：库内无目标词污染）")
    fact_review = sum(1 for i in plan.review if i["table"] == "user_facts" and i.get("kind") == "review")
    fact_keep = sum(1 for i in plan.review if i["table"] == "user_facts" and i.get("kind") == "keep")
    print(f"  · user_facts: 删除 {sum(1 for i in plan.clean if i['table'] == 'user_facts')} 行"
          f" / 保留 {fact_keep} 行 / 人工复核 {fact_review} 行")
    print(f"  · runtime_plane 人工复核: {sum(1 for i in plan.review if i['table'].startswith('runtime_plane'))} 行")
    print("  · outbound_commands: 已跳过（已投递事实审计，保留原样）")

    if not args.apply:
        print("\n[DRY-RUN] 未做任何写入。确认无误后加 --apply 执行（自动先备份）。")
        return 0

    actions = [i for i in plan.clean if i.get("kind") in ("clean", "delete")] + list(plan.placeholder)
    if not actions:
        print("\n[APPLY] 无变更，直接退出（不产生备份）。")
        return 0
    _backup(data_dir, names)
    failures: list[str] = []
    applied = 0
    if sqlite_db.exists():
        applied += _apply_actions(sqlite_db, data_dir, actions, failures)
    applied += _apply_ase_states(ase_dir, actions, failures)
    print(f"\n[APPLY] 完成，共落盘 {applied} 项。")
    if failures:
        print(f"⚠️ 有 {len(failures)} 处失败（需主控处理）：")
        for msg in failures:
            print(f"  · {msg}")
        return 1
    print("⚠️ 服务不会自动重启（由主控决定重启窗口）；人工复核项未动。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

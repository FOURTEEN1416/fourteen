"""删号 purge 覆盖已退役的 per-user 偏好表（2026-11-08 D-A 验收修复）。

背景：`user_humanize`（拟人度覆盖）与 `user_reply_mode`（回复模式覆盖）两张表的
**读层**已随用户裁决整体退役，写入入口也删了；但生产库里的存量行还在。
`StructuredMemory.purge_owner_data` 的删号清单此前只登记了
`user_proactive_config` —— 于是删号后这两张表的个人偏好行仍残留，
触碰项目「数据隐私 / 多用户隔离」硬约束。

本文件钉住：
1. `purge_owner_data` 清掉该 owner 在 `user_humanize` / `user_reply_mode` /
   `user_proactive_config` 三表的行；
2. 别人的行不受影响（隔离）；
3. `count_owner_rows` 与 `verify_owner_purged` 同口径（不会「删了但验证看不见」）；
4. 新库没有这些表时（表已不再被创建）不抛异常（`_has_table` 守卫）。
"""

from __future__ import annotations

import pytest

from shisi.memory.legacy.structured_memory import StructuredMemory

KEY_A = "2:wxid_aaa@im.wechat"
KEY_B = "4:wxid_bbb@im.wechat"

_RETIRED_TABLES = {
    "user_humanize": "CREATE TABLE user_humanize (user_key TEXT PRIMARY KEY, config TEXT)",
    "user_reply_mode": "CREATE TABLE user_reply_mode (user_key TEXT PRIMARY KEY, mode TEXT)",
    "user_proactive_config": (
        "CREATE TABLE user_proactive_config "
        "(user_key TEXT PRIMARY KEY, config TEXT)"
    ),
}


@pytest.fixture()
def sm(tmp_path):
    memory = StructuredMemory(str(tmp_path / "purge.db"))
    yield memory
    memory.close()


def _create_retired_tables(memory: StructuredMemory) -> None:
    with memory._conn(write=True) as conn:
        for ddl in _RETIRED_TABLES.values():
            conn.execute(ddl)
        conn.commit()


def _rows(memory: StructuredMemory, table: str) -> list[str]:
    with memory._conn() as conn:
        return [str(r["user_key"]) for r in conn.execute(f"SELECT user_key FROM {table}")]


def _seed(memory: StructuredMemory) -> None:
    _create_retired_tables(memory)
    with memory._conn(write=True) as conn:
        for table in _RETIRED_TABLES:
            conn.execute(f"INSERT INTO {table}(user_key) VALUES(?)", (KEY_A,))
            conn.execute(f"INSERT INTO {table}(user_key) VALUES(?)", (KEY_B,))
        # `_conn` 只共享连接、不代管事务：手动提交，否则 purge 的
        # `BEGIN IMMEDIATE` 会撞「cannot start a transaction within a transaction」。
        conn.commit()


@pytest.mark.parametrize("table", sorted(_RETIRED_TABLES))
def test_purge_clears_retired_pref_rows(sm, table):
    """删号必须清掉退役表里该 owner 的行（此前完全没登记 → 残留）。"""
    _seed(sm)

    sm.purge_owner_data(2, session_keys=[KEY_A])

    assert _rows(sm, table) == [KEY_B], (
        f"{table} 未进删号清单：{KEY_A} 的个人偏好行残留（隐私红线）"
    )


def test_purge_reports_retired_tables_in_counts(sm):
    """回执/计数里要看得见这三张表（静默跳过会让「验证已清」变成假绿）。"""
    _seed(sm)

    counts = sm.purge_owner_data(2, session_keys=[KEY_A])

    for table in _RETIRED_TABLES:
        assert counts.get(table) == 1, f"{table} 未被 purge 计数登记：{counts}"


def test_count_and_verify_agree_on_retired_tables(sm):
    """`count_owner_rows` 与 `verify_owner_purged` 必须与 purge 同口径。"""
    _seed(sm)

    assert sm.count_owner_rows(2, session_keys=[KEY_A]).get("user_humanize") == 1
    assert sm.count_owner_rows(2, session_keys=[KEY_A]).get("user_reply_mode") == 1

    sm.purge_owner_data(2, session_keys=[KEY_A])

    residual = sm.verify_owner_purged(2, session_keys=[KEY_A])
    for table in _RETIRED_TABLES:
        assert residual.get(table, 0) == 0, f"{table} 删号后仍有残留计数：{residual}"


def test_missing_retired_tables_do_not_break_purge(sm):
    """新库不再创建这三张表 ⇒ purge/计数必须安静跳过（`_has_table` 守卫）。"""
    counts = sm.purge_owner_data(2, session_keys=[KEY_A])

    for table in _RETIRED_TABLES:
        assert table not in counts
    assert sm.count_owner_rows(2, session_keys=[KEY_A]).get("user_humanize") is None

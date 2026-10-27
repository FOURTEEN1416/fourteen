"""拟人度按用户覆盖（G-2 / Q2 裁决）回归。

覆盖设计《2026-10-27-仿真人化引入方案》§3.1 P0-A 与 v1.56 R7 同型契约：

- 读取顺序：用户覆盖 → 全局块 → 默认值；**键缺失 = 继承**；
- 写入键 = 运行时读取键（R7 的 P0 教训：键形态不一致会让功能无效却回执成功）；
- 多用户严格隔离（A 的覆盖绝不泄漏给 B）；
- 库内坏值宽松修复（该字段回落全局，不抛异常炸主链）；
- ``user_override_enabled=false`` 为回滚开关（覆盖表整体忽略）；
- 按用户写入同样走**严格校验**（越界/跨字段冲突 → ValueError）。

数据全部合成：配置文件与覆盖库均重定向到 per-test 临时路径。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from utils import humanize_config as hc

_ALICE = "2:alice@im.wechat"
_BOB = "3:bob@im.wechat"


@pytest.fixture
def hm_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    db = tmp_path / "humanize_override.db"
    monkeypatch.setattr(hc, "_OVERRIDE_DB_PATH", db)
    return db


# ══════════════════════════════════════════════════════════
#  继承与覆盖
# ══════════════════════════════════════════════════════════


def test_no_row_inherits_global(hm_tmp):
    """无覆盖行 = 完全继承全局（不是钉死默认值）。"""
    hc.write_humanize(per_char_ms=70, fixed_ms=800)
    cfg = hc.read_humanize(_ALICE)
    assert cfg.per_char_ms == 70
    assert cfg.fixed_ms == 800


def test_per_user_override_wins_and_global_untouched(hm_tmp):
    hc.write_humanize(per_char_ms=70)
    hc.write_humanize(user_key=_ALICE, per_char_ms=30)
    assert hc.read_humanize(_ALICE).per_char_ms == 30
    # 全局与其它用户不受影响
    assert hc.read_humanize().per_char_ms == 70
    assert hc.read_humanize(_BOB).per_char_ms == 70


def test_per_user_override_isolated_between_users(hm_tmp):
    """多用户隔离硬约束：A 的覆盖绝不泄漏给 B。"""
    hc.write_humanize(user_key=_ALICE, fixed_ms=100, delay_mode="fixed")
    hc.write_humanize(user_key=_BOB, fixed_ms=900, delay_mode="off")
    assert hc.read_humanize(_ALICE).fixed_ms == 100
    assert hc.read_humanize(_ALICE).delay_mode == "fixed"
    assert hc.read_humanize(_BOB).fixed_ms == 900
    assert hc.read_humanize(_BOB).delay_mode == "off"
    assert hc.read_humanize("9:stranger@im.wechat").fixed_ms == hc.DEFAULTS["fixed_ms"]


def test_write_key_equals_read_key_roundtrip(hm_tmp):
    """R7 同型断言：写入时用的 user_key 形态 = 运行时读取键形态。"""
    hc.write_humanize(user_key=_ALICE, bracket_delay_ms=250)
    # 直接查库确认落盘键就是完整会话键
    conn = sqlite3.connect(str(hm_tmp))
    try:
        row = conn.execute(
            f"SELECT user_key, config FROM {hc.OVERRIDE_TABLE} WHERE user_key = ?", (_ALICE,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == _ALICE
    stored = json.loads(row[1])
    assert stored["bracket_delay_ms"] == 250
    assert hc.read_humanize(_ALICE).bracket_delay_ms == 250


def test_per_user_partial_write_merges(hm_tmp):
    """按用户 partial merge：第二次只改一个键，其余覆盖保持。"""
    hc.write_humanize(user_key=_ALICE, per_char_ms=30, fixed_ms=500)
    hc.write_humanize(user_key=_ALICE, fixed_ms=900)
    cfg = hc.read_humanize(_ALICE)
    assert cfg.per_char_ms == 30
    assert cfg.fixed_ms == 900


def test_clear_override_falls_back_to_global(hm_tmp):
    hc.write_humanize(per_char_ms=70)
    hc.write_humanize(user_key=_ALICE, per_char_ms=30)
    assert hc.clear_humanize_override(_ALICE) is True
    assert hc.read_humanize(_ALICE).per_char_ms == 70
    assert hc.clear_humanize_override(_ALICE) is False  # 幂等


# ══════════════════════════════════════════════════════════
#  坏数据与回滚开关
# ══════════════════════════════════════════════════════════


def test_stored_invalid_values_fall_back_per_field(hm_tmp):
    """库里被手工写坏 → 坏字段回落全局，合法字段照常生效，不抛异常。"""
    hc.write_humanize(per_char_ms=70)
    conn = hc._override_conn()
    try:
        conn.execute(
            f"INSERT INTO {hc.OVERRIDE_TABLE} (user_key, config, updated_at) VALUES (?, ?, '') "
            f"ON CONFLICT(user_key) DO UPDATE SET config = excluded.config",
            (_ALICE, json.dumps({"per_char_ms": 99999, "delay_mode": "bogus", "fixed_ms": 777})),
        )
        conn.commit()
    finally:
        conn.close()
    cfg = hc.read_humanize(_ALICE)
    assert cfg.per_char_ms == 70          # 越界 → 回落全局
    assert cfg.delay_mode == "dynamic"    # 非法枚举 → 回落全局
    assert cfg.fixed_ms == 777            # 合法值照常生效


def test_stored_garbage_json_falls_back(hm_tmp):
    hc.write_humanize(per_char_ms=70)
    conn = hc._override_conn()
    try:
        conn.execute(
            f"INSERT INTO {hc.OVERRIDE_TABLE} (user_key, config, updated_at) VALUES (?, 'not-json', '')",
            (_ALICE,),
        )
        conn.commit()
    finally:
        conn.close()
    assert hc.read_humanize(_ALICE).per_char_ms == 70


def test_override_disabled_ignores_rows(hm_tmp):
    """回滚开关：user_override_enabled=false 时覆盖表整体忽略（行保留不删）。"""
    hc.write_humanize(per_char_ms=70)
    hc.write_humanize(user_key=_ALICE, per_char_ms=30)
    assert hc.read_humanize(_ALICE).per_char_ms == 30

    hc.write_humanize(user_override_enabled=False)
    assert hc.read_humanize(_ALICE).per_char_ms == 70  # 覆盖被忽略

    hc.write_humanize(user_override_enabled=True)
    assert hc.read_humanize(_ALICE).per_char_ms == 30  # 行仍在，重新生效


def test_per_user_write_is_strict(hm_tmp):
    """按用户写入同样严格校验（越界 / 跨字段冲突 → ValueError，不落行）。"""
    with pytest.raises(ValueError):
        hc.write_humanize(user_key=_ALICE, per_char_ms=999)
    with pytest.raises(ValueError):
        hc.write_humanize(user_key=_ALICE, random_min_ms=2000, random_max_ms=1000)
    assert hc.read_humanize(_ALICE).per_char_ms == hc.DEFAULTS["per_char_ms"]
    conn = sqlite3.connect(str(hm_tmp))
    try:
        row = conn.execute(
            f"SELECT config FROM {hc.OVERRIDE_TABLE} WHERE user_key = ?", (_ALICE,)
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_per_user_cross_field_uses_merged_view(hm_tmp):
    """跨字段校验按「全局 + 既有覆盖 + 本次写入」的合并视图判定。"""
    hc.write_humanize(random_max_ms=800)
    hc.write_humanize(user_key=_ALICE, random_min_ms=300)  # 300 ≤ 800 合法
    assert hc.read_humanize(_ALICE).random_min_ms == 300
    with pytest.raises(ValueError):
        hc.write_humanize(user_key=_ALICE, random_min_ms=900)  # 900 > 800 冲突

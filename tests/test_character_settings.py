"""per-(用户 × 角色) 设置唯一真源回归（2026-11-07 痛点批 P1）。

锁定用户裁决的语义：
- **每个角色一份完整快照**（不是「与全局的差异」）——首次读取即从出厂默认整份物化；
- **两维隔离**：A 用户调林挽夏不影响 B 用户调林挽夏，也不影响 A 自己调孙颖莎；
- **读侧容错 / 写侧严格**：坏行逐字段回落默认，非法写入必须抛 ValueError（禁止假成功）；
- **2026-11-08**：旧的 per-user 覆盖层退役后，物化只取当前出厂默认（存量用户
  设置由 `scripts/migrate_per_user_prefs_to_my_settings.py` 迁入）。
"""

from __future__ import annotations

import json
import sqlite3
import sys

import pytest

sys.path.insert(0, ".")

from utils import character_settings as cs


def _keys(tag: str) -> tuple[str, str]:
    return f"9:{tag}@im.wechat", f"ch_{tag}"


def test_read_materializes_full_snapshot() -> None:
    user, ch = _keys("materialize")
    assert not cs.has_row(user, ch)

    values = cs.read(user, ch)

    assert cs.has_row(user, ch), "首次读取必须物化一行（每人每角色一份）"
    assert set(values) == {f.name for f in cs.SETTING_FIELDS}
    # 全量快照：包含所有四组字段，不是只存差异
    assert values["delay_mode"] == "dynamic"
    assert values["max_daily_messages"] >= 1
    assert values["typing_enabled"] is True
    assert "intensity" in values


def test_write_persists_and_isolated_by_user_and_character() -> None:
    alice, ch_x = _keys("iso_a")
    bob, _ = _keys("iso_b")
    _, ch_y = _keys("iso_c")

    cs.write(alice, ch_x, {"per_char_ms": 99, "max_daily_messages": 3})

    # 同一用户同一角色：读回
    assert cs.read(alice, ch_x)["per_char_ms"] == 99
    # 同一用户不同角色：不受影响（各自物化自己的行）
    assert cs.read(alice, ch_y)["per_char_ms"] == 45
    # 不同用户同一角色 id：不受影响
    assert cs.read(bob, ch_x)["per_char_ms"] == 45


def test_write_rejects_unknown_field() -> None:
    user, ch = _keys("unknown")
    with pytest.raises(ValueError):
        cs.write(user, ch, {"not_a_field": 1})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("per_char_ms", 999),           # 超上界
        ("max_segments", 7),            # 枚举外
        ("delay_mode", "bogus"),        # 枚举外
        ("typing_enabled", "yes"),      # 类型错
        ("threshold", float("nan")),    # NaN
        ("style_hint", "x" * 201),      # 超长
    ],
)
def test_write_rejects_invalid_values(field: str, value: object) -> None:
    user, ch = _keys(f"invalid_{field}")
    with pytest.raises(ValueError):
        cs.write(user, ch, {field: value})


def test_write_rejects_cross_field_inversion() -> None:
    user, ch = _keys("cross")
    with pytest.raises(ValueError):
        cs.write(user, ch, {"random_min_ms": 2000, "random_max_ms": 100})


def test_reset_deletes_row_and_re_materializes() -> None:
    user, ch = _keys("reset")
    cs.write(user, ch, {"per_char_ms": 120})
    assert cs.reset(user, ch) is True
    assert not cs.has_row(user, ch)
    # 再读 → 从出厂默认重新物化
    assert cs.read(user, ch)["per_char_ms"] == 45


def test_bad_row_falls_back_per_field_and_heals() -> None:
    user, ch = _keys("badrow")
    cs.read(user, ch)  # 先建表
    with sqlite3.connect(str(cs._DB_PATH)) as conn:
        conn.execute(
            f"INSERT INTO {cs.TABLE} (user_key, character_id, config, updated_at) "
            "VALUES (?, ?, ?, 'now') ON CONFLICT(user_key, character_id) DO UPDATE SET config=excluded.config",
            (
                user,
                ch,
                json.dumps(
                    {
                        "per_char_ms": 99999,       # 越界 → 回落
                        "delay_mode": "broken",     # 枚举外 → 回落
                        "typing_enabled": "no",     # 类型错 → 回落
                        "max_daily_messages": 12,   # 合法 → 保留
                        "__junk__": 1,              # 未知键 → 忽略
                    }
                ),
            ),
        )

    values = cs.read(user, ch)

    assert values["per_char_ms"] == 45
    assert values["delay_mode"] == "dynamic"
    assert values["typing_enabled"] is True
    assert values["max_daily_messages"] == 12
    assert "__junk__" not in values


def test_seed_from_defaults_materializes_current_defaults() -> None:
    """2026-11-08：物化初值 = 当前出厂默认（旧 per-user 层已退役，不再隐形继承）。"""
    user, ch = _keys("seed")
    values = cs.seed_from_defaults(user, ch)

    assert cs.has_row(user, ch)
    assert values["per_char_ms"] == cs.global_defaults()["per_char_ms"]
    assert values["max_daily_messages"] == cs.global_defaults()["max_daily_messages"]
    assert values["reply_mode"] == ""


def test_backfill_skips_existing_rows() -> None:
    user_a, ch_a = _keys("backfill_a")
    user_b, ch_b = _keys("backfill_b")
    cs.read(user_a, ch_a)  # 已存在

    created = cs.backfill_pairs([(user_a, ch_a), (user_b, ch_b)])

    assert created == 1
    assert cs.has_row(user_b, ch_b)

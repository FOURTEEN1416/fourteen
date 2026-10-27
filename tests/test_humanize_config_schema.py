"""拟人度声明式字段表（G-1）回归。

覆盖设计《2026-10-27-仿真人化引入方案》§3.1 P0-A：

- :data:`utils.humanize_config.HUMANIZE_FIELDS` 是唯一字段真源，
  ``DEFAULTS`` / ``INT_BOUNDS`` / ``WRITABLE_FIELDS`` 全部由它派生
  （v1.56 R7 键漂移 P0 教训：禁止多处字面量分叉）；
- 默认值必须落在各自字段边界内；``max_segments`` 只读；
- **严格写**（API/写入路径）：未知键、类型不符、越界、跨字段冲突 →
  ``ValueError`` 且真源文件保持原样；
- **宽松读**（磁盘手改）：坏值逐字段回落默认，跨字段倒置两侧愈合，不抛异常。

数据全部合成：``_CONFIG_PATH`` / ``_OVERRIDE_DB_PATH`` 经 fixture 重定向到
per-test 临时路径，**绝不触碰**开发机真实 ``data/``。
"""

from __future__ import annotations

import json

import pytest

from utils import humanize_config as hc


@pytest.fixture
def hm_tmp(monkeypatch, tmp_path):
    """隔离配置文件与按用户覆盖库。"""
    monkeypatch.setattr(hc, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    monkeypatch.setattr(hc, "_OVERRIDE_DB_PATH", tmp_path / "humanize_override.db")
    return tmp_path / "scheduler_config.json"


# ══════════════════════════════════════════════════════════
#  字段表 → 派生常量（单一真源）
# ══════════════════════════════════════════════════════════


def test_field_table_is_single_source():
    """DEFAULTS/INT_BOUNDS/WRITABLE_FIELDS 必须与字段表逐项一致。"""
    names = [f.name for f in hc.HUMANIZE_FIELDS]
    assert len(names) == len(set(names)), "字段名不得重复"

    writable = {f.name for f in hc.HUMANIZE_FIELDS if f.writable}
    assert set(hc.WRITABLE_FIELDS) == writable
    assert set(hc.DEFAULTS) == writable
    for name, default in hc.DEFAULTS.items():
        field = next(f for f in hc.HUMANIZE_FIELDS if f.name == name)
        assert field.default == default

    for name, (lo, hi) in hc.INT_BOUNDS.items():
        field = next(f for f in hc.HUMANIZE_FIELDS if f.name == name)
        assert field.type == "int"
        assert field.bounds == (lo, hi)
        assert lo <= field.default <= hi, f"{name} 默认值不在边界内"


def test_defaults_within_bounds_and_stable():
    """兜底断言：默认值组合不越界，且与既有口径一致（零行为变更）。"""
    for name, (lo, hi) in hc.INT_BOUNDS.items():
        assert lo <= hc.DEFAULTS[name] <= hi
    assert hc.DEFAULTS["delay_mode"] == "dynamic"
    assert hc.DEFAULTS["per_char_ms"] == 45
    assert hc.DEFAULTS["delay_floor_ms"] == 400
    assert hc.DEFAULTS["delay_ceil_ms"] == 1600
    assert hc.DEFAULTS["user_override_enabled"] is True


def test_max_segments_choices(hm_tmp):
    """Q6：拆条上限可配 2/3/4（默认 2），其余值拒绝；全局开关、不支持按用户覆盖。"""
    assert hc.HumanizeConfig().max_segments == hc.MAX_SEGMENTS == 2
    for cap in (2, 3, 4):
        hc.write_humanize(max_segments=cap)
        assert hc.read_humanize().max_segments == cap
        raw = json.loads(hm_tmp.read_text(encoding="utf-8"))
        assert raw["humanize"]["max_segments"] == cap
    with pytest.raises(ValueError):
        hc.write_humanize(max_segments=5)
    with pytest.raises(ValueError):
        hc.write_humanize(user_key="2:alice@im.wechat", max_segments=3)
    # 磁盘坏值宽松回落默认
    hm_tmp.write_text(json.dumps({"humanize": {"max_segments": 9}}), encoding="utf-8")
    assert hc.read_humanize().max_segments == 2


def test_user_override_switch_is_global_only(hm_tmp):
    """user_override_enabled 是全局开关，字段表标记 per_user=False。"""
    field = next(f for f in hc.HUMANIZE_FIELDS if f.name == "user_override_enabled")
    assert field.per_user is False
    hc.write_humanize(user_override_enabled=False)
    assert hc.read_humanize().user_override_enabled is False
    with pytest.raises(ValueError):
        hc.write_humanize(user_key="2:alice@im.wechat", user_override_enabled=False)


# ══════════════════════════════════════════════════════════
#  严格写
# ══════════════════════════════════════════════════════════


def test_strict_write_rejects_unknown_field(hm_tmp):
    with pytest.raises(ValueError):
        hc.write_humanize(bogus_field=1)


def test_strict_write_rejects_out_of_bounds(hm_tmp):
    with pytest.raises(ValueError):
        hc.write_humanize(per_char_ms=4)
    with pytest.raises(ValueError):
        hc.write_humanize(per_char_ms=201)
    with pytest.raises(ValueError):
        hc.write_humanize(delay_mode="turbo")


def test_strict_write_rejects_wrong_type(hm_tmp):
    with pytest.raises(ValueError):
        hc.write_humanize(per_char_ms="45")
    with pytest.raises(ValueError):
        hc.write_humanize(proactive_split="yes")


def test_strict_write_rejects_cross_field_conflict(hm_tmp):
    with pytest.raises(ValueError):
        hc.write_humanize(random_min_ms=2000, random_max_ms=1000)
    with pytest.raises(ValueError):
        hc.write_humanize(delay_floor_ms=5000, delay_ceil_ms=1000)


def test_failed_write_leaves_file_unchanged(hm_tmp):
    """校验失败必须在写盘前抛出：文件不得出现半份配置。"""
    hc.write_humanize(per_char_ms=60)
    before = hm_tmp.read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        hc.write_humanize(per_char_ms=999)
    assert hm_tmp.read_text(encoding="utf-8") == before
    assert hc.read_humanize().per_char_ms == 60


# ══════════════════════════════════════════════════════════
#  宽松读（磁盘手改）
# ══════════════════════════════════════════════════════════


def test_lenient_read_bad_values_fall_back_per_field(hm_tmp):
    """坏值只丢该字段：类型错/越界 → 默认，合法字段照常生效。"""
    hm_tmp.write_text(
        json.dumps(
            {
                "humanize": {
                    "delay_mode": "bogus",
                    "per_char_ms": "oops",
                    "fixed_ms": 99999,
                    "random_min_ms": 300,
                    "random_max_ms": 800,
                    "proactive_split": "yes",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = hc.read_humanize()
    assert cfg.delay_mode == hc.DEFAULT_DELAY_MODE
    assert cfg.per_char_ms == hc.DEFAULTS["per_char_ms"]
    assert cfg.fixed_ms == hc.DEFAULTS["fixed_ms"]
    assert cfg.random_min_ms == 300
    assert cfg.random_max_ms == 800
    assert cfg.proactive_split is False


def test_lenient_read_heals_cross_field(hm_tmp):
    """跨字段倒置两侧一并回落默认（否则 randint(min,max) 会炸在投递时刻）。"""
    hm_tmp.write_text(
        json.dumps(
            {"humanize": {"random_min_ms": 9000, "random_max_ms": 1000}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = hc.read_humanize()
    assert cfg.random_min_ms == hc.DEFAULTS["random_min_ms"]
    assert cfg.random_max_ms == hc.DEFAULTS["random_max_ms"]

    hm_tmp.write_text(
        json.dumps(
            {"humanize": {"delay_floor_ms": 9000, "delay_ceil_ms": 1000}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = hc.read_humanize()
    assert cfg.delay_floor_ms == hc.DEFAULTS["delay_floor_ms"]
    assert cfg.delay_ceil_ms == hc.DEFAULTS["delay_ceil_ms"]


def test_missing_block_uses_defaults(hm_tmp):
    cfg = hc.read_humanize()
    assert cfg.delay_mode == "dynamic"
    assert cfg.per_char_ms == 45
    assert cfg.user_override_enabled is True

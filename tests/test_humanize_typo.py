"""沉浸式可选口误（Q4）回归。

用户裁决：仅沉浸模式可选、默认关闭。机制约束：
- ``typo_rate=0`` 恒不改动；未达概率阈值不改动；短文本（<8 字）不改动；
- 只做**一次相邻汉字换位**（字符多重集不变、长度不变、不碰数字/英文/标点）；
- 无可用汉字位置时原样返回；
- 接线：``_finalize_reply`` 仅在 immersive 模式应用（源码级钉住）。
"""

from __future__ import annotations

import inspect

from utils.humanize_config import HumanizeConfig
from utils.humanize_typo import apply_typo_once, maybe_apply_typo


def _cfg(**kw) -> HumanizeConfig:
    return HumanizeConfig(**kw)


def test_rate_zero_keeps_text():
    text = "今天天气真不错啊，要不要出去走走"
    assert maybe_apply_typo(text, _cfg(typo_rate=0)) == text


def test_rate_not_reached_keeps_text():
    text = "今天天气真不错啊，要不要出去走走"
    assert maybe_apply_typo(text, _cfg(typo_rate=1), rng=lambda: 0.99) == text


def test_full_rate_applies_one_adjacent_swap():
    text = "今天天气真不错啊，要不要出去走走"
    out = maybe_apply_typo(text, _cfg(typo_rate=100), rng=lambda: 0.0)
    assert out != text
    assert len(out) == len(text)
    assert sorted(out) == sorted(text), "换位不得增删字符"
    diff = [i for i, (a, b) in enumerate(zip(text, out, strict=True)) if a != b]
    assert len(diff) == 2 and diff[1] == diff[0] + 1, "必须且仅一处相邻换位"


def test_no_han_text_unchanged():
    text = "hello 12345, ok!"
    assert apply_typo_once(text, rng=lambda: 0.0) == text


def test_short_text_unchanged():
    assert maybe_apply_typo("你好", _cfg(typo_rate=100), rng=lambda: 0.0) == "你好"


def test_apply_once_never_touches_numbers():
    text = "我有3个苹果和4个梨子"
    out = apply_typo_once(text, rng=lambda: 0.0)
    assert "3" in out and "4" in out
    assert len(out) == len(text)


def test_finalize_reply_wires_typo_immersive_only():
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    src = inspect.getsource(OptimizedOrchestrator._finalize_reply)
    assert "maybe_apply_typo" in src
    assert 'effective_mode == "immersive"' in src

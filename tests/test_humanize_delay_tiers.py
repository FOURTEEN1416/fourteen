"""拟人度「段间停顿档位」回归（``wechat_direct.wechat_connector._segment_delay``）。

覆盖设计契约里由连接器独占的三块（``wiring_spec`` 第 1 步 / ``humanize_schema``）：

1. ``_segment_delay`` 支持档位（fast/normal/slow/random/fixed/auto，auto≡契约
   dynamic），**默认档位逐位复现旧式** ``max(0.4, min(1.6, len(prev)*0.045))``。
2. ``split_reply_for_wechat`` 参数化 max_segments/max_chars，**默认值不变（2/45）**。
3. 「纯指令零延时」判定复用 ``OUTBOUND_CHANNEL_MARKERS`` +
   ``prompt_sanitize._strip_system_markers`` 单一真源（**不新开清单**）。
4. ``_send_reply_segments`` → 公有 ``send_reply_segments`` 后受理前缀语义不变。

数据全部合成：``utils.humanize_config._CONFIG_PATH`` 经 monkeypatch 重定向到
per-test 临时文件，**绝不触碰**开发机真实 ``data/scheduler_config.json``。
"""

from __future__ import annotations

import inspect

import pytest

from wechat_direct.wechat_connector import (
    _segment_delay,
    is_pure_instruction,
    split_reply_for_wechat,
)


@pytest.fixture
def hm_file(tmp_path, monkeypatch):
    """把拟人度真源重定向到 per-test 临时文件（禁止写开发机真实 data/）。"""
    from utils import humanize_config

    path = tmp_path / "scheduler_config.json"
    monkeypatch.setattr(humanize_config, "_CONFIG_PATH", path)
    return path


def test_conftest_sandboxes_humanize_config_path():
    """兜底防线（复审 P1「隔离防线缺口」）：``tests/conftest.py`` 的进程级
    沙箱清单必须包含 ``utils.humanize_config._CONFIG_PATH``。

    与 ``ProactiveScheduler._CONFIG_PATH`` 是**同一个物理文件**
    ``data/scheduler_config.json``（`.gitignore` 忽略、git 不可见）——任一测试
    漏带本文件自带的 ``hm_file`` fixture 就会直写开发机真实文件，且污染**不会**
    被 git 发现（与 2026-09-22 块E 自伤事故同型）。

    断言方式：本用例**不**用 ``hm_file``，直接读 autouse 夹具重定向后的生效值
    —— 必须落在 pytest 临时根内，而非仓库 ``data/``。
    """
    from pathlib import Path

    from utils import humanize_config

    effective = Path(humanize_config._CONFIG_PATH)
    repo_data = (Path(__file__).resolve().parents[1] / "data").resolve()
    assert effective.name == "scheduler_config.json"
    assert effective != repo_data / "scheduler_config.json", (
        "conftest 未隔离 humanize 真源 —— 测试会直写开发机真实 data/scheduler_config.json"
    )


# ═══════════════════════════════════════════════════════════
# 1. 档位：默认零变更 + 各档语义
# ═══════════════════════════════════════════════════════════


def test_signature_keeps_single_positional_param():
    """既有契约以 ``lambda text: 0`` monkeypatch 本函数（单参位置调用）——
    ``mode`` 只能是 keyword-only，不得成为第二个位置参数。"""
    params = list(inspect.signature(_segment_delay).parameters.values())
    assert len(params) == 2
    assert params[0].name == "prev"
    assert params[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params[1].name == "mode"
    assert params[1].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[1].default is None


@pytest.mark.parametrize("n", [0, 1, 5, 9, 20, 40, 100, 200])
def test_default_mode_is_bitwise_identical_to_legacy_formula(n, hm_file):
    """零行为变更核心断言：默认档位逐位 == 旧式（不是「近似」，是 ``==``）。

    ``len*45/1000.0`` 与 ``len*0.045`` 在 n=9/20 等处差 1 ULP，故实现必须把
    ms→s 换算放在**参数层**（``45/1000.0 == 0.045``）；本断言即钉死这一点。
    """
    legacy = max(0.4, min(1.6, n * 0.045))
    assert _segment_delay("啊" * n) == legacy
    assert _segment_delay("啊" * n, mode="auto") == legacy


def test_default_mode_is_bounded(hm_file):
    for n in (0, 1, 200, 5000):
        assert 0.4 <= _segment_delay("啊" * n) <= 1.6


def test_fast_normal_slow_presets(hm_file):
    """fast/normal/slow = 25/45/90 ms/字（经 floor/ceil 钳制）。"""
    assert _segment_delay("啊" * 20, mode="normal") == pytest.approx(0.9)
    assert _segment_delay("啊" * 20, mode="fast") == pytest.approx(0.5)
    # slow: 20*90/1000 = 1.8 → 上钳 1.6
    assert _segment_delay("啊" * 20, mode="slow") == pytest.approx(1.6)
    # 下钳：短句仍 ≥ 0.4
    assert _segment_delay("啊", mode="fast") == pytest.approx(0.4)
    # normal 与默认档位（default per_char_ms=45）同值
    assert _segment_delay("啊" * 20, mode="normal") == _segment_delay("啊" * 20, mode="auto")


def test_fixed_random_off(hm_file):
    assert _segment_delay("abc", mode="fixed") == pytest.approx(0.6)  # 默认 fixed_ms=600
    assert _segment_delay("abc", mode="off") == 0.0
    for _ in range(50):  # 默认 random 区间 400..1600ms，且受 floor/ceil 钳制
        v = _segment_delay("abc", mode="random")
        assert 0.4 <= v <= 1.6
        assert v in {ms / 1000.0 for ms in range(400, 1601)}


def test_unknown_mode_falls_back_to_dynamic(hm_file):
    assert _segment_delay("啊" * 20, mode="bogus") == _segment_delay("啊" * 20, mode="auto")


# ═══════════════════════════════════════════════════════════
# 3. 纯指令零延时（复用既有标记真源，不新开清单）
# ═══════════════════════════════════════════════════════════


@pytest.mark.parametrize("text", [
    "[主动]",
    "[定时提醒]",
    "[主动] [10-15 18:27]",
    "[定时提醒] [10-15 18:27]",
])
def test_pure_instruction_detected_and_zero_delay(text, hm_file):
    assert is_pure_instruction(text) is True
    assert _segment_delay(text) == 0.0


@pytest.mark.parametrize("text", [
    "[主动] [10-15 18:27] 七点吃饭啊",  # 标记后有正文 → 不是纯指令
    "[微笑]",                          # 微信表情不是系统标记
    "在呢",
    "",
    "   ",
])
def test_non_instruction_is_not_zero_delay(text, hm_file):
    assert is_pure_instruction(text) is False
    if text.strip():
        assert _segment_delay(text) >= 0.4  # 仍走有界延时


def test_pure_instruction_reuses_outbound_marker_source(monkeypatch):
    """真源跟随：``OUTBOUND_CHANNEL_MARKERS`` 里新增的标记应被本判定识别。"""
    from orchestrator import context_budget

    monkeypatch.setitem(context_budget.OUTBOUND_CHANNEL_MARKERS, "_test_chan", "[自检]")
    assert is_pure_instruction("[自检]") is True


# ═══════════════════════════════════════════════════════════
# 4. 配置热读驱动 + 读取失败 fail-soft
# ═══════════════════════════════════════════════════════════


def test_config_drives_delay_mode_and_per_char(hm_file):
    from utils import humanize_config

    humanize_config.write_humanize(delay_mode="fixed", fixed_ms=900)
    assert _segment_delay("啊" * 20) == pytest.approx(0.9)

    humanize_config.write_humanize(delay_mode="off")
    assert _segment_delay("啊" * 20) == 0.0

    humanize_config.write_humanize(delay_mode="dynamic", per_char_ms=100)
    # 20*100/1000 = 2.0 → 上钳 1.6（floor/ceil 默认 400/1600）
    assert _segment_delay("啊" * 20) == pytest.approx(1.6)
    assert _segment_delay("啊" * 4) == pytest.approx(0.4)  # 4*100/1000=0.4


def test_config_unreadable_falls_back_to_legacy(hm_file, monkeypatch):
    """配置不可读绝不能把整条回复截断（段循环对异常是 break）→ fail-soft 旧式。"""
    from utils import humanize_config

    def _boom():
        raise RuntimeError("simulated config read failure")

    monkeypatch.setattr(humanize_config, "read_humanize", _boom)
    assert _segment_delay("啊" * 20) == max(0.4, min(1.6, 20 * 0.045))


def test_bracket_delay_inert_by_default_then_effective(hm_file):
    from utils import humanize_config

    bracket = "（她笑着点了点头）"
    # 默认 bracket_delay_ms=0 ⇒ 惰性：括号段与普通段同走 dynamic 公式
    assert _segment_delay(bracket) == _segment_delay("啊" * len(bracket))
    humanize_config.write_humanize(bracket_delay_ms=1200)
    assert _segment_delay(bracket) == pytest.approx(1.2)
    assert _segment_delay("啊" * len(bracket)) == _segment_delay("啊" * len(bracket))


# ═══════════════════════════════════════════════════════════
# 2. split_reply_for_wechat 参数化 + 默认值（Q6 起默认 3 段 / 45 字）
# ═══════════════════════════════════════════════════════════


def test_split_defaults_unchanged():
    from utils import humanize_config as hc

    params = inspect.signature(split_reply_for_wechat).parameters
    assert params["max_segments"].default == hc.MAX_SEGMENTS == 3
    assert params["max_chars"].default == 45


def test_split_default_behavior_capped_at_three_preserving_content():
    reply = "\n".join(f"第{i}句" for i in range(12))
    segs = split_reply_for_wechat(reply)
    assert len(segs) == 3
    assert "\n".join(segs) == reply


def test_split_accepts_explicit_max_chars_is_lossless():
    reply = "一。" * 30
    segs = split_reply_for_wechat(reply, max_chars=10)
    assert len(segs) == 3
    assert "".join(segs) == reply


# ═══════════════════════════════════════════════════════════
# 4'. 公有化 send_reply_segments 后受理前缀语义不变
# ═══════════════════════════════════════════════════════════


def test_segment_method_is_public_without_private_shim():
    from wechat_direct.wechat_connector import WeChatConnector

    assert hasattr(WeChatConnector, "send_reply_segments")
    assert not hasattr(WeChatConnector, "_send_reply_segments"), (
        "契约第 1 步要求单一 owner 公有化，不留 '_' 别名 shim"
    )


def test_public_send_reply_segments_returns_only_accepted_prefix(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(SimpleNamespace(), owner_user_id=42)
    c.token = "test-token"
    monkeypatch.setattr(c, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(c, "_save_context_tokens", lambda: None)
    monkeypatch.setattr(wc, "_segment_delay", lambda text: 0)
    calls: list[str] = []

    def send(**kwargs):
        calls.append(kwargs["text"])
        return {"ret": 0 if len(calls) == 1 else -2}  # 第 2 段被拒

    monkeypatch.setattr(wc, "_send_text", send)
    accepted = c.send_reply_segments("peer", "第一段\n第二段\n第三段")
    assert calls == ["第一段", "第二段"], "第 2 段被拒后不再尝试第 3 段"
    assert accepted == "第一段", "只返回 API 已受理前缀，失败段及后续不入历史"


# ═══════════════════════════════════════════════════════════
# 5. 跨文件单一真源（2026-10-16 复审 P2 词表分裂 / P3 常量镜像）
# ═══════════════════════════════════════════════════════════


def test_delay_mode_pattern_derives_from_single_source():
    """API 层的 ``delay_mode`` pattern 必须由 ``DELAY_MODES`` 派生，不另立字面量。

    旧实现 ``api/routers/training_routes.py`` 硬编码 ``^(dynamic|fixed|random|off)$``，
    与 ``utils/humanize_config.DELAY_MODES`` 无交叉约束 —— 新增档位两处静默分叉。
    """
    from utils import humanize_config as hc

    expected = f"^({'|'.join(hc.DELAY_MODES)})$"
    assert expected == hc.DELAY_MODE_PATTERN, (
        f"API 层 pattern 未由 DELAY_MODES 派生（实得 {hc.DELAY_MODE_PATTERN!r}）"
    )

    import api.routers.training_routes as tr

    field = tr.HumanizeConfigRequest.model_fields["delay_mode"]
    patterns = [
        getattr(m, "pattern", None) for m in field.metadata
        if getattr(m, "pattern", None) is not None
    ]
    assert patterns == [hc.DELAY_MODE_PATTERN], (
        f"API pattern 未与 DELAY_MODES 同源（实得 {patterns}）"
    )


def test_connector_tier_vocabulary_covers_persistable_modes():
    """连接器档位词表必须**包含**全部可落盘档位（``DELAY_MODES``），
    且只多出 fast/normal/slow 三个打字速度预设（不可落盘）。"""
    from utils import humanize_config as hc
    from wechat_direct import wechat_connector as wc

    assert set(hc.DELAY_MODES) <= set(wc._KNOWN_TIERS)
    extra = set(wc._KNOWN_TIERS) - set(hc.DELAY_MODES) - {"auto"}
    assert extra <= {"fast", "normal", "slow"}, f"出现未登记的档位：{extra}"


def test_max_segments_single_source_across_connector_and_frontend():
    """``MAX_SEGMENTS`` 的跨文件单一真源（复审 P3「常量镜像」）。

    真源 = ``utils/humanize_config.MAX_SEGMENTS``（2026-10-27 Q6 起默认 3）；
    连接器只许引用别名，``frontend/src/store/humanizeStore.ts`` 的
    ``HUMANIZE_MAX_SEGMENTS`` 只许等于同一值（无跨语言 import，故以文本约束钉死不漂移）。
    """
    from pathlib import Path

    from utils import humanize_config as hc
    from wechat_direct import wechat_connector as wc

    assert wc._SPLIT_MAX_SEGMENTS is hc.MAX_SEGMENTS, (
        "连接器拆条上限未引用真源（应为同一对象别名，而非等值字面量）"
    )
    assert hc.MAX_SEGMENTS == 3

    store = (
        Path(__file__).resolve().parents[1]
        / "frontend" / "src" / "store" / "humanizeStore.ts"
    ).read_text(encoding="utf-8")
    assert f"export const HUMANIZE_MAX_SEGMENTS = {hc.MAX_SEGMENTS}" in store, (
        "前端 HUMANIZE_MAX_SEGMENTS 与后端 MAX_SEGMENTS 漂移"
    )

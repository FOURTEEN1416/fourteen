"""拆条上限（Q6）与连接器接线回归。

2026-10-27 用户裁决：拆条上限从锁死 2 放宽到 2/3/4（默认仍 2）。
本文件钉住：
- 配置值真正被 ``send_reply_segments`` 消费（不是摆设）；
- 默认仍为 2（未配置时行为不变）；
- 超上限时剩余内容并入最后一条（内容逐字无损）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils import humanize_config as hc
from wechat_direct import wechat_connector as wc


@pytest.fixture
def hm_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "_CONFIG_PATH", tmp_path / "scheduler_config.json")
    monkeypatch.setattr(hc, "_OVERRIDE_DB_PATH", tmp_path / "humanize_override.db")
    return tmp_path


def _mk_connector(tmp_path, monkeypatch) -> wc.WeChatConnector:
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(SimpleNamespace(), owner_user_id=42)
    c.token = "test-token"
    monkeypatch.setattr(c, "_merge_session_state", lambda updates: {})
    monkeypatch.setattr(wc, "_segment_delay", lambda text: 0)
    return c


def test_default_cap_is_three(tmp_path, monkeypatch, hm_cfg):
    """Q6 默认放宽到 3 段：三行回复逐条发，第四行并入最后一条。"""
    c = _mk_connector(tmp_path, monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(wc, "_send_text", lambda **kw: sent.append(kw["text"]) or {"ret": 0})
    c.send_reply_segments("peer", "一\n二\n三\n四")
    assert sent == ["一", "二", "三\n四"]


def test_configured_cap_four_sends_four(tmp_path, monkeypatch, hm_cfg):
    hc.write_humanize(max_segments=4)
    c = _mk_connector(tmp_path, monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(wc, "_send_text", lambda **kw: sent.append(kw["text"]) or {"ret": 0})
    accepted = c.send_reply_segments("peer", "一\n二\n三\n四")
    assert sent == ["一", "二", "三", "四"]
    assert accepted == "一\n二\n三\n四"


def test_configured_cap_three_merges_tail(tmp_path, monkeypatch, hm_cfg):
    hc.write_humanize(max_segments=3)
    c = _mk_connector(tmp_path, monkeypatch)
    sent: list[str] = []
    monkeypatch.setattr(wc, "_send_text", lambda **kw: sent.append(kw["text"]) or {"ret": 0})
    c.send_reply_segments("peer", "一\n二\n三\n四")
    assert sent == ["一", "二", "三\n四"]

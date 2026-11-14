"""A2 控制面 kind 归一（2026-11-12）。

覆盖契约：
- ``_drain_outbox`` 对 ``proactive`` / ``important_date`` / ``manual`` /
  ``reminder`` 四种 kind 统一调用 ``utils.humanize_delivery.direct_send_proactive_result``
  （返回 ``(accepted_any, complete, accepted_body)``）；
- 未知 kind 保持 ``self.send_text`` 路径；
- ``send_text`` 抛异常时仍以 ``complete_send(ok=False)`` 回执，不向上抛。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from wechat_direct import wechat_connector as wc

OWNER = 42
PEER = "wx_a2"
SESSION_KEY = "42:wx_a2"
TEXT = "记得吃药"


class _FakePlane:
    """最小控制面替身：一次投递行 + 回执记录。"""

    def __init__(self, row: dict) -> None:
        self._rows = [row]
        self.completed: list[dict] = []

    def claim_next_send(self, **kwargs):
        return self._rows.pop(0) if self._rows else None

    def complete_send(self, cid, ok, reason="", slot=None, accepted_text="",
                      claimed_by=None):
        self.completed.append(
            {
                "id": cid, "ok": ok, "reason": reason, "slot": slot,
                "accepted_text": accepted_text,
            }
        )


@pytest.fixture()
def connector(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(
        SimpleNamespace(), owner_user_id=OWNER, session_dir=tmp_path
    )
    c.token = "tok"
    return c


def _drain(connector, monkeypatch, kind: str):
    row = {
        "id": 11,
        "message": TEXT,
        "session_key": SESSION_KEY,
        "peer": PEER,
        "kind": kind,
    }
    plane = _FakePlane(row)
    monkeypatch.setattr(wc, "_plane", lambda: plane)
    connector._drain_outbox()
    return plane


def _patch_direct_send(
    monkeypatch, *, result: bool = True, complete: bool | None = None,
    accepted_text: str = TEXT,
):
    from utils import humanize_delivery

    calls: list[tuple[object, str, str]] = []

    def fake(connector, peer, msg):
        calls.append((connector, peer, msg))
        return result, (result if complete is None else complete), accepted_text

    monkeypatch.setattr(humanize_delivery, "direct_send_proactive_result", fake)
    return calls


def _patch_send_text(monkeypatch, connector, *, result=True, raises=False):
    calls: list[tuple[str, str]] = []

    def fake(text, to_user=""):
        calls.append((text, to_user))
        if raises:
            raise RuntimeError("send_text boom")
        return result

    monkeypatch.setattr(connector, "send_text", fake)
    return calls


# ---------------------------------------------------------------------------
# 四条出站线统一走 direct_send_proactive_result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", ["proactive", "important_date", "manual", "reminder"]
)
def test_known_kinds_go_through_direct_send_proactive_result(
    connector, monkeypatch, kind
):
    direct_calls = _patch_direct_send(monkeypatch)
    text_calls = _patch_send_text(monkeypatch, connector)

    plane = _drain(connector, monkeypatch, kind)

    assert direct_calls == [(connector, PEER, TEXT)], (
        f"kind={kind} 未走 direct_send_proactive_result"
    )
    assert text_calls == [], f"kind={kind} 不得绕过统一真源直调 send_text"
    assert plane.completed == [
        {
            "id": 11, "ok": True, "reason": "", "slot": connector.slot,
            "accepted_text": TEXT,
        }
    ]


# ---------------------------------------------------------------------------
# 未知 kind 保持 send_text
# ---------------------------------------------------------------------------


def test_unknown_kind_keeps_send_text(connector, monkeypatch):
    direct_calls = _patch_direct_send(monkeypatch)
    text_calls = _patch_send_text(monkeypatch, connector)

    plane = _drain(connector, monkeypatch, "broadcast")

    assert direct_calls == []
    assert text_calls == [(TEXT, PEER)]
    assert plane.completed == [
        {
            "id": 11, "ok": True, "reason": "", "slot": connector.slot,
            "accepted_text": TEXT,
        }
    ]


def test_reminder_partial_prefix_accepted_with_text(connector, monkeypatch):
    """2026-11-13 提醒拆条：部分受理按成功回执，并把前缀写回。"""
    _patch_direct_send(
        monkeypatch, result=True, complete=False, accepted_text="记得",
    )

    plane = _drain(connector, monkeypatch, "reminder")

    assert plane.completed == [
        {
            "id": 11, "ok": True, "reason": "", "slot": connector.slot,
            "accepted_text": "记得",
        }
    ]


def test_proactive_partial_prefix_remains_failed(connector, monkeypatch):
    """主动线保持完整受理才算成功（既有语义不动）。"""
    _patch_direct_send(
        monkeypatch, result=True, complete=False, accepted_text="前半",
    )

    plane = _drain(connector, monkeypatch, "proactive")

    assert plane.completed == [
        {
            "id": 11,
            "ok": False,
            "reason": "send_api_rejected",
            "slot": connector.slot,
            "accepted_text": "前半",
        }
    ]


# ---------------------------------------------------------------------------
# send_text 异常 → complete_send(ok=False)，不向上抛
# ---------------------------------------------------------------------------


def test_send_text_exception_completes_failed(connector, monkeypatch):
    _patch_direct_send(monkeypatch)
    _patch_send_text(monkeypatch, connector, raises=True)

    plane = _drain(connector, monkeypatch, "broadcast")

    assert plane.completed == [
        {
            "id": 11,
            "ok": False,
            "reason": "send_api_rejected",
            "slot": connector.slot,
            "accepted_text": "",
        }
    ]

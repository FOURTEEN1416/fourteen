"""会话键格式唯一真源 + 定向投递路由回归（2026-09-21 重扫）。

钉住的事实来自**生产取样**（`/opt/ai-girlfriend/data/sqlite.db`）:

    chat_history.session_id = 2:o9cq80-_yMyfY247BTeFL5JvNgoI@im.wechat
                              4:o9cq805ifqDz9eaFN5YWuUFHF-10@im.wechat

即 `@im.wechat` 是 **wxid 自带后缀**，不是可剥离的通道标记 ——
剥掉会让发送目标变成不存在的账号。

三类回归：
  ① `utils.session_key` 解析口径（三种方言 + 遗留一段式）；
  ② `scheduler._send_targeted` 路由：web 键 `N:web:hex` **不得**被当成微信
     （旧规则「含 `:` 且左段数字」会误判 → 走微信通道失败后按「微信绝不广播」
     拒绝回退 → web 会话定向消息静默丢失，且把 wechat 通道实例置 None）；
  ③ `reminder_delivery._send_to_session` 对微信键走微信发送器、对 web 键走 ws，
     且投递目标保留 `@im.wechat` 后缀。
"""

from __future__ import annotations

import asyncio

import pytest

from utils import session_key as sk

WECHAT_PROD = "2:o9cq80-_yMyfY247BTeFL5JvNgoI@im.wechat"


# ── ① 解析口径 ────────────────────────────────────────────

def test_production_wechat_key_parses_with_suffix_intact():
    parsed = sk.parse(WECHAT_PROD)
    assert parsed.owner == 2
    assert parsed.peer == "o9cq80-_yMyfY247BTeFL5JvNgoI@im.wechat", (
        "`@im.wechat` 属 wxid 自身，剥离会导致发送目标错误"
    )
    assert parsed.channel == sk.WECHAT_CHANNEL
    assert parsed.is_wechat and sk.is_wechat_key(WECHAT_PROD)


def test_web_and_ws_keys_are_not_wechat():
    """三段/四段键是 web / WS 方言 —— 这是 scheduler 误判的源头。"""
    assert not sk.is_wechat_key("7:web:ab12cd34")
    assert not sk.is_wechat_key("7:7:web:ab12cd34")
    assert sk.parse("7:web:ab12cd34").channel == "web"
    assert sk.parse("7:web:ab12cd34").peer == "ab12cd34"


def test_two_segment_and_legacy_forms():
    assert sk.is_wechat_key("2:wxid_abc")          # 二段且左段数字
    assert sk.is_wechat_key("wxid_abc@im.wechat")  # 遗留一段式（带通道标记）
    assert not sk.is_wechat_key("")                # 空键
    assert not sk.is_wechat_key("peer_only")       # 无归属裸 peer
    assert sk.peer_of("2:wxid_abc") == "wxid_abc"
    assert sk.peer_of("wxid@im.wechat") == "wxid@im.wechat"
    assert sk.owner_of(WECHAT_PROD) == 2
    assert sk.build(2, "wxid@im.wechat") == "2:wxid@im.wechat"


def test_parse_never_raises_on_garbage():
    for bad in ("", None, ":::", "@", "a:b:c:d:e", "  "):
        sk.parse(bad)  # 不抛即为通过（解析器在热路径上）


def test_peer_character_session_key_delegates_to_owner():
    from wechat_direct.peer_character import session_key

    assert session_key(4, "wxid@im.wechat") == "4:wxid@im.wechat"


def test_bare_peer_from_session_keeps_suffix():
    from shisi.memory.legacy.structured_memory import StructuredMemory

    assert StructuredMemory.bare_peer_from_session(WECHAT_PROD) == (
        "o9cq80-_yMyfY247BTeFL5JvNgoI@im.wechat"
    )


# ── ② scheduler 定向路由 ───────────────────────────────────

class _SchedHarness:
    """最小替身：只提供 _send_targeted 依赖（quiet hours / 通道实例 / 广播）。"""

    def __init__(self, wechat_sender=None):
        from proactive.scheduler import ProactiveScheduler

        self._is_quiet_hours = lambda: False
        self._channel_instances = {"wechat": wechat_sender}
        self.broadcast_calls: list[str] = []

        async def _send_to_all(message: str) -> bool:
            self.broadcast_calls.append(message)
            return True

        self._send_to_all = _send_to_all
        self._targeted = ProactiveScheduler._send_targeted.__get__(self)

    def _is_wechat_session_key(self, key):
        from proactive.scheduler import ProactiveScheduler

        return ProactiveScheduler._is_wechat_session_key(key)


def test_web_session_key_routes_to_broadcast_not_wechat():
    """web 键必须回退 ws 广播；旧实现判成微信后拒发（消息静默丢失）。"""
    calls: list = []

    async def _wechat_sender(message, session_key=None):
        calls.append(session_key)

    h = _SchedHarness(wechat_sender=_wechat_sender)
    assert asyncio.run(h._targeted("hi", "7:web:ab12cd34")) is True
    assert h.broadcast_calls == ["hi"], "web 会话键必须走 ws 广播"
    assert calls == [], "web 会话键不得调用微信通道"


def test_wechat_session_key_never_falls_back_to_broadcast():
    """微信键在通道不可用时绝不广播（跨用户泄漏防护，P1-21）。"""
    h = _SchedHarness(wechat_sender=None)
    assert asyncio.run(h._targeted("hi", WECHAT_PROD)) is False
    assert h.broadcast_calls == []


def test_wechat_session_key_delivers_through_wechat_channel():
    seen: list = []

    async def _wechat_sender(message, session_key=None):
        seen.append((message, session_key))

    h = _SchedHarness(wechat_sender=_wechat_sender)
    assert asyncio.run(h._targeted("hi", WECHAT_PROD)) is True
    assert seen == [("hi", WECHAT_PROD)]
    assert h.broadcast_calls == []


# ── ③ reminder 投递路由 ────────────────────────────────────

class _FakeSm:
    def get_due_reminders(self):
        return []


def _reminder_task(wechat_sender=None, ws_sender=None):
    from proactive.reminder_delivery import ReminderDeliveryTask

    return ReminderDeliveryTask(
        _FakeSm(), llm=None, wechat_sender=wechat_sender, ws_sender=ws_sender,
    )


def test_reminder_routes_wechat_key_to_wechat_sender_with_full_peer():
    seen: list = []

    def _wechat_send(owner_id, peer, text):
        seen.append((owner_id, peer, text))
        return True

    task = _reminder_task(wechat_sender=_wechat_send)
    assert asyncio.run(task._send_to_session(WECHAT_PROD, "起床")) is True
    assert seen == [(2, "o9cq80-_yMyfY247BTeFL5JvNgoI@im.wechat", "起床")], (
        "投递目标必须是完整 wxid（含 @im.wechat）"
    )


def test_reminder_routes_web_key_to_ws_sender():
    ws: list = []
    task = _reminder_task(
        wechat_sender=lambda *a: pytest.fail("web 键不得走微信通道"),
        ws_sender=lambda text: (ws.append(text), True)[1],
    )
    assert asyncio.run(task._send_to_session("7:web:ab12cd34", "喝水")) is True
    assert ws == ["喝水"]


def test_reminder_empty_key_is_noop():
    task = _reminder_task(wechat_sender=lambda *a: True, ws_sender=lambda t: True)
    assert asyncio.run(task._send_to_session("", "x")) is False

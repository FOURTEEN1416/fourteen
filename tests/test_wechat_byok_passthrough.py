"""微信路径的 BYOK 透传回归（2026-09-19）。

用户问：「用户使用自己的 API key 能不能顺利用上，毕竟用户智能体接触到 web 端」。
排查发现**web 端传了 `user_llm_config`（用户 key 生效），微信端从不传**
（`user_scheduler.py` 全文 0 处引用）→ 微信聊天一直用全局 key，
用户在控制台填的自己的 key 形同虚设。
"""

from __future__ import annotations


class _FakeOrch:
    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    async def process_message(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"reply": "ok"}


class _FakeInstance:
    session_id = "sess-1"
    character_card_id = "62105bca"
    total_chats = 0
    last_active = 0.0


def _manager(monkeypatch, orch, llm_cfg_result):
    from user_scheduler import UserManager

    m = UserManager.__new__(UserManager)          # 跳过 __init__（避免起线程/连库）
    m._bindings = {"u1@im.wechat": {"user_id": 7}}
    m._orch = orch
    m._llm_cfg_cache = {}

    monkeypatch.setattr(m, "_get_or_create", lambda uid: _FakeInstance())
    monkeypatch.setattr(m, "_get_character_engine", lambda inst, cid: None)

    async def _fake_cfg(wxid):
        return llm_cfg_result

    monkeypatch.setattr(m, "_get_user_llm_config", _fake_cfg)
    return m


# ── 核心不变量：微信路径必须把用户专属配置传下去 ──────────────

def test_wechat_path_passes_user_llm_config(monkeypatch):
    import asyncio

    orch = _FakeOrch()
    m = _manager(monkeypatch, orch, (7, {"provider": "custom", "api_key": "sk-user"}))

    asyncio.run(m._process_message_inner("u1@im.wechat", "你好"))

    assert len(orch.calls) == 1
    _, kwargs = orch.calls[0]
    assert kwargs.get("user_llm_config") == {"provider": "custom", "api_key": "sk-user"}, \
        "微信路径必须传 user_llm_config，否则用户的 API Key 不生效"
    assert kwargs.get("user_id") == 7


def test_wechat_path_without_user_config_falls_back_to_global(monkeypatch):
    """用户没配 key 时回落全局 gateway：两个参数都应为 None。"""
    import asyncio

    orch = _FakeOrch()
    m = _manager(monkeypatch, orch, (7, None))

    asyncio.run(m._process_message_inner("u1@im.wechat", "你好"))

    _, kwargs = orch.calls[0]
    assert kwargs.get("user_llm_config") is None
    assert kwargs.get("user_id") == 7


def test_wechat_path_still_passes_character_id(monkeypatch):
    """回归保护：加 BYOK 时不得漏掉既有的 character_id（多用户角色隔离）。"""
    import asyncio

    orch = _FakeOrch()
    m = _manager(monkeypatch, orch, (7, None))

    asyncio.run(m._process_message_inner("u1@im.wechat", "你好"))

    _, kwargs = orch.calls[0]
    assert kwargs.get("character_id") == "62105bca"


# ── 无绑定时的行为 ─────────────────────────────────────────

def test_no_binding_yields_no_user_config(monkeypatch):
    """wxid 无绑定时不应查库、也不应报错，直接回落全局。"""
    import asyncio

    from user_scheduler import UserManager

    m = UserManager.__new__(UserManager)
    m._bindings = {}
    m._llm_cfg_cache = {}

    uid, cfg = asyncio.run(m._get_user_llm_config("unknown@im.wechat"))
    assert uid is None
    assert cfg is None

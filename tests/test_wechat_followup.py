"""对话内追问回归（2026-09-19 用户反馈）。

用户报：「只有我发一条消息他才会回一条消息……我不接着发消息他就不回」
—— 要的是真人式的「回复后没等到接话就自己再补一句」，
而不是一问一答的客服/豆包式体验。
"""

from __future__ import annotations

import time

import pytest


class _FakeUserMgr:
    def get_bound_wxids(self):
        return ["u1@im.wechat"]


def _connector(tmp_path, monkeypatch, token="tok"):
    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "CONTEXT_TOKENS_PATH", str(tmp_path / "ctx.json"))
    c = wc.WeChatConnector(_FakeUserMgr())
    c.token = token
    c._context_tokens = {"u1@im.wechat": {"token": "ctx-tok", "ts": time.time()}}
    monkeypatch.setattr(c, "_in_quiet_hours", lambda: False)
    return c


# ── 登记 / 取消 ────────────────────────────────────────────

def test_reply_schedules_followup(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    c._schedule_followup("u1@im.wechat", "还没呢，正想着你呢")

    st = c._pending_followups["u1@im.wechat"]
    assert st["step"] == 0
    assert st["due"] > time.time() + 40          # 首次延迟 45s
    assert st["last_reply"].startswith("还没呢")


def test_user_reply_cancels_pending_followup(tmp_path, monkeypatch):
    """用户接话后不得再追 —— 这是他反馈里最核心的预期。"""
    c = _connector(tmp_path, monkeypatch)
    c._schedule_followup("u1@im.wechat", "在吗")
    assert c._pending_followups

    c._cancel_followup("u1@im.wechat")
    assert c._pending_followups == {}


def test_no_followup_in_quiet_hours(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    monkeypatch.setattr(c, "_in_quiet_hours", lambda: True)
    c._schedule_followup("u1@im.wechat", "晚安")
    assert c._pending_followups == {}


# ── 预算 ───────────────────────────────────────────────────

def test_followup_daily_budget(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    c = _connector(tmp_path, monkeypatch)
    cap = wc.read_follow_up_config()["daily_max"]
    assert c._followup_budget_ok("u1@im.wechat")
    c._followup_daily["u1@im.wechat"] = cap
    assert not c._followup_budget_ok("u1@im.wechat")


# ── 配置读取（web 控制端可调）───────────────────────────────

def test_read_follow_up_config_defaults(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    monkeypatch.setattr(wc, "_SCHEDULER_CONFIG_PATH", tmp_path / "nope.json")
    assert wc.read_follow_up_config() == wc.FOLLOW_UP_DEFAULTS


def test_read_follow_up_config_file_override(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    path = tmp_path / "scheduler_config.json"
    path.write_text(
        '{"quiet_hours": {"start": 23, "end": 7},'
        ' "follow_up": {"enabled": false, "delay1_seconds": 120,'
        ' "delay2_seconds": 300, "daily_max": 3}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(wc, "_SCHEDULER_CONFIG_PATH", path)

    cfg = wc.read_follow_up_config()
    assert cfg == {
        "enabled": False, "delay1_seconds": 120, "delay2_seconds": 300, "daily_max": 3,
    }


def test_read_follow_up_config_clamps_bad_values(tmp_path, monkeypatch):
    """控制端写入非法值不得把行为搞坏（值域守卫）。"""
    from wechat_direct import wechat_connector as wc

    path = tmp_path / "scheduler_config.json"
    path.write_text(
        '{"follow_up": {"delay1_seconds": 99999, "delay2_seconds": 1, "daily_max": 9999}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(wc, "_SCHEDULER_CONFIG_PATH", path)

    cfg = wc.read_follow_up_config()
    assert cfg["delay1_seconds"] == 3600            # 上界
    assert cfg["delay2_seconds"] >= cfg["delay1_seconds"]   # 二次不早于首次
    assert cfg["daily_max"] == 200                  # 上界


def test_read_follow_up_config_broken_file_falls_back(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    path = tmp_path / "scheduler_config.json"
    path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(wc, "_SCHEDULER_CONFIG_PATH", path)
    assert wc.read_follow_up_config() == wc.FOLLOW_UP_DEFAULTS


def test_disabled_config_blocks_scheduling(tmp_path, monkeypatch):
    """控制端关掉开关后不得再登记追问。"""
    from wechat_direct import wechat_connector as wc

    path = tmp_path / "scheduler_config.json"
    path.write_text('{"follow_up": {"enabled": false}}', encoding="utf-8")
    monkeypatch.setattr(wc, "_SCHEDULER_CONFIG_PATH", path)

    c = _connector(tmp_path, monkeypatch)
    c._schedule_followup("u1@im.wechat", "在吗")
    assert c._pending_followups == {}


def test_followup_budget_resets_next_day(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    c._followup_daily["u1@im.wechat"] = 999
    c._followup_daily_date = "2000-01-01"
    assert c._followup_budget_ok("u1@im.wechat")   # 跨日清零
    assert c._followup_daily == {}


# ── 发送与续排 ─────────────────────────────────────────────

def test_send_followup_sends_and_schedules_second_round(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    monkeypatch.setattr(c, "_generate_followup", lambda prompt, last_reply="": "那本书你看完了吗")

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        c, "send_text", lambda text, to_user="": (sent.append((text, to_user)), True)[1],
    )

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "在吗"})

    assert sent == [("那本书你看完了吗", "u1@im.wechat")]
    # 还有第二轮延迟 → 已续排，且 step 递增
    assert c._pending_followups["u1@im.wechat"]["step"] == 1
    assert c._followup_daily["u1@im.wechat"] == 1


def test_send_followup_stops_after_last_round(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    monkeypatch.setattr(c, "_generate_followup", lambda prompt, last_reply="": "风还挺大的")
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": True)

    c._send_followup("u1@im.wechat", {"step": 1, "due": 0, "last_reply": "x"})  # 已是第 2 次
    assert c._pending_followups == {}          # 不再续排，避免无限骚扰


def test_send_followup_skips_when_delivery_fails(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    monkeypatch.setattr(c, "_generate_followup", lambda prompt, last_reply="": "雨停了吗")
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": False)

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "x"})
    assert c._pending_followups == {}          # 未送达不算，也不续排
    assert c._followup_daily == {}


def test_send_followup_respects_budget(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    c = _connector(tmp_path, monkeypatch)
    c._followup_daily["u1@im.wechat"] = wc.read_follow_up_config()["daily_max"]
    c._followup_daily_date = time.strftime("%Y-%m-%d")
    monkeypatch.setattr(c, "_generate_followup", lambda prompt, last_reply="": "花浇完了吗")
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": pytest.fail("超预算不应发送"))

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "x"})


def test_send_followup_skips_empty_generation(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    monkeypatch.setattr(c, "_generate_followup", lambda prompt, last_reply="": "")
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": pytest.fail("空文本不应发送"))
    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "x"})


# ── 生成守卫 ───────────────────────────────────────────────

class _FakeLLM:
    def __init__(self, out: str):
        self.out = out
        self.calls: list[str] = []

    def chat_sync(self, query: str = "", **kw) -> str:
        self.calls.append(query)
        return self.out


def _with_llm(c, out: str) -> _FakeLLM:
    llm = _FakeLLM(out)
    c.orchestrator = type("O", (), {"components": {"llm": llm}})()
    return llm


def test_generate_followup_accepts_normal(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    llm = _with_llm(c, "「那本书你看完了吗？」")
    assert c._generate_followup("p") == "那本书你看完了吗？"
    assert llm.calls == ["p"]


def test_generate_followup_rejects_reasoning_leak(tmp_path, monkeypatch):
    """与主动消息同源的输出清洗：推理腔不得作为消息发出。"""
    c = _connector(tmp_path, monkeypatch)
    _with_llm(c, "02:55属于深夜，不在早安或晚安的特定时间点，但更")
    assert c._generate_followup("p") == ""


def test_generate_followup_rejects_overlong(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    _with_llm(c, "啊" * 200)
    assert c._generate_followup("p") == ""


def test_generate_followup_returns_empty_without_llm(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    c.orchestrator = None
    assert c._generate_followup("p") == ""


# ═══════════════════════════════════════════════════════════════
#  追问必须带真实上下文（2026-09-19 用户反馈）
#  「追问没有和上下文形成逻辑，而是强行地插入一句『在吗？』『人呢？』」
# ═══════════════════════════════════════════════════════════════

def test_prompt_includes_real_conversation_context(tmp_path, monkeypatch):
    """发给 LLM 的追问 prompt 必须含真实往来记录，而不是只有上一句。"""
    c = _connector(tmp_path, monkeypatch)
    c._remember_exchange("u1@im.wechat", "你们那下雨啦？", "嗯，下了一下午了")
    c._remember_exchange("u1@im.wechat", "我在忙呢", "哦，那你先忙")

    seen: list[str] = []

    def _fake_gen(prompt, last_reply=""):
        seen.append(prompt)
        return "那雨停了叫我"

    monkeypatch.setattr(c, "_generate_followup", _fake_gen)
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": True)

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "哦，那你先忙"})

    assert seen, "未生成追问"
    prompt = seen[0]
    assert "你们那下雨啦？" in prompt, "追问 prompt 必须包含对方的原话"
    assert "我在忙呢" in prompt
    assert "接着上面的聊天内容" in prompt


def test_prompt_forbids_generic_nags(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    c._remember_exchange("u1@im.wechat", "想你了呗", "就这点出息")
    seen: list[str] = []
    monkeypatch.setattr(c, "_generate_followup",
                        lambda prompt, last_reply="": (seen.append(prompt), "嗯嗯")[1])
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": True)

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "就这点出息"})
    assert "严禁「在吗」" in seen[0]


def test_generic_nag_is_dropped_even_if_model_returns_it(tmp_path, monkeypatch):
    """防呆：模型无视指令吐通用催促语时必须丢弃（宁可不发）。"""

    c = _connector(tmp_path, monkeypatch)
    llm = _with_llm(c, "在吗？")
    assert c._generate_followup("p", last_reply="就这点出息") == ""
    assert llm.calls == ["p"]          # 确实调用了 LLM，只是结果被守卫丢弃


def test_repeated_last_reply_is_dropped(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    _with_llm(c, "就这点出息")
    assert c._generate_followup("p", last_reply="就这点出息") == ""


def test_remember_exchange_is_bounded(tmp_path, monkeypatch):
    """上下文缓冲不得无限增长。"""
    from wechat_direct import wechat_connector as wc

    c = _connector(tmp_path, monkeypatch)
    for i in range(50):
        c._remember_exchange("u1@im.wechat", f"用户第{i}句", f"回复第{i}句")
    buf = c._recent_exchanges["u1@im.wechat"]
    assert len(buf) <= wc._FOLLOWUP_CONTEXT_TURNS
    assert buf[-1][1] == "回复第49句"

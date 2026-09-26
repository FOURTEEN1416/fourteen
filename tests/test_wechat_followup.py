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


def test_read_follow_up_config_delay2_floor(tmp_path, monkeypatch):
    """④：第二轮追问有 60s 下限。

    生产真源 `data/scheduler_config.json` 曾被写成 `delay2_seconds: 10` —— 两条
    追问相隔 10 秒到达，用户侧看到的就是她连发两句自问自答。下限拦得住任何
    已写入的小值，且不需要改动用户数据文件。
    """
    from wechat_direct import wechat_connector as wc

    path = tmp_path / "scheduler_config.json"
    path.write_text(
        '{"follow_up": {"delay1_seconds": 30, "delay2_seconds": 10}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(wc, "_SCHEDULER_CONFIG_PATH", path)

    cfg = wc.read_follow_up_config()
    assert cfg["delay1_seconds"] == 30
    assert cfg["delay2_seconds"] == 60


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

def _stub_gen(c, monkeypatch, out="那本书你看完了吗", record=None):
    """打桩生成器（2026-09-21 签名扩展：追问生成必须带 session 与真实历史）。"""

    def _gen(prompt, last_reply="", session_key="", history=None, character_id=None):
        if record is not None:
            record.append(
                {"prompt": prompt, "last_reply": last_reply,
                 "session_key": session_key, "history": history}
            )
        return out

    monkeypatch.setattr(c, "_generate_followup", _gen)


def _stub_history(c, messages):
    """钉住会话历史真源（chat_history 的读入口），避免测试依赖真实 DB。"""
    c._session_messages = lambda session_key, keep=10, character_id=None: list(messages)


def test_send_followup_sends_and_schedules_second_round(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    _stub_gen(c, monkeypatch)

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        c, "send_text", lambda text, to_user="": (sent.append((text, to_user)), True)[1],
    )
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(c, "_record_outbound", lambda text, session_key, character_id: recorded.append((text, session_key)))

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "在吗"})

    assert sent == [("那本书你看完了吗", "u1@im.wechat")]
    # 还有第二轮延迟 → 已续排，且 step 递增
    assert c._pending_followups["u1@im.wechat"]["step"] == 1
    assert c._followup_daily["u1@im.wechat"] == 1
    # ① 数据闭环：发出去的这一句必须回写会话历史 —— 不回写则下一轮她不记得
    # 自己问过什么，接着又问一遍（生产实证的自问自答）。
    assert recorded == [("那本书你看完了吗", "u1@im.wechat")]


def test_send_followup_stops_after_last_round(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    _stub_gen(c, monkeypatch, out="风还挺大的")
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": True)

    c._send_followup("u1@im.wechat", {"step": 1, "due": 0, "last_reply": "x"})  # 已是第 2 次
    assert c._pending_followups == {}          # 不再续排，避免无限骚扰


def test_send_followup_skips_when_delivery_fails(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    _stub_gen(c, monkeypatch, out="雨停了吗")
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": False)
    recorded: list[str] = []
    monkeypatch.setattr(c, "_record_outbound", lambda text, session_key, character_id: recorded.append(text))

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "x"})
    assert c._pending_followups == {}          # 未送达不算，也不续排
    assert c._followup_daily == {}
    assert recorded == []                      # 未送达更不得写进历史（否则凭空多出她没说过的话）


def test_send_followup_respects_budget(tmp_path, monkeypatch):
    from wechat_direct import wechat_connector as wc

    c = _connector(tmp_path, monkeypatch)
    c._followup_daily["u1@im.wechat"] = wc.read_follow_up_config()["daily_max"]
    from utils.local_time import now_local

    c._followup_daily_date = now_local().strftime("%Y-%m-%d")
    _stub_gen(c, monkeypatch, out="花浇完了吗")
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": pytest.fail("超预算不应发送"))

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "x"})


def test_send_followup_skips_empty_generation(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    _stub_gen(c, monkeypatch, out="")
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": pytest.fail("空文本不应发送"))
    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "x"})


def test_send_followup_skips_when_user_already_replied(tmp_path, monkeypatch):
    """线程取走待发后用户接了话 → 发前复查最后一条是谁说的，不能追在人家话上。"""
    c = _connector(tmp_path, monkeypatch)
    _stub_history(c, [
        {"role": "assistant", "content": "你那边下雨了吗"},
        {"role": "user", "content": "下了，正躲雨呢"},
    ])
    _stub_gen(c, monkeypatch)
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": pytest.fail("对方已回话不得追问"))

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "你那边下雨了吗"})


def test_send_followup_passes_real_history_with_roles(tmp_path, monkeypatch):
    """④ 生成上下文收口：真实往来以**带 role 的 messages**下传，不是"我/对方"转写。

    自问自答的一条成因：模型分不清哪句是自己说的。文本转写等于把归属交给
    自然语言标签；messages 的 role 才是模型训练时就读得懂的东西。
    """
    c = _connector(tmp_path, monkeypatch)
    _stub_history(c, [
        {"role": "user", "content": "你们那下雨啦？"},
        {"role": "assistant", "content": "嗯，下了一下午了"},
        {"role": "user", "content": "我在忙呢"},
        {"role": "assistant", "content": "哦，那你先忙"},
    ])
    seen: list[dict] = []
    _stub_gen(c, monkeypatch, out="那雨停了叫我", record=seen)
    monkeypatch.setattr(c, "send_text", lambda text, to_user="": True)

    c._send_followup("u1@im.wechat", {"step": 0, "due": 0, "last_reply": "哦，那你先忙"})

    assert seen, "未生成追问"
    call = seen[0]
    assert call["session_key"] == "u1@im.wechat"
    assert [m["role"] for m in call["history"]] == ["user", "assistant", "user", "assistant"]
    assert call["history"][-1]["content"] == "哦，那你先忙"
    assert "延伸你自己" in call["prompt"]
    assert "禁止虚构对方" in call["prompt"]
    assert "哦，那你先忙" in call["prompt"]      # 明确点出她自己最后那句


# ── 生成守卫 ───────────────────────────────────────────────

class _FakeLLM:
    def __init__(self, out: str):
        self.out = out
        self.calls: list[str] = []
        self.kwargs: list[dict] = []

    def chat_sync(self, query: str = "", **kw) -> str:
        self.calls.append(query)
        self.kwargs.append(kw)
        return self.out


def _with_llm(c, out: str) -> _FakeLLM:
    llm = _FakeLLM(out)
    c.orchestrator = type("O", (), {"components": {"llm": llm}})()
    return llm


def _with_persona(c, llm, prompt="你是十四。\n表面傲娇，嘴硬心软", cid_holder=None):
    persona = type("P", (), {"build_system_prompt": staticmethod(
        lambda character_id=None, **kw: (cid_holder.append(character_id) if cid_holder is not None else None) or prompt,
    )})()
    c.orchestrator = type("O", (), {"components": {"llm": llm, "persona": persona}})()
    return persona


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


def test_generate_followup_injects_role_system_and_history(tmp_path, monkeypatch):
    """④：追问不再是裸调用 —— 必须带角色 system 与真实往来 history。"""
    c = _connector(tmp_path, monkeypatch)
    llm = _FakeLLM("那雨停了叫我")
    _with_persona(c, llm, cid_holder=[])
    hist = [{"role": "assistant", "content": "哦，那你先忙"}]
    from unittest.mock import AsyncMock

    from api import byok
    monkeypatch.setattr(byok, "session_llm", AsyncMock(return_value=llm))

    got = c._generate_followup("p", session_key="4:u1@im.wechat", history=hist)

    assert got == "那雨停了叫我"
    kw = llm.kwargs[0]
    assert kw["system_prompt"] == "你是十四。\n表面傲娇，嘴硬心软"
    assert kw["history"] == hist


# ═══════════════════════════════════════════════════════════════
#  追问身份源（2026-09-21 自问自答根治）
# ═══════════════════════════════════════════════════════════════

def test_followup_system_prompt_uses_session_character(tmp_path, monkeypatch):
    """追问的角色必须与该会话绑定的角色卡一致（同一身份源，不另写一份人设）。"""
    c = _connector(tmp_path, monkeypatch)
    c.user_manager = type("M", (), {"get_user_character": staticmethod(lambda uid: "米彩")})()
    captured: list = []
    _with_persona(c, _FakeLLM(""), prompt="你是米彩。", cid_holder=captured)

    assert c._followup_system_prompt("4:u1@im.wechat") == "你是米彩。"
    assert captured == ["米彩"]


def test_followup_system_prompt_falls_back_to_builtin_when_unbound(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    captured: list = []
    _with_persona(c, _FakeLLM(""), prompt="你是十四。", cid_holder=captured)

    assert c._followup_system_prompt("u1@im.wechat") == "你是十四。"
    assert captured == ["default"]              # 内置角色同样使用明确 id


def test_followup_system_prompt_degrades_to_empty(tmp_path, monkeypatch):
    """装配缺失时返回空串（主链不受影响），不得抛异常打断守护线程。"""
    c = _connector(tmp_path, monkeypatch)
    c.orchestrator = None
    assert c._followup_system_prompt("u1@im.wechat") == ""


# ═══════════════════════════════════════════════════════════════
#  上下文唯一真源（2026-09-21）
#  「追问没有和上下文形成逻辑，而是强行地插入一句『在吗？』『人呢？』」
#  —— 旧实现另起一份进程内 deque 缓冲：重启即空、与主链两套真源。
# ═══════════════════════════════════════════════════════════════

def test_no_in_process_history_copy(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    assert not hasattr(c, "_recent_exchanges")
    assert not hasattr(c, "_remember_exchange")
    assert not hasattr(c, "_followup_context")


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


def test_followup_cancelled_during_generation_never_sends(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    _stub_history(c, [{"role": "assistant", "content": "那本书挺好"}])
    c._schedule_followup("u1@im.wechat", "那本书挺好")
    st = c._pending_followups["u1@im.wechat"]

    def generate(*args, **kwargs):
        c._cancel_followup("u1@im.wechat")
        return "结尾也很暖"

    monkeypatch.setattr(c, "_generate_followup", generate)
    sent = []
    monkeypatch.setattr(c, "send_text", lambda *a, **kw: sent.append(a) or True)
    c._send_followup("u1@im.wechat", st)
    assert sent == []
    assert c._pending_followups == {}
    assert c._followup_daily == {}


def test_followup_character_switch_during_generation_cancels(tmp_path, monkeypatch):
    c = _connector(tmp_path, monkeypatch)
    current = ["charA"]
    monkeypatch.setattr(c, "_resolve_character_id", lambda sk: current[0])
    _stub_history(c, [{"role": "assistant", "content": "那本书挺好"}])
    c._schedule_followup("u1@im.wechat", "那本书挺好")
    st = c._pending_followups["u1@im.wechat"]

    def generate(*args, **kwargs):
        current[0] = "charB"
        return "结尾也很暖"

    monkeypatch.setattr(c, "_generate_followup", generate)
    sent = []
    monkeypatch.setattr(c, "send_text", lambda *a, **kw: sent.append(a) or True)
    c._send_followup("u1@im.wechat", st)
    assert sent == []
    assert c._pending_followups == {}


def test_followup_cancelled_during_send_does_not_reschedule_or_change_owner(tmp_path, monkeypatch):
    from types import SimpleNamespace

    c = _connector(tmp_path, monkeypatch)
    current = ["charA"]
    monkeypatch.setattr(c, "_resolve_character_id", lambda sk: current[0])
    _stub_history(c, [{"role": "assistant", "content": "那本书挺好"}])
    _stub_gen(c, monkeypatch, out="结尾也很暖")
    recorded = []
    c.orchestrator = SimpleNamespace(components={
        "memory": SimpleNamespace(record_outbound_message=lambda **kw: recorded.append(kw)),
    })
    c._schedule_followup("u1@im.wechat", "那本书挺好")
    st = c._pending_followups["u1@im.wechat"]

    def send(*args, **kwargs):
        c._cancel_followup("u1@im.wechat")
        current[0] = "charB"
        return True

    monkeypatch.setattr(c, "send_text", send)
    c._send_followup("u1@im.wechat", st)
    assert recorded[0]["character_id"] == "charA"
    assert c._pending_followups == {}


def test_followup_history_filters_character_at_read_source(tmp_path, monkeypatch):
    from types import SimpleNamespace

    c = _connector(tmp_path, monkeypatch)
    captured = []

    def history(**kwargs):
        captured.append(kwargs)
        return [], ""

    c.orchestrator = SimpleNamespace(components={"memory": SimpleNamespace(get_chat_context=history)})
    monkeypatch.setattr(c, "_resolve_character_id", lambda sk: "charA")
    c._session_messages("4:u1@im.wechat")
    assert captured == [{"session_id": "4:u1@im.wechat", "keep_recent": 10, "character_id": "charA", "summarize": False}]


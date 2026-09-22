"""归属 / 隔离 / 情感 / 主动消息接地的激进重构回归（2026-09-21 重扫）。

## 本轮修复的根因（用户原话：「用户隔离、LLM 分不清谁说的、记忆、情感、
## 主动提醒都很羸弱」）

四条症状共享同一根因：**「谁、对谁、在什么时候说了什么」在存储层丢失**。

1. `chat_history` 只有 role + session_id → assistant 行**没有身份**
   （同会话切角色后继承他人台词）；排序/去重都靠秒级 `created_at`
   （同秒次序不保证 + **内容相同的两条被折叠**）；importance 算完即弃
   （重建时硬编码 0.5）。
2. 情绪只在进程内存（`emotion_trajectory` 表建了从未写入）→ 重启/引擎淘汰后
   "记得你但心情归零"，而好感度却持久化。
3. 主动消息生成提示词的 `hours_since_chat` 被硬编码 0.0、无用户画像、
   无用户最后一句 → 生成必然泛化。

对标：SillyTavern `setOpenAIMessages()`（每条消息带 name + type 出处）、
nana `ConversationTurn(ask, answer)` / `emotional_state.py` / `heartbeat.py`。
"""

from __future__ import annotations

import time

import pytest

# ══════════════════════════════════════════════════════════
#  1. 存储层：归属列 + id 判序 + 不去重
# ══════════════════════════════════════════════════════════

def _sm(tmp_path):
    from shisi.memory.legacy.structured_memory import StructuredMemory

    return StructuredMemory(str(tmp_path / "attrib.db"))


def test_chat_history_stores_and_returns_attribution(tmp_path):
    sm = _sm(tmp_path)
    sm.add_chat("user", "我今天很累", session_id="2:wx@im.wechat",
                character_id="charA", user_key="2:wx@im.wechat",
                turn_id="t1", importance=0.8, channel="im.wechat")
    sm.add_chat("assistant", "那早点休息", session_id="2:wx@im.wechat",
                character_id="charA", user_key="2:wx@im.wechat",
                turn_id="t1", importance=0.8, channel="im.wechat")
    rows = sm.get_chats_by_session_limit("2:wx@im.wechat", 10)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert all(r["character_id"] == "charA" for r in rows), "assistant 行必须带角色归属"
    assert all(r["turn_id"] == "t1" for r in rows)
    assert all(float(r["importance"]) == 0.8 for r in rows), "importance 必须真落库（旧实现恒 0.5 假值）"
    sm.close()


def test_same_second_messages_keep_write_order_and_duplicates(tmp_path):
    """同一秒写入 + 内容完全相同的两条消息**都必须在**且顺序正确。

    旧实现：`ORDER BY created_at`（秒精度）+ 以 `(created_at, content)` 为键去重
    → 连续两条「嗯」被折叠成一条，且同秒相对次序不保证。
    """
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline

    sm = _sm(tmp_path)
    for text in ("嗯", "嗯", "好的"):
        sm.add_chat("user", text, session_id="7:web:ab12", character_id="c1")
    pipe = MemoryPipeline.__new__(MemoryPipeline)  # 只测加载函数，跳过重初始化
    pipe.sm = sm
    hist = pipe._load_session_history("7:web:ab12", 10)
    assert [h["content"] for h in hist] == ["嗯", "嗯", "好的"], (
        "重复内容不得被折叠、同秒顺序须按写入序"
    )
    assert [h["id"] for h in hist] == sorted(h["id"] for h in hist)
    sm.close()


def test_history_read_is_character_scoped(tmp_path):
    """切角色后不得继承另一角色的台词（character_id 过滤）。"""
    sm = _sm(tmp_path)
    sm.add_chat("user", "对A说的", session_id="s1", character_id="charA")
    sm.add_chat("assistant", "A的回复", session_id="s1", character_id="charA")
    sm.add_chat("user", "对B说的", session_id="s1", character_id="charB")
    sm.add_chat("assistant", "B的回复", session_id="s1", character_id="charB")
    rows_a = sm.get_chats_by_session_limit("s1", 10, character_id="charA")
    assert [r["content"] for r in rows_a] == ["对A说的", "A的回复"]
    sm.close()


# ══════════════════════════════════════════════════════════
#  2. 轮 / 发言者模型
# ══════════════════════════════════════════════════════════

def test_build_turns_pairs_user_and_character():
    from shisi.core.conversation_turn import build_turns, render_turns

    turns = build_turns([
        {"id": 1, "role": "user", "content": "在吗", "character_id": "c1"},
        {"id": 2, "role": "assistant", "content": "在的", "character_id": "c1"},
        {"id": 3, "role": "user", "content": "忙不忙", "character_id": "c1"},
    ])
    assert len(turns) == 2
    assert turns[0].is_complete and turns[0].user_text == "在吗" and turns[0].reply_text == "在的"
    assert not turns[1].is_complete, "缺回复的轮不算完整"
    # 渲染必须自带发言者标注（否则模型只能猜谁说的）
    assert render_turns(turns) == ["用户: 在吗", "我: 在的", "用户: 忙不忙"]


def test_filter_by_character_keeps_legacy_rows():
    from shisi.core.conversation_turn import build_turns, filter_by_character

    turns = build_turns([
        {"id": 1, "role": "user", "content": "旧数据无归属", "character_id": ""},
        {"id": 2, "role": "user", "content": "别人的", "character_id": "other"},
        {"id": 3, "role": "user", "content": "我的", "character_id": "me"},
    ])
    kept = [t.user_text for t in filter_by_character(turns, "me")]
    assert kept == ["旧数据无归属", "我的"], "迁移前无归属行必须保留（升级不失忆）"


def test_isolation_key_shape_is_shared_owner():
    from shisi.core.conversation_turn import isolation_key

    assert isolation_key("2:wx@im.wechat", "charA") == "2:wx@im.wechat::charA"
    assert isolation_key("", "") == "::"


# ══════════════════════════════════════════════════════════
#  3. 情感状态持久化（含离线衰减）
# ══════════════════════════════════════════════════════════

def test_emotion_snapshot_restore_with_offline_decay():
    from my_character.emotion_engine import Emotion, EmotionEngine

    eng = EmotionEngine(use_llm=False)
    eng._state.primary_emotion = Emotion.HAPPY
    eng._state.primary_intensity = 0.9
    eng._state.energy = 0.2
    eng._state.affection_points = 50.0
    snap = eng.snapshot()

    fresh = EmotionEngine(use_llm=False)
    # 离线 3 小时：强度衰减、能量恢复
    snap["last_update"] = time.time() - 3 * 3600
    assert fresh.restore(snap) is True
    assert fresh.state.primary_emotion == Emotion.HAPPY
    assert fresh.state.primary_intensity < 0.9, "离线必须衰减强度（旧实现无持久化）"
    assert fresh.state.energy > 0.2, "离线必须恢复能量"
    assert fresh.state.affection_points == pytest.approx(50.0, abs=1.0)


def test_emotion_state_store_isolated_by_user_and_character(tmp_path, monkeypatch):
    from utils import emotion_state as es

    monkeypatch.setattr(es, "_PATH", tmp_path / "emotion_state.json")
    es.save_emotion_state("2:wx@im.wechat", "charA", {"primary_intensity": 0.7})
    es.save_emotion_state("4:other@im.wechat", "charA", {"primary_intensity": 0.2})
    es.save_emotion_state("2:wx@im.wechat", "charB", {"primary_intensity": 0.9})

    assert es.load_emotion_state("2:wx@im.wechat", "charA")["primary_intensity"] == 0.7
    assert es.load_emotion_state("4:other@im.wechat", "charA")["primary_intensity"] == 0.2
    assert es.load_emotion_state("2:wx@im.wechat", "charB")["primary_intensity"] == 0.9
    assert es.load_emotion_state("3:nobody", "charA") == {}, "不得跨用户/跨角色串读"
    es.clear_emotion_state("2:wx@im.wechat", "charA")
    assert es.load_emotion_state("2:wx@im.wechat", "charA") == {}


def test_restore_rejects_garbage_without_raising():
    from my_character.emotion_engine import EmotionEngine

    eng = EmotionEngine(use_llm=False)
    assert eng.restore(None) is False
    assert eng.restore({}) is False
    assert eng.restore({"primary_emotion": "不存在的情绪"}) is False


# ══════════════════════════════════════════════════════════
#  4. 主动消息：真实接地 + 注意力
# ══════════════════════════════════════════════════════════

class _CapturingLLM:
    """记录生成提示词（ASE 的 llm_func 路径）。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def chat_sync(self, query: str = "", **kwargs) -> str:
        self.prompts.append(query)
        return "在忙什么呢"


def _engine(tmp_path, **kw):
    from proactive.ase_engine import ASEEngine

    return ASEEngine(
        llm_gateway=kw.pop("llm_gateway", None),
        generation_mode="llm",
        state_path=str(tmp_path / "ase.json"),
        **kw,
    )


def test_ase_prompt_is_grounded_with_real_state(tmp_path):
    """提示词必须含**真实** hours_since_chat / 画像 / 用户最后一句。

    旧实现 `hours_since_chat` 被硬编码 0.0，且完全不喂画像与最后一句。
    """
    from datetime import datetime, timedelta, timezone

    from proactive.ase_engine import ProactiveType

    llm = _CapturingLLM()
    eng = _engine(tmp_path, llm_gateway=llm)
    eng.set_user_key("2:wx@im.wechat")
    eng._last_user_message = "明天要军训"
    eng._last_user_interaction = datetime.now(tz=timezone.utc) - timedelta(hours=5)
    eng._user_profile_cache = "生日：腊月初一"
    eng._user_profile_loaded_at = time.time()

    content = eng._message_generator.generate_with_llm(
        ProactiveType.MISS_YOU,
        {"primary": {"type": "想念"}},
        3,
        hours_since_chat=eng._hours_since_last_chat(),
        user_profile=eng._user_profile_snippet(),
        last_user_message=eng._last_user_message,
        response_rate=eng.response_rate,
    )
    assert content
    prompt = llm.prompts[-1]
    assert "距上次聊天：5.0 小时" in prompt, "必须是真实小时数（旧实现恒 0.0）"
    assert "明天要军训" in prompt, "必须包含用户最后一句（接地）"
    assert "生日：腊月初一" in prompt, "必须包含用户画像（接地）"
    assert "互动热度" in prompt, "必须包含注意力信号"


def test_note_user_interaction_boosts_and_decay_reduces(tmp_path):
    eng = _engine(tmp_path)
    assert eng.response_rate == 0.0
    eng.note_user_interaction()
    boosted = eng.response_rate
    assert boosted > 0
    for _ in range(200):
        eng.decay_response_rate()
    assert eng.response_rate < boosted, "每 tick 必须衰减（对标 nana 0.997）"


def test_hours_since_last_chat_uses_user_interaction_not_our_send(tmp_path):
    """「距上次聊天」必须是用户开口的时间，不能被我们自己发消息刷新。"""
    from datetime import datetime, timedelta, timezone

    eng = _engine(tmp_path)
    eng.note_user_interaction()
    eng._last_user_interaction = datetime.now(tz=timezone.utc) - timedelta(hours=8)
    # 模拟"我们刚发过主动消息"
    eng._last_proactive_time = datetime.now(tz=timezone.utc)
    eng._last_delivery_time = eng._last_proactive_time
    assert eng._hours_since_last_chat() == pytest.approx(8.0, abs=0.1)


def test_ase_state_roundtrip_keeps_attention_and_last_user_message(tmp_path):
    eng = _engine(tmp_path)
    eng.note_user_interaction()
    eng._last_user_message = "记得叫我起床"
    eng.save_state()

    restored = _engine(tmp_path)
    assert restored.response_rate == pytest.approx(eng.response_rate, abs=1e-6)
    assert restored._last_user_message == "记得叫我起床"


def test_hub_note_user_interaction_routes_to_that_user_only(tmp_path, monkeypatch):
    from proactive import ase_hub as hub_mod

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")

    counter = {"n": 0}

    def _factory(user_key: str = "", state_path: str = ""):
        # ⚠️ 每个 user_key 必须用**独立状态文件**：`note_user_interaction` 现在会
        # 就近落盘（块D 修复 10 分钟窗口丢注意力），若两用户共用 ase.json，
        # 第二个引擎构造时会读到第一个的状态 —— 那是**夹具串台**而非代码缺陷。
        # 生产同构：hub 的 `_resolve_state_path(user_key)` 本就按用户分文件。
        counter["n"] += 1
        eng = _engine(tmp_path)
        eng._state_path = tmp_path / f"ase-{counter['n']}.json"
        return eng

    hub = hub_mod.ASEHub(_factory)
    hub.note_user_interaction("2:a@im.wechat")
    e1 = hub.get("2:a@im.wechat")
    e2 = hub.get("4:b@im.wechat")
    assert e1.response_rate > 0, "被记交互的用户引擎必须提升注意力"
    assert e2.response_rate == 0.0, "其他用户不得被连带提升（隔离）"


def test_hub_binds_user_key_for_profile_grounding(tmp_path, monkeypatch):
    from proactive import ase_hub as hub_mod

    monkeypatch.setattr(hub_mod, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(hub_mod, "_INDEX_PATH", tmp_path / "index.json")

    def _factory(user_key: str = "", state_path: str = ""):
        return _engine(tmp_path)

    hub = hub_mod.ASEHub(_factory)
    eng = hub.get("9:peer@im.wechat")
    assert eng._user_key == "9:peer@im.wechat", "引擎必须绑定归属，画像不得跨用户读"


# ══════════════════════════════════════════════════════════
#  5. 被静默关停的 LLM 记忆能力（callable(llm) 恒 False）
# ══════════════════════════════════════════════════════════

class _GatewayLike:
    """网关形态替身：**不可调用**，但有 chat_sync（与真实网关一致）。"""

    def __init__(self, reply: str = "ok", raises: bool = False) -> None:
        self.reply = reply
        self.raises = raises
        self.calls: list[dict] = []

    def chat_sync(self, **kwargs) -> str:
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("boom")
        return self.reply


def test_llm_bridge_adapts_gateway_object():
    from utils.llm_bridge import to_sync_callable

    gw = _GatewayLike(reply="用户喜欢猫")
    fn = to_sync_callable(gw)
    assert fn is not None, "网关对象（不可调用但有 chat_sync）必须能适配 —— 旧判据 callable(llm) 恒 False"
    assert fn("提取事实") == "用户喜欢猫"
    assert gw.calls[0]["query"] == "提取事实"


def test_llm_bridge_returns_none_without_llm_and_never_raises():
    from utils.llm_bridge import to_sync_callable

    assert to_sync_callable(None) is None
    assert to_sync_callable(object()) is None
    failing = to_sync_callable(_GatewayLike(raises=True))
    assert failing is not None and failing("x") == "", "LLM 失败必须降级为空结果，不得打断记忆批处理"


def test_memory_pipeline_enables_llm_extraction(tmp_path):
    """回归钉：记忆管道必须把网关接成 llm_func（旧实现三条 LLM 能力全为 None）。"""
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "p.db"))
    pipe = MemoryPipeline(
        structured_memory=sm,
        llm_gateway=_GatewayLike(),
        fact_extract_interval=1,
    )
    assert pipe.fe.llm_func is not None, "FactExtractor 必须拿到 LLM（否则事实只剩正则碎片）"
    assert pipe.ds.llm_func is not None, "DiarySummarizer 必须拿到 LLM"
    assert pipe.reflection._llm is not None, "ReflectionEngine 必须拿到 LLM"
    sm.close()


def test_memory_pipeline_stays_template_without_llm(tmp_path):
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(tmp_path / "p2.db"))
    pipe = MemoryPipeline(structured_memory=sm, llm_gateway=None, fact_extract_interval=1)
    assert pipe.fe.llm_func is None, "无网关时必须回落规则抽取（不得报错）"
    sm.close()

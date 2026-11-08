"""2026-11-08 图片/语音/话题三案根治批（用户四条报障的回归钉）。

生产实况（v1.65.2 @9df80cc，2026-11-08 01:29，chat_history 4021-4033 + app.log）：

1. **图片案**：直传图（B 路径）无配文 → chat_history 连 user 行都没有，下一轮
   历史里只剩一句凭空冒出的「哟，这橙子切得好」→ 模型幻觉「图我这边没收到」；
   该 36 字回复又命中 ``is_system_fallback_line`` 的模糊规则（≤40 字 + 含
   「没收到」）→ ``stored_reply=""`` → **已发送的发言从历史蒸发**。
2. **语音案**：TTSManager 因缺 MiMo Key 初始化失败 → ``components["voice"]``
   为 None；能力声明段也没有语音条目 → 用户问「可以给我发语音吗」，模型只能
   幻觉「手边哪来麦克风」。
3. **话题案**：「对方最近在说」锚点无已接住衰减——「哈哈哈」轮被回注的锚点
   竟是几分钟前已回答过的「你看图片里是什么呢」「你可以给我发语音吗」；
   元指令「别只讲这个话题了」还被抽成了话题（topic_state title 实锤）。

本文件逐案锁死修复行为；历史行时间戳均取自生产 chat_history 原值（UTC）。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")

# ── 生产实况回放素材（chat_history 4009-4033，UTC 时间戳） ──

_PROD_HISTORY = [
    {"role": "user", "content": "刘十三是谁", "timestamp": "2026-11-07 17:20:48"},
    {"role": "assistant", "content": "刘十三？你该不会在考我？他就是个倒霉蛋，不过是我认识的人里，最傻又最好笑的。", "timestamp": "2026-11-07 17:20:48"},
    {"role": "user", "content": "手术成功了吗", "timestamp": "2026-11-07 17:21:18"},
    {"role": "assistant", "content": "谁跟你了。我还没到躺手术台的时候呢，现在满血满状态，还能骂人。", "timestamp": "2026-11-07 17:21:18"},
    {"role": "user", "content": "你最后手术成功了吗", "timestamp": "2026-11-07 17:21:51"},
    {"role": "assistant", "content": "嗯，手术成功了。虽然医生说我这病，像天气预报一样，总是说变就变，但我现在还好好的。", "timestamp": "2026-11-07 17:21:51"},
    {"role": "user", "content": "辩论赛结束了", "timestamp": "2026-11-07 17:22:09"},
    {"role": "assistant", "content": "辩论赛结束了？那你辛苦了。辩论赛这种事情，输赢不重要，重要的是你说了你想说的，对吧？", "timestamp": "2026-11-07 17:22:09"},
    {"role": "user", "content": "你可以给我发语音吗", "timestamp": "2026-11-07 17:25:55"},
    {"role": "assistant", "content": "发语音？我现在就在这儿跟你打字，语音那玩意儿我倒是想发，但我这手边哪来麦克风呀。", "timestamp": "2026-11-07 17:25:55"},
    {"role": "assistant", "content": "哟，这橙子切得好，看着就新鲜。", "timestamp": "2026-11-07 17:29:21"},
    {"role": "user", "content": "[图片]", "timestamp": "2026-11-07 17:29:15"},
    {"role": "user", "content": "你看图片里是什么呢", "timestamp": "2026-11-07 17:29:28"},
    {"role": "assistant", "content": "一个杯子，一半是咖啡粉，一半是切好的橙子，底下好像还有布丁或者奶油之类的。", "timestamp": "2026-11-07 17:33:34"},
    {"role": "user", "content": "看清楚了吗", "timestamp": "2026-11-07 17:33:42"},
    {"role": "assistant", "content": "你看图说话可别只挑好听的说，杯子是咖啡粉，橙子是鲜切，底下那层金黄的明显是焦糖布丁。", "timestamp": "2026-11-07 17:33:42"},
    {"role": "user", "content": "哈哈哈", "timestamp": "2026-11-07 17:34:19"},
    {"role": "assistant", "content": "笑得这么开心，看来是被我猜中了？把焦糖布丁吃了，那玩意儿在常温下放久了会化。", "timestamp": "2026-11-07 17:34:19"},
    {"role": "user", "content": "辩论早就结束了", "timestamp": "2026-11-07 17:34:31"},
    {"role": "assistant", "content": "辩论赛早就结束了啊，那你怎么现在才告诉我？辩论赛结果怎么样？赢了吗？", "timestamp": "2026-11-07 17:34:31"},
    {"role": "user", "content": "别只讲这个话题了", "timestamp": "2026-11-07 17:34:47"},
    {"role": "assistant", "content": "哦，那我们换个话题？你平时喜欢做什么来放松自己？", "timestamp": "2026-11-07 17:34:47"},
]

# 生产原文：模型生成的 36 字回复（llm_done reply_len=36），曾被模糊规则吞掉
_PROD_SWALLOWED_REPLY = "你刚发的图我这边没收到啊，可能是网络卡住了。你重新发一次？我看看看清。"


# ═══════════════════════════════════════════════
# 一、话题锚点：已接住衰减 + 元指令/占位过滤
# ═══════════════════════════════════════════════


def _anchors(messages, current="", limit=2):
    from utils.prompt_sanitize import _extract_user_anchor_sentences

    return _extract_user_anchor_sentences(
        messages, current_user_message=current, max_age_hours=2.0, limit=limit,
        now=datetime(2026, 11, 7, 17, 35, 4),
    )


def test_answered_questions_not_reinjected():
    """「哈哈哈」轮（生产 01:34:19）：已回答过的疑问不得回注为「对方最近在说」。"""
    history = [row for row in _PROD_HISTORY if row["timestamp"] <= "2026-11-07 17:34:19"]
    anchors = _anchors(history, current="哈哈哈")
    joined = "|".join(anchors)
    assert "你看图片里是什么呢" not in joined, (
        "已回答的疑问回注 = 「几轮反复提同一话题」的燃料（生产实锤）"
    )
    assert "你可以给我发语音吗" not in joined
    assert "看清楚了吗" not in joined


def test_open_statement_kept():
    """「辩论早就结束了」轮：尚未被接住的本轮话头必须保留（延续性）。"""
    history = [row for row in _PROD_HISTORY if row["timestamp"] <= "2026-11-07 17:34:31"]
    anchors = _anchors(history, current="辩论早就结束了")
    assert "辩论早就结束了" in anchors


def test_meta_topic_control_never_anchor():
    """「别只讲这个话题了」是会话管理动作不是话题：作为历史行或本轮消息都不进锚点。"""
    anchors = _anchors(_PROD_HISTORY, current="别只讲这个话题了")
    assert anchors == [], "元指令必须被过滤，且它身后已有 assistant 行（已接住）"
    # 无 assistant 行时也不得进（元指令过滤独立于已接住衰减）
    anchors2 = _anchors([{"role": "user", "content": "别只讲这个话题了"}], current="")
    assert anchors2 == []


def test_image_placeholder_never_anchor():
    """直传图落库占位 `[图片]` 无话题信息，不得成为锚点。"""
    anchors = _anchors(
        [{"role": "user", "content": "[图片]", "timestamp": "2026-11-07 17:29:15"}],
        current="",
    )
    assert anchors == []


def test_unanswered_question_still_anchored():
    """身后没有 assistant 行的疑问（真未接住）仍要回注——衰减不误伤延续性。"""
    anchors = _anchors(
        [{"role": "user", "content": "你明天有空吗", "timestamp": "2026-11-07 17:30:00"}],
        current="",
    )
    assert any("明天" in a for a in anchors)


def test_statement_engaged_by_token_overlap_dropped():
    """陈述句与身后回复共享 ≥2 字片段 → 已接住，不再回注（辩论赛同型）。"""
    anchors = _anchors(
        [
            {"role": "user", "content": "辩论赛结束了", "timestamp": "2026-11-07 17:22:09"},
            {"role": "assistant", "content": "辩论赛结束了？那你辛苦了。", "timestamp": "2026-11-07 17:22:09"},
        ],
        current="",
    )
    assert anchors == []


# ═══════════════════════════════════════════════
# 二、落库窄口径：已发送的话不再被「兜底腔」判定吞掉
# ═══════════════════════════════════════════════


def _make_pipeline(tmp_path):
    from shisi.memory.legacy.memory_pipeline import MemoryPipeline
    from shisi.memory.legacy.structured_memory import StructuredMemory

    sm = StructuredMemory(str(Path(tmp_path) / "narrow.db"))
    return MemoryPipeline(vector_memory=SimpleNamespace(), structured_memory=sm), sm


def test_production_reply_recorded_not_swallowed(tmp_path):
    """生产 36 字回复必须落库（修复前：命中模糊规则 → assistant 行蒸发）。"""
    pipe, sm = _make_pipeline(tmp_path)
    ok = pipe.write_chat_history_sync(
        user_msg="你看图片里是什么呢", reply=_PROD_SWALLOWED_REPLY,
        session_id="2:o9cq80-test@im.wechat", character_id="87d46831", turn_id="t1",
    )
    assert ok
    with sm.get_connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_history WHERE turn_id = 't1' ORDER BY id"
        ).fetchall()
    roles = {r["role"]: r["content"] for r in rows}
    assert roles.get("assistant") == _PROD_SWALLOWED_REPLY, (
        "已发送的真实回复不得被「兜底腔」模糊规则吞掉（生产 01:29 案）"
    )
    assert roles.get("user") == "你看图片里是什么呢"


def test_exact_pool_line_still_filtered(tmp_path):
    """逐字学舌兜底池句仍不入库（窄口径保留精确匹配）。"""
    pipe, sm = _make_pipeline(tmp_path)
    pool_line = "刚才没接上，你再说一句？"
    from utils.fallback_lines import FALLBACK_POOL_LINES

    assert pool_line in FALLBACK_POOL_LINES, "测试前提：该句确属兜底池"
    ok = pipe.write_chat_history_sync(
        user_msg="在吗", reply=pool_line,
        session_id="2:o9cq80-test@im.wechat", character_id="87d46831", turn_id="t2",
    )
    assert ok  # user 行写入
    with sm.get_connection() as conn:
        rows = conn.execute(
            "SELECT role FROM chat_history WHERE turn_id = 't2'"
        ).fetchall()
    assert {r["role"] for r in rows} == {"user"}, "池句逐字复读仍判为系统占位，不入库"


def test_raw_machine_sentinel_not_recorded(tmp_path):
    """裸机器哨兵（处理超时等）依旧不入库。"""
    pipe, sm = _make_pipeline(tmp_path)
    ok = pipe.write_chat_history_sync(
        user_msg="在吗", reply="处理超时, 请稍候...",
        session_id="2:o9cq80-test@im.wechat", character_id="87d46831", turn_id="t3",
    )
    assert ok
    with sm.get_connection() as conn:
        rows = conn.execute(
            "SELECT role FROM chat_history WHERE turn_id = 't3'"
        ).fetchall()
    assert {r["role"] for r in rows} == {"user"}


def test_sentinel_predicate_semantics_pinned():
    """宽窄双口径各自钉死，防后续漂移。"""
    from utils.fallback_lines import is_machine_sentinel_line, is_system_fallback_line

    # 窄口径：模型自己的话不是哨兵
    assert is_machine_sentinel_line(_PROD_SWALLOWED_REPLY) is False
    assert is_machine_sentinel_line("刚才没接上，你再说一句？") is True
    assert is_machine_sentinel_line("处理超时，所有 LLM 提供商均不可用") is True
    assert is_machine_sentinel_line("") is True
    # 宽口径（出站守卫等消费方）：保持原行为
    assert is_system_fallback_line(_PROD_SWALLOWED_REPLY) is True
    assert is_system_fallback_line("随便聊聊天呀，今天过得怎么样？") is False


def test_record_outbound_narrow(tmp_path):
    """主动线出站记录同口径：含「没收到」的正常文案照记。"""
    pipe, sm = _make_pipeline(tmp_path)
    text = "你刚才说的那件事我没收到回音呀，再说一次？"
    ok = pipe.record_outbound_message(
        text, session_id="2:o9cq80-test@im.wechat", character_id="87d46831", channel="proactive",
    )
    assert ok
    with sm.get_connection() as conn:
        rows = conn.execute(
            "SELECT content FROM chat_history WHERE session_id = '2:o9cq80-test@im.wechat'"
        ).fetchall()
    assert any(r["content"] == text for r in rows)


# ═══════════════════════════════════════════════
# 三、直传图占位：历史恢复「你发图 → 我看见」因果形态
# ═══════════════════════════════════════════════


def _conn_with_image(png: bytes):
    """最小连接器夹具（与 test_ilink_media_parsing._make_conn 同构）。"""
    from unittest.mock import MagicMock

    from wechat_direct.wechat_connector import WeChatConnector

    conn = WeChatConnector(MagicMock(), owner_user_id=42)
    conn.token = "test_token"
    conn._last_user_id = "wx_user_1"
    conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
    conn._image_mode = MagicMock(return_value=("direct", "vm-test", 5 * 1024 * 1024))
    conn._download_media = MagicMock(return_value=png)
    return conn


def _image_item():
    media = {"encrypt_query_param": "enc-query-token"}
    return {"type": 2, "image_item": {"media": media, "aeskey": "a" * 32}}


_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


def test_direct_image_no_caption_gets_placeholder(monkeypatch):
    """生产 01:29 案：直传图无配文 → 主链收到 `[图片]` 占位（user 行可落库）。"""
    from unittest.mock import patch

    conn = _conn_with_image(_PNG_BYTES)
    with patch("wechat_direct.wechat_connector._send_text"), \
            patch("wechat_direct.wechat_connector._call_user_manager") as mock_call:
        mock_call.return_value = {"reply": "图看到了"}
        conn._handle_message({
            "message_type": 1,
            "message_id": "img_no_caption_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [_image_item()],
        })
    assert mock_call.call_count == 1
    assert mock_call.call_args[0][2] == "[图片]", (
        "直传图无配文必须落占位，否则历史里只剩凭空的图片评论（生产实锤）"
    )
    assert mock_call.call_args[0][3], "附件仍必须直传"


def test_direct_image_with_caption_unchanged(monkeypatch):
    """有配文的直传图：配文原样进主链，不覆盖。"""
    from unittest.mock import patch

    conn = _conn_with_image(_PNG_BYTES)
    with patch("wechat_direct.wechat_connector._send_text"), \
            patch("wechat_direct.wechat_connector._call_user_manager") as mock_call:
        mock_call.return_value = {"reply": "图看到了"}
        conn._handle_message({
            "message_type": 1,
            "message_id": "img_caption_1",
            "from_user_id": "wx_user_1",
            "context_token": "ctx",
            "item_list": [
                {"type": 1, "text_item": {"text": "你看图片里是什么呢"}},
                _image_item(),
            ],
        })
    assert mock_call.call_args[0][2] == "你看图片里是什么呢"


# ═══════════════════════════════════════════════
# 四、语音能力声明：真实可用才声明
# ═══════════════════════════════════════════════


def _build_prompt(capability_flags=None) -> str:
    from shisi.application.persona_service import PersonaService

    ps = PersonaService(config_loader=None, llm_gateway=None)
    kwargs = {"character_id": "default", "user_message": "在吗"}
    if capability_flags is not None:
        kwargs["capability_flags"] = capability_flags
    return ps.build_system_prompt(**kwargs)


def test_voice_capability_line_rendered_when_available():
    prompt = _build_prompt({"memory_recall": True, "voice_enabled": True})
    assert "可以发语音" in prompt, "TTS 真实可用时必须声明（否则模型否认真实能力）"
    assert "麦克风" in prompt


def test_voice_capability_line_absent_when_unavailable():
    prompt = _build_prompt({"memory_recall": True, "voice_enabled": False})
    assert "可以发语音" not in prompt, "TTS 不可用时声明=教模型说谎（生产缺 MiMo Key 实况）"


def test_voice_capability_default_off():
    prompt = _build_prompt(None)
    assert "可以发语音" not in prompt


def test_voice_sending_available_judges_components():
    from orchestrator.optimized_orchestrator import _voice_sending_available

    assert _voice_sending_available({}) is False
    assert _voice_sending_available({"voice": None}) is False
    assert _voice_sending_available({"voice": SimpleNamespace(enabled=False)}) is False
    assert _voice_sending_available({"voice": SimpleNamespace(enabled=True)}) is True


def test_voice_detector_production_utterance():
    """生产原句回归：「你可以给我发语音吗」必须命中语音请求（2026-11-08 实测）。"""
    from orchestrator.voice_detector import detect_voice_request

    assert detect_voice_request("你可以给我发语音吗") is True
    assert detect_voice_request("你可以说话吗") is True


def test_voice_detector_natural_phrasings():
    """自然说法矩阵（2026-11-08 生产实测补洞：旧模式漏「想听你的声音」整类）。"""
    from orchestrator.voice_detector import detect_voice_request

    must_hit = [
        "给我发个语音", "发语音", "发条语音", "发一条语音", "能不能发段语音",
        "说句话听听", "我想听你的声音", "听一下你的声音", "想听一下你的声音",
        "用你的声音说一句", "你发语音给我听听",
    ]
    for c in must_hit:
        assert detect_voice_request(c) is True, f"漏判（用户明确要语音却回文字）: {c}"
    must_miss = [
        "你别发语音了，打字就行",   # 明确拒绝
        "你的声音真好听",         # 夸奖不是点单（防配额浪费）
        "今天我们吃什么", "听我说完",
    ]
    for c in must_miss:
        assert detect_voice_request(c) is False, f"误触发: {c}"


# ═══════════════════════════════════════════════
# 五、图片历史铁则（reply_mode 指令）
# ═══════════════════════════════════════════════


def test_immersive_image_history_rule():
    from utils.reply_mode import _INSTRUCTION_IMMERSIVE

    assert "[图片]" in _INSTRUCTION_IMMERSIVE
    assert "没收到" in _INSTRUCTION_IMMERSIVE, "必须显式禁止对已见图声称「没收到」"


def test_novel_image_history_rule():
    from utils.reply_mode import _INSTRUCTION_NOVEL

    assert "[图片]" in _INSTRUCTION_NOVEL
    assert "没收到" in _INSTRUCTION_NOVEL


# ═══════════════════════════════════════════════
# 六、语音轮「仅发语音条，不发文字气泡」（2026-11-08 用户追加裁决）
# ═══════════════════════════════════════════════


def test_voice_reply_ready_matrix():
    """语音轮前置判定：要语音 + 正文≥8 字 + 组件真实可用，缺一不可。"""
    from orchestrator.optimized_orchestrator import _voice_reply_ready

    on = SimpleNamespace(enabled=True, synthesize=lambda *a, **k: None)
    off = SimpleNamespace(enabled=False, synthesize=lambda *a, **k: None)
    long_reply = "好啊，那我今天就慢慢说给你听，别嫌我啰嗦。"
    assert _voice_reply_ready("你可以给我发语音吗", long_reply, on) is True
    assert _voice_reply_ready("你可以给我发语音吗", "嗯嗯", on) is False, "短招呼不转语音"
    assert _voice_reply_ready("今天吃什么", long_reply, on) is False, "用户没要语音"
    assert _voice_reply_ready("你可以给我发语音吗", long_reply, off) is False
    assert _voice_reply_ready("你可以给我发语音吗", long_reply, None) is False


def _text_msg(msg_id: str = "vo_1"):
    return {
        "message_type": 1,
        "message_id": msg_id,
        "from_user_id": "wx_user_1",
        "context_token": "ctx",
        "item_list": [{"type": 1, "text_item": {"text": "你可以给我发语音吗"}}],
    }


def _voice_result_dict(voice_only: bool) -> dict:
    from voice.audio_result import SynthesizedAudio, voice_result_payload

    payload = voice_result_payload(SynthesizedAudio(b"fake-mp3", "mp3"))
    return {
        "reply": "好啊，那我今天就慢慢说给你听，别嫌我啰嗦。",
        "voice": payload,
        "voice_duration_ms": 2000,
        "voice_only": voice_only,
    }


def _conn_with_voice(monkeypatch, send_voice_ok: bool):
    from unittest.mock import MagicMock

    from wechat_direct.wechat_connector import WeChatConnector

    conn = WeChatConnector(MagicMock(), owner_user_id=42)
    conn.token = "test_token"
    conn._last_user_id = "wx_user_1"
    conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
    # 2026-11-08 语音送达批：传输层入口改为 send_voice_clip（形态决策 owner）
    conn.send_voice_clip = MagicMock(return_value=send_voice_ok)
    return conn


def test_voice_only_skips_text_bubble(monkeypatch):
    """语音轮发送成功 → 只发语音条，`_send_text` 零调用（用户裁决）。"""
    from unittest.mock import patch

    from voice.audio_converter import AudioFormatConverter

    monkeypatch.setattr(AudioFormatConverter, "to_silk", lambda self, data, fmt: b"silk-bytes")
    conn = _conn_with_voice(monkeypatch, send_voice_ok=True)
    with patch("wechat_direct.wechat_connector._send_text") as mock_send, \
            patch("wechat_direct.wechat_connector._call_user_manager") as mock_call:
        mock_send.return_value = {"ret": 0}
        mock_call.return_value = _voice_result_dict(voice_only=True)
        conn._handle_message(_text_msg("vo_only_ok"))
    conn.send_voice_clip.assert_called_once()
    assert mock_send.call_count == 0, "语音轮不得再发文字气泡"


def test_voice_only_falls_back_to_text_on_send_failure(monkeypatch):
    """语音发送失败 → 补发文字（内容不凭空消失），不是静默丢。"""
    from unittest.mock import patch

    from voice.audio_converter import AudioFormatConverter

    monkeypatch.setattr(AudioFormatConverter, "to_silk", lambda self, data, fmt: b"silk-bytes")
    conn = _conn_with_voice(monkeypatch, send_voice_ok=False)
    with patch("wechat_direct.wechat_connector._send_text") as mock_send, \
            patch("wechat_direct.wechat_connector._call_user_manager") as mock_call:
        mock_send.return_value = {"ret": 0}
        mock_call.return_value = _voice_result_dict(voice_only=True)
        conn._handle_message(_text_msg("vo_only_fail"))
    conn.send_voice_clip.assert_called_once()
    assert mock_send.call_count >= 1, "语音失败必须回退文字，用户不能什么都收不到"


def test_legacy_voice_result_keeps_text_and_voice(monkeypatch):
    """无 voice_only 标记的旧结果（如 API/旧调用方）→ 文字+语音双发，行为不变。"""
    from unittest.mock import patch

    from voice.audio_converter import AudioFormatConverter

    monkeypatch.setattr(AudioFormatConverter, "to_silk", lambda self, data, fmt: b"silk-bytes")
    conn = _conn_with_voice(monkeypatch, send_voice_ok=True)
    with patch("wechat_direct.wechat_connector._send_text") as mock_send, \
            patch("wechat_direct.wechat_connector._call_user_manager") as mock_call:
        mock_send.return_value = {"ret": 0}
        mock_call.return_value = _voice_result_dict(voice_only=False)
        conn._handle_message(_text_msg("vo_legacy"))
    conn.send_voice_clip.assert_called_once()
    assert mock_send.call_count >= 1


# ═══════════════════════════════════════════════
# 七、语音 LLM 决策（2026-11-08 用户裁决：把语音从字段匹配升级为 L1 工具）
# ═══════════════════════════════════════════════


def test_voice_cue_is_voice_desire_only():
    """隐式语音晋级只收「语音愿望/表达方式」类；纯情绪句不唤醒 L1（成本纪律）。"""
    from orchestrator.tool_gate import voice_cue

    for t in ["好想听你说话", "陪我聊会嘛", "撒个娇给我听听", "哄哄我", "说点好听的", "语气温柔点"]:
        assert voice_cue(t) is True, f"语音愿望句应晋级 L1: {t}"
    for t in ["今天好累啊，也不想动", "今天吃什么", "明天几点开会", "我想你了"]:
        assert voice_cue(t) is False, f"非语音愿望句不应因语音而晋级: {t}"


def test_should_escalate_includes_voice_cue():
    from orchestrator.tool_gate import should_escalate

    assert should_escalate("好想听你说话") is True
    assert should_escalate("今天吃什么") is False
    assert should_escalate("今天好累啊，也不想动") is False


def test_send_voice_tool_schema():
    from orchestrator.tool_gate import SEND_VOICE_TOOL, extract_voice_decision

    fn = SEND_VOICE_TOOL["function"]
    assert fn["name"] == "send_voice"
    # 2026-11-08 语音送达批：投递形态是音频文件卡片（原生语音条平台不渲染），
    # 描述里不再承诺「语音条」，只承诺「语音」+「本轮不再发明文」。
    assert "语音" in fn["description"]
    assert "语音条" not in fn["description"]
    assert "不再发明文" in fn["description"]
    # 解析：命中 / 未命中 / 空
    assert extract_voice_decision([{"function": {"name": "send_voice", "arguments": "{}"}}]) is True
    assert extract_voice_decision([{"function": {"name": "set_reminder"}}]) is False
    assert extract_voice_decision(None) is False


def test_review_messages_has_voice_rule():
    from orchestrator.tool_gate import build_review_messages

    system, _ = build_review_messages("在吗", "角色设定", [])
    assert "语音裁决" in system and "send_voice" in system


def test_voice_auto_cooldown_gate():
    """LLM 自主语音每会话 10 分钟一次；显式请求不经过此闸。"""
    from orchestrator.optimized_orchestrator import (
        _reset_voice_auto_gate,
        _voice_auto_allowed,
    )

    _reset_voice_auto_gate()
    assert _voice_auto_allowed("s1", now=1000.0) is True
    assert _voice_auto_allowed("s1", now=1001.0) is False
    assert _voice_auto_allowed("s2", now=1001.0) is True  # 会话独立
    assert _voice_auto_allowed("s1", now=1000.0 + 601) is True  # 冷却到期
    _reset_voice_auto_gate()


def test_voice_reply_ready_force_for_llm_decision():
    """LLM 裁决的隐式语音：正文/组件满足即可，不再要求词表命中（force）。"""
    from orchestrator.optimized_orchestrator import _voice_reply_ready

    on = SimpleNamespace(enabled=True, synthesize=lambda *a, **k: None)
    short = "嗯嗯"
    long_reply = "过来吧，我陪你待一会儿，别一个人扛着。"
    assert _voice_reply_ready("今天吃什么", long_reply, on) is False
    assert _voice_reply_ready("今天吃什么", long_reply, on, force=True) is True
    assert _voice_reply_ready("今天吃什么", short, on, force=True) is False


# ═══════════════════════════════════════════════
# 八、主链 agent 化（2026-11-08 用户裁决：能力不能停在字段匹配）
# ═══════════════════════════════════════════════


class _FakeToolResult:
    def to_dict(self):
        return {"success": True, "result": "记忆里的事实：喜欢咖啡"}


class _FakeTools:
    def __init__(self, lines=None):
        self.dispatched = []
        self.registry = SimpleNamespace(
            get_tools_by_permission=lambda level: [
                {"type": "function", "function": {"name": "memory"}}
            ] if lines is None else lines,
            get=lambda name: None,
        )

    def dispatch(self, name, args, affinity_level=0, caller_id=""):
        self.dispatched.append(name)
        return _FakeToolResult()


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _bare_orchestrator(tools):
    from orchestrator.optimized_orchestrator import OptimizedOrchestrator

    obj = object.__new__(OptimizedOrchestrator)
    obj.components = {"tools": tools}
    return obj


def test_main_chain_tool_loop_dispatches_and_regenerates():
    """主链 agent：LLM 调工具 → dispatch → 结果回喂 → 重生成终稿。"""
    import asyncio

    tools = _FakeTools()
    llm = _FakeLLM([
        {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "memory", "arguments": "{}"}},
        ]},
        {"content": "喜欢咖啡呀，我记得。", "tool_calls": []},
    ])
    orch = _bare_orchestrator(tools)
    reply, ran = asyncio.run(orch._main_chain_tool_loop(
        llm, query="你还记得我喜欢什么吗", system_prompt="人设",
        history=[{"role": "user", "content": "在吗"}],
        session_key="s", user_id=1,
    ))
    assert ran is True
    assert reply == "喜欢咖啡呀，我记得。"
    assert tools.dispatched == ["memory"], "主链工具必须真执行一次"
    assert len(llm.calls) == 2, "工具后必须重新生成"
    msgs = llm.calls[1]["messages"]
    assert any(m.get("role") == "tool" and m.get("tool_call_id") == "c1" for m in msgs), (
        "工具结果必须按 tool_call_id 回喂（否则模型拿不到结果）"
    )
    assert any(m.get("role") == "system" for m in msgs)


def test_main_chain_tool_loop_no_tools_returns_plain():
    """无工具调用：单跳返回正文且 ran=True —— 调用方不得再调一次主链。

    2026-11-08 自查缺陷回归：旧语义 ran=False 会让调用方丢掉正文再调 `chat()`，
    常规轮（大多数）变成双倍 LLM 调用 + 双倍延迟。
    """
    import asyncio

    tools = _FakeTools()
    llm = _FakeLLM([{"content": "今天挺忙的。", "tool_calls": []}])
    orch = _bare_orchestrator(tools)
    reply, ran = asyncio.run(orch._main_chain_tool_loop(
        llm, query="在忙吗", system_prompt="人设", history=[],
    ))
    assert (reply, ran) == ("今天挺忙的。", True), "首跳成功=生成已完成，不得二次调用"
    assert len(llm.calls) == 1
    assert tools.dispatched == []


def test_main_chain_tool_loop_rounds_exhausted_final_text():
    """轮次用尽仍要说话：去工具收尾一次纯文本（不允许空回复）。"""
    import asyncio

    tools = _FakeTools()
    tc = {"id": "c1", "function": {"name": "memory", "arguments": "{}"}}
    llm = _FakeLLM([
        {"content": "", "tool_calls": [tc]},
        {"content": "", "tool_calls": [tc]},
        {"content": "我这边有点卡，但我记得你在。", "tool_calls": []},
    ])
    orch = _bare_orchestrator(tools)
    reply, ran = asyncio.run(orch._main_chain_tool_loop(
        llm, query="你还记得吗", system_prompt="人设", history=[],
    ))
    assert ran is True
    assert reply == "我这边有点卡，但我记得你在。"
    assert llm.calls[-1]["tools"] is None, "收尾跳必须去掉工具，逼出正文"


def test_main_chain_tool_loop_disabled_and_no_registry():
    """回滚开关/无注册表：返回 ('', False)，调用方走旧单跳（fail-open）。"""
    import asyncio

    from orchestrator import optimized_orchestrator as mod

    tools = _FakeTools()
    llm = _FakeLLM([])
    orch = _bare_orchestrator(tools)
    old = mod.MAIN_CHAIN_AGENT_ENABLED
    try:
        mod.MAIN_CHAIN_AGENT_ENABLED = False
        assert asyncio.run(orch._main_chain_tool_loop(
            llm, query="q", system_prompt="s", history=[]
        )) == ("", False)
    finally:
        mod.MAIN_CHAIN_AGENT_ENABLED = old
    empty = _bare_orchestrator(SimpleNamespace(registry=SimpleNamespace(
        get_tools_by_permission=lambda level: [], get=lambda name: None)))
    assert asyncio.run(empty._main_chain_tool_loop(
        llm, query="q", system_prompt="s", history=[]
    )) == ("", False)


def test_tools_inline_flag_and_capability_line():
    from orchestrator.optimized_orchestrator import _tools_inline_available
    from shisi.application.persona_service import capability_block

    on = {"tools": SimpleNamespace(registry=SimpleNamespace(
        get_tools_by_permission=lambda level: [{"type": "function"}]))}
    off = {"tools": SimpleNamespace(registry=SimpleNamespace(
        get_tools_by_permission=lambda level: []))}
    assert _tools_inline_available(on) is True
    assert _tools_inline_available(off) is False
    assert _tools_inline_available({}) is False
    assert "可以直接调用" in capability_block(tools_inline=True)
    assert "可以直接调用" not in capability_block(tools_inline=False)
    # 静态条不得再出现「你不能自己调用」的过时断言（主链已能调）
    assert "不能自己调用" not in capability_block(tools_inline=True)


# ═══════════════════════════════════════════════
# 九、主动语音广播脚本（2026-11-08 催动批）
# ═══════════════════════════════════════════════


def test_collect_targets_freshness_and_binding(tmp_path):
    """目标枚举：24h 窗口新鲜度 + 绑定角色 + 凭据解析（不泄露 token）。"""
    import json
    import sqlite3
    import time as _t

    from scripts.broadcast_proactive_voice import collect_targets

    sessions = tmp_path / "sessions"
    slot = sessions / "2" / "slot0"
    slot.mkdir(parents=True)
    (slot / "credentials.json").write_text(
        json.dumps({"token": "t-secret-123", "base_url": "https://x"}), encoding="utf-8"
    )
    (slot / "context_tokens.json").write_text(
        json.dumps({
            "peer-fresh": {"token": "ctx1", "ts": _t.time() - 3600},
            "peer-stale": {"token": "ctx2", "ts": _t.time() - 48 * 3600},
        }),
        encoding="utf-8",
    )
    # 未绑定用户（user 3）→ 角色回落 default（不猜）
    slot3 = sessions / "3" / "slot0"
    slot3.mkdir(parents=True)
    (slot3 / "credentials.json").write_text(
        json.dumps({"token": "t3", "base_url": "https://x"}), encoding="utf-8"
    )
    (slot3 / "context_tokens.json").write_text(
        json.dumps({"peer-unbound": {"token": "ctx3", "ts": _t.time() - 600}}),
        encoding="utf-8",
    )
    db = tmp_path / "users.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE wechat_bindings (id INTEGER PRIMARY KEY, user_id INTEGER, "
        "wxid TEXT, character_card_id TEXT, bound_at TEXT)"
    )
    conn.execute(
        "INSERT INTO wechat_bindings (user_id, wxid, character_card_id, bound_at) "
        "VALUES (2, 'peer-fresh', 'abc12345', '2026-11-08')"
    )
    conn.commit()
    conn.close()

    targets = collect_targets(sessions_dir=sessions, users_db=db, max_age_hours=24)
    by_peer = {t["peer"]: t for t in targets}
    assert by_peer["peer-fresh"]["fresh"] is True
    assert by_peer["peer-stale"]["fresh"] is False
    # 绑定是**按用户**的：同一用户的多个 peer 都是该角色
    assert by_peer["peer-fresh"]["character_id"] == "abc12345"
    assert by_peer["peer-stale"]["character_id"] == "abc12345"
    # 未绑定用户回落 default（不猜）
    assert by_peer["peer-unbound"]["character_id"] == "default"
    assert by_peer["peer-fresh"]["token"] == "t-secret-123"


def test_broadcast_helpers_mask_and_fallback_line():
    from scripts.broadcast_proactive_voice import _LINE_FALLBACK, _mask
    from utils.fallback_lines import is_machine_sentinel_line

    assert _mask("o9cq80-_yMyfY247BTeFL5JvNgoI@im.wechat") == "o9cq80-_…"
    assert _mask("short") == "short"
    # 兜底句必须是「能被存库窄口径接受」的真实台词（不是系统哨兵）
    assert is_machine_sentinel_line(_LINE_FALLBACK) is False

# ═══════════════════════════════════════════════
# 十、常驻主动线语音（2026-11-08 用户裁决：让她拥有并驱动语音）
# ═══════════════════════════════════════════════


def test_parse_decision_voice_strict_true():
    """决策契约：voice 严格 True 才算（字符串/数字一律按文字）。"""
    import json as _json

    from proactive.llm_proactive import parse_decision

    def _raw(obj):
        return "前缀说明 " + _json.dumps(obj, ensure_ascii=False)

    assert parse_decision(_raw({
        "should_contact": True, "message": "在吗", "voice": True,
    }))["voice"] is True
    assert parse_decision(_raw({
        "should_contact": True, "message": "在吗", "voice": "true",
    }))["voice"] is False
    assert parse_decision(_raw({
        "should_contact": True, "message": "在吗",
    }))["voice"] is False
    # should=False 时字段同样存在（调用方无需 .get 兜底）
    assert parse_decision(_raw({"should_contact": False}))["voice"] is False


def test_find_wechat_credentials_prefers_fresh(tmp_path, monkeypatch):
    """凭据定位：多 slot 时优先窗口内那份；过期也返回（由调用方决定是否尝试）。"""
    import json as _json
    import time as _t

    from utils import proactive_voice_delivery as pvd

    monkeypatch.setattr(pvd, "SESSIONS_DIR", tmp_path)
    (tmp_path / "2" / "slot0").mkdir(parents=True)
    (tmp_path / "2" / "slot1").mkdir(parents=True)
    (tmp_path / "2" / "slot0" / "credentials.json").write_text(
        _json.dumps({"token": "old", "base_url": "u"}), encoding="utf-8")
    (tmp_path / "2" / "slot0" / "context_tokens.json").write_text(
        _json.dumps({"p1": {"token": "c-old", "ts": _t.time() - 48 * 3600}}), encoding="utf-8")
    (tmp_path / "2" / "slot1" / "credentials.json").write_text(
        _json.dumps({"token": "new", "base_url": "u"}), encoding="utf-8")
    (tmp_path / "2" / "slot1" / "context_tokens.json").write_text(
        _json.dumps({"p1": {"token": "c-new", "ts": _t.time() - 600}}), encoding="utf-8")

    creds = pvd.find_wechat_credentials(2, "p1")
    assert creds is not None and creds["fresh"] is True
    assert creds["context_token"] == "c-new" and creds["token"] == "new"

    # 只有过期：仍返回，如实标记 fresh=False
    monkeypatch.setattr(pvd, "SESSIONS_DIR", tmp_path)
    (tmp_path / "2" / "slot1" / "context_tokens.json").write_text(
        _json.dumps({"p1": {"token": "c-new", "ts": _t.time() - 99 * 3600}}), encoding="utf-8")
    stale = pvd.find_wechat_credentials(2, "p1")
    assert stale is not None and stale["fresh"] is False


def test_send_proactive_voice_rejects_non_wechat_and_missing_creds(tmp_path, monkeypatch):
    """失败路径：非微信键/无凭据 → (False, 原因)，绝不抛异常（主动线要能回落文字）。"""
    from utils import proactive_voice_delivery as pvd

    monkeypatch.setattr(pvd, "SESSIONS_DIR", tmp_path)
    ok, reason = pvd.send_proactive_voice("2:web:abc", "在吗")
    assert ok is False and reason == "not_wechat"
    ok2, reason2 = pvd.send_proactive_voice("2:p1@im.wechat", "在吗")
    assert ok2 is False and reason2 == "no_credentials"

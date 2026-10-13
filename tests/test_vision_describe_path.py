"""A 档 describe 链（VisionHandler）契约测试（2026-10-12 多模态域批）。

现状：该链生产 image_describe=0、测试 0 覆盖。本批契约：
- image_url.url 必须经 to_data_url 归一化（data: 前缀），不得塞裸 base64；
- 损坏 base64 → VisionHandler 返回占位文本不抛异常、不调网关，
  connector ``_describe_image`` 前缀过滤语义保持（得空串）；
- 图文并存：配文与描述合成注入（配文在前、描述作图片上下文行），
  不得 ``or`` 短路丢图片语义；
- 描述 prompt 放宽为场景/人物/情绪粒度（上限 ≥100 字），不再硬编「20字」。
"""
import asyncio
import base64
from unittest.mock import MagicMock, patch

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-body"


class _FakeGateway:
    """记录 chat 调用并返回固定描述的假网关。"""

    def __init__(self):
        self.calls: list = []

    async def chat(self, messages=None, **kwargs):
        self.calls.append(messages)
        return "图中是一只白猫趴在窗台上"


def _make_conn():
    from wechat_direct.wechat_connector import WeChatConnector

    conn = WeChatConnector(MagicMock(), owner_user_id=42)
    conn.token = "test_token"
    conn._last_user_id = "wx_user_1"
    conn._context_tokens = {"wx_user_1": {"token": "ctx", "ts": 0}}
    return conn


def test_vision_handler_sends_data_url():
    """① image_url.url 必须是 data URL（裸 base64 → 旧码红）。"""
    from multimodal.multimodal_processor import VisionHandler

    gw = _FakeGateway()
    b64 = base64.b64encode(_PNG_BYTES).decode("ascii")
    result = asyncio.run(VisionHandler(gw).process(b64))

    assert gw.calls, "必须调用一次网关"
    content = gw.calls[0][0]["content"]
    img_part = next(p for p in content if p.get("type") == "image_url")
    url = img_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,"), "裸 base64 不可直塞 image_url.url"
    assert base64.b64decode(url.split(",", 1)[1]) == _PNG_BYTES
    assert result["text"] == "图中是一只白猫趴在窗台上"


def test_corrupt_b64_placeholder_without_gateway_call():
    """② 损坏 base64 → 占位文本不抛异常、不调网关；connector 前缀过滤得空串。"""
    from multimodal.multimodal_processor import VisionHandler

    gw = _FakeGateway()
    result = asyncio.run(VisionHandler(gw).process("!!!not-base64!!!"))

    assert not gw.calls, "损坏数据不得发给网关（白烧一次视觉调用）"
    assert result["text"].startswith("[收到一张图片"), "占位文本须保持 connector 前缀过滤契约"

    conn = _make_conn()
    conn._vision_handler = VisionHandler(gw)
    assert conn._describe_image("!!!not-base64!!!") == "", "_describe_image 对损坏数据得空串"


def test_caption_and_desc_both_enter_main_chain():
    """③ mode=describe + 图文并存 → 配文与描述都进主链（旧码 or 短路丢描述 → 红）。"""
    conn = _make_conn()
    conn._image_mode = MagicMock(return_value=("describe", "", 5 * 1024 * 1024))
    conn._describe_image = MagicMock(return_value="图中是一只白猫趴在窗台上")
    raw_msg = {
        "message_type": 3,
        "message_id": "img_desc_1",
        "from_user_id": "wx_user_1",
        "context_token": "ctx",
        "item_list": [
            {"type": 3, "image_item": {"image_data": base64.b64encode(_PNG_BYTES).decode()}},
            {"type": 1, "text_item": {"text": "你看我拍的猫"}},
        ],
    }
    with patch("wechat_direct.wechat_connector._send_text"), \
            patch("wechat_direct.wechat_connector._call_user_manager") as mock_call:
        mock_call.return_value = {"reply": "好"}
        conn._handle_message(raw_msg)

    assert mock_call.call_count == 1
    text = mock_call.call_args[0][2]
    assert "你看我拍的猫" in text, "配文必须保留"
    assert "图中是一只白猫趴在窗台上" in text, "描述必须作为图片上下文并入（不得 or 短路丢弃）"
    # 2026-10-13 拟人化：标记从「[图片：…]」改「[你亲眼看到：…]」（亲历腔，
    # 配合 reply_mode 图片铁则让角色以第一人称消化、不复述报告）。
    assert "[你亲眼看到：图中是一只白猫趴在窗台上]" in text, "必须用亲历腔标记注入"
    assert "[图片：" not in text, "旧「[图片：…]」报告腔标记不得残留"


def test_describe_prompt_relaxed():
    """④ prompt 常量：不含「20字」、上限 ≥100 字、覆盖场景/人物/情绪粒度。"""
    import re

    from multimodal.multimodal_processor import IMAGE_DESCRIBE_PROMPT

    assert "20字" not in IMAGE_DESCRIBE_PROMPT
    m = re.search(r"(\d+)\s*字", IMAGE_DESCRIBE_PROMPT)
    assert m, "prompt 必须声明字数上限"
    assert int(m.group(1)) >= 100, "上限须放宽到 ≥100 字"
    for kw in ("场景", "人物", "情绪"):
        assert kw in IMAGE_DESCRIBE_PROMPT, f"prompt 须覆盖粒度维度：{kw}"


def test_describe_prompt_is_materialized_facts():
    """2026-10-13 拟人化：describe prompt 必须素材化（禁总结/解说/意图推测），
    供角色以第一人称消化——旧「请描述这张图片…只输出描述本身」的解说腔已废。"""
    from multimodal.multimodal_processor import IMAGE_DESCRIBE_PROMPT

    assert "事实要素" in IMAGE_DESCRIBE_PROMPT
    assert "不要总结" in IMAGE_DESCRIBE_PROMPT and "不要解说" in IMAGE_DESCRIBE_PROMPT
    assert "只输出描述本身" not in IMAGE_DESCRIBE_PROMPT


def test_reply_mode_contains_image_witness_rule():
    """沉浸式指令必须含图片亲历铁则：[你亲眼看到：…] 是你的亲身经历，
    禁止复述罗列文本、禁止「图片显示」转述腔（A→A+ 拟人化的 prompt 侧）。"""
    from utils.reply_mode import _INSTRUCTION_IMMERSIVE

    assert "[你亲眼看到" in _INSTRUCTION_IMMERSIVE
    assert "禁止复述" in _INSTRUCTION_IMMERSIVE
    assert "图片显示" in _INSTRUCTION_IMMERSIVE  # 转述腔反例词在案

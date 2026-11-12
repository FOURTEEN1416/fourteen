"""运行时能力模型（Speaker 第 1 刀，2026-11-12）。

背景：能力声明此前只有主链 ``persona_service.capability_block`` 一份，且是
初始化期静态快照；提醒/日期/主动/手动四条 prompt 完全没有能力声明（见
``docs/reports/2026-11-12-Speaker施工盘点.json`` 的 persona.gaps）。本模块
提供**可探测、可渲染**的最小能力模型，供 Speaker 统一拼进系统提示词。

边界：只描述「当前通道能发什么」，不做发送、不探测凭据、不建 LLM 客户端。
``image`` 在当前微信通道恒为 False（iLink 只支持文本/语音文件卡片）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityModel:
    """一次发声的通道能力快照。

    Attributes:
        text: 是否可发文字（当前所有通道恒 True）。
        voice: 是否可发语音（由调用方按真实可用性探测后传入）。
        image: 是否可发图片（当前通道不支持，``detect`` 恒 False）。
    """

    text: bool = True
    voice: bool = False
    image: bool = False

    @classmethod
    def detect(cls, *, voice_available: bool = False) -> CapabilityModel:
        """按运行期探测结果构造能力模型。

        ``image`` 恒 False：当前微信通道只能发文字与音频文件卡片，发不出
        图片气泡；宁可声明不支持，也不让模型承诺一个发不出去的交付。
        """
        return cls(text=True, voice=bool(voice_available), image=False)

    def prompt_block(self) -> str:
        """渲染中文能力声明段（非空，供 Speaker 直接拼接）。"""
        lines = ["【能力声明】"]
        # 2026-11-12 复核修复：text+voice 只出一句，避免「可以发语音」重复
        # 两遍；text only 仍保留语音否定句供模型知道不可用。
        if self.text and self.voice:
            lines.append("- 你可以发送文字消息，也可以发送语音消息。")
        elif self.text:
            lines.append("- 你只能发送文字消息。")
            lines.append("- 你目前不能发送语音消息。")
        elif self.voice:
            lines.append("- 你只能发送语音消息。")
        else:
            lines.append("- 你当前不能发送文字消息，也不能发送语音消息。")
        if self.image:
            lines.append("- 你也可以发送图片。")
        else:
            lines.append("- 你不能发送文件、链接或图片。")
        lines.append(
            "- 在承诺交付任何内容之前，先确认当前能力是否支持；"
            "不支持就直说，不要假装已经发送。"
        )
        return "\n".join(lines)

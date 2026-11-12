"""主动消息上下文统一真源 — 决策线（llm_proactive）与生成线（ase_engine）共用。

2026-10-15 质量回调批：此前两条主动线各自拼 prompt、各自截断人设、各自投影
画像（无 preferences）、各自检测否认称呼（单源）——同一概念多个 owner。
本模块收敛主动侧入口：完整版人设加载（渲染唯一 owner =
``shisi.application.persona_context``）、沉浸式约束渲染。画像唯一读入口 =
shisi.agent_plane.runtime.get_profile_prompt_block（主链同款）。
所有函数动态读取、禁止模块级缓存（角色切换即时生效 + 用户隔离铁律）。
"""

from __future__ import annotations

import logging

from shisi.application import persona_context

logger = logging.getLogger("proactive_context")

# 渲染上限唯一 owner 在 shisi.application.persona_context；此处只保留同名常量
# 的历史引用，不复制数值定义。
_PERSONA_HINT_MAX_CHARS = persona_context._PERSONA_HINT_MAX_CHARS
_MES_EXAMPLE_MAX_CHARS = persona_context._MES_EXAMPLE_MAX_CHARS
_CREATOR_NOTES_MAX_CHARS = persona_context._CREATOR_NOTES_MAX_CHARS

# B-3b 迁移：渲染实现唯一 owner 在 shisi.application.persona_context。
# 以下两个名字仅作既有测试直调入场的转发（proactive_context 不再持逻辑副本）。
_persona_from_card = persona_context._persona_from_card
_persona_from_yaml = persona_context._persona_from_yaml


def load_full_persona_hint(character_id: str, user_key: str | None = None) -> str:
    """完整版人设（替代旧 ``llm_proactive.load_persona_hint`` 的 400+200 裁剪版）。

    - ``default``/``demo`` → ``config/persona.yaml``：``description`` 全量
      （YAML 多行块，自带【核心性格】【说话风格】【回复示例】【绝对禁止】四段）
      + ``creator_notes`` 的「## 通用禁令」小节（到 creator_notes 结尾）。
      「身份拷问应答」段主动场景用不上，跳过。
    - 其他 id → ``config/characters/{cid}.json``（miss 时 glob 按 id/name 兜底）：
      ``description`` + ``personality_text`` + ``speaking_style`` +
      ``mes_example``[:600] + ``creator_notes``[:400]，每段带小标题行。
    - 组装后总长上限 1200 字；取不到任何内容返回 ""。

    渲染逻辑唯一 owner = ``shisi.application.persona_context.render_persona_hint``；
    本函数只做 cid 空值短路与异常兜底（与迁移前公共边界一致）。
    每次调用动态读盘（无模块级缓存）：切角色即时生效 + 用户隔离铁律。
    """
    cid = str(character_id or "").strip()
    if not cid:
        return ""
    try:
        return persona_context.render_persona_hint(cid, user_key)
    except Exception as e:  # noqa: BLE001
        logger.debug("load_full_persona_hint failed cid=%s: %s", cid, e)
        return ""


def immersive_proactive_constraints(
    user_key: str | None = None, character_id: str | None = None,
) -> str:
    """按当前回复模式渲染主动消息约束段（决策/生成两线共用）。

    主链 ``utils.reply_mode`` 已有沉浸式/小说式双模式指令，但主动两条线从未
    注入——主动消息游离在模式约束之外（生产实证：沉浸式模式下主动消息仍带
    括号动作、客服腔追问）。本函数按当前模式动态渲染浓缩版（每次调用读真源，
    不缓存）。
    """
    try:
        from utils.reply_mode import REPLY_MODE_NOVEL, read_reply_mode

        # 2026-10-21 R7 / D-C 2026-11-08：「会话 × 角色」快照优先，未设置回落全局
        if read_reply_mode(user_key, character_id) == REPLY_MODE_NOVEL:
            return _NOVEL_CONSTRAINTS
        return _IMMERSIVE_CONSTRAINTS
    except Exception as e:  # noqa: BLE001
        logger.debug("immersive_proactive_constraints failed: %s", e)
        return ""


_IMMERSIVE_CONSTRAINTS = """【主动消息模式约束（沉浸式真人聊天）】
- 只发一条微信里真的会打出来的那句话，10~60 字；严禁括号动作/神态/旁白描写，
  严禁星号叙述。
- 非共处：你和对方不在同一物理空间，只能靠手机文字交流；禁止「我去占位置等你」
  「我们面对面聊」「咖啡馆见」这类需要身体在场的共处话术。
- 禁客服腔：不得连抛疑问句轰炸（「有什么心事吗？需要我陪你聊聊吗？」式）；
  不得模板化安慰（「我在这里陪着你」）；不得索取式收尾（「分享一下吧」）。
- 禁打卡式嘱咐：不得发送「记得吃早饭/按时吃饭/早点休息/早点睡/多喝热水」这类
  无信息量的例行关心——真人不会定时打卡发这个。问候可以，但别配套吃饭睡觉的
  嘱咐，除非画像或对话明确说对方此刻正饿着/累着。
- 时间感是底色不是台词：自然清楚现在几点、隔了多久没说话，顺着这个时间寒暄/
  接话即可；不报时、不点破「隔了多久」、不解释你怎么知道现在几点。
- 严禁生成或发送任何链接/URL：编出来的地址对方一定打不开；想推荐内容就用
  文字说清楚是什么。
- 话锋对齐：必须接得上最近对话的话题或画像里的真实近况；很久前的话题不得
  当作「刚刚」提起，不得凭空开新话头。"""

_NOVEL_CONSTRAINTS = """【主动消息模式约束（小说式）】
- 允许用括号写动作、神态细节营造画面感，但一条就好，对话本身仍要自然。
- 仍禁客服腔：不得连续疑问句轰炸（「有什么心事吗？需要我陪你聊聊吗？」式）；
  不得模板化安慰（「我在这里陪着你」）；不得索取式收尾（「分享一下吧」）；
  不得编造对方没说过的处境。
- 禁打卡式嘱咐：不得发送「记得吃早饭/按时吃饭/早点休息/早点睡/多喝热水」这类
  无信息量的例行关心——真人不会定时打卡发这个。问候可以，但别配套吃饭睡觉的
  嘱咐，除非画像或对话明确说对方此刻正饿着/累着。
- 时间感是底色不是台词：自然清楚现在几点、隔了多久没说话，顺着这个时间写即可；
  不报时、不点破「隔了多久」、不解释你怎么知道现在几点。
- 严禁生成或发送任何链接/URL：编出来的地址对方一定打不开；想推荐内容就用
  文字说清楚是什么。
- 话锋对齐：必须接得上最近对话的话题或画像里的真实近况；很久前的话题不得
  当作「刚刚」提起，不得凭空开新话头。"""

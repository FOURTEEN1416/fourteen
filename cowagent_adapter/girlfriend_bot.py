"""
GirlfriendBot — CowAgent 自定义 Bot

实现 CowAgent Bot 接口，将微信收到的消息经过完整的 AI 女友管线处理：

    消息 → 情感引擎 → 记忆上下文 → 人格 System Prompt → LLM → 回复

关键管线流程：
1. process_message() — 更新情感状态
2. get_formatted_context() — 获取最近记忆
3. build_system_prompt() — 构建完整人格提示
4. LLM chat() — 生成回复
5. after_chat() — 存储对话到记忆管线
6. ASE 引擎紧迫度更新
"""

import logging
import time
from typing import Any, Optional

from bridge.context import Context  # type: ignore
from bridge.reply import Reply, ReplyType  # type: ignore

logger = logging.getLogger("girlfriend.bot")


class GirlfriendBot:
    """
    CowAgent 兼容的 AI 女友 Bot

    与标准 CowAgent Bot 的区别：
    - 不使用 CowAgent 的 Session 管理，使用我们自己的 MemoryPipeline
    - 不使用 CowAgent 的 LLM 路由，使用我们自己的 LLM Provider + 情感引擎
    - 支持情感状态、语气风格、人格设定
    """

    def __init__(
        self,
        persona_engine: Any,
        emotion_engine: Any,
        memory_pipeline: Any,
        tone_mimic: Any,
        llm: Any,
        ase_engine: Optional[Any] = None,
    ):
        self.persona = persona_engine
        self.emotion = emotion_engine
        self.memory = memory_pipeline
        self.tone_mimic = tone_mimic
        self.llm = llm
        self.ase = ase_engine

        # CowAgent 兼容: sessions 属性
        self.sessions = _GirlfriendSessionManager(self)

        # 主动消息目标联系人 WeChat ID
        self.WECHAT_CONTACT_ID = ""

        self._conversation_count = 0
        self._start_time = time.time()

    def reply(self, query: str, context: Context = None) -> Reply:
        """
        CowAgent Bot 接口 — 接收消息并返回回复

        Args:
            query: 用户消息文本
            context: CowAgent 上下文

        Returns:
            Reply 对象
        """
        try:
            # ── 1. 情感引擎：处理消息 → 更新情感状态 ──
            #    返回 EmotionalState 对象（含 emotion, energy, affinity, intensity）
            emotion_state = self.emotion.process_message(query)
            logger.debug("情感状态: emotion=%s energy=%.2f affinity=%d",
                         emotion_state.emotion.value, emotion_state.energy, emotion_state.affinity)

            # ── 2. 获取记忆上下文 ──
            memory_context = self.memory.get_formatted_context(n_chats=6)

            # ── 3. 语气风格提示 ──
            style_prompt = ""
            if self.tone_mimic:
                try:
                    style_prompt = self.tone_mimic.get_style_prompt()
                except Exception:
                    pass

            # ── 4. 构建 System Prompt（人格 + 情感 + 记忆 + 风格） ──
            system_prompt = self.persona.build_system_prompt(
                emotion_state=emotion_state,
                style_prompt=style_prompt,
                chat_history=memory_context or "",
                user_input=query,
            )

            # ── 5. LLM 生成回复 ──
            reply_text = self.llm.chat_sync(
                query=query,
                system_prompt=system_prompt,
                temperature=self._get_temperature(),
            )

            # ── 6. 存储对话到记忆管线 ──
            self.memory.after_chat(
                user_msg=query,
                reply=reply_text,
                emotion_tag=emotion_state.emotion.value,
            )

            if self.ase:
                try:
                    self.ase.on_chat(query, reply_text)
                except Exception:
                    pass

            self._conversation_count += 1

            logger.info(
                "[%d] %s → %s  | 情感=%s",
                self._conversation_count,
                query[:40],
                reply_text[:40],
                emotion_state.emotion.value,
            )

            return Reply(ReplyType.TEXT, reply_text)

        except Exception as e:
            logger.exception("GirlfriendBot 处理消息异常: %s", e)
            return Reply(ReplyType.TEXT, "嗯…我刚刚走神了，你刚才说什么？")

    def _get_temperature(self) -> float:
        """根据当前情感状态调整 LLM 温度"""
        emotion_temps = {
            "开心": 0.90,
            "生气": 0.65,
            "伤心": 0.70,
            "撒娇": 0.95,
            "吃醋": 0.80,
            "傲娇": 0.85,
            "温柔": 0.85,
            "调皮": 0.95,
            "疲惫": 0.70,
            "平常": 0.85,
        }
        return emotion_temps.get(self.emotion.state.emotion.value, 0.85)

    def health_check(self) -> dict:
        """健康检查"""
        return {
            "persona_ok": self.persona is not None,
            "emotion_ok": self.emotion is not None,
            "memory_ok": self.memory is not None,
            "llm_ok": self.llm is not None,
        }

    def send_message(self, to_user: str, message: str) -> bool:
        """
        Send proactive message via CowAgent WeChat channel.

        Args:
            to_user: WeChat user ID (wxid) of the recipient
            message: Message text to send

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            # Try to get WeChat channel from running CowAgent instance
            import cowagent_src.app as cowapp

            if hasattr(cowapp, "_channel_mgr") and cowapp._channel_mgr:
                app = cowapp._channel_mgr
                # Iterate through registered channels to find WeChat
                for ch_name in ["weixin", "wechat"]:
                    ch = app.get_channel(ch_name)
                    if ch is not None:
                        from bridge.context import Context, ContextType  # type: ignore
                        from bridge.reply import Reply, ReplyType  # type: ignore

                        context = Context(ContextType.TEXT, message)
                        context["receiver"] = to_user
                        context["is_group"] = False

                        reply = Reply(ReplyType.TEXT, message)
                        ch.send(reply, context)
                        logger.info("主动消息已发送 -> %s: %s", to_user, message[:50])
                        return True

            # Fallback: log the message if channel not available
            logger.warning("微信通道不可用，消息未发送: %s", message[:50])
            return False
        except Exception as e:
            logger.error("发送主动消息失败: %s", e)
            return False


class _GirlfriendSessionManager:
    """
    伪 SessionManager — 兼容 CowAgent 的 Bot.sessions 接口

    CowAgent 的 Role 插件、Bridge 等会调用 bot.sessions.build_session() 等，
    这里我们提供兼容的哑实现。
    """

    def __init__(self, bot: GirlfriendBot):
        self.bot = bot

    def build_session(self, session_id: str, system_prompt: str = None):  # type: ignore
        """兼容 CowAgent SessionManager.build_session"""
        return _CompatSession(session_id)

    def session_query(self, query: str, session_id: str):
        """兼容接口"""
        return query, session_id


class _CompatSession:
    """兼容 CowAgent Session 接口的哑对象"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.system_prompt = ""

    def set_system_prompt(self, prompt: str):
        self.system_prompt = prompt

"""人格应用服务 — 将 my_character.persona_engine 接入 shisi 应用层。

这是双轨架构统一的第一步：main.py 不再直接依赖 my_character.persona_engine，
而是通过 shisi.application.persona_service.PersonaService 访问人格构建能力。
底层实现暂时保留 my_character.persona_engine，后续可逐步替换为 shisi 原生实现。
"""

from __future__ import annotations

from typing import Any

from my_character.persona_engine import PersonaEngine


class PersonaService:
    """包装 PersonaEngine，为 orchestrator 提供兼容的 build_system_prompt 入口。"""

    def __init__(
        self,
        config_loader: Any,
        llm_gateway: Any,
        emotion_engine: Any | None = None,
        **engine_kwargs: Any,
    ) -> None:
        self._engine = PersonaEngine(
            config_loader=config_loader,
            llm_gateway=llm_gateway,
            emotion_engine=emotion_engine,
            **engine_kwargs,
        )

    def build_system_prompt(
        self,
        emotion_state: Any = None,
        memory_context: str = "",
        rag_context: str = "",
        chat_summary: str = "",
        world_info: str = "",
    ) -> str:
        """代理到底层 PersonaEngine 的 system prompt 构建。"""
        return self._engine.build_system_prompt(
            emotion_state=emotion_state,
            memory_context=memory_context,
            rag_context=rag_context,
            chat_summary=chat_summary,
            world_info=world_info,
        )

    @property
    def engine(self) -> PersonaEngine:
        """暴露底层引擎，供 consistency_checker 等仍依赖 PersonaEngine 的组件使用。"""
        return self._engine

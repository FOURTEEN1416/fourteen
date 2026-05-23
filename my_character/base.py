from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EmotionBase(ABC):
    @abstractmethod
    def analyze(self, user_message: str, context: str = "") -> Any:
        ...

    @abstractmethod
    def process_message(self, user_message: str, context: Any = None) -> Any:
        ...

    @abstractmethod
    def health_check(self) -> dict:
        ...


class PersonaBase(ABC):
    @abstractmethod
    def build_system_prompt(self, emotion_state: str = "", memory_context: str = "",
                           rag_context: str = "", **kwargs) -> str:
        ...

    @property
    @abstractmethod
    def profile(self) -> Any:
        ...

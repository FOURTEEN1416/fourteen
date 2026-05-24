"""领域服务导出"""

from .emotion_detector import calculate_affection_delta, detect_emotion
from .prompt_builder import build, merge_with_persona_prompt

__all__ = ["detect_emotion", "calculate_affection_delta", "build", "merge_with_persona_prompt"]

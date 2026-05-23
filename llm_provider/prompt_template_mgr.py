from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("prompt_template")

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

VAR_PATTERN = re.compile(r"\{(\w+)\}")


class PromptTemplate:
    def __init__(self, name: str, template: str, version: str = "1.0",
                 description: str = ""):
        self.name = name
        self.template = template
        self.version = version
        self.description = description
        self._variables = set(VAR_PATTERN.findall(template))

    def render(self, **kwargs) -> str:
        missing = self._variables - set(kwargs.keys())
        if missing:
            logger.warning("Template %s missing variables: %s", self.name, missing)
        result = self.template
        for key, value in kwargs.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result

    @property
    def variables(self) -> set:
        return self._variables


class PromptTemplateMgr:
    def __init__(self, template_dir: str = "config/prompts"):
        self.template_dir = Path(os.path.abspath(template_dir))
        self.template_dir.mkdir(parents=True, exist_ok=True)
        self._templates: Dict[str, PromptTemplate] = {}
        self._load_all()

    def _load_all(self):
        if not HAS_YAML:
            logger.warning("PyYAML not installed, using default templates only")
            self._load_defaults()
            return
        for filepath in self.template_dir.glob("*.yaml"):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}  # type: ignore
                name = data.get("name", filepath.stem)
                self._templates[name] = PromptTemplate(
                    name=name,
                    template=data.get("template", ""),
                    version=data.get("version", "1.0"),
                    description=data.get("description", ""),
                )
            except Exception as e:
                logger.warning("Failed to load template %s: %s", filepath, e)
        self._load_defaults()

    def _load_defaults(self):
        defaults = {
            "system_prompt": PromptTemplate(
                name="system_prompt",
                template=(
                    "{persona_desc}\n\n"
                    "【核心锚点】\n{core_anchors}\n\n"
                    "【当前情感状态】\n{emotion_state}\n\n"
                    "【记忆上下文】\n{memory_context}\n\n"
                    "【风格参考】\n{style_reference}\n\n"
                    "请严格按照以上人设和情感状态回复，保持语气一致。"
                ),
                version="2.0",
                description="Main system prompt template",
            ),
            "emotion_analysis": PromptTemplate(
                name="emotion_analysis",
                template=(
                    "分析以下消息的情感，考虑近几轮对话上下文：\n\n"
                    "近3轮对话：\n{recent_context}\n\n"
                    "当前消息：{message}\n\n"
                    '回复JSON格式：{"primary": {"type": "情感类型", "intensity": 0.0-1.0}, '
                    '"secondary": [{"type": "情感类型", "intensity": 0.0-1.0}]}'
                ),
                version="1.0",
                description="Emotion analysis prompt",
            ),
            "reflection": PromptTemplate(
                name="reflection",
                template=(
                    "基于以下对话内容，生成简短的内心独白（20字以内）：\n\n"
                    "最近对话：\n{recent_chats}\n\n"
                    "当前情感：{emotion_state}\n"
                    "只输出内心想法，不要输出其他内容。"
                ),
                version="1.0",
                description="Inner reflection prompt",
            ),
            "proactive_message": PromptTemplate(
                name="proactive_message",
                template=(
                    "基于近3天的对话上下文，生成一条自然的主动消息：\n\n"
                    "近期对话摘要：{recent_summary}\n"
                    "当前情感：{emotion_state}\n"
                    "好感度等级：{affinity_level}\n"
                    "距离上次聊天：{hours_since_last}小时\n\n"
                    "要求：符合小暖的傲娇人设，自然不做作，15字以内。"
                ),
                version="1.0",
                description="Proactive message generation prompt",
            ),
        }
        for name, tmpl in defaults.items():
            if name not in self._templates:
                self._templates[name] = tmpl

    def get(self, name: str) -> Optional[PromptTemplate]:
        return self._templates.get(name)

    def render(self, name: str, **kwargs) -> str:
        tmpl = self._templates.get(name)
        if tmpl:
            return tmpl.render(**kwargs)
        logger.warning("Template not found: %s", name)
        return ""

    def reload(self):
        self._templates.clear()
        self._load_all()
        logger.info("Templates reloaded: %d templates", len(self._templates))

    @property
    def template_names(self) -> list:
        return list(self._templates.keys())

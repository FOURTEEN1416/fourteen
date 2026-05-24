"""chara_card_v2解析器 — 支持标准V2/V3 + 十四APP prompts格式 + SillyTavern格式。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .models import (
    CardFormat,
    CharaCardV2,
    CharacterData,
    CharacterExtensions,
    WorldInfoBook,
    WorldInfoEntry,
)

logger = logging.getLogger("shisi.character.character_card_v2")


class CharaCardV2Parser:
    """标准chara_card_v2/V3解析器"""

    @staticmethod
    def parse(data: dict[str, Any]) -> CharaCardV2:
        if "spec" in data:
            return CharaCardV2Parser._from_standard(data)
        if "data" in data and "prompts" in data.get("data", {}):
            return AiyuPromptsParser.parse(data)
        return CharaCardV2Parser._from_v1(data)

    @staticmethod
    def parse_file(path: Path | str) -> CharaCardV2:
        p = Path(path)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return CharaCardV2Parser.parse(data)

    @classmethod
    def _from_standard(cls, data: dict[str, Any]) -> CharaCardV2:
        char_data = data.get("data", {})
        return CharaCardV2(
            spec=data.get("spec", "chara_card_v2"),
            spec_version=data.get("spec_version", "2.0"),
            data=CharacterData(
                name=char_data.get("name", ""),
                description=char_data.get("description", ""),
                character_version=char_data.get("character_version", "1.0"),
                personality=char_data.get("personality", ""),
                scenario=char_data.get("scenario", ""),
                first_mes=char_data.get("first_mes", ""),
                mes_example=char_data.get("mes_example", ""),
                alternate_greetings=char_data.get("alternate_greetings", []),
                system_prompt=char_data.get("system_prompt", ""),
                post_history_instructions=char_data.get("post_history_instructions", ""),
                creator_notes=char_data.get("creator_notes", ""),
                tags=char_data.get("tags", []),
                creator=char_data.get("creator", "unknown"),
                character_book=cls._parse_world_info(char_data.get("character_book")),
                extensions=cls._parse_extensions(char_data.get("extensions", {})),
            ),
        )

    @classmethod
    def _from_v1(cls, data: dict[str, Any]) -> CharaCardV2:
        return CharaCardV2(
            spec="chara_card_v2",
            spec_version="2.0",
            data=CharacterData(
                name=data.get("name", ""),
                description=data.get("description", ""),
                personality=data.get("personality", ""),
                scenario=data.get("scenario", ""),
                first_mes=data.get("first_mes", ""),
                mes_example=data.get("mes_example", ""),
                creator_notes=data.get("creatorcomment", ""),
                tags=data.get("tags", []),
                creator=data.get("creator", "unknown"),
                extensions=CharacterExtensions(
                    talkativeness=data.get("talkativeness", 0.5),
                    fav=data.get("fav", False),
                ),
            ),
        )

    @staticmethod
    def _parse_world_info(wb: Any) -> WorldInfoBook | None:
        if not wb or not isinstance(wb, dict):
            return None
        entries = []
        for entry in wb.get("entries", []):
            try:
                entries.append(WorldInfoEntry(
                    id=entry.get("id", 0),
                    keys=entry.get("keys", []),
                    content=entry.get("content", ""),
                    secondary_keys=entry.get("secondary_keys", []),
                    comment=entry.get("comment", ""),
                    constant=entry.get("constant", False),
                    selective=entry.get("selective", True),
                    insertion_order=entry.get("insertion_order", 100),
                    enabled=entry.get("enabled", True),
                    position=str(entry.get("position", "0")),
                    extensions=entry.get("extensions", {}),
                ))
            except Exception as e:
                logger.warning("WorldInfo条目解析跳过: %s", e)
        return WorldInfoBook(
            name=wb.get("name", ""),
            entries=entries,
            extensions=wb.get("extensions", {}),
        )

    @staticmethod
    def _parse_extensions(ext: dict[str, Any]) -> CharacterExtensions:
        return CharacterExtensions(
            talkativeness=ext.get("talkativeness", 0.5),
            fav=ext.get("fav", False),
            world=ext.get("world", ""),
            depth_prompt=ext.get("depth_prompt"),
            regex_scripts=ext.get("regex_scripts", []),
        )


class AiyuPromptsParser:
    """十四APP prompts格式解析器 — 将十四导出的prompts结构转为标准CharaCardV2"""

    @staticmethod
    def parse(data: dict[str, Any]) -> CharaCardV2:
        prompts = data.get("data", {}).get("prompts", {})
        if not prompts:
            raise ValueError("十四prompts格式中无prompts数据")

        pid = next(iter(prompts))
        prompt_data = prompts[pid].get("data", {})

        name = prompt_data.get("name", "")
        description = prompt_data.get("description", "")
        personality = prompt_data.get("personality", "")
        scenario = prompt_data.get("scenario", "")
        creator_notes = prompt_data.get("creator_notes", "")

        first_mes = AiyuPromptsParser._extract_first_mes(creator_notes)
        tags = AiyuPromptsParser._extract_tags(creator_notes)

        return CharaCardV2(
            spec="chara_card_v2",
            spec_version="2.0",
            data=CharacterData(
                name=name or "未命名角色",
                description=description,
                personality=personality,
                scenario=scenario,
                first_mes=first_mes,
                creator_notes=creator_notes,
                tags=tags,
                creator="shisi_app",
            ),
        )

    @staticmethod
    def _extract_first_mes(creator_notes: str) -> str:
        patterns = [
            r"第一句话[：:]\s*(.+)",
            r"开场白[：:]\s*(.+)",
            r"first_mes[：:]\s*(.+)",
        ]
        for pat in patterns:
            m = re.search(pat, creator_notes)
            if m:
                return m.group(1).strip()
        return ""

    @staticmethod
    def _extract_tags(creator_notes: str) -> list[str]:
        tags: list[str] = []
        keyword_map = {
            "病娇": "病娇", "傲娇": "傲娇", "温柔": "温柔", "可爱": "可爱",
            "高冷": "高冷", "活泼": "活泼", "内向": "内向", "纯爱": "纯爱",
            "妹妹": "妹妹", "女友": "女友", "老师": "老师",
        }
        text_lower = creator_notes.lower()
        for kw, tag in keyword_map.items():
            if kw in text_lower:
                tags.append(tag)
        return tags


class SillyTavernParser:
    """SillyTavern格式解析器 — 委托给现有character_card模块"""

    @staticmethod
    def parse(data: dict[str, Any]) -> CharaCardV2:
        return CharaCardV2Parser.parse(data)


class ParserDispatcher:
    """策略模式分发 — 自动检测格式并选择对应解析器"""

    _parsers: list[tuple[type, str]] = [
        (AiyuPromptsParser, "shisi_prompts"),
        (CharaCardV2Parser, "chara_card_v2"),
    ]

    @classmethod
    def parse(cls, data: dict[str, Any]) -> tuple[CharaCardV2, CardFormat]:
        if "data" in data and "prompts" in data.get("data", {}):
            card = AiyuPromptsParser.parse(data)
            return card, CardFormat.AIYU_PROMPTS

        if "spec" in data:
            spec = data["spec"]
            if "v3" in spec:
                card = CharaCardV2Parser.parse(data)
                return card, CardFormat.CHARA_CARD_V3
            card = CharaCardV2Parser.parse(data)
            return card, CardFormat.CHARA_CARD_V2

        card = CharaCardV2Parser.parse(data)
        return card, CardFormat.CHARA_CARD_V2

    @classmethod
    def parse_file(cls, path: Path | str) -> tuple[CharaCardV2, CardFormat]:
        p = Path(path)
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return cls.parse(data)


def to_persona_config(card: CharaCardV2) -> dict[str, Any]:
    """将CharaCardV2转为PersonaEngine兼容配置"""
    return {
        "name": card.data.name,
        "description": card.data.description,
        "personality": card.data.personality,
        "scenario": card.data.scenario,
        "greeting": card.data.first_mes,
        "examples": card.data.mes_example,
        "system_prompt": card.data.system_prompt,
        "tags": card.data.tags,
        "creator_notes": card.data.creator_notes,
    }

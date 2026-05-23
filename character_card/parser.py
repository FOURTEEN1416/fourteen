"""
角色卡解析器 - 支持JSON/PNG格式解析

设计要点:
  - 不依赖pypng，使用Python内置struct+zlib解析PNG元数据
  - 支持V1规范自动升级到V2
  - V3 (chara_card_v3) 优先解析
  - 所有解析异常都有明确错误信息
"""

from __future__ import annotations

import base64
import json
import logging
import struct
from typing import Any, Dict, Optional

from .models import (
    CharacterCard,
    CharacterData,
    CharacterExtensions,
    WorldInfoBook,
    WorldInfoEntry,
)

logger = logging.getLogger("character_card.parser")


# 最大PNG文件大小: 50MB (防止恶意超大图片消耗内存)
MAX_PNG_SIZE = 50 * 1024 * 1024

# PNG chunk迭代上限 (防止畸形PNG死循环)
MAX_PNG_CHUNKS = 10000


class CharacterCardParser:
    """角色卡解析器 - 线程安全，无状态"""

    @staticmethod
    def parse_json(json_str: str) -> CharacterCard:
        """从JSON字符串解析角色卡"""
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"角色卡JSON格式错误: {e}") from e
        return CharacterCardParser._from_dict(data)

    @staticmethod
    def parse_json_file(file_path: str) -> CharacterCard:
        """从JSON文件解析角色卡"""
        import pathlib
        path = pathlib.Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"角色卡文件不存在: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return CharacterCardParser.parse_json(f.read())

    @staticmethod
    def parse_png(png_bytes: bytes) -> CharacterCard:
        """
        从PNG二进制数据解析角色卡（从tEXt/ccv3 chunk提取）

        纯Python实现，不依赖pypng:
          - PNG文件结构: signature(8) + IHDR + ... + tEXt/ccv3 + ... + IEND
          - tEXt chunk: 长度(4) + "tEXt"(4) + keyword + NUL + text
          - 我们遍历chunks查找 'chara' 或 'ccv3' 关键字
        """
        if png_bytes[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError("不是有效的PNG文件")

        # 大小限制检查
        if len(png_bytes) > MAX_PNG_SIZE:
            raise ValueError(f"PNG文件过大 ({len(png_bytes)} > {MAX_PNG_SIZE} bytes)")

        pos = 8  # 跳过PNG signature
        chara_data = None
        ccv3_data = None
        chunk_count = 0

        while pos < len(png_bytes):
            chunk_count += 1
            if chunk_count > MAX_PNG_CHUNKS:
                raise ValueError("PNG chunk数量超过上限，可能为畸形文件")
            # 每个chunk: length(4) + type(4) + data(length) + CRC(4)
            if pos + 8 > len(png_bytes):
                break
            length = struct.unpack_from('>I', png_bytes, pos)[0]
            chunk_type = png_bytes[pos + 4:pos + 8].decode('latin-1', errors='replace')

            if pos + 8 + length > len(png_bytes):
                break
            chunk_data = png_bytes[pos + 8:pos + 8 + length]

            if chunk_type == 'IEND':
                break

            if chunk_type == 'tEXt' and length > 0:
                # tEXt: keyword + NUL(1) + text
                null_pos = chunk_data.find(b'\x00')
                if null_pos > 0:
                    keyword = chunk_data[:null_pos].decode('latin-1').lower()
                    text_data = chunk_data[null_pos + 1:]
                    try:
                        decoded = base64.b64decode(text_data)
                        if keyword == 'chara':
                            chara_data = decoded.decode('utf-8')
                        elif keyword == 'ccv3':
                            ccv3_data = decoded.decode('utf-8')
                    except (base64.binascii.Error, UnicodeDecodeError):
                        # 可能是未编码的文本，直接尝试
                        try:
                            if keyword == 'chara':
                                chara_data = text_data.decode('utf-8')
                            elif keyword == 'ccv3':
                                ccv3_data = text_data.decode('utf-8')
                        except UnicodeDecodeError:
                            pass

            pos += 12 + length  # length(4) + type(4) + data + CRC(4)

        # V3优先
        if ccv3_data:
            try:
                return CharacterCardParser.parse_json(ccv3_data)
            except Exception as e:
                logger.warning("V3角色卡解析失败，尝试V2: %s", e)

        if chara_data:
            try:
                return CharacterCardParser.parse_json(chara_data)
            except Exception as e:
                raise ValueError(f"角色卡数据解析失败: {e}") from e

        raise ValueError("PNG中未找到角色卡数据 (tEXt chunk中缺少chara/ccv3关键字)")

    @staticmethod
    def parse_png_file(file_path: str) -> CharacterCard:
        """从PNG文件解析角色卡"""
        import pathlib
        path = pathlib.Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"角色卡PNG文件不存在: {file_path}")
        with open(file_path, "rb") as f:
            return CharacterCardParser.parse_png(f.read())

    @staticmethod
    def parse_auto(path_or_json: str) -> CharacterCard:
        """
        自动检测格式并解析

        支持:
          - .json 文件
          - .png 文件
          - JSON字符串
        """
        s = path_or_json.strip()
        # 判断是否为文件路径
        if '\n' not in s and not s.startswith('{'):
            import pathlib
            p = pathlib.Path(s)
            if p.exists():
                if s.lower().endswith('.png'):
                    return CharacterCardParser.parse_png_file(s)
                else:
                    return CharacterCardParser.parse_json_file(s)
        # 尝试JSON解析
        return CharacterCardParser.parse_json(s)

    # ── 内部方法 ──────────────────────────────────────────

    @classmethod
    def _from_dict(cls, data: Dict[str, Any]) -> CharacterCard:
        """从字典构建CharacterCard"""
        # V1格式（无spec字段，顶层直接是data）
        if "spec" not in data:
            return cls._from_v1(data)

        spec = data.get("spec", "chara_card_v2")
        spec_version = data.get("spec_version", "2.0")
        char_data = data.get("data", {})

        return CharacterCard(
            spec=spec,
            spec_version=spec_version,
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
    def _from_v1(cls, v1_data: Dict[str, Any]) -> CharacterCard:
        """V1格式 → V2格式转换"""
        return CharacterCard(
            spec="chara_card_v2",
            spec_version="2.0",
            data=CharacterData(
                name=v1_data.get("name", ""),
                description=v1_data.get("description", ""),
                personality=v1_data.get("personality", ""),
                scenario=v1_data.get("scenario", ""),
                first_mes=v1_data.get("first_mes", ""),
                mes_example=v1_data.get("mes_example", ""),
                creator_notes=v1_data.get("creatorcomment", ""),
                tags=v1_data.get("tags", []),
                creator=v1_data.get("creator", "unknown"),
                extensions=CharacterExtensions(
                    talkativeness=v1_data.get("talkativeness", 0.5),
                    fav=v1_data.get("fav", False),
                ),
            ),
        )

    @staticmethod
    def _parse_world_info(wb: Any) -> Optional[WorldInfoBook]:
        """解析WorldInfo角色书"""
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
    def _parse_extensions(ext: Dict[str, Any]) -> CharacterExtensions:
        """解析扩展字段"""
        return CharacterExtensions(
            talkativeness=ext.get("talkativeness", 0.5),
            fav=ext.get("fav", False),
            world=ext.get("world", ""),
            depth_prompt=ext.get("depth_prompt"),
            regex_scripts=ext.get("regex_scripts", []),
        )

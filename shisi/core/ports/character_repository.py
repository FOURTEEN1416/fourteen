"""角色仓储端口（Protocol接口）"""

from __future__ import annotations

from typing import Protocol

from ..models.character_aggregate import CharacterAggregate


class CharacterRepository(Protocol):
    def get_by_id(self, character_id: str) -> CharacterAggregate | None: ...

    def get_active(self) -> CharacterAggregate | None: ...

    def list_all(self) -> list[CharacterAggregate]: ...

    def save(self, character: CharacterAggregate) -> None: ...

    def set_active(self, character_id: str) -> bool: ...

    def delete(self, character_id: str) -> bool: ...

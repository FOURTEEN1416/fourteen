"""角色ID值对象"""

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class CharacterId:
    value: str

    def __post_init__(self):
        if not self.value:
            raise ValueError("CharacterId cannot be empty")

    @classmethod
    def generate(cls) -> "CharacterId":
        return cls(str(uuid.uuid4())[:8])

    @classmethod
    def from_string(cls, value: str) -> "CharacterId":
        return cls(value)
